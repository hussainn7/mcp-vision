"""
Model backends — bring your own model.

agent.py talks to exactly one shape, regardless of provider:

    chat(messages, tools=None) -> {"role": "assistant", "content": str,
                                    "tool_calls": [{"id": str,
                                        "function": {"name": str, "arguments": dict}}]}

`messages` is the same canonical transcript agent.py keeps: role in
{system,user,assistant,tool}, content str, tool_calls (assistant only, each
carrying an "id" every backend can round-trip), tool_name + tool_call_id
(tool role only — tool_name is what Ollama expects, tool_call_id is what
every cloud provider expects; a tool-result message carries both so it works
unmodified against whichever backend produced the call).

Providers disagree hard on wire format: Anthropic has no "tool" role and
wants system as a top-level field with tool_result blocks folded into a user
turn; OpenAI-style APIs want tool_calls.arguments serialized as a JSON
*string*, not a dict, and tool results addressed by tool_call_id. Each
backend's whole job is that transcoding, both directions, so nothing above
this file ever needs to know which provider it's talking to.

Backends, selected by cfg.model_backend (or `--model` on the CLI):
    local     — Ollama, fully on-machine. Default. No API key, no network.
    anthropic — Claude, via the Messages API.
    openai    — GPT, via the Chat Completions API.
    gemini    — Google's OpenAI-compatible endpoint. Free tier available.
    nvidia    — NIM, also OpenAI-compatible (build.nvidia.com).

Cloud backends need their key in .env (never committed, see config.py for
the exact env var names) and each call leaves the machine and costs money —
that trade is opt-in per run, never silent.
"""

import json
import time
import urllib.error
import urllib.request

from config import cfg


class BackendError(Exception):
    """A cloud backend couldn't produce a reply: bad/missing key, network
    failure, or a response shape we don't understand. agent.run() catches
    this at the top level so a bad key ends the run cleanly (traced, judged)
    instead of crashing the process."""


# --- transport: one real poster, swappable in tests --------------------------

def _urllib_post(url, headers, body, timeout):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < max_retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise BackendError(f"HTTP {e.code} from {url}: {e.read().decode()[:500]}")
        except urllib.error.URLError as e:
            raise BackendError(f"network error calling {url}: {e.reason}")
        except (TimeoutError, OSError) as e:
            raise BackendError(f"error calling {url}: {e}")


def _post_json(url, headers, body, timeout=60, _post=None):
    return (_post or _urllib_post)(url, headers, body, timeout)


# --- canonical <-> OpenAI-style wire format (openai, nvidia, gemini) --------

def _to_openai_messages(messages):
    out = []
    for m in messages:
        role = m.get("role", "user")
        if role == "assistant" and m.get("tool_calls"):
            tc_list = []
            for i, tc in enumerate(m["tool_calls"]):
                entry = {
                    "id": tc.get("id", f"call_{i}"),
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": json.dumps(tc["function"].get("arguments", {})),
                    },
                }
                # Gemini 3.x: echo thought_signature back or tool calls fail
                if tc.get("thought_signature"):
                    entry["thought_signature"] = tc["thought_signature"]
                tc_list.append(entry)
            out.append({
                "role": "assistant",
                "content": m.get("content") or None,
                "tool_calls": tc_list,
            })
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id", "call_0"),
                "content": str(m.get("content", "")),
            })
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out


def _parse_openai_response(data):
    try:
        choice = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise BackendError(f"unexpected response shape: {str(data)[:300]}")

    tool_calls = []
    for tc in choice.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        entry = {"id": tc.get("id", ""),
                 "function": {"name": tc["function"]["name"], "arguments": args}}
        # Gemini 3.x: preserve thought_signature for round-trip
        if tc.get("thought_signature"):
            entry["thought_signature"] = tc["thought_signature"]
        tool_calls.append(entry)

    msg = {"role": "assistant", "content": (choice.get("content") or "").strip()}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def make_openai_compat_chat(base_url, api_key, model, key_env, _post=None):
    """Shared by openai, gemini (OpenAI-compat endpoint) and nvidia (NIM) —
    same wire format, different base URL/key/model."""
    def chat(messages, tools=None):
        if not api_key:
            raise BackendError(f"missing API key: set {key_env} in .env")
        body = {"model": model, "messages": _to_openai_messages(messages)}
        if tools:
            body["tools"] = tools  # already OpenAI-shaped — this repo's native format
        data = _post_json(f"{base_url}/chat/completions",
                          {"Authorization": f"Bearer {api_key}"}, body, _post=_post)
        return _parse_openai_response(data)
    return chat


