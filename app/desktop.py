"""Read macOS app state and perform only locally named app actions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from time import monotonic

import ApplicationServices as AX
from AppKit import NSWorkspace, NSWorkspaceOpenConfiguration
from Foundation import NSDate, NSRunLoop

from .models import ActionCandidate


@dataclass(frozen=True)
class AppState:
    """A running app's stable native identity and display name."""

    name: str
    bundle_id: str
    pid: int


@dataclass(frozen=True)
class DesktopState:
    """The compact state needed for the first app-control milestone."""

    active: AppState | None
    running: tuple[AppState, ...]
    ax_trusted: bool
    focused_window_available: bool

    def provider_state(self) -> dict[str, object]:
        """Send only the active app; candidates already describe named targets."""

        return {
            "active_application": _app_dict(self.active) if self.active else None,
            "accessibility_trusted": self.ax_trusted,
            "focused_window_available": self.focused_window_available,
        }


def _app_dict(app: AppState) -> dict[str, object]:
    return {"name": app.name, "bundle_identifier": app.bundle_id, "process_identifier": app.pid}


# A small named-app vocabulary proves the native path without guessing arbitrary bundle IDs.
SUPPORTED_APPS = (
    ("Finder", "com.apple.finder", ("finder",)),
    ("Terminal", "com.apple.Terminal", ("terminal",)),
    ("Visual Studio Code", "com.microsoft.VSCode", ("vs code", "vscode", "visual studio code")),
    ("Safari", "com.apple.Safari", ("safari",)),
    ("Google Chrome", "com.google.Chrome", ("chrome", "google chrome")),
    ("System Settings", "com.apple.systempreferences", ("settings", "system settings")),
    ("Notes", "com.apple.Notes", ("notes",)),
)


class DesktopError(RuntimeError):
    """A safe-to-display local macOS action or observation failure."""


class MacDesktop:
    """Uses AppKit for app effects and AX for readiness without reading user content."""

    def __init__(self) -> None:
        self.workspace = NSWorkspace.sharedWorkspace()

    def snapshot(self) -> DesktopState:
        """Observe the foreground app, running apps, and focused-window availability."""

        frontmost = self.workspace.frontmostApplication()
        if frontmost is None:
            raise DesktopError("No frontmost application is available.")
        active = self._app(frontmost)
        running = tuple(
            sorted(
                (self._app(app) for app in self.workspace.runningApplications() if app.bundleIdentifier()),
                key=lambda app: (app.name.casefold(), app.pid),
            )
        )
        trusted = bool(AX.AXIsProcessTrusted())
        focused = False
        if trusted:
            application = AX.AXUIElementCreateApplication(active.pid)
            error, window = AX.AXUIElementCopyAttributeValue(
                application, AX.kAXFocusedWindowAttribute, None
            )
            focused = error == AX.kAXErrorSuccess and window is not None
        return DesktopState(active, running, trusted, focused)

    def candidates(self, goal: str, state: DesktopState) -> tuple[ActionCandidate, ...]:
        """Offer only an explicitly named installed app plus STOP."""

        actions: list[ActionCandidate] = []
        for name, bundle_id, aliases in SUPPORTED_APPS:
            if not any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", goal, re.IGNORECASE) for alias in aliases):
                continue
            if state.active and state.active.bundle_id == bundle_id:
                continue
            if self.workspace.URLForApplicationWithBundleIdentifier_(bundle_id) is None:
                continue
            running = any(app.bundle_id == bundle_id for app in state.running)
            kind = "FOCUS_APP" if running else "OPEN_APP"
            actions.append(
                ActionCandidate(
                    id=f"action_{len(actions)}",
                    kind=kind,
                    parameters={"bundle_identifier": bundle_id, "name": name},
                    description=f"{'Focus' if running else 'Open'} {name}.",
                )
            )
        actions.append(
            ActionCandidate(
                id=f"action_{len(actions)}",
                kind="STOP",
                parameters={},
                description="Stop when the goal is complete or no supplied action advances it.",
            )
        )
        return tuple(actions)

    def execute(self, action: ActionCandidate) -> None:
        """Request one AppKit activation and wait for its native completion callback."""

        if action.kind not in {"OPEN_APP", "FOCUS_APP"}:
            raise DesktopError("This action is not executable in the first Python milestone.")
        bundle_id = action.parameters.get("bundle_identifier")
        if not isinstance(bundle_id, str):
            raise DesktopError("The selected app has no valid bundle identifier.")
        url = self.workspace.URLForApplicationWithBundleIdentifier_(bundle_id)
        if url is None:
            raise DesktopError("The selected app is no longer installed.")
        configuration = NSWorkspaceOpenConfiguration.configuration()
        configuration.setActivates_(True)
        completion: list[tuple[object, object]] = []
        self.workspace.openApplicationAtURL_configuration_completionHandler_(
            url, configuration, lambda app, error: completion.append((app, error))
        )
        deadline = monotonic() + 5
        while not completion and monotonic() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
        if not completion or completion[0][0] is None or completion[0][1] is not None:
            raise DesktopError("macOS did not confirm the application activation request.")

    @staticmethod
    def _app(application: object) -> AppState:
        return AppState(
            name=application.localizedName() or "Unknown",
            bundle_id=application.bundleIdentifier() or "",
            pid=int(application.processIdentifier()),
        )
