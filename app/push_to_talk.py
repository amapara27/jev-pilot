"""Listen for Right Option without consuming or rewriting keyboard events."""

from threading import Event, Thread

import Quartz
from CoreFoundation import (
    CFMachPortCreateRunLoopSource, CFMachPortInvalidate, CFRunLoopAddSource,
    CFRunLoopGetCurrent, CFRunLoopRemoveSource, CFRunLoopRunInMode, CFRunLoopStop,
    kCFRunLoopDefaultMode,
)

from .speech_runtime import AudioInputError


RIGHT_OPTION = 61
RIGHT_OPTION_FLAG = 0x40  # IOKit NX_DEVICERALTKEYMASK also distinguishes a held Left Option.


class RightOptionTap:
    """Feed ordered press, release and shortcut cancellation events to speech capture."""

    def __init__(self, on_key):
        self.on_key = on_key
        self.ready = Event()
        self.stopping = Event()
        self.thread = None
        self.loop = None
        self.tap = None
        self.error = None
        self.pressed = False

    def start(self):
        """Ask for Input Monitoring before creating the listen-only tap."""

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
            raise AudioInputError("Push-to-talk needs Input Monitoring. Enable this terminal in "
                                  "System Settings > Privacy & Security > Input Monitoring and restart it.")
        self.thread = Thread(target=self._run, name="right-option-event-tap", daemon=True)
        self.thread.start()
        if not self.ready.wait(2) or self.error:
            raise AudioInputError(self.error or "The Right Option event tap did not start.")

    def _run(self):
        """Own the tap and its run-loop source on a separate thread."""

        source = None
        try:
            mask = (1 << Quartz.kCGEventFlagsChanged) | (1 << Quartz.kCGEventKeyDown)
            self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                               Quartz.kCGEventTapOptionListenOnly, mask, self._event, None)
            if self.tap is None:
                raise AudioInputError("Could not listen for Right Option. Check Input Monitoring for this terminal.")
            self.loop = CFRunLoopGetCurrent()
            source = CFMachPortCreateRunLoopSource(None, self.tap, 0)
            CFRunLoopAddSource(self.loop, source, kCFRunLoopDefaultMode)
            Quartz.CGEventTapEnable(self.tap, True)
            self.ready.set()
            while not self.stopping.is_set():
                CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)
        except Exception as error:
            self.error = str(error) or "The Right Option event tap stopped."
        finally:
            if source is not None:
                CFRunLoopRemoveSource(self.loop, source, kCFRunLoopDefaultMode)
            if self.tap is not None:
                CFMachPortInvalidate(self.tap)
            self.ready.set()

    def _event(self, proxy, kind, event, info):
        """Cancel chords and discard an utterance if macOS interrupts the tap."""

        if kind in {Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput}:
            if self.pressed:
                self.on_key("cancel")
                self.on_key("release")
                self.pressed = False
            Quartz.CGEventTapEnable(self.tap, True)
        elif kind == Quartz.kCGEventFlagsChanged and Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode) == RIGHT_OPTION:
            pressed = bool(Quartz.CGEventGetFlags(event) & RIGHT_OPTION_FLAG)
            if pressed != self.pressed:
                self.pressed = pressed
                self.on_key("press" if pressed else "release")
        elif kind == Quartz.kCGEventKeyDown and self.pressed:
            self.on_key("cancel")
        return event

    def stop(self):
        """Wake the tap and let its owner remove the native source."""

        self.stopping.set()
        if self.loop is not None:
            CFRunLoopStop(self.loop)
        if self.thread is not None:
            self.thread.join()
