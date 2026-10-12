"""Observe live app/editor/control targets and guard their native effects."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from time import monotonic

from AppKit import NSWorkspace, NSWorkspaceOpenConfiguration
from Foundation import NSDate, NSRunLoop, NSBundle, NSURL

from .models import ActionCandidate
from .semantics import SemanticMenu, extract_payloads
from .text_input import NativeText, TextField, TextInputError
from .workflows import NativeWorkflows, WorkflowTarget, WORKFLOW_KINDS, file_identity
from .verification import selected_notes
from .app_catalog import APP_ALIASES, APPLICATION_ROOTS, discover_applications
from .web_navigation import BROWSER_BUNDLES, SEARCH_SITES, WEB_OPERATIONS, browser_url, normalize_web_url
from .interactions import NativeInteractions, InteractionTarget


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
    workflow_targets: tuple[WorkflowTarget, ...] = ()
    document_url: str | None = None
    browser_url: str | None = None
    interaction_targets: tuple[InteractionTarget, ...] = ()

    def provider_state(self) -> dict[str, object]:
        """Send only the active app; candidates already describe named targets."""

        return {
            "active_application": _app_dict(self.active) if self.active else None,
            "accessibility_trusted": self.ax_trusted,
            "focused_window_available": self.focused_window_available,
        }


def _app_dict(app: AppState) -> dict[str, object]:
    return {"name": app.name, "bundle_identifier": app.bundle_id, "process_identifier": app.pid}


class DesktopError(RuntimeError):
    """A safe-to-display local macOS action or observation failure."""


class MacDesktop:
    """Ground AX editors locally and execute only fresh, validated app/text effects."""

    def __init__(self, *, workspace=None, text=None, application_roots=APPLICATION_ROOTS) -> None:
        self.workspace = workspace if workspace is not None else NSWorkspace.sharedWorkspace()
        self.text = text if text is not None else NativeText()
        self.workflows = NativeWorkflows(self.text)
        self.interactions = NativeInteractions(self.text)
        self.applications = discover_applications(application_roots)

    def application_catalog(self, state: DesktopState | None = None) -> tuple:
        """Combine discovered bundles, running apps and familiar installed aliases."""

        catalog = {bundle: (name, bundle, aliases) for name, bundle, aliases in APP_ALIASES}
        catalog.update({item[1]: item for item in getattr(self, "applications", ())})
        if state is not None:
            for app in state.running + ((state.active,) if state.active else ()):
                if app.bundle_id not in catalog:
                    catalog[app.bundle_id] = (app.name, app.bundle_id, (app.name.casefold(),))
        return tuple(item for item in catalog.values()
                     if self.workspace.URLForApplicationWithBundleIdentifier_(item[1]) is not None)

    def application_aliases(self) -> dict[str, str]:
        """Expose only unique installed aliases to early speech-prefix recognition."""

        owners = {}
        for _, bundle, aliases in self.application_catalog():
            for alias in aliases:
                owners.setdefault(alias, set()).add(bundle)
        return {alias: next(iter(bundles)) for alias, bundles in owners.items() if len(bundles) == 1}

    def default_browser(self) -> str | None:
        """Ask Launch Services for the current HTTPS handler without opening a URL."""

        if not hasattr(self.workspace, "URLForApplicationToOpenURL_"):
            return None
        url = self.workspace.URLForApplicationToOpenURL_(NSURL.URLWithString_("https://example.com/"))
        bundle = NSBundle.bundleWithURL_(url) if url is not None else None
        return str(bundle.bundleIdentifier()) if bundle and bundle.bundleIdentifier() else None

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
        workflow_targets, document_url, current_browser_url = (), None, None
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
                workflow_targets = self.workflows.scan(active.pid, active.bundle_id, application, window)
                resolved = file_identity(self.text.read(window, "AXDocument") or self.text.read(window, "AXURL"))
                document_url = resolved[0] if resolved else None
                if active.bundle_id in BROWSER_BUNDLES or active.bundle_id == self.default_browser():
                    current_browser_url = browser_url(self.text, window)
        state = DesktopState(active, running, trusted, focused, fields, diagnostics, workflow_targets, document_url, current_browser_url)
        if trusted and focused and getattr(self, 'interaction_commands', ()):
            state = replace(state, interaction_targets=self.interactions.scan(state, application, window))
        return state

    def semantic_menu(self, goal: str, state: DesktopState) -> SemanticMenu:
        """Ground all supported installed apps without locally classifying the goal."""

        payloads = extract_payloads(goal)
        targets = {"none": {"description": "No grounded target."}}
        catalog = self.application_catalog(state)
        owners = {}
        for _, bundle, aliases in catalog:
            for alias in aliases:
                owners.setdefault(alias, set()).add(bundle)
        quoted = r'"(?:\\.|[^"\\])*"|“[^”]*”|(?<!\w)\x27[^\x27]*\x27(?!\w)'
        masked = re.sub(quoted, lambda match: " " * len(match.group()), goal)
        literal = re.search(r"\b(?:type|write|dictate|insert|google|search\s+(?:google|youtube|(?:the\s+)?web)(?:\s+for)?)\s+", masked, re.IGNORECASE)
        prefix = goal[:literal.start()] if literal else goal
        command = re.sub(quoted, lambda match: match.group()[1:-1], prefix).casefold()
        if literal:
            context = re.search(r"\b(?:in|using|with)\s+[\w .-]+$", masked, re.IGNORECASE)
            if context:
                command += " " + context.group().casefold()
        for alias, bundles in owners.items():
            if len(bundles) > 1 and re.search(r"\b(?:open|launch|focus|switch\s+to|in|using|with)\s+" + re.escape(alias) + r"(?=$|[.,!?]|\s+(?:app\b|and\b|then\b))", command):
                raise DesktopError(f"The app name {alias!r} is ambiguous: {', '.join(sorted(bundles))}. Specify its bundle identifier.")
        default_browser = self.default_browser()
        for name, bundle_id, aliases in catalog:
            targets[bundle_id] = {
                "name": name, "bundle_identifier": bundle_id, "aliases": list(aliases),
                "running": any(app.bundle_id == bundle_id for app in state.running),
                "active": bool(state.active and state.active.bundle_id == bundle_id),
                "process_identifiers": sorted(app.pid for app in state.running if app.bundle_id == bundle_id),
                "scope": "application",
                "is_browser": bundle_id in BROWSER_BUNDLES or bundle_id == default_browser,
                "default_browser": bundle_id == default_browser,
            }
        for bundle, facts in list(targets.items()):
            if facts.get("is_browser"):
                for site in SEARCH_SITES:
                    targets[f"web:{site}:{bundle}"] = {"scope": "website_search", "site": site,
                        "bundle_identifier": bundle, "browser": facts["name"], "default_browser": facts["default_browser"]}
        for item in state.text_fields:
            targets[item.id] = item.provider_target()
        controls = {}
        for kind in ("new_note", "find", "query", "open"):
            matches = [item for item in state.workflow_targets if item.kind == kind]
            menus = [item for item in matches if item.role == "AXMenuItem"]
            if kind != "query" and menus:
                matches = menus
            if len(matches) == 1:
                controls[kind] = matches[0].id
        if state.active and state.active.bundle_id in targets:
            targets[state.active.bundle_id]["workflow_controls"] = controls
        for item in state.workflow_targets:
            if item.kind in {"item", "control"}:
                targets[item.id] = item.provider_target()
        for item in state.interaction_targets:
            if item.command in getattr(self, 'interaction_commands', ()):
                targets[item.id] = item.provider_target()
        return SemanticMenu(goal, targets, payloads)

    def validate_action(self, action: ActionCandidate, before: DesktopState, fresh: DesktopState) -> None:
        """Bind a typing/focus effect to exact local AX identity and current text facts."""

        if before.active != fresh.active or fresh.active is None:
            raise DesktopError("The foreground app changed while deciding or approving.")
        if action.kind in {'KEYBOARD_ACTION', 'SCROLL'}:
            if before.browser_url != fresh.browser_url:
                raise TextInputError('The browser page changed before the command; no effect was sent.')
            if fresh.active.bundle_id == 'com.apple.Notes' and selected_notes(before) != selected_notes(fresh):
                raise TextInputError('The selected note changed before the command; no effect was sent.')
            identifier = action.parameters['element_id']
            original = next((item for item in before.interaction_targets if item.id == identifier), None)
            current = next((item for item in fresh.interaction_targets if item.id == identifier), None)
            if not fresh.ax_trusted or not original or not current or not self.interactions.same_target(original, current):
                raise TextInputError('The command focus, editor, menu or scroll context changed; no effect was sent.')
            self.validated_state = fresh
        if action.kind in WEB_OPERATIONS:
            if normalize_web_url(action.parameters.get("url")) != action.parameters.get("url"):
                raise DesktopError("The browser destination is not a validated HTTP URL.")
            if self.workspace.URLForApplicationWithBundleIdentifier_(action.parameters["bundle_identifier"]) is None:
                raise DesktopError("The selected browser is no longer installed.")
            self.validated_state = fresh
        if action.kind in WORKFLOW_KINDS:
            identifiers = [action.parameters["element_id"]]
            if action.kind == "OPEN_ITEM":
                identifiers.append(action.parameters["open_control_id"])
            for identifier in identifiers:
                original = next((item for item in before.workflow_targets if item.id == identifier), None)
                current = next((item for item in fresh.workflow_targets if item.id == identifier), None)
                if not fresh.ax_trusted or not original or not current or not self.workflows.same_target(original, current):
                    raise DesktopError("The app-specific control, file, selection or query changed; no effect was sent.")
            self.validated_state = fresh
        if action.kind in {"TYPE_TEXT", "FOCUS_FIELD"}:
            if fresh.active.bundle_id == "com.apple.Notes" and selected_notes(before) != selected_notes(fresh):
                raise TextInputError("The selected note changed before native input; no text or focus was sent.")
            identifier = action.parameters["element_id"]
            original = next((item for item in before.text_fields if item.id == identifier), None)
            current = next((item for item in fresh.text_fields if item.id == identifier), None)
            if not fresh.ax_trusted or not original or not current or not self.text.same_target(original, current, typing=action.kind == "TYPE_TEXT"):
                raise TextInputError("The editor, window, text, or caret changed; no text was sent.")
            self.validated_state = fresh

    def execute(self, action: ActionCandidate) -> None:
        """Dispatch a native app, field focus, or insertion effect without replay."""

        if action.kind in {'KEYBOARD_ACTION', 'SCROLL'}:
            fresh = self.snapshot()
            self.validate_action(action, self.validated_state, fresh)
            target = next(item for item in fresh.interaction_targets if item.id == action.parameters['element_id'])

            def guard():
                frontmost = self.workspace.frontmostApplication()
                if frontmost is None or int(frontmost.processIdentifier()) != target.pid:
                    return False
                application = self.text.ax.AXUIElementCreateApplication(target.pid)
                return (self.text.equal(self.text.read(application, 'AXFocusedWindow'), target.window)
                        and self.text.equal(self.text.read(application, 'AXFocusedUIElement'), target.focused))

            self.interactions.execute(target, guard)
            return
        if action.kind in WORKFLOW_KINDS:
            fresh = self.snapshot()
            try:
                self.validate_action(action, self.validated_state, fresh)
            except DesktopError as error:
                raise TextInputError(str(error), effect_sent=False) from error
            self.workflows.execute(action, fresh)
            return
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
        if action.kind not in {"OPEN_APP", "FOCUS_APP"} | WEB_OPERATIONS:
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
        if action.kind in WEB_OPERATIONS:
            try:
                self.validate_action(action, self.validated_state, self.snapshot())
            except DesktopError as error:
                raise TextInputError(str(error), effect_sent=False) from error
            self.workspace.openURLs_withApplicationAtURL_configuration_completionHandler_(
                [NSURL.URLWithString_(action.parameters["url"])], url, configuration,
                lambda app, error: completion.append((app, error)))
        else:
            self.workspace.openApplicationAtURL_configuration_completionHandler_(
                url, configuration, lambda app, error: completion.append((app, error)))
        deadline = monotonic() + 5
        while not completion and monotonic() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
        if not completion or completion[0][0] is None or completion[0][1] is not None:
            raise DesktopError("macOS did not confirm the browser URL request." if action.kind in WEB_OPERATIONS
                               else "macOS did not confirm the application activation request.")

    @staticmethod
    def _app(application: object) -> AppState:
        return AppState(
            name=application.localizedName() or "Unknown",
            bundle_id=application.bundleIdentifier() or "",
            pid=int(application.processIdentifier()),
        )
