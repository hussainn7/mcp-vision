"""Build a local launcher with a stable macOS privacy-permission identity.

Accessibility binds to the Mach-O code hash. We only recompile/resign when the
native launcher source actually changes, so everyday Python edits keep the same
TCC identity. The app always installs to /Applications/MCP-Vision.app.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import sysconfig

from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument('--output', default='outputs/MCP-Vision.app')
parser.add_argument('--planning-model', default=None)
parser.add_argument('--force', action='store_true', help='Force native rebuild + resign')
parser.add_argument('--no-install', action='store_true', help='Build and sign without replacing /Applications/MCP-Vision.app')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
app = Path(args.output).resolve()
install_path = Path('/Applications/MCP-Vision.app')
macos = app / 'Contents' / 'MacOS'
resources = app / 'Contents' / 'Resources'
macos.mkdir(parents=True, exist_ok=True)
resources.mkdir(parents=True, exist_ok=True)
stamp_name = hashlib.sha256(str(app).encode()).hexdigest()[:12] + '.sha256'
stamp = root / 'outputs' / 'signing' / stamp_name
stamp.parent.mkdir(parents=True, exist_ok=True)

info = {
    'CFBundleIdentifier': 'org.mcpvision.contextual',
    'CFBundleName': 'MCP-Vision',
    'CFBundleDisplayName': 'MCP-Vision',
    'CFBundleExecutable': 'MCP-Vision',
    'CFBundleIconFile': 'MCPVision.icns',
    'CFBundlePackageType': 'APPL',
    'CFBundleVersion': '1',
    'CFBundleShortVersionString': '1.0',
    'LSUIElement': True,
    'NSAppleEventsUsageDescription': 'MCP-Vision reads and controls your selected Chrome tab when you ask it to.',
    'NSMicrophoneUsageDescription': 'MCP-Vision listens only while you hold the talk shortcut.',
    'NSSpeechRecognitionUsageDescription': 'MCP-Vision transcribes speech into the request you ask it to perform.',
}
plist_data = plistlib.dumps(info)
(app / 'Contents' / 'Info.plist').write_bytes(plist_data)

icon_source = root / 'dist' / 'logoW.png'
if not icon_source.is_file():
    raise SystemExit(f'App icon is unavailable: {icon_source}')
cropped_icon = root / 'outputs' / 'MCPVision.icon.png'
with Image.open(icon_source).convert('RGBA') as source_image:
    visible_alpha = source_image.getchannel('A').point(lambda value: 255 if value > 10 else 0)
    bounds = visible_alpha.getbbox()
    if bounds is None:
        raise SystemExit(f'App icon has no visible pixels: {icon_source}')
    left, top, right, bottom = bounds
    side = min(source_image.width, source_image.height, int(max(right - left, bottom - top) * 1.2))
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    crop_left = max(0, min(source_image.width - side, center_x - side // 2))
    crop_top = max(0, min(source_image.height - side, center_y - side // 2))
    source_image.crop((crop_left, crop_top, crop_left + side, crop_top + side)).save(cropped_icon)
iconset = root / 'outputs' / 'MCPVision.iconset'
if iconset.exists():
    shutil.rmtree(iconset)
iconset.mkdir(parents=True)
for points in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        pixels = points * scale
        suffix = '' if scale == 1 else '@2x'
        subprocess.run([
            'sips', '-z', str(pixels), str(pixels), str(cropped_icon),
            '--out', str(iconset / f'icon_{points}x{points}{suffix}.png'),
        ], check=True, stdout=subprocess.DEVNULL)
subprocess.run([
    'iconutil', '-c', 'icns', str(iconset), '-o', str(resources / 'MCPVision.icns'),
], check=True)
shutil.rmtree(iconset)
cropped_icon.unlink()

library = Path(sys.base_prefix) / 'lib' / sysconfig.get_config_var('LDLIBRARY')
if not library.exists():
    raise SystemExit(f'Python shared library is unavailable: {library}')

env_lines = ['setenv("SCREEN_AGENT_MODEL_BACKEND", "local", 1);',
             'setenv("SCREEN_AGENT_OLLAMA_HOST", "http://127.0.0.1:11434", 1);']
if args.planning_model:
    env_lines.insert(0, f'setenv("SCREEN_AGENT_PLANNING_MODEL", {json.dumps(args.planning_model)}, 1);')

source = r'''#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <Carbon/Carbon.h>
#include <dlfcn.h>
#include <stdlib.h>

static EventHandlerRef gHotKeyHandler;
static EventHotKeyRef gHotKeyOptionSpace;
static EventHotKeyRef gHotKeyControlOptionSpace;

static OSStatus HotKeyChanged(EventHandlerCallRef next, EventRef event, void *data) {
    (void)next; (void)data;
    NSString *phase = GetEventKind(event) == kEventHotKeyPressed ? @"down" : @"up";
    [[NSDistributedNotificationCenter defaultCenter]
        postNotificationName:@"org.mcpvision.contextual.hotkey"
        object:nil userInfo:@{@"phase": phase} deliverImmediately:YES];
    return noErr;
}

static void InstallHotKeys(void) {
    EventTypeSpec specs[] = {
        {kEventClassKeyboard, kEventHotKeyPressed},
        {kEventClassKeyboard, kEventHotKeyReleased},
    };
    InstallEventHandler(GetEventDispatcherTarget(), HotKeyChanged, 2, specs, NULL, &gHotKeyHandler);
    EventHotKeyID opt = {'MCPV', 1};
    EventHotKeyID ctrl = {'MCPV', 2};
    RegisterEventHotKey(kVK_Space, optionKey, opt, GetEventDispatcherTarget(), 0, &gHotKeyOptionSpace);
    RegisterEventHotKey(kVK_Space, controlKey | optionKey, ctrl, GetEventDispatcherTarget(), 0, &gHotKeyControlOptionSpace);
}

int main(int argc, char **argv) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)@{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES});
        InstallHotKeys();
        chdir(ROOT);
        ENVIRONMENT
        void *library = dlopen(LIBRARY, RTLD_NOW | RTLD_GLOBAL);
        int (*pythonMain)(int, char **) = library ? dlsym(library, "Py_BytesMain") : NULL;
        if (!pythonMain) {
            NSAlert *alert = [NSAlert new]; alert.messageText = @"MCP-Vision could not load Python";
            alert.informativeText = @"Rebuild the launcher from the current virtual environment.";
            [alert runModal]; return 1;
        }
        char **arguments = calloc(argc + 5, sizeof(char *));
        arguments[0] = PYTHON; arguments[1] = "-m"; arguments[2] = "mcp_vision.cli"; arguments[3] = "ui";
        for (int i = 1; i < argc; i++) arguments[i + 3] = argv[i];
        return pythonMain(argc + 3, arguments);
    }
}
'''.replace('PYTHON', json.dumps(str(Path(sys.executable).absolute()))).replace(
    'ROOT', json.dumps(str(root))).replace('LIBRARY', json.dumps(str(library))).replace(
    'ENVIRONMENT', '\n        '.join(env_lines))

launcher = root / 'outputs' / 'MCPVisionLauncher.m'
launcher.write_text(source)
digest = hashlib.sha256(source.encode() + plist_data + icon_source.read_bytes()).hexdigest()
binary = macos / 'MCP-Vision'
need_rebuild = args.force or not binary.exists() or not stamp.exists() or stamp.read_text().strip() != digest

if need_rebuild:
    subprocess.run([
        'clang', '-fmodules-cache-path=' + str(root / 'outputs' / 'clang-cache'), str(launcher),
        '-o', str(binary), '-framework', 'AppKit', '-framework', 'ApplicationServices', '-framework', 'Carbon',
        '-framework', 'AVFoundation', '-framework', 'Speech',
    ], check=True)
    # An explicit designated requirement keeps TCC identity stable across
    # launcher and resource rebuilds even though the ad-hoc CDHash changes.
    subprocess.run([
        'codesign', '--force', '--sign', '-', '--identifier', 'org.mcpvision.contextual',
        '--requirements', '=designated => identifier "org.mcpvision.contextual"', str(app),
    ], check=True)
    stamp.write_text(digest + '\n')
    print('Native launcher rebuilt and signed.')
    print('The stable bundle requirement preserves privacy identity across future rebuilds.')
else:
    print('Native launcher unchanged — keeping existing code signature (Accessibility identity preserved).')

if not args.no_install and app != install_path:
    if install_path.exists():
        shutil.rmtree(install_path)
    shutil.copytree(app, install_path, symlinks=True)
if not args.no_install:
    print(install_path)
print(app)
