#!/usr/bin/env python3
"""Smoke test for perception module imports."""

import sys


MODULES = [
    "phase1_vision.app_detector",
    "phase1_vision.accessibility",
    "phase1_vision.perception",
    "phase2_mcp.playwright_tools",
    "phase1_vision",
    "phase2_mcp",
]


def main():
    failed = []
    for module in MODULES:
        try:
            __import__(module)
            print(f"ok  {module}")
        except Exception as e:
            print(f"FAIL {module}: {e}")
            failed.append(module)

    if failed:
        print(f"\n{len(failed)} import(s) failed")
        return 1

    print(f"\nall {len(MODULES)} imports passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
