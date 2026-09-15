"""Build a local launcher with a stable macOS privacy-permission identity."""
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--output', default='outputs/MCP-Vision.app')
parser.add_argument('--planning-model', default=None)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
app = Path(args.output).resolve()
macos = app / 'Contents' / 'MacOS'
macos.mkdir(parents=True, exist_ok=True)
info = {'CFBundleIdentifier': 'org.mcpvision.contextual', 'CFBundleName': 'MCP-Vision',
        'CFBundleDisplayName': 'MCP-Vision', 'CFBundleExecutable': 'MCP-Vision',
        'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'LSUIElement': True,
        'NSAppleEventsUsageDescription': 'MCP-Vision reads and controls your selected Chrome tab when you ask it to.'}
(app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(info))
import sysconfig
library = Path(sys.base_prefix) / 'lib' / sysconfig.get_config_var('LDLIBRARY')
if not library.exists():
    raise SystemExit(f'Python shared library is unavailable: {library}')
source = r'''#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#include <dlfcn.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)@{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES});
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
'''.replace('PYTHON', json.dumps(str(Path(sys.executable).absolute()))).replace('ROOT', json.dumps(str(root))).replace(
    'LIBRARY', json.dumps(str(library))).replace('ENVIRONMENT',
    ('setenv("SCREEN_AGENT_PLANNING_MODEL", ' + json.dumps(args.planning_model) + ', 1);') if args.planning_model else '')
launcher = root / 'outputs' / 'MCPVisionLauncher.m'
launcher.parent.mkdir(exist_ok=True)
launcher.write_text(source)
subprocess.run(['clang', '-fmodules-cache-path=' + str(root / 'outputs' / 'clang-cache'), str(launcher),
                '-o', str(macos / 'MCP-Vision'), '-framework', 'AppKit', '-framework', 'ApplicationServices'], check=True)
subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
print(app)