# --- canonical <-> Anthropic wire format -------------------------------------

def _to_anthropic_tools(schemas):
    return [{"name": s["function"]["name"],
             "description": s["function"].get("description", ""),
             "input_schema": s["function"].get("parameters", {"type": "object", "properties": {}})}
            for s in schemas]


def _to_anthropic_messages(messages):
    """Anthropic has no top-level system role and no tool role: system text
    becomes a separate field, and tool results fold into a user turn as
    tool_result blocks addressed by tool_use_id."""
    system = "\n\n".join(m["content"] for m in messages
                         if m.get("role") == "system" and m.get("content")) or None

    out, pending = [], []

    def flush():
        nonlocal pending
        if pending:
            out.append({"role": "user", "content": pending})
            pending = []

    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            pending.append({"type": "tool_result",
                            "tool_use_id": m.get("tool_call_id", "call_0"),
                            "content": str(m.get("content", ""))})
            continue
        flush()
        if role == "assistant" and m.get("tool_calls"):
            content = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            content += [{"type": "tool_use", "id": tc.get("id", "call_0"),
                        "name": tc["function"]["name"],
                        "input": tc["function"].get("arguments", {})}
                       for tc in m["tool_calls"]]
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": role, "content": m.get("content", "")})
    flush()
    return system, out


def _parse_anthropic_response(data):
    blocks = data.get("content")
    if blocks is None:
        raise BackendError(f"unexpected response shape: {str(data)[:300]}")

    text = "".join(b["text"] for b in blocks if b.get("type") == "text").strip()
    tool_calls = [{"id": b.get("id", ""), "function": {"name": b["name"], "arguments": b.get("input", {})}}
                  for b in blocks if b.get("type") == "tool_use"]

    msg = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def make_anthropic_chat(api_key, model, _post=None):
    def chat(messages, tools=None):
        if not api_key:
            raise BackendError("missing API key: set ANTHROPIC_API_KEY in .env")
        system, anth_messages = _to_anthropic_messages(messages)
        body = {"model": model, "max_tokens": 1024, "messages": anth_messages}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = _to_anthropic_tools(tools)
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        data = _post_json("https://api.anthropic.com/v1/messages", headers, body, _post=_post)
        return _parse_anthropic_response(data)
    return chat


# --- local (Ollama) — the default, nothing leaves the machine ---------------

def make_local_chat(host, model, keep_alive):
    def chat(messages, tools=None):
        import ollama  # lazy: bench/demos run without ollama installed or running
        client = ollama.Client(host=host)
        kwargs = {"tools": tools} if tools else {}
        reply = client.chat(model=model, messages=messages, think=False,
                            keep_alive=keep_alive, **kwargs)
        raw = reply["message"]
        tool_calls = [{"id": f"local_{i}",
                       "function": {"name": tc["function"]["name"],
                                   "arguments": tc["function"].get("arguments", {})}}
                      for i, tc in enumerate(raw.get("tool_calls") or [])]
        msg = {"role": "assistant", "content": (raw.get("content") or "").strip()}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg
    return chat


# --- native Gemini API (generateContent) ----------------------------------
# The OpenAI-compat endpoint is broken for Gemini 3.x tool calling because it
# requires thought_signature round-tripping that the compat layer doesn't
# expose. This backend uses the native REST API directly.

def _to_gemini_contents(messages):
    """Convert canonical messages to Gemini's contents format."""
    system_parts = []
    contents = []
    # Track thought signatures per tool call for round-tripping
    pending_thought_sigs = {}

    for m in messages:
        role = m.get("role", "user")
        if role == "system":
            system_parts.append({"text": m.get("content", "")})
            continue
        if role == "assistant":
            parts = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fc_part = {
                    "functionCall": {
                        "name": tc["function"]["name"],
                        "args": tc["function"].get("arguments", {}),
                    }
                }
                # Gemini 3.x: thoughtSignature is a sibling of functionCall
                if tc.get("thought_signature"):
                    fc_part["thoughtSignature"] = tc["thought_signature"]
                parts.append(fc_part)
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            # Tool results are "user" role with functionResponse parts
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": m.get("tool_name", "unknown"),
                        "response": {"result": str(m.get("content", ""))},
                    }
                }]
            })
        else:
            contents.append({"role": "user", "parts": [{"text": m.get("content", "")}]})

    return system_parts, contents


