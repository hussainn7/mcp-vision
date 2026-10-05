# PyInstaller spec for the downloadable Plip.app. Build with scripts/build_dmg.sh.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

import os

import mcp_vision

VERSION = mcp_vision.__version__
hidden = collect_submodules("mcp_vision")
for framework in ("AppKit", "Foundation", "Quartz", "Speech", "AVFoundation", "WebKit",
                  "ApplicationServices", "CoreText", "PyObjCTools"):
    hidden += collect_submodules(framework)

a = Analysis(
    [os.path.join(SPECPATH, "plip_main.py")],
    pathex=[os.path.join(SPECPATH, "..", "src")],
    datas=collect_data_files("mcp_vision"),
    hiddenimports=hidden,
    excludes=["playwright", "PySide6", "tkinter", "pytest", "IPython", "matplotlib", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Plip", console=False,
          codesign_identity=None, entitlements_file=os.path.join(SPECPATH, "entitlements.plist"))
coll = COLLECT(exe, a.binaries, a.datas, name="Plip")
app = BUNDLE(
    coll,
    name="Plip.app",
    icon=os.path.join(SPECPATH, "..", "build", "Plip.icns"),
    bundle_identifier="org.mcpvision.plip",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Plip",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSUIElement": True,
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "Plip asks apps like Chrome, Reminders and Notes to do things when you ask it to.",
        "NSMicrophoneUsageDescription": "Plip listens only while you hold Control+Option to talk.",
        "NSSpeechRecognitionUsageDescription": "Plip turns what you say into the question you ask it.",
        "NSContactsUsageDescription": "Plip can learn your name and contacts for Memory, only if you import them.",
    },
)
