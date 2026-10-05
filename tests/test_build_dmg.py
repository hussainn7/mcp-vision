"""scripts/build_dmg.py: what the app says about itself, how it's signed, and the DMG window."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_dmg", ROOT / "scripts" / "build_dmg.py")
build_dmg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_dmg)


def test_the_app_is_plip_with_the_one_bundle_id():
    from mcp_vision.identity import BUNDLE_ID

    info = build_dmg.info_plist("1.2.3")
    assert info["CFBundleIdentifier"] == BUNDLE_ID and info["CFBundleExecutable"] == "Plip"
    assert info["CFBundleShortVersionString"] == "1.2.3" and info["LSUIElement"] is True
    for key in ("NSMicrophoneUsageDescription", "NSSpeechRecognitionUsageDescription", "NSAppleEventsUsageDescription"):
        assert info[key]                         # macOS kills an app that asks without saying why


def test_launcher_runs_plip_from_the_bundled_python():
    source = build_dmg.LAUNCHER.replace("PYVERSION", build_dmg.PY)
    assert '"mcp_vision.cli", "buddy", "run"' in source and "PYTHONHOME" in source
    assert "lib/libpython3.12.dylib" in source and "PYVERSION" not in source


def test_every_build_is_hardened_and_only_ad_hoc_pins_the_bundle_id():
    assert build_dmg.sign_options("-") == ["--options", "runtime"]
    release = "Developer ID Application: Hussain Syed (ABCDE12345)"
    assert build_dmg.sign_options(release) == ["--options", "runtime", "--timestamp"]
    assert build_dmg.app_requirement("-") == ["--requirements", f'=designated => identifier "{build_dmg.BUNDLE_ID}"']
    assert build_dmg.app_requirement(release) == []       # Apple's default: tied to the team, can't be copied


def test_dmg_window_is_a_bare_icon_view_with_plip_left_of_applications():
    settings: dict = {}
    exec(build_dmg.dmg_settings(Path("/tmp/Plip.app"), Path("/tmp/Plip.icns"), Path("/tmp/bg.tiff")), settings)
    assert settings["window_rect"][1] == (660, 468) and settings["default_view"] == "icon-view"
    assert not any(settings[key] for key in ("show_toolbar", "show_sidebar", "show_status_bar", "show_pathbar"))
    assert settings["icon_size"] == 112
    assert settings["icon_locations"]["Plip.app"] == (180, 232)
    assert settings["icon_locations"]["Applications"] == (480, 232)
    assert settings["symlinks"] == {"Applications": "/Applications"}


DEV_ID = "Developer ID Application: Hussain Syed (ABCDE12345)"
IDENTITIES = f'  1) 0123456789ABCDEF0123456789ABCDEF01234567 "{DEV_ID}"\n     1 valid identities found\n'


def test_release_needs_a_developer_id_in_the_keychain_and_a_notary_profile():
    assert build_dmg.release_problems("-", "", IDENTITIES, None) == []              # everyday ad hoc build
    assert "needs --sign" in build_dmg.release_problems("-", "plip", IDENTITIES, True)[0]
    assert "isn't a Developer ID" in build_dmg.release_problems("Apple Development: me", "", IDENTITIES, None)[0]
    missing = build_dmg.release_problems(DEV_ID, "plip", "     0 valid identities found\n", True)
    assert len(missing) == 1 and "isn't in your keychain" in missing[0]
    assert "store-credentials plip" in build_dmg.release_problems(DEV_ID, "plip", IDENTITIES, False)[0]
    assert build_dmg.release_problems(DEV_ID, "plip", IDENTITIES, True) == []
