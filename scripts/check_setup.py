"""
Quick sanity check to run before diving into the full pipeline.

This doesn't require Ollama or OmniParser to be set up yet. It just confirms
that the environment is working, imports are clean, and the output directory
structure is in place.

Run with:
    python scripts/check_setup.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

issues = []
checks_passed = 0


def check(name: str, fn):
    global checks_passed
    try:
        result = fn()
        print(f"  [OK] {name}" + (f" -- {result}" if result else ""))
        checks_passed += 1
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        issues.append(f"{name}: {e}")


print("\nChecking Python version...")
check("Python >= 3.12", lambda: (
    f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 12)
    else (_ for _ in ()).throw(RuntimeError(f"Need 3.12+, got {sys.version}"))
))

print("\nChecking core imports...")
check("mss", lambda: __import__("mss") and "ok")
check("PIL (Pillow)", lambda: __import__("PIL") and "ok")
check("pyautogui", lambda: __import__("pyautogui") and "ok")
check("ollama", lambda: __import__("ollama") and "ok")
check("mcp", lambda: __import__("mcp") and "ok")
check("torch", lambda: (
    lambda t: f"torch {t.__version__}, MPS={'available' if t.backends.mps.is_available() else 'not available'}"
)(__import__("torch")))
check("transformers", lambda: __import__("transformers") and "ok")
check("ultralytics", lambda: __import__("ultralytics") and "ok")
check("rich", lambda: __import__("rich") and "ok")

print("\nChecking project config...")
check("config.py loads", lambda: (
    lambda c: f"output_dir={c.output_dir}, model={c.ollama_model}"
)(__import__("config").cfg))

print("\nChecking directory structure...")
check("outputs/ exists", lambda: Path("outputs").mkdir(exist_ok=True) or "ok")
check("weights/ exists", lambda: Path("weights").mkdir(exist_ok=True) or "ok")
check("weights/icon_detect/ exists", lambda: Path("weights/icon_detect").mkdir(exist_ok=True) or "ok")
check("weights/icon_caption_florence/ exists", lambda: Path("weights/icon_caption_florence").mkdir(exist_ok=True) or "ok")

print("\nChecking Ollama connectivity (optional)...")
try:
    import ollama
    models_response = ollama.list()
    if hasattr(models_response, "models"):
        model_names = [m.model for m in models_response.models]
    elif isinstance(models_response, dict):
        model_names = [m.get("model") or m.get("name") for m in models_response.get("models", [])]
    else:
        model_names = []
    if "moondream:1.8b" in model_names:
        print("  [OK] Ollama is running and moondream:1.8b is available")
        checks_passed += 1
    else:
        print(f"  [WARN] Ollama is running but moondream:1.8b not found.")
        print(f"         Available models: {model_names or 'none'}")
        print(f"         Run: ollama pull moondream:1.8b")
except Exception as e:
    print(f"  [WARN] Ollama not reachable: {e}")
    print(f"         Run: ollama serve")

print("\nChecking OmniParser weights (optional)...")
check(
    "icon_detect/model.pt",
    lambda: (
        "found" if Path("weights/icon_detect/model.pt").exists()
        else (_ for _ in ()).throw(FileNotFoundError("not downloaded yet -- run scripts/download_weights.py"))
    ),
)

print(f"\n{'='*50}")
if issues:
    print(f"Setup check finished with {len(issues)} issue(s):")
    for issue in issues:
        print(f"  - {issue}")
    print("\nFix the issues above, then re-run this script.")
else:
    print(f"All checks passed ({checks_passed} total). You're good to go.")
print()
