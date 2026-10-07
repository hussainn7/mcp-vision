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


def test_only_a_clean_python_gets_bundled(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    for name in ("README.txt", "pip", "pip-26.2.1.dist-info"):
        (site / name).mkdir() if "." not in name or name.endswith("dist-info") else (site / name).write_text("")
    assert build_dmg.extra_packages(site) == []
    (site / "pandas").mkdir()                    # someone's base interpreter with packages installed into it
    assert build_dmg.extra_packages(site) == ["pandas"]


def test_a_release_carries_the_sign_in_project_but_never_a_secret_key(monkeypatch):
    from mcp_vision.buddy import settings as settings_module

    assert build_dmg.release_env() == "" and build_dmg.release_env("https://abc.supabase.co", "") == ""
    assert build_dmg.release_env("https://abc.supabase.co", "sb_publishable_x") == \
        "PLIP_SUPABASE_URL=https://abc.supabase.co\nPLIP_SUPABASE_KEY=sb_publishable_x\n"
    assert build_dmg.secret_key("sb_secret_abc") and build_dmg.secret_key("eyJ...service_role...")
    assert not build_dmg.secret_key("sb_publishable_x")
    monkeypatch.setenv("PLIP_SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("PLIP_SUPABASE_KEY", "sb_publishable_x")
    configured = settings_module.BuddySettings(_env_file=None)
    assert (configured.supabase_url, configured.supabase_key) == ("https://abc.supabase.co", "sb_publishable_x")


def test_only_the_app_bundle_reads_its_plip_env(monkeypatch, tmp_path):
    from mcp_vision.buddy import settings as settings_module

    bundled = tmp_path / "Plip.app" / "Contents" / "Resources" / "python"
    monkeypatch.setattr(settings_module.sys, "prefix", str(bundled))
    assert settings_module.release_env() == bundled.parent / "plip.env"
    monkeypatch.setattr(settings_module.sys, "prefix", str(tmp_path / ".venv"))
    assert settings_module.release_env() is None
