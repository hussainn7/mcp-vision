"""Build a Plip.app anyone can run, and the DMG to share it.

Self-contained: Python, Plip and every dependency live inside the bundle, so it runs
on any Apple-silicon Mac with nothing installed. It writes only to ``dist/``.

    .venv/bin/python scripts/build_dmg.py         # dist/Plip.app + dist/Plip-<version>.dmg, signed ad hoc

A release Apple trusts (opens with no "can't check this app" warning):

    xcrun notarytool store-credentials plip --apple-id <you> --team-id <TEAM> --password <app-specific>
    .venv/bin/python scripts/build_dmg.py --sign "Developer ID Application: <Name> (<TEAM>)" --notarize plip

That checks the certificate and the notary profile before building, signs everything with the
Developer ID, notarizes and staples the app, then signs, notarizes and staples the DMG, and asks
Gatekeeper about both.

Needs macOS on Apple silicon, the Xcode command line tools (clang, codesign) and ``uv``
(it copies uv's own CPython 3.12 into the app and runs dmgbuild through ``uv tool run``).
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mcp_vision.identity import BUNDLE_ID  # noqa: E402  macOS permissions belong to this ID

ICON = ROOT / "assets" / "plip-icon.png"
BACKGROUND = ROOT / "assets" / "brand" / "dmg"
PY = "python3.12"
ENTITLEMENTS = {
    "com.apple.security.cs.allow-unsigned-executable-memory": True,    # ctypes / pyobjc trampolines
    "com.apple.security.cs.disable-library-validation": True,          # wheels bring their own dylibs
    "com.apple.security.device.audio-input": True,
    "com.apple.security.automation.apple-events": True,
}
# What Plip never loads: tests, Tk, pip.
PRUNE = [f"lib/{PY}/test", f"lib/{PY}/idlelib", f"lib/{PY}/tkinter", f"lib/{PY}/turtledemo", f"lib/{PY}/ensurepip",
         f"lib/{PY}/site-packages/pip", f"lib/{PY}/site-packages/PyObjCTest", "include", "share", "lib/pkgconfig"]
PRUNE_GLOBS = ["lib/libtcl*", "lib/libtk*", "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*",
               f"lib/{PY}/lib-dynload/_tkinter*", f"lib/{PY}/site-packages/*/tests", "bin/idle*", "bin/pydoc*",
               "bin/pip*", "bin/2to3*"]

LAUNCHER = r'''#import <AppKit/AppKit.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <unistd.h>

// Plip.app/Contents/MacOS/Plip: run the bundled Python in this process, so macOS sees one
// app (one icon, one set of permissions) and everything is found inside the bundle.
int main(int argc, char **argv) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NSString *home = [[[NSBundle mainBundle] resourcePath] stringByAppendingPathComponent:@"python"];
        NSString *python = [home stringByAppendingPathComponent:@"bin/PYVERSION"];
        NSString *library = [home stringByAppendingPathComponent:@"lib/libPYVERSION.dylib"];
        setenv("PYTHONHOME", home.fileSystemRepresentation, 1);
        setenv("PYTHONNOUSERSITE", "1", 1);
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1);     // bytecode is precompiled; writing it would break the signature
        unsetenv("PYTHONPATH");
        chdir(NSHomeDirectory().fileSystemRepresentation);
        void *handle = dlopen(library.fileSystemRepresentation, RTLD_NOW | RTLD_GLOBAL);
        int (*pythonMain)(int, char **) = handle ? dlsym(handle, "Py_BytesMain") : NULL;
        if (!pythonMain) {
            NSAlert *alert = [NSAlert new];
            alert.messageText = @"Plip is incomplete";
            alert.informativeText = @"Its Python runtime is missing. Download Plip again and drag it into Applications.";
            [alert runModal];
            return 1;
        }
        const char *entry[] = {"-m", "mcp_vision.cli", "buddy", "run"};
        int count = 1 + 4 + (argc - 1);
        char **arguments = calloc(count + 1, sizeof(char *));
        arguments[0] = strdup(python.fileSystemRepresentation);
        for (int i = 0; i < 4; i++) arguments[1 + i] = (char *)entry[i];
        for (int i = 1; i < argc; i++) arguments[4 + i] = argv[i];
        return pythonMain(count, arguments);
    }
}
'''


def run(*argv: str | os.PathLike, **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(a) for a in argv)[:160], flush=True)
    return subprocess.run([str(a) for a in argv], check=True, **kw)


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def info_plist(app_version: str) -> dict:
    return {
        "CFBundleIdentifier": BUNDLE_ID, "CFBundleName": "Plip", "CFBundleDisplayName": "Plip",
        "CFBundleExecutable": "Plip", "CFBundleIconFile": "Plip.icns", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": app_version, "CFBundleVersion": app_version,
        "LSMinimumSystemVersion": "13.0", "LSUIElement": True, "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity", "LSArchitecturePriority": ["arm64"],
        "NSAppleEventsUsageDescription": "Plip asks apps like Finder, Reminders and Notes to do things when you ask it to.",
        "NSMicrophoneUsageDescription": "Plip listens only while you hold Control+Option to talk.",
        "NSSpeechRecognitionUsageDescription": "Plip turns what you say into the question you ask it.",
    }


def sign_options(sign: str) -> list[str]:
    """Hardened runtime on every build, ad hoc too, so what runs on this Mac runs under the same rules as a release."""
    return ["--options", "runtime", *(["--timestamp"] if sign != "-" else [])]


def app_requirement(sign: str) -> list[str]:
    """Ad hoc builds change their code hash every time; pinning macOS's check to the bundle ID keeps permissions
    across rebuilds. A Developer ID build keeps Apple's default check (team + ID), which is already stable and
    can't be matched by some other app that copies the ID."""
    return ["--requirements", f'=designated => identifier "{BUNDLE_ID}"'] if sign == "-" else []


