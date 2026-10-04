"""Ground installed application targets and perform locally resolved app actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from AppKit import NSWorkspace, NSWorkspaceOpenConfiguration
from Foundation import NSDate, NSRunLoop

from .models import ActionCandidate
from .semantics import SemanticMenu, extract_payloads
from .text_input import NativeText, TextField, TextInputError


@dataclass(frozen=True)
class AppState:
    """A running app's stable native identity and display name."""

    name: str
    bundle_id: str
    pid: int


@dataclass(frozen=True)
class DesktopState:
    """Compact native facts used to ground semantic choices and recheck effects."""

    active: AppState | None
    running: tuple[AppState, ...]
    ax_trusted: bool
    focused_window_available: bool
    text_fields: tuple[TextField, ...] = ()
    editor_diagnostics: dict[str, object] = field(default_factory=dict)

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
    ("TextEdit", "com.apple.TextEdit", ("textedit", "text edit")),
)


class DesktopError(RuntimeError):
    """A safe-to-display local macOS action or observation failure."""


class MacDesktop:
    """Ground AX editors locally and execute only fresh, validated app/text effects."""

    def __init__(self, *, workspace=None, text=None) -> None:
        self.workspace = workspace if workspace is not None else NSWorkspace.sharedWorkspace()
        self.text = text if text is not None else NativeText()

    def snapshot(self) -> DesktopState:
        """Observe native app/window facts and bounded, locally retained AX editors."""

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
        ax = self.text.ax
        trusted = bool(ax.AXIsProcessTrusted())
        focused = False
        fields = ()
        diagnostics = {}
        if trusted:
            application = ax.AXUIElementCreateApplication(active.pid)
            ax.AXUIElementSetMessagingTimeout(application, 0.2)
            error, window = ax.AXUIElementCopyAttributeValue(application, "AXFocusedWindow", None)
            diagnostics["focused_window_ax_error"] = int(error)
            if error != 0:
                window = None
            focused = window is not None
            if focused:
                fields = self.text.scan(active.pid, active.bundle_id, application, window)
                diagnostics.update(self.text.diagnostics)
        return DesktopState(active, running, trusted, focused, fields, diagnostics)

    def semantic_menu(self, goal: str, state: DesktopState) -> SemanticMenu:
        """Ground all supported installed apps without locally classifying the goal."""

        payloads = extract_payloads(goal)
        targets = {"none": {"description": "No grounded target."}}
        catalog = list(SUPPORTED_APPS)
        if state.active and state.active.bundle_id not in {item[1] for item in catalog}:
            catalog.append((state.active.name, state.active.bundle_id, (state.active.name.casefold(),)))
        for name, bundle_id, aliases in catalog:
            if self.workspace.URLForApplicationWithBundleIdentifier_(bundle_id) is None:
                continue
            targets[bundle_id] = {
                "name": name, "bundle_identifier": bundle_id, "aliases": list(aliases),
                "running": any(app.bundle_id == bundle_id for app in state.running),
                "active": bool(state.active and state.active.bundle_id == bundle_id),
                "process_identifiers": sorted(app.pid for app in state.running if app.bundle_id == bundle_id),
                "scope": "application",
            }
        for item in state.text_fields:
            targets[item.id] = item.provider_target()
        return SemanticMenu(goal, targets, payloads)

    def validate_action(self, action: ActionCandidate, before: DesktopState, fresh: DesktopState) -> None:
        """Bind a typing/focus effect to exact local AX identity and current text facts."""

        if before.active != fresh.active or fresh.active is None:
            raise DesktopError("The foreground app changed while deciding or approving.")
        if action.kind in {"TYPE_TEXT", "FOCUS_FIELD"}:
            identifier = action.parameters["element_id"]
            original = next((item for item in before.text_fields if item.id == identifier), None)
            current = next((item for item in fresh.text_fields if item.id == identifier), None)
            if not fresh.ax_trusted or not original or not current or not self.text.same_target(original, current, typing=action.kind == "TYPE_TEXT"):
                raise TextInputError("The editor, window, text, or caret changed; no text was sent.")
            self.validated_state = fresh

    def execute(self, action: ActionCandidate) -> None:
        """Dispatch a native app, field focus, or insertion effect without replay."""

        if action.kind in {"TYPE_TEXT", "FOCUS_FIELD"}:
            fresh = self.snapshot()
            self.validate_action(action, self.validated_state, fresh)
            target = next(item for item in fresh.text_fields if item.id == action.parameters["element_id"])

            def guard() -> bool:
                """Recheck PID/focus without copying existing document text per event."""

                frontmost = self.workspace.frontmostApplication()
                if frontmost is None or int(frontmost.processIdentifier()) != target.pid:
                    return False
                if action.kind == "FOCUS_FIELD":
                    return True
                application = self.text.ax.AXUIElementCreateApplication(target.pid)
                focused = self.text.read(application, "AXFocusedUIElement")
                return focused is not None and bool(self.text.equal(focused, target.element))

            if action.kind == "FOCUS_FIELD":
                self.text.focus(target, guard)
            else:
                self.text.insert(target, action.parameters["text"], guard)
            return
        if action.kind not in {"OPEN_APP", "FOCUS_APP"}:
            raise DesktopError("This operation has no native executor yet.")
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
