"""Ground generic AXPress controls plus stricter Notes/Finder workflows in live native state."""

from collections import deque, Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic
from urllib.parse import unquote, urlparse

from .text_input import TextInputError, native_true, COLLECTION_ROLES


NOTES = "com.apple.Notes"
FINDER = "com.apple.finder"
WORKFLOW_KINDS = {"CREATE_NOTE", "PREPARE_SEARCH", "SEARCH", "SELECT_ITEM", "OPEN_ITEM", "PRESS_CONTROL"}


def file_identity(url):
    """Resolve only local files, keeping identity and changes bound to the decision."""

    parsed = urlparse(str(url or ""))
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
        return None
    try:
        path = Path(unquote(parsed.path)).resolve(strict=True)
        info = path.stat()
    except (OSError, ValueError):
        return None
    return path.as_uri(), (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


@dataclass(frozen=True)
class WorkflowTarget:
    """Keep native handles and file facts local; send only bounded targeting metadata."""

    id: str
    kind: str
    pid: int
    bundle_id: str
    label: str
    element: object = field(compare=False, repr=False)
    window: object = field(compare=False, repr=False)
    selected: bool = False
    url: str | None = None
    identity: tuple | None = None
    value: str | None = field(default=None, repr=False)
    ambiguous: bool = False
    role: str = ""
    sole_selected: bool = False
    expanded: bool | None = None

    def provider_target(self):
        """Expose file identity paths, never existing query/document contents or handles."""

        return {"scope": "control" if self.kind == "control" else "finder_item", "bundle_identifier": self.bundle_id,
                "process_identifier": self.pid, "label": self.label, "url": self.url,
                "selected": self.selected, "sole_selected": self.sole_selected, "ambiguous": self.ambiguous,
                "role": self.role}


class NativeWorkflows:
    """Observe bounded native controls and dispatch one guarded app-specific effect."""

    def __init__(self, text):
        self.text = text
        self.previous = ()
        self.next_id = 0

    def sole_selection(self, node):
        """Require one native collection selection; opening must never affect other files."""

        current = self.text.read(node, "AXParent")
        for _ in range(16):
            if current is None or monotonic() >= self.text.scan_deadline:
                return False
            rows = self.text.read(current, "AXSelectedRows")
            if rows is None:
                rows = self.text.read(current, "AXSelectedChildren")
            if rows is not None:
                return len(rows) == 1 and bool(self.text.equal(rows[0], node))
            current = self.text.read(current, "AXParent")
        return False

    def scan(self, pid, bundle, application, window):
        """Discover exact English menu actions, search fields and visible URL-backed items."""

        text, ax = self.text, self.text.ax
        queue = deque([window, text.read(application, "AXMenuBar")])
        deferred = deque()
        visited, found, selected_notes = [], [], []
        deadline = monotonic() + 1
        text.scan_deadline = deadline
        while (queue or deferred) and len(visited) < 256 and monotonic() < deadline:
            if not queue:
                queue, deferred = deferred, deque()
            node = queue.popleft()
            if node is None or any(text.equal(node, old) for old in visited):
                continue
            visited.append(node)
            if not text.safe_ancestry(node):
                continue
            role = text.read(node, "AXRole")
            label = next((str(value) for value in (text.read(node, "AXTitle"), text.read(node, "AXDescription")) if isinstance(value, str) and value), "")[:240]
            enabled_error, enabled = ax.AXUIElementCopyAttributeValue(node, "AXEnabled", None)
            error, actions = ax.AXUIElementCopyActionNames(node, None)
            press = error == 0 and "AXPress" in (actions or ())
            available = (enabled_error == 0 and native_true(enabled)) or (enabled_error == -25205 and press)
            kind, url, identity, value = None, None, None, None
            if bundle == NOTES and role in {"AXTable", "AXOutline", "AXList"}:
                rows = list(text.read(node, "AXSelectedRows") or ())[:32]
                selected_notes.extend(rows)
                queue.extendleft(rows[:max(0, 256 - len(queue))])
            if bundle == NOTES and role == "AXRow" and (native_true(text.read(node, "AXSelected")) or any(text.equal(node, row) for row in selected_notes)):
                kind, label = "note_selection", "Selected note"
            if available and press and role in {"AXMenuItem", "AXButton"}:
                if label and not text.read(node, "AXURL") and not text.read(node, "AXFilename"):
                    kind = "control"
                if bundle == NOTES and label.casefold() == "new note":
                    kind = "new_note"
                elif bundle == FINDER and label.casefold() in {"find", "find…", "find..."}:
                    kind = "find"
                elif bundle == FINDER and label.casefold() == "open":
                    kind = "open"
            if bundle == FINDER and role == "AXTextField" and native_true(enabled):
                search = text.read(node, "AXSubrole") == "AXSearchField" or label.casefold() in {"search", "search text", "search field"}
                raw_value = text.read(node, "AXValue") if search else None
                if search and text.settable(node, "AXValue") and isinstance(raw_value, str) and len(raw_value) <= 4096:
                    kind, value = "query", raw_value
            if bundle == FINDER and role in {"AXRow", "AXCell", "AXButton", "AXImage"}:
                resolved = file_identity(text.read(node, "AXURL"))
                if resolved and text.settable(node, "AXSelected") and available:
                    kind, (url, identity) = "item", resolved
                    label = label or Path(unquote(urlparse(url).path)).name[:240]
            if kind:
                old = next((item for item in self.previous if item.pid == pid and text.equal(item.element, node)), None)
                identifier = old.id if old else f"workflow_{self.next_id}"
                if not old:
                    self.next_id += 1
                found.append(WorkflowTarget(identifier, kind, pid, bundle, label, node, window,
                                            native_true(text.read(node, "AXSelected")), url, identity, value, role=role,
                                            sole_selected=self.sole_selection(node) if kind == "item" else False,
                                            expanded=native_true(text.read(node, "AXExpanded")) if kind == "control" and text.read(node, "AXExpanded") is not None else None))
            # Document text and paragraph children never contribute workflow candidates.
            if role in {"AXTextArea", "AXTextField"}:
                continue
            children = text.read(node, "AXVisibleChildren")
            if children is None:
                children = text.read(node, "AXChildren")
            pending = list(children or ())[:max(0, 256 - len(queue) - len(deferred))]
            if role in COLLECTION_ROLES:
                deferred.extend(pending)
            else:
                queue.extendleft(reversed(pending))
        # Multiple AX representations of the same URL are one item, not duplicate files.
        deduplicated = {}
        for item in found:
            key = (item.kind, item.url) if item.kind == "item" else item.id
            if key not in deduplicated or item.selected:
                deduplicated[key] = item
        found = list(deduplicated.values())
        counts = Counter(item.label.casefold() for item in found if item.kind == "item")
        found = [replace(item, ambiguous=counts[item.label.casefold()] > 1) if item.kind == "item" else item for item in found]
        self.previous = tuple(found)
        return self.previous

    def same_target(self, original, current):
        """Reject changed controls, windows, query baselines, selection or file identity."""

        return (original == current and self.text.equal(original.element, current.element)
                and self.text.equal(original.window, current.window))

    def execute(self, action, state):
        """Send one prevalidated AX operation; uncertain effects never get a fallback."""

        target = next(item for item in state.workflow_targets if item.id == action.parameters["element_id"])
        ax = self.text.ax
        if action.kind == "SEARCH":
            error = ax.AXUIElementSetAttributeValue(target.element, "AXValue", action.parameters["text"])
        elif action.kind == "SELECT_ITEM":
            error = ax.AXUIElementSetAttributeValue(target.element, "AXSelected", True)
        else:
            if action.kind == "OPEN_ITEM":
                target = next(item for item in state.workflow_targets if item.id == action.parameters["open_control_id"])
            error = ax.AXUIElementPerformAction(target.element, "AXPress")
        if error:
            raise TextInputError(f"The app-specific Accessibility effect failed ({int(error)}); it was not retried.", effect_sent=True)
