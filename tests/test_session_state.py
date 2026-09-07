"""State vs inference: navigation gate, identity, mismatch. No site adapters."""

from phase2_mcp.session_state import (
    INFERRED,
    OBSERVED,
    USER_ASSERTION,
    VERIFIED,
    bind_task,
    discover_identity,
    entity_segs,
    format_debug,
    gate_navigate,
    identity_verified,
    observe_page,
    recovery_prompt,
    reject_unverified_answer,
    user_supplied_destination,
    STATE,
)


def test_exact_url_is_fast_path():
    bind_task("Open https://example.com/ada/notes")
    d = gate_navigate("https://example.com/ada/notes")
    assert d.kind == "exact" and d.source == USER_ASSERTION
    assert d.url == "https://example.com/ada/notes"


def test_canonical_app_root():
    bind_task("Open Gmail")
    d = gate_navigate("https://mail.google.com")
    assert d.kind == "canonical"
    bind_task("check my messages")
    d = gate_navigate("https://mail.google.com/mail/u/0/#inbox")
    assert d.kind == "canonical"  # u / 0 / inbox are app chrome


def test_inferred_profile_rewritten_generic():
    cases = [
        ("find my latest repository", "https://github.com/not-the-user"),
        ("find my latest post", "https://www.linkedin.com/in/guessed-handle"),
        ("check my profile", "https://x.com/somehandle"),
        ("look at my files", "https://drive.example.com/users/imaginary"),
        ("open my dashboard", "https://app.example.com/orgs/wrong-co/home"),
    ]
    for task, url in cases:
        bind_task(task)
        d = gate_navigate(url)
        assert d.kind == "rewrite", (task, url, d)
        assert d.needs_identity
        assert d.source == INFERRED
        assert entity_segs(d.url) == []


def test_named_resource_in_task_allowed():
    bind_task("open the linux repo at torvalds/linux")
    d = gate_navigate("https://github.com/torvalds/linux")
    assert d.kind == "exact"


def test_wrong_model_assumption_loses_to_browser():
    bind_task("Tell me which account I'm currently logged into.")
    gate_navigate("https://example.com/wrong-person")
    observe_page(
        url="https://example.com/home",
        title="Home",
        text="Signed in as real-user. Welcome.",
        elements=[{"role": "button", "name": "Account menu for real-user"}],
    )
    assert STATE.identity.value == "real-user"
    assert STATE.identity.source == VERIFIED
    debug = format_debug()
    assert "real-user" in debug
    assert "VERIFIED" in debug


def test_url_slug_is_not_verified_identity():
    bind_task("who am I")
    ident = discover_identity(url="https://example.com/looks-like-a-user", text="Welcome")
    assert ident.value == "looks-like-a-user"
    assert ident.source == OBSERVED
    assert ident.via == "url path"


def test_mismatch_when_url_is_someone_else():
    bind_task("open https://example.com/other-person")
    gate_navigate("https://example.com/other-person")
    tag = observe_page(
        url="https://example.com/other-person",
        text="Signed in as ada.lovelace",
    )
    assert STATE.mismatch
    assert "MISMATCH" in tag
    assert "ada.lovelace" in STATE.mismatch


def test_auth_boundary_logged_out():
    bind_task("check my mail")
    tag = observe_page(
        url="https://accounts.google.com/signin",
        title="Sign in - Google Accounts",
        text="Sign in to continue to Gmail. Email or phone.",
        elements=[{"role": "textbox", "name": "Email or phone"}, {"role": "button", "name": "Next"}],
    )
    assert STATE.auth_required
    assert STATE.authenticated is False
    assert "AUTH_REQUIRED" in tag


def test_captcha_security_challenge():
    bind_task("open the site")
    tag = observe_page(
        url="https://challenges.cloudflare.com/turnstile/v0/api.js",
        title="Just a moment...",
        text="Verify you are human. Checking your browser before accessing the website.",
    )
    assert STATE.auth_required
    assert "AUTH_REQUIRED" in tag


def test_user_supplied_destination():
    assert user_supplied_destination(
        "https://news.ycombinator.com/item?id=1",
        "open https://news.ycombinator.com/item?id=1",
    )
    assert not user_supplied_destination(
        "https://github.com/someone",
        "find my latest repository",
    )


def test_workspace_path_without_name_is_rewritten():
    bind_task("Find my Slack messages")
    d = gate_navigate("https://app.slack.com/client/T00FAKE/C00FAKE")
    assert d.kind == "rewrite"


def test_verified_identity_unlocks_personal_url():
    bind_task("Find my repositories")
    d = gate_navigate("https://example.com/guessed-user")
    assert d.kind == "rewrite"
    observe_page(
        url="https://example.com/home",
        text="Signed in as real-user. Welcome.",
        elements=[{"role": "button", "name": "Account menu for real-user"}],
    )
    assert identity_verified() == "real-user"
    d = gate_navigate("https://example.com/real-user/items")
    assert d.kind == "exact" and d.source == VERIFIED
    d = gate_navigate("https://example.com/other-user")
    assert d.kind == "rewrite"


def test_unverified_answer_is_rejected():
    bind_task("Find my latest repository")
    assert reject_unverified_answer("Your username is hussainn7")
    observe_page(url="https://example.com/home", text="Signed in as real-user")
    assert reject_unverified_answer("Your username is hussainn7")
    assert reject_unverified_answer("Your username is real-user") is None
    bind_task("summarize the top story on Hacker News")
    assert reject_unverified_answer("The top story is about widgets") is None


def test_recovery_prompt_after_guess():
    bind_task("Find my files")
    gate_navigate("https://drive.example.com/users/nope")
    hint = recovery_prompt()
    assert hint and "unknown" in hint.lower()


def test_debug_dump_has_ledger():
    bind_task("Check my email.")
    gate_navigate("https://mail.google.com")
    observe_page(url="https://mail.google.com/mail", text="Signed in as ada@example.com")
    dump = format_debug()
    assert dump.startswith("[STATE]")
    assert "ASSUMPTIONS" in dump
    assert "CYCLE" in dump
    assert "ada" in dump or "identity" in dump.lower()
