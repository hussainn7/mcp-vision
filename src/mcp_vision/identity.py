"""Plip's identity on macOS.

Permissions (Accessibility, Screen Recording, Microphone, Speech, Automation)
are granted to this bundle ID, so changing it makes macOS ask for each one
again. The open-source build has its own ID so it never shares permissions
with the Plip from plip.dev (dev.plip.Plip) on the same Mac.
"""

BUNDLE_ID = "dev.plip.oss"