def managed_python() -> Path:
    """uv's own CPython 3.12: relocatable and clean, whatever Python (or venv) runs this script."""
    find = ["uv", "python", "find", "--system", "--no-project", "--python-preference", "only-managed", "3.12"]
    found = subprocess.run(find, capture_output=True, text=True)
    if found.returncode != 0:
        run("uv", "python", "install", "3.12")
        found = subprocess.run(find, capture_output=True, text=True, check=True)
    prefix = Path(found.stdout.strip()).resolve().parents[1]
    if not (prefix / "lib" / f"lib{PY}.dylib").exists():
        raise SystemExit(f"{prefix} has no lib/lib{PY}.dylib, so it can't be bundled")
    extra = extra_packages(prefix / "lib" / PY / "site-packages")
    if extra:
        raise SystemExit(f"{prefix} has packages of its own ({', '.join(extra[:5])}…); the app would carry them")
    return prefix


def extra_packages(site_packages: Path) -> list[str]:
    """Anything in a fresh CPython's site-packages beyond pip/setuptools."""
    stock = ("pip", "setuptools", "_distutils_hack", "distutils-precedence", "README")
    return sorted(path.name for path in site_packages.iterdir() if not path.name.startswith(stock)) \
        if site_packages.is_dir() else []


def macho(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
    except OSError:
        return False


def program(path: Path) -> bool:
    """bin/python3.12 runs on its own, so it carries the entitlements too; libraries don't."""
    return os.access(path, os.X_OK) and path.suffix not in {".so", ".dylib"} and ".so." not in path.name


def make_icns(png: Path, target: Path) -> None:
    iconset = target.with_suffix(".iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    for size in (16, 32, 128, 256, 512):
        for factor in (1, 2):
            name = f"icon_{size}x{size}{'@2x' if factor == 2 else ''}.png"
            run("sips", "-z", size * factor, size * factor, png, "--out", iconset / name, stdout=subprocess.DEVNULL)
    run("iconutil", "-c", "icns", iconset, "-o", target)
    shutil.rmtree(iconset)


def release_env(supabase_url: str = "", supabase_key: str = "") -> str:
    """``Contents/Resources/plip.env``: the sign-in project, so a shared DMG asks people to sign in. Supabase
    anon/publishable keys are public by design (row-level security); they still stay out of git."""
    if not (supabase_url and supabase_key):
        return ""
    return f"PLIP_SUPABASE_URL={supabase_url}\nPLIP_SUPABASE_KEY={supabase_key}\n"


def configured_sign_in() -> tuple[str, str]:
    """The sign-in project to bake in: what Plip itself reads (environment, then the .env files)."""
    try:
        from mcp_vision.buddy.settings import load_settings
    except ImportError:              # uv run --no-project (the release workflow): no pydantic, secrets come as env vars
        return os.environ.get("PLIP_SUPABASE_URL", ""), os.environ.get("PLIP_SUPABASE_KEY", "")
    configured = load_settings()
    return configured.supabase_url or "", configured.supabase_key or ""


def secret_key(key: str) -> bool:
    """A Supabase secret / service_role key: it would ship to everyone who downloads the app."""
    return key.startswith("sb_secret_") or "service_role" in key


def build_app(dist: Path, sign: str = "-", env: str = "") -> Path:
    app = dist / "Plip.app"
    shutil.rmtree(app, ignore_errors=True)
    macos, resources = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    home = resources / "python"

    print("1/5 python")
    shutil.copytree(managed_python(), home, symlinks=True)
    (home / "lib" / PY / "EXTERNALLY-MANAGED").unlink(missing_ok=True)
    python = home / "bin" / PY

    print("2/5 plip and its dependencies")
    run("uv", "pip", "install", "--python", python, "--quiet", ROOT)
    for relative in PRUNE:
        target = home / relative
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    for pattern in PRUNE_GLOBS:
        for target in home.glob(pattern):
            shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink()
    for cache in list(home.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    # Precompiled, hash-checked bytecode: a fast first launch that stays valid wherever the app is copied.
    run(python, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash", home / "lib" / PY,
        env={**os.environ, "PYTHONHOME": str(home), "PYTHONNOUSERSITE": "1"}, stdout=subprocess.DEVNULL)

    print("3/5 icon, Info.plist, launcher")
    make_icns(ICON, resources / "Plip.icns")
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info_plist(version())))
    if env:
        (resources / "plip.env").write_text(env)
    source = dist / "PlipLauncher.m"
    source.write_text(LAUNCHER.replace("PYVERSION", PY))
    run("clang", "-O2", "-fobjc-arc", "-arch", "arm64", "-mmacosx-version-min=13.0", source, "-o", macos / "Plip",
        "-framework", "AppKit")
    source.unlink()

    print("4/5 sign")
    nested = sorted((path for path in home.rglob("*") if path.is_file() and not path.is_symlink() and macho(path)),
                    key=lambda path: len(path.parts), reverse=True)        # innermost first
    entitlements = dist / "entitlements.plist"
    entitlements.write_bytes(plistlib.dumps(ENTITLEMENTS))
    options = sign_options(sign)
    libraries = [path for path in nested if not program(path)]
    for chunk in range(0, len(libraries), 200):
        run("codesign", "--force", "--sign", sign, *options, *libraries[chunk:chunk + 200],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    programs = [path for path in nested if program(path)]
    if programs:
        run("codesign", "--force", "--sign", sign, *options, "--entitlements", entitlements, *programs,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run("codesign", "--force", "--sign", sign, *options, "--entitlements", entitlements, "--identifier", BUNDLE_ID,
        *app_requirement(sign), app)
    entitlements.unlink()
    run("codesign", "--verify", "--deep", "--strict", app)
    return app


def dmg_settings(app: Path, icns: Path, background: Path) -> str:
    """dmgbuild settings: a 660x440 icon view, no toolbar or sidebar, Plip on the left, Applications on the right."""
    return f'''
files = [{str(app)!r}]
symlinks = {{"Applications": "/Applications"}}
icon = {str(icns)!r}
background = {str(background)!r}
window_rect = ((200, 140), (660, 468))   # 440 of background + the title bar
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 112
text_size = 13
icon_locations = {{"Plip.app": (180, 232), "Applications": (480, 232),
                  # out of sight even for people who show hidden files
                  ".background.tiff": (180, 900), ".VolumeIcon.icns": (480, 900), ".DS_Store": (330, 900),
                  ".fseventsd": (330, 1000), ".Trashes": (480, 1000)}}
hide_extension = ["Plip.app"]
format = "ULMO"
filesystem = "APFS"
'''


def build_dmg(app: Path, dist: Path) -> Path:
    print("5/5 dmg")
    background = dist / "dmg-background.tiff"
    run("tiffutil", "-cathidpicheck", BACKGROUND / "background.png", BACKGROUND / "background@2x.png",
        "-out", background, stderr=subprocess.DEVNULL)
    icns = app / "Contents" / "Resources" / "Plip.icns"
    settings = dist / "dmg_settings.py"
    settings.write_text(dmg_settings(app, icns, background))
    target = dist / f"Plip-{version()}.dmg"
    target.unlink(missing_ok=True)
    run("uv", "tool", "run", "--from", "dmgbuild", "dmgbuild", "-s", settings, "Plip", target)
    settings.unlink()
    background.unlink()
    return target


# -- a release Apple trusts ---------------------------------------------------------------------------------------

def release_problems(sign: str, profile: str, identities: str, profile_ok: bool | None) -> list[str]:
    """Why a --sign / --notarize release can't work, found before the build instead of after it."""
    problems = []
    if profile and sign == "-":
        problems.append('--notarize needs --sign "Developer ID Application: <Name> (<TEAM>)"')
    if sign != "-":
        if not sign.startswith("Developer ID Application") and not re.fullmatch(r"[0-9A-F]{40}", sign):
            problems.append(f"{sign!r} isn't a Developer ID Application identity (Apple only notarizes those)")
        elif sign not in identities:
            problems.append(f"{sign!r} isn't in your keychain (`security find-identity -v -p codesigning` lists what "
                            "is). Make one in Xcode > Settings > Accounts > Manage Certificates > Developer ID "
                            "Application")
    if profile and profile_ok is False:
        problems.append(f"no notarytool profile {profile!r}: run xcrun notarytool store-credentials {profile} "
                        "--apple-id <you> --team-id <TEAM> --password <app-specific password>")
    return problems


def notarize(path: Path, profile: str) -> None:
    """Upload, wait for Apple's verdict, print its log if it says no, then staple the ticket."""
    upload = path
    if path.suffix == ".app":
        upload = path.with_suffix(".zip")
        upload.unlink(missing_ok=True)
        run("ditto", "-c", "-k", "--keepParent", path, upload)
    result = subprocess.run(["xcrun", "notarytool", "submit", str(upload), "--keychain-profile", profile, "--wait",
                             "--output-format", "json"], capture_output=True, text=True)
    if upload != path:
        upload.unlink(missing_ok=True)
    try:
        verdict = json.loads(result.stdout or "{}")
    except ValueError:
        verdict = {}
    status, submission = verdict.get("status", ""), verdict.get("id", "")
    print(f"   notary: {status or 'no answer'} {submission}")
    if status != "Accepted":
        if submission:
            run("xcrun", "notarytool", "log", submission, "--keychain-profile", profile)
        raise SystemExit(f"Apple didn't accept {path.name}: {status or result.stderr.strip()[-400:]}")
    run("xcrun", "stapler", "staple", path)
    run("xcrun", "stapler", "validate", path)


def gatekeeper(app: Path, dmg: Path | None) -> None:
    run("spctl", "--assess", "--type", "execute", "--verbose=4", app)
    if dmg is not None:
        run("spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "--verbose=4", dmg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sign", default="-", help='codesign identity (default: ad hoc). "Developer ID Application: …"')
    parser.add_argument("--notarize", default="", help="notarytool keychain profile; needs a Developer ID --sign")
    parser.add_argument("--app-only", action="store_true", help="build dist/Plip.app, skip the DMG")
    parser.add_argument("--supabase-url", default="", help="sign-in project URL (default: PLIP_SUPABASE_URL); "
                        "without it and --supabase-key, Plip doesn't ask anyone to sign in")
    parser.add_argument("--supabase-key", default="", help="the project's anon or publishable key (default: "
                        "PLIP_SUPABASE_KEY)")
    args = parser.parse_args()
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        raise SystemExit("Build on an Apple-silicon Mac (the bundled Python is arm64).")
    sign, profile = args.sign, args.notarize
    identities = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"], capture_output=True,
                                text=True).stdout
    profile_ok = None
    if profile:
        profile_ok = subprocess.run(["xcrun", "notarytool", "history", "--keychain-profile", profile],
                                    capture_output=True).returncode == 0
    problems = release_problems(sign, profile, identities, profile_ok)
    if problems:
        raise SystemExit("Can't make a release yet:\n  - " + "\n  - ".join(problems))
    configured_url, configured_key = configured_sign_in()
    supabase_url = (args.supabase_url or configured_url).strip().rstrip("/")
    supabase_key = (args.supabase_key or configured_key).strip()
    if secret_key(supabase_key):
        raise SystemExit("That's a Supabase secret key. Use the anon or publishable key: the app ships it to everyone.")
    signs_in = bool(supabase_url and supabase_key)
    print(f"   sign-in: {'Google, through ' + supabase_url if signs_in else 'none (no Supabase project configured)'}")
    if not signs_in and sign != "-":
        print("   note: this release won't ask anyone to sign in. Set PLIP_SUPABASE_URL and PLIP_SUPABASE_KEY.")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    app = build_app(dist, sign, release_env(supabase_url, supabase_key))
    size = sum(path.stat().st_size for path in app.rglob("*") if path.is_file() and not path.is_symlink())
    print(f"   {app} ({size / 1e6:.0f} MB)")
    if profile:
        print("notarize the app")
        notarize(app, profile)
    dmg = None if args.app_only else build_dmg(app, dist)
    if dmg is not None and sign != "-":
        run("codesign", "--force", "--sign", sign, "--timestamp", dmg)
        if profile:
            print("notarize the dmg")
            notarize(dmg, profile)
    if profile:
        gatekeeper(app, dmg)
    if dmg is not None:
        print(f"\n{dmg} ({dmg.stat().st_size / 1e6:.0f} MB)" + ("" if profile else
              "\nsigned ad hoc: people see \"can't check this app\" until it's built with --sign and --notarize"
              if sign == "-" else "\nsigned but not notarized: add --notarize <profile>"))


if __name__ == "__main__":
    main()
