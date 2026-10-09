"""Plip's few UI sounds (made by ``ui/scripts/sounds.mjs``): the notch popping open on hover and
tucking back, a question tossed to the brain when you let go of ⌃⌥, and a task really finished.

They play as macOS UI sounds (AudioServices), like Finder's own: at the alert volume, through the
sound-effects output, and silent when "Play user interface sound effects" is off. Plip's own toggle
(Settings → General → Sounds) turns them off too.
"""
from __future__ import annotations

import ctypes
import logging
import os
import random
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

HERE = Path(__file__).with_name("sounds")
# name -> takes (files without .wav); a sound with several takes never plays the same one twice in a row
TAKES = {"open": ("open-1", "open-2", "open-3"), "close": ("close",), "sent": ("sent",), "done": ("done",)}
IS_UI_SOUND = int.from_bytes(b"isui", "big")          # kAudioServicesPropertyIsUISound


class Sounds:
    def __init__(self, enabled: Callable[[], bool] = lambda: True,
                 player: Callable[[str], None] | None = None, folder: Path = HERE):
        self.enabled = enabled
        self.folder = folder
        self._player = player                         # plays one take; built on first use
        self._last: dict[str, str] = {}

    def play(self, name: str) -> None:
        """Safe from any thread, returns at once, and never raises (a sound must not break a turn)."""
        takes = TAKES.get(name)
        try:
            if not takes or not self.enabled():
                return
            fresh = [take for take in takes if take != self._last.get(name)] or list(takes)
            take = random.choice(fresh)
            self._last[name] = take
            if self._player is None:
                self._player = _system_player(self.folder) or (lambda _take: None)
            self._player(take)
        except Exception:
            log.debug("couldn't play %s", name, exc_info=True)


def _system_player(folder: Path) -> Callable[[str], None] | None:
    """Register every take as a system sound once; then playing one is a single non-blocking call."""
    try:
        toolbox = ctypes.CDLL("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    except OSError:
        return None
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                                           ctypes.c_bool]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    toolbox.AudioServicesCreateSystemSoundID.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    toolbox.AudioServicesSetProperty.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                                 ctypes.c_uint32, ctypes.c_void_p]
    toolbox.AudioServicesPlaySystemSound.argtypes = [ctypes.c_uint32]
    ids: dict[str, int] = {}
    for path in sorted(folder.glob("*.wav")):
        raw = os.fsencode(path)
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
        if not url:
            continue
        sound = ctypes.c_uint32(0)
        status = toolbox.AudioServicesCreateSystemSoundID(url, ctypes.byref(sound))
        cf.CFRelease(url)
        if status == 0:
            yes = ctypes.c_uint32(1)                  # the default already, but say it: obey the system toggle
            toolbox.AudioServicesSetProperty(IS_UI_SOUND, 4, ctypes.byref(sound), 4, ctypes.byref(yes))
            ids[path.stem] = sound.value
    if not ids:
        return None

    def play(take: str) -> None:
        if take in ids:
            toolbox.AudioServicesPlaySystemSound(ids[take])

    return play
