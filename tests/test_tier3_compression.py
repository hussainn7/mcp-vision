"""Test 3.2: Visual Delta & Trajectory Compression across 15-step browsing session."""

from PIL import Image, ImageDraw
from phase1_vision.diff import FrameBudget, crop_delta, changed, delta_bbox
from phase1_vision.compress import compress_messages
import agent


def test_visual_delta_cropping():
    budget = FrameBudget()

    # 1. First frame -> always full
    f1 = Image.new("RGB", (1280, 800), (255, 255, 255))
    out1, kind1 = budget.next(f1)
    assert kind1 == "full"
    assert out1.size == (1280, 800)

    # 2. Unchanged frame (e.g. waiting/polling) -> skip
    f2 = f1.copy()
    out2, kind2 = budget.next(f2)
    assert kind2 == "skip"
    assert out2 is None

    # 3. Small local change (e.g. dropdown, tooltip, or small delta) -> crop
    f3 = f1.copy()
    draw = ImageDraw.Draw(f3)
    draw.rectangle([100, 100, 250, 180], fill=(0, 120, 255))
    out3, kind3 = budget.next(f3)
    assert kind3 == "crop"
    assert out3 is not None
    assert out3.width < 1280 and out3.height < 800
    # verify pixel savings
    area_savings = 1.0 - (out3.width * out3.height) / (1280 * 800)
    assert area_savings > 0.85, f"Cropping should save >85% tokens, got {area_savings:.2%}"

    # 4. Full page navigation -> full
    f4 = Image.new("RGB", (1280, 800), (40, 40, 40))
    out4, kind4 = budget.next(f4)
    assert kind4 == "full"
    assert out4.size == (1280, 800)


def test_trajectory_compression_15_steps():
    # Simulate a 15-step browsing session message history
    messages = [{"role": "system", "content": "You are a browsing agent."}]
    messages.append({"role": "user", "content": "Browse 3 Wikipedia articles across 15 steps."})

    for step in range(1, 16):
        # assistant call
        messages.append({
            "role": "assistant",
            "content": f"Step {step}: reading section.",
            "tool_calls": [{"id": f"call_{step}", "function": {"name": "web_read", "arguments": {}}}],
        })
        # lengthy page text
        messages.append({
            "role": "tool",
            "tool_name": "web_read",
            "tool_call_id": f"call_{step}",
            "content": f"Wikipedia article content for step {step}: " + ("text chunk " * 50),
        })

    total_chars_before = sum(len(m.get("content", "")) for m in messages)
    
    # Compress messages (keeping last 4 intact and summarizing older steps)
    compressed = compress_messages(messages, keep_last=4)
    total_chars_after = sum(len(m.get("content", "")) for m in compressed)

    assert total_chars_after < total_chars_before, "Compression must reduce character/token size"
    char_savings = 1.0 - (total_chars_after / total_chars_before)
    assert char_savings > 0.40, f"Expected >40% token savings, got {char_savings:.2%}"
    assert compressed[0]["role"] == "system"
    assert compressed[1]["role"] == "user"
    
    # Check that older tool outputs are truncated / compressed to avoid context exhaustion
    older_tools = [m for m in compressed[2:-8] if m.get("role") == "tool"]
    for t in older_tools:
        assert len(t["content"]) < 200, "Older tool outputs must be compressed/truncated"


if __name__ == "__main__":
    test_visual_delta_cropping()
    test_trajectory_compression_15_steps()
    print("Test 3.2 passed successfully!")