def _gemini_tools_from_schemas(schemas):
    """Convert OpenAI-style tool schemas to Gemini function declarations."""
    if not schemas:
        return None
    decls = []
    for s in schemas:
        fn = s.get("function", s)
        params = fn.get("parameters", {"type": "object", "properties": {}})
        # Gemini doesn't accept "required" inside parameters the same way;
        # keep it if present, strip empty lists
        decl = {
            "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": params,
        }
        decls.append(decl)
    return [{"functionDeclarations": decls}]


def _parse_gemini_response(data):
    """Parse Gemini generateContent response into canonical format."""
    try:
        candidate = data["candidates"][0]
        parts = candidate["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise BackendError(f"unexpected Gemini response: {str(data)[:500]}")

    content_text = ""
    tool_calls = []

    for part in parts:
        if "text" in part:
            content_text += part["text"]
        elif "functionCall" in part:
            fc = part["functionCall"]
            entry = {
                "id": fc.get("id", f"call_{len(tool_calls)}"),
                "function": {
                    "name": fc["name"],
                    "arguments": dict(fc.get("args", {})),
                },
            }
            # Gemini 3.x: thoughtSignature is a sibling of functionCall in the part
            if "thoughtSignature" in part:
                entry["thought_signature"] = part["thoughtSignature"]
            tool_calls.append(entry)

    msg = {"role": "assistant", "content": content_text.strip()}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def make_gemini_native_chat(api_key, model, _post=None):
    """Native Gemini API backend — handles thought_signature properly."""
    def chat(messages, tools=None):
        if not api_key:
            raise BackendError("missing API key: set GEMINI_API_KEY in .env")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        system_parts, contents = _to_gemini_contents(messages)

        body = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": system_parts}
        gemini_tools = _gemini_tools_from_schemas(tools) if tools else None
        if gemini_tools:
            body["tools"] = gemini_tools
        body["generationConfig"] = {"temperature": 0.2}

        data = _post_json(url, {}, body, _post=_post)
        return _parse_gemini_response(data)
    return chat


# --- resolver ------------------------------------------------------------

BACKENDS = ("local", "anthropic", "openai", "gemini", "nvidia")


def get_chat(backend=None):
    """Build the chat callable for cfg.model_backend, or an explicit override."""
    backend = (backend or cfg.model_backend).lower()
    if backend == "local":
        return make_local_chat(cfg.ollama_host, cfg.planning_model, cfg.ollama_keep_alive)
    if backend == "anthropic":
        return make_anthropic_chat(cfg.anthropic_api_key, cfg.anthropic_model)
    if backend == "openai":
        return make_openai_compat_chat("https://api.openai.com/v1", cfg.openai_api_key,
                                       cfg.openai_model, "OPENAI_API_KEY")
    if backend == "gemini":
        return make_gemini_native_chat(cfg.gemini_api_key, cfg.gemini_model)
    if backend == "nvidia":
        return make_openai_compat_chat("https://integrate.api.nvidia.com/v1", cfg.nvidia_api_key,
                                       cfg.nvidia_model, "NVIDIA_API_KEY")
    raise BackendError(f"unknown model backend '{backend}'. choose from: {', '.join(BACKENDS)}")


def demo():
    # --- OpenAI-compatible transcoding (also covers gemini/nvidia) ---------
    canonical = [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "list ~"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_7", "function": {"name": "list_dir", "arguments": {"path": "~"}}}]},
        {"role": "tool", "tool_name": "list_dir", "tool_call_id": "call_7", "content": "Desktop\nDocuments"},
    ]
    wire = _to_openai_messages(canonical)
    assert wire[2]["tool_calls"][0]["id"] == "call_7"
    assert json.loads(wire[2]["tool_calls"][0]["function"]["arguments"]) == {"path": "~"}
    assert wire[3] == {"role": "tool", "tool_call_id": "call_7", "content": "Desktop\nDocuments"}

    openai_response = {"choices": [{"message": {"content": " listed. ", "tool_calls": [
        {"id": "call_9", "function": {"name": "open_app", "arguments": '{"name": "Notes"}'}}]}}]}
    parsed = _parse_openai_response(openai_response)
    assert parsed == {"role": "assistant", "content": "listed.",
                      "tool_calls": [{"id": "call_9", "function": {"name": "open_app", "arguments": {"name": "Notes"}}}]}

    # malformed arguments JSON degrades to {} instead of raising
    bad = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "x", "function": {"name": "f", "arguments": "{not json"}}]}}]}
    assert _parse_openai_response(bad)["tool_calls"][0]["function"]["arguments"] == {}

    calls = []
    def fake_post(url, headers, body, timeout):
        calls.append((url, headers, body))
        return openai_response
    chat = make_openai_compat_chat("https://api.example.com/v1", "sk-test", "gpt-x", "OPENAI_API_KEY", _post=fake_post)
    out = chat(canonical, tools=[{"type": "function", "function": {"name": "list_dir", "parameters": {}}}])
    assert out["content"] == "listed."
    url, headers, body = calls[0]
    assert url == "https://api.example.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "gpt-x" and body["tools"][0]["function"]["name"] == "list_dir"

    no_key_chat = make_openai_compat_chat("https://api.example.com/v1", None, "gpt-x", "OPENAI_API_KEY")
    try:
        no_key_chat(canonical)
        assert False, "should have raised"
    except BackendError as e:
        assert "OPENAI_API_KEY" in str(e)

    # --- Anthropic transcoding ----------------------------------------------
    system, anth_msgs = _to_anthropic_messages(canonical)
    assert system == "be terse"
    assert anth_msgs[0] == {"role": "user", "content": "list ~"}
    assert anth_msgs[1]["content"][0] == {"type": "tool_use", "id": "call_7",
                                          "name": "list_dir", "input": {"path": "~"}}
    assert anth_msgs[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_7", "content": "Desktop\nDocuments"}]}

    anth_response = {"content": [{"type": "text", "text": "done."},
                                 {"type": "tool_use", "id": "toolu_1", "name": "create_note",
                                  "input": {"title": "x", "body": "y"}}]}
    parsed_a = _parse_anthropic_response(anth_response)
    assert parsed_a["content"] == "done."
    assert parsed_a["tool_calls"] == [{"id": "toolu_1",
                                       "function": {"name": "create_note", "arguments": {"title": "x", "body": "y"}}}]

    a_calls = []
    def fake_post_a(url, headers, body, timeout):
        a_calls.append((url, headers, body))
        return anth_response
    achat = make_anthropic_chat("sk-ant-test", "claude-x", _post=fake_post_a)
    out_a = achat(canonical, tools=[{"type": "function", "function": {"name": "create_note", "description": "d", "parameters": {"type": "object"}}}])
    assert out_a["content"] == "done."
    aurl, aheaders, abody = a_calls[0]
    assert aurl == "https://api.anthropic.com/v1/messages"
    assert aheaders["x-api-key"] == "sk-ant-test"
    assert abody["system"] == "be terse"
    assert abody["tools"][0]["input_schema"] == {"type": "object"}

    try:
        make_anthropic_chat(None, "claude-x")(canonical)
        assert False, "should have raised"
    except BackendError as e:
        assert "ANTHROPIC_API_KEY" in str(e)

    # tool result with no matching assistant tool_calls still round-trips
    # (trailing tool messages flush even with nothing after them)
    trailing = canonical[:3] + [{"role": "tool", "tool_name": "list_dir", "tool_call_id": "call_7", "content": "ok"}]
    _, trailing_out = _to_anthropic_messages(trailing)
    assert trailing_out[-1]["role"] == "user" and trailing_out[-1]["content"][0]["type"] == "tool_result"

    # --- resolver -------------------------------------------------------------
    assert callable(get_chat("local"))          # no network/import until called
    assert callable(get_chat("openai"))
    try:
        get_chat("not-a-real-backend")
        assert False, "should have raised"
    except BackendError as e:
        assert "unknown model backend" in str(e)

    print("ok")


if __name__ == "__main__":
    demo()
