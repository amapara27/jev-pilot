"""Run commands against disposable real-app fixtures and check each result independently.

Run from an Accessibility-trusted Terminal: python -m scripts.native_matrix [--voice | --live-voice] [--only copy,zoom] [--repeat 3]
Every case calls live Jev and real native effects, but only inside harness-owned fixtures:
a temporary TextEdit document, a Safari window serving local pages, and a temporary Finder folder.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
from time import monotonic, perf_counter, sleep
from types import SimpleNamespace
from typing import Callable
from urllib.parse import parse_qs, urlparse

import ApplicationServices as AX
from AppKit import NSRunningApplication

from app.cli import _parser, _run_voice, _save_report
from app.config import load_api_key
from app.desktop import MacDesktop
from app.runtime import run_goal
from app.safety import requires_confirmation
from app.text_input import TextInputError


ROOT = Path(__file__).resolve().parents[1]
# The page reports its own load, scroll and viewport facts, so web checks never rely on Jev Pilot's observations.
PAGE = """<!doctype html><title>Jev matrix {path}</title>
<body style="margin:0;height:{height}px;background:linear-gradient(#fff,#ccd)"><h1>{path}</h1>
<a href="/clicked">Matrix Downloads</a>
<a href="/duplicate-a">Matrix Duplicate</a><a href="/duplicate-b">Matrix Duplicate</a>
<button aria-expanded="false" onclick="this.setAttribute('aria-expanded','true');send('button')">Matrix Details</button><script>
const id = Math.random().toString(36).slice(2);
const send = kind => fetch(`/beacon?kind=${{kind}}&page={path}&load=${{id}}&y=${{Math.round(scrollY)}}`
  + `&max=${{document.documentElement.scrollHeight - innerHeight}}&w=${{innerWidth}}&dpr=${{devicePixelRatio}}`);
let timer; addEventListener('scroll', () => {{ clearTimeout(timer); timer = setTimeout(() => send('scroll'), 120); }});
addEventListener('resize', () => send('resize')); send('load');
</script></body>"""

# Finder rejects `close every … whose`, so close the uniquely named fixture window(s) one at a time, bounded.
FINDER_CLOSE = """tell application "Finder"
    repeat 5 times
        if exists Finder window "{name}" then close Finder window "{name}"
    end repeat
end tell"""

def osa(script: str, *, preserve_whitespace=False) -> str:
    """Run AppleScript for fixture setup and oracle reads; never for the command under test."""

    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"AppleScript failed: {result.stderr.strip()}")
    return result.stdout.removesuffix('\n') if preserve_whitespace else result.stdout.strip()


def keys(stroke: str) -> None:
    """Send a setup keystroke to the frontmost fixture app, e.g. 'keystroke "a" using command down'."""

    osa(f'tell application "System Events" to {stroke}')
    sleep(0.3)


def frontmost() -> str:
    return osa('tell application "System Events" to name of first process whose frontmost is true')


def clipboard(text: str | None = None) -> str:
    if text is not None:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
    return subprocess.run(["pbpaste"], capture_output=True, text=True, check=True).stdout


def wait_for(check: Callable[[], tuple[bool, object]], seconds: float = 4) -> tuple[bool, object]:
    """Poll an oracle until it passes or times out; effects and page beacons arrive asynchronously."""

    deadline = monotonic() + seconds
    while True:
        try:
            ok, detail = check()
        except Exception as error:
            ok, detail = False, f"oracle error: {error}"
        if ok or monotonic() >= deadline:
            return ok, detail
        sleep(0.2)


class Beacons:
    """Serve fixture pages and keep every event they report."""

    def __init__(self):
        self.events, self.lock = [], Lock()
        beacons = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                if url.path == "/beacon":
                    with beacons.lock:
                        beacons.events.append({key: values[0] for key, values in parse_qs(url.query).items()})
                    body = b""
                else:
                    body = PAGE.format(path=url.path, height=6000 if url.path == "/long" else 1200).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # Jev Pilot only observes dotted web hostnames, never IP literals; .localhost is loopback (RFC 6761).
        self.base = f"http://jev-matrix.localhost:{self.server.server_port}"
        Thread(target=self.server.serve_forever, daemon=True).start()

    def latest(self, page: str, kind: str | None = None) -> dict | None:
        with self.lock:
            return next((event for event in reversed(self.events)
                         if event["page"] == page and (kind is None or event["kind"] == kind)), None)

    def loads(self, page: str) -> int:
        with self.lock:
            return sum(event["page"] == page and event["kind"] == "load" for event in self.events)


def ax_nodes(bundle: str, limit: int = 4000):
    """Walk the app's focused window independently of app/text_input.py and app/interactions.py."""

    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)
    if not apps:
        return
    app = AX.AXUIElementCreateApplication(apps[0].processIdentifier())
    error, window = AX.AXUIElementCopyAttributeValue(app, "AXFocusedWindow", None)
    queue = [window] if not error and window is not None else []
    while queue and limit:
        node, limit = queue.pop(0), limit - 1
        yield node
        error, children = AX.AXUIElementCopyAttributeValue(node, "AXChildren", None)
        queue.extend(children or [])


def ax(node, attribute):
    error, value = AX.AXUIElementCopyAttributeValue(node, attribute, None)
    return None if error else value


def ax_area(node) -> float:
    ok, size = AX.AXValueGetValue(ax(node, "AXSize"), AX.kAXValueCGSizeType, None)
    return size.width * size.height if ok else 0


class Fixtures:
    """Own every document/window/folder the commands may touch, and remove them afterwards."""

    def __init__(self):
        self.directory = Path(tempfile.mkdtemp(prefix="jev-matrix-", dir=ROOT / ".build")).resolve()
        self.document = self.directory / f"Jev matrix {self.directory.name}.txt"
        self.document.write_text("", encoding="utf-8")
        self.folder = self.directory / f"{self.directory.name} finder"
        self.folder.mkdir()
        for index in range(300):
            (self.folder / f"jev matrix item {index:03}.txt").touch()
        (self.folder / "jevneedle.txt").touch()
        self.web = Beacons()
        self.safari_ready = False

    # TextEdit
    def textedit(self, text: str, *, caret_end=True, select_all=False) -> None:
        subprocess.run(["open", "-a", "TextEdit", str(self.document)], check=True)
        osa('tell application "TextEdit" to activate')
        focused = ('tell application "TextEdit"\n'
                   'if not frontmost or (count of documents) is 0 then return false\n'
                   f'return name of front document is {json.dumps(self.document.name)}\nend tell')
        if not wait_for(lambda: (osa(focused) == "true", None))[0]:
            raise RuntimeError("The disposable TextEdit document did not gain focus.")
        osa(f'tell application "TextEdit" to set text of document "{self.document.name}" to {json.dumps(text, ensure_ascii=False)}')
        sleep(0.3)
        if select_all:
            keys('keystroke "a" using command down')
        elif caret_end:
            keys("key code 125 using command down")
        length = len(text.encode("utf-16-le")) // 2
        expected = (0, length) if select_all else (length, 0) if caret_end else None
        if expected is not None:
            ready, observed = wait_for(lambda: ((value := self.selection()) == expected, value))
            if not ready:
                raise RuntimeError(f"The TextEdit fixture selection was {observed}, expected {expected}; {self.selection_diagnostics}")

    def text(self) -> str | None:
        return osa(f'tell application "TextEdit" to get text of document "{self.document.name}"', preserve_whitespace=True)

    def selected_text(self) -> str | None:
        return next((ax(node, "AXSelectedText") for node in ax_nodes("com.apple.TextEdit")
                     if ax(node, "AXRole") == "AXTextArea"), None)

    def selection(self):
        """Read the fixture's native selection range independently of Jev Pilot."""

        # Resolve the live PID independently: AppKit's cached running-app list can be empty after dispatch.
        pid = int(osa('tell application "System Events" to get unix id of process "TextEdit"'))
        self.selection_diagnostics = {'pid': pid}
        application = AX.AXUIElementCreateApplication(pid)
        AX.AXUIElementSetMessagingTimeout(application, 0.5)
        role_error, _ = AX.AXUIElementCopyAttributeValue(application, "AXRole", None)
        error, node = AX.AXUIElementCopyAttributeValue(application, "AXFocusedUIElement", None)
        self.selection_diagnostics.update(application_error=role_error, focus_error=error,
                                          focused_role=ax(node, "AXRole") if node is not None else None)
        if error or node is None or self.selection_diagnostics["focused_role"] != "AXTextArea":
            return None
        error, value = AX.AXUIElementCopyAttributeValue(node, "AXSelectedTextRange", None)
        self.selection_diagnostics["range_error"] = error
        if error == 0 and value is not None:
            valid, bounds = AX.AXValueGetValue(value, AX.kAXValueCFRangeType, None)
            if valid:
                return (int(bounds[0]), int(bounds[1]))
        return None

    def move_fixture_caret(self):
        """Introduce drift only while the expected disposable document has focus."""

        name = json.dumps(self.document.name)
        osa(f'tell application "TextEdit"\n'
            f'if not frontmost or name of front document is not {name} then error "Fixture lost focus"\n'
            'end tell\n'
            'tell application "System Events"\n'
            'tell process "TextEdit"\n'
            'if not frontmost then error "Fixture lost focus"\n'
            'key code 123\nend tell\nend tell')
        moved, selection = wait_for(lambda: ((value := self.selection()) == (len(SAMPLE) - 1, 0), value))
        if not moved:
            raise RuntimeError(f"The fixture caret did not move: {selection}")

    # Safari: tab 1 is /a, tab 2 is /long, in a window only the harness opened.
    def safari(self, tab: int = 1, *, tabs: int = 2) -> None:
        a, long = f"{self.web.base}/a", f"{self.web.base}/long"
        if not self.safari_ready:
            osa(f'tell application "Safari" to make new document with properties {{URL:"{a}"}}')
            osa(f'tell application "Safari" to tell front window to make new tab with properties {{URL:"{long}"}}')
            self.safari_ready = True
        script = f"""tell application "Safari"
            activate
            set fixture to first window whose URL of tab 1 starts with "{self.web.base}"
            set index of fixture to 1
            repeat while (count of tabs of fixture) > 2
                close last tab of fixture
            end repeat
            repeat while (count of tabs of fixture) < {tabs}
                tell fixture to make new tab with properties {{URL:"{self.web.base}/c"}}
            end repeat
            -- Earlier cases navigate these tabs, so restore the pages each case's oracle reads.
            if URL of tab 1 of fixture is not "{a}" then set URL of tab 1 of fixture to "{a}"
            if URL of tab 2 of fixture is not "{long}" then set URL of tab 2 of fixture to "{long}"
            set current tab of fixture to tab {tab} of fixture
        end tell"""
        osa(script)
        urls = lambda: osa('tell application "Safari" to tell front window to return {URL of tab 1, URL of tab 2}')
        if not wait_for(lambda: (urls() == f"{a}, {long}" and self.web.latest("/long") and self.web.latest("/a"), None))[0]:
            raise RuntimeError("The Safari fixture tabs did not return to /a and /long; no command was sent.")
        sleep(0.5)

    def safari_tab(self) -> dict:
        index, count, url = osa('tell application "Safari" to tell front window to return '
                                '{index of current tab, count of tabs, URL of current tab}').split(", ", 2)
        return {"index": int(index), "count": int(count), "url": url}

    def navigate(self, *paths: str) -> None:
        for path in paths:
            osa(f'tell application "Safari" to set URL of current tab of front window to "{self.web.base}{path}"')
            wait_for(lambda: (self.safari_tab()["url"].endswith(path) and self.web.latest(path) is not None, None))
        sleep(0.5)

    def viewport(self, page="/a") -> int:
        event = self.web.latest(page, "resize") or self.web.latest(page)
        return int(event["w"])

    def scroll_y(self) -> tuple[int, int]:
        event = self.web.latest("/long")
        return int(event["y"]), int(event["max"])

    def settle(self, read: Callable[[], object], seconds: float = 0.6, timeout: float = 5):
        """Wait until an async page fact stops changing, so setup baselines are never read mid-transition."""

        last, since, deadline = read(), monotonic(), monotonic() + timeout
        while monotonic() < deadline:
            sleep(0.1)
            if (now := read()) != last:
                last, since = now, monotonic()
            elif monotonic() - since >= seconds:
                return now
        raise RuntimeError("The fixture page kept changing; no command was sent.")

    def zoom(self, *strokes: str) -> tuple[int, int]:
        """Reset Safari zoom, apply setup strokes, and return (actual-size width, settled width)."""

        keys('key code 29 using command down')
        actual = self.settle(self.viewport)
        for stroke in strokes:
            keys(stroke)
        width = self.settle(self.viewport)
        if strokes and width == actual:
            raise RuntimeError("The setup zoom did not take effect; no command was sent.")
        return actual, width

    def scroll_to(self, edge: str) -> int:
        """Move /long to its top or bottom with Home/End and return the settled offset."""

        keys("key code 115" if edge == "top" else "key code 119")
        y, most = self.settle(self.scroll_y)
        if y != 0 if edge == "top" else y < most - 1:
            raise RuntimeError(f"The setup could not reach the page {edge}; no command was sent.")
        return y

    def expect_url(self, path: str) -> None:
        if not wait_for(lambda: (self.safari_tab()["url"].endswith(path), None))[0]:
            raise RuntimeError(f"The setup did not reach {path}; no command was sent.")

    # Finder: a window showing only the uniquely named harness folder; the user's windows are never touched.
    def finder(self) -> None:
        # A fresh window starts at the top of the list with no search, whatever earlier cases left behind.
        self.close_finder()
        subprocess.run(["open", str(self.folder)], check=True)
        osa('tell application "Finder" to activate')
        front = lambda: osa('tell application "Finder" to name of front Finder window')
        # `open` returns before activation lands, so wait for Finder itself to be the frontmost app too.
        if not wait_for(lambda: (frontmost() == "Finder" and front() == self.folder.name, None))[0]:
            raise RuntimeError("The fixture Finder window did not come to the front; no command was sent.")
        osa(f'tell application "Finder" to set current view of Finder window "{self.folder.name}" to list view')
        sleep(1)
        # Finder restores each folder's scroll position, so put the file pane back at the top.
        if (bar := self.finder_bar()) is not None:
            AX.AXUIElementSetAttributeValue(bar, "AXValue", 0.0)
        if not wait_for(lambda: (self.finder_scroll() == 0, None))[0]:
            raise RuntimeError("The fixture Finder list did not return to the top; no command was sent.")

    def close_finder(self) -> None:
        # A Finder search renames the window, so close both forms of the unique name.
        for name in (self.folder.name, f"Searching “{self.folder.name}”"):
            osa(FINDER_CLOSE.format(name=name))

    def finder_query(self) -> str | None:
        return next((ax(node, "AXValue") for node in ax_nodes("com.apple.finder")
                     if ax(node, "AXSubrole") == "AXSearchField"), None)

    def finder_bar(self):
        # The file pane is the largest scroll area; the sidebar is the smaller one.
        areas = [node for node in ax_nodes("com.apple.finder") if ax(node, "AXRole") == "AXScrollArea"]
        return ax(max(areas, key=ax_area), "AXVerticalScrollBar") if areas else None

    def finder_scroll(self) -> float | None:
        bar = self.finder_bar()
        return None if bar is None else float(ax(bar, "AXValue"))

    def close(self) -> None:
        for script in (f'tell application "TextEdit" to close (every document whose name is "{self.document.name}") saving no',
                       f'tell application "Safari" to close (every window whose URL of tab 1 starts with "{self.web.base}")'):
            try:
                osa(script)
            except RuntimeError:
                pass
        try:
            self.close_finder()
        except RuntimeError:
            pass
        self.web.server.shutdown()
        shutil.rmtree(self.directory, ignore_errors=True)


@dataclass
class Case:
    """One spoken command: setup returns a baseline, check proves the effect from outside Jev Pilot."""

    name: str
    goal: str
    scope: str  # v0.1 must keep passing; v0.2 is the repair backlog
    expects: str | None  # discovery token: interaction command or finder_<control>
    setup: Callable[[Fixtures], object]
    check: Callable[[Fixtures, object], tuple[bool, object]]
    drift_at: str | None = None
    refused: bool = False


SAMPLE = "Jev matrix sample text"


def cases() -> list[Case]:
    def same(value, expected):
        return value == expected, value

    def setup_caret_drift(fixtures):
        fixtures.textedit(SAMPLE)

        def ready():
            observed = {"text": fixtures.text(), "selection": fixtures.selection()}
            return observed == {"text": SAMPLE, "selection": (len(SAMPLE), 0)}, observed

        settled, observed = wait_for(ready)
        if not settled:
            raise RuntimeError(f"TextEdit did not settle to the fixture text and caret at the end: {observed}")

    def check_caret_drift(fixtures, _):
        detail = {"text": fixtures.text(), "selection": fixtures.selection()}
        return detail == {"text": SAMPLE, "selection": (len(SAMPLE) - 1, 0)}, detail

    def click_baseline(fixtures):
        fixtures.safari()
        # A fresh page makes every button and navigation oracle start unsatisfied.
        fixtures.navigate('/click-' + str(monotonic()).replace('.', '-'))
        return fixtures.safari_tab()['url']

    def click_check(fixtures, baseline, kind):
        current = fixtures.safari_tab()
        page = urlparse(baseline).path
        event = fixtures.web.latest(page, 'button')
        if kind == 'button':
            return event is not None and current['url'] == baseline, event
        if kind == 'refused':
            return current['url'] == baseline and event is None, current
        return current['url'] == fixtures.web.base + '/clicked' and fixtures.web.loads('/clicked') > 0, current

    def check_deletion(fixtures, _):
        value, selection = fixtures.text(), fixtures.selection()
        return (value == 'hello 🌍 ' and selection == (9, 0),
                {'text': value, 'selection': selection, 'selection_diagnostics': fixtures.selection_diagnostics})

    return [
        Case('delete_last_word', 'delete the last word', 'v0.3', 'delete_last_word',
             lambda f: f.textedit('hello 🌍 café!   '),
             check_deletion),
        Case('new_paragraph', 'new paragraph', 'v0.3', 'new_paragraph',
             lambda f: f.textedit(SAMPLE),
             lambda f, _: same((f.text(), f.selection()), (SAMPLE + '\n\n', (len(SAMPLE) + 2, 0)))),
        Case('named_link', 'click Matrix Downloads', 'v0.3', None, click_baseline,
             lambda f, baseline: click_check(f, baseline, 'link')),
        Case('named_button', 'click Matrix Details', 'v0.3', None, click_baseline,
             lambda f, baseline: click_check(f, baseline, 'button')),
        Case('ambiguous_click', 'click Matrix Duplicate', 'v0.3', None, click_baseline,
             lambda f, baseline: click_check(f, baseline, 'refused'), refused=True),
        # v0.1 regressions
        Case("type_text", 'type "matrix typed"', "v0.1", None,
             lambda f: f.textedit(SAMPLE), lambda f, _: same(f.text(), SAMPLE + "matrix typed")),
        Case("select_all", "select all", "v0.1", "select_all",
             lambda f: f.textedit(SAMPLE), lambda f, _: same(f.selected_text(), SAMPLE)),
        Case("open_app", "open TextEdit", "v0.1", None,
             lambda f: f.finder(),
             lambda f, _: same(frontmost(), "TextEdit")),
        Case("open_website", "open example.com in Safari", "v0.1", None,
             lambda f: f.safari(), lambda f, _: (f.safari_tab()["url"].startswith("https://example.com"), f.safari_tab())),
        Case("google_search", "google jev matrix check", "v0.1", None,
             lambda f: f.safari(),
             lambda f, _: ("google." in (url := f.safari_tab()["url"]) and "jev+matrix+check" in url, url)),
        # v0.2 text editing
        Case("copy", "copy", "v0.2", "copy",
             lambda f: (clipboard("JEV-SENTINEL"), f.textedit(SAMPLE, select_all=True)),
             lambda f, _: same(clipboard(), SAMPLE)),
        Case("cut", "cut", "v0.2", "cut",
             lambda f: (clipboard("JEV-SENTINEL"), f.textedit(SAMPLE, select_all=True)),
             lambda f, _: ((clipboard(), f.text()) == (SAMPLE, ""), {"clipboard": clipboard(), "text": f.text()})),
        Case("paste", "paste", "v0.2", "paste",
             lambda f: (f.textedit(SAMPLE), clipboard(" pasted")),
             lambda f, _: same(f.text(), SAMPLE + " pasted")),
        Case("undo", "undo", "v0.2", "undo",
             lambda f: (f.textedit(SAMPLE), keys('keystroke "zq"')),
             lambda f, _: same(f.text(), SAMPLE)),
        Case("redo", "redo", "v0.2", "redo",
             lambda f: (f.textedit(SAMPLE), keys('keystroke "zq"'), keys('keystroke "z" using command down')),
             lambda f, _: same(f.text(), SAMPLE + "zq")),
        # v0.2 Safari
        Case("new_tab", "open a new tab", "v0.2", "new_tab",
             lambda f: f.safari(), lambda f, _: same(f.safari_tab()["count"], 3)),
        Case("next_tab", "go to the next tab", "v0.2", "next_tab",
             lambda f: f.safari(1), lambda f, _: same(f.safari_tab()["index"], 2)),
        Case("previous_tab", "go to the previous tab", "v0.2", "previous_tab",
             lambda f: f.safari(2), lambda f, _: same(f.safari_tab()["index"], 1)),
        Case("close_tab", "close this tab", "v0.2", "close_tab",
             lambda f: f.safari(3, tabs=3), lambda f, _: same(f.safari_tab()["count"], 2)),
        Case("back", "go back", "v0.2", "back",
             lambda f: (f.safari(), f.navigate("/a", "/b")),
             lambda f, _: (f.safari_tab()["url"].endswith("/a"), f.safari_tab()["url"])),
        Case("forward", "go forward", "v0.2", "forward",
             lambda f: (f.safari(), f.navigate("/a", "/b"), keys('key code 33 using command down'), f.expect_url("/a")),
             lambda f, _: (f.safari_tab()["url"].endswith("/b"), f.safari_tab()["url"])),
        Case("reload", "reload the page", "v0.2", "reload",
             lambda f: (f.safari(), f.navigate("/a"), f.web.loads("/a"))[-1],
             lambda f, loads: (f.web.loads("/a") > loads, f.web.loads("/a") - loads)),
        Case("zoom_in", "zoom in", "v0.2", "zoom_in",
             lambda f: (f.safari(), f.zoom()[1])[-1],
             lambda f, width: (f.viewport() < width, {"before": width, "after": f.viewport()})),
        Case("zoom_out", "zoom out", "v0.2", "zoom_out",
             lambda f: (f.safari(), f.zoom()[1])[-1],
             lambda f, width: (f.viewport() > width, {"before": width, "after": f.viewport()})),
        Case("reset_zoom", "reset zoom", "v0.2", "reset_zoom",
             lambda f: (f.safari(), f.zoom('key code 24 using command down')[0])[-1],
             lambda f, width: (f.viewport() == width, {"actual_size": width, "after": f.viewport()})),
        Case("scroll_down", "scroll down", "v0.2", "scroll_down",
             lambda f: (f.safari(2), f.scroll_to("top"))[-1],
             lambda f, y: (f.scroll_y()[0] > y, {"before": y, "after": f.scroll_y()})),
        Case("scroll_up", "scroll up", "v0.2", "scroll_up",
             lambda f: (f.safari(2), f.scroll_to("bottom"))[-1],
             lambda f, y: (f.scroll_y()[0] < y, {"before": y, "after": f.scroll_y()})),
        Case("scroll_top", "scroll to the top", "v0.2", "scroll_top",
             lambda f: (f.safari(2), f.scroll_to("bottom")),
             lambda f, _: (f.scroll_y()[0] == 0, f.scroll_y())),
        Case("scroll_bottom", "scroll to the bottom", "v0.2", "scroll_bottom",
             lambda f: (f.safari(2), f.scroll_to("top")),
             lambda f, _: ((y := f.scroll_y())[0] >= y[1] - 1, y)),
        # v0.2 Finder
        Case("finder_search", "search Finder for jevneedle", "v0.2", "finder_find",
             lambda f: f.finder(), lambda f, _: same(f.finder_query(), "jevneedle")),
        Case("finder_scroll", "scroll down", "v0.2", "scroll_down",
             lambda f: (f.finder(), f.finder_scroll())[-1],
             lambda f, value: ((after := f.finder_scroll()) is not None and value is not None and after > value,
                               {"before": value, "after": after})),
        Case("caret_drift_validation", 'type "matrix typed"', "latency", None,
             setup_caret_drift, check_caret_drift, "validation"),
        Case("caret_drift_executor", 'type "matrix typed"', "latency", None,
             setup_caret_drift, check_caret_drift, "executor"),
    ]


class RecordingDesktop(MacDesktop):
    """Production desktop that also records which targets each Jev request was offered."""

    offered: set[str]
    drift_at = None
    drift_hook = None
    drift_applied = False
    drift_error = None
    dispatching = False

    def semantic_menu(self, goal, state):
        menu = super().semantic_menu(goal, state)
        for facts in menu.targets.values():
            if facts.get("command"):
                self.offered.add(facts["command"])
            self.offered.update(f"finder_{kind}" for kind in facts.get("workflow_controls", {}))
        return menu

    def recheck(self, action, before):
        """Inject fixture drift at the requested production boundary exactly once."""

        phase = "executor" if self.dispatching else "validation"
        if self.drift_at == phase and not self.drift_applied and action.kind == "TYPE_TEXT":
            try:
                self.drift_hook()
            except Exception as error:
                self.drift_error = str(error)
                raise TextInputError("The harness could not establish caret drift; no input was sent.") from error
            self.drift_applied = True
        return super().recheck(action, before)

    def execute(self, action):
        """Identify rechecks inside the real native executor."""

        self.dispatching = True
        try:
            super().execute(action)
        finally:
            self.dispatching = False


def stage(case: Case, offered: set[str], report: dict, oracle: bool, *, drift_applied=False) -> str:
    """Name the first failing stage: discovery → selection → blocked → dispatch → verification."""

    if case.refused:
        return 'pass' if (oracle and not report.get('effect_sent') and not report.get('verified')
                          and 'unique full' in report.get('reason', '')) else 'unsafe_or_unproven_refusal'
    if case.drift_at:
        if report.get("effect_sent") or report.get("verified"):
            return "unsafe_dispatch"
        if (drift_applied and oracle and report.get("outcome") == "failed"
                and "caret changed" in report.get("reason", "")):
            return "pass"
        return "drift_not_proven"
    if oracle and report.get("verified"):
        return "pass"
    if oracle:
        return "verification"  # the effect really happened; Jev Pilot could not observe it
    if report.get("effect_sent"):
        return "dispatch"  # an effect was sent, but the app did not change as intended
    if case.expects and case.expects not in offered:
        return "discovery"
    selected = [step["decision"].get("selected_candidate") for step in report.get("steps", [])]
    if not any(selected) or report.get("outcome") in {"stopped", "unsupported"}:
        return "selection"
    return "blocked"  # chosen, then stopped by safety, approval or fresh-target drift


def synthesize(goal: str, directory: Path) -> Path:
    """Create a disposable PCM recording for either voice path."""

    audio = directory / "goal.wav"
    subprocess.run(["say", "-o", str(audio), "--data-format=LEI16@16000", goal.replace('"', "")], check=True)
    return audio


def speak(goal: str, directory: Path, model) -> tuple[str, dict]:
    """Preserve the file-transcription path for comparing historical voice runs."""

    from scripts.test_stt_e2e import word_error
    from app.speech_runtime import transcribe_audio_file

    audio = synthesize(goal, directory)
    transcript = transcribe_audio_file(model, str(audio))
    return transcript, word_error(goal, transcript)


class LivePlayback:
    """Replay synthesized audio at microphone pace and optionally simulate the talk key."""

    def __init__(self, audio, *, trim_silence=True):
        self.audio = audio
        self.trim_silence = trim_silence
        self.thread = None
        self.on_key = None
        self.done = False
        self.error = None
        self.stopped = Event()

    def microphone(self, rate, on_audio):
        from mlx_audio.audio_io import read
        import numpy as np

        samples, _ = read(str(self.audio), dtype="float32", sample_rate=rate, nchannels=1)
        samples = np.asarray(samples).reshape(-1)
        speech = np.flatnonzero(np.abs(samples) >= 0.012)
        if not speech.size:
            raise RuntimeError("The synthesized utterance contained no audible speech.")
        self.rate, self.on_audio = rate, on_audio
        self.samples = samples[:speech[-1] + 1] if self.trim_silence else samples
        return self

    def keys(self, on_key):
        self.on_key = on_key
        return SimpleNamespace(start=lambda: None, stop=lambda: None)

    def start(self):
        self.thread = Thread(target=self.play, name="matrix-audio-playback", daemon=True)
        self.thread.start()

    def play(self):
        import numpy as np

        try:
            deadline = monotonic()
            for samples in (self.samples, np.zeros(self.rate, dtype=np.float32)):
                if self.on_key is not None:
                    self.on_key("press" if samples is self.samples else "release")
                for offset in range(0, samples.size, 1024):
                    block = samples[offset:offset + 1024]
                    deadline += block.size / self.rate
                    if self.stopped.wait(max(0, deadline - monotonic())):
                        return
                    self.on_audio(block)
        except Exception as error:
            self.error = str(error)
        finally:
            self.done = True

    def stop(self):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join()


def live_voice(case, fixtures, model, desktop, approve, *, drafts, push_to_talk):
    """Exercise the real voice CLI with paced audio, live Jev and real native dispatch."""

    import io

    path = fixtures.directory / "voice-report.json"
    playback = LivePlayback(synthesize(case.goal, fixtures.directory))
    flags = ["--listen", "--report", str(path)]
    if drafts:
        flags.append("--draft")
    if push_to_talk:
        flags.append("--push-to-talk")
    _run_voice(_parser().parse_args(flags), desktop=desktop, client=None, output=io.StringIO(),
               speech_model=model, audio_source_factory=playback.microphone,
               key_source_factory=playback.keys if push_to_talk else None, approve_action=approve)
    return json.loads(path.read_text(encoding="utf-8"))


def grant(action, approvals: list) -> bool:
    """Fixtures are disposable, so approvals are granted and recorded, except unfamiliar control presses:
    those can reach things outside the fixtures (Finder's sidebar Eject, Rename, Duplicate), so the case stays blocked."""

    granted = not (action.kind == "PRESS_CONTROL" and requires_confirmation(action))
    approvals.append({"kind": action.kind, "mandatory": requires_confirmation(action), "granted": granted,
                      "label": action.parameters.get("label")})
    return granted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="Comma-separated case names or scopes (v0.1, v0.2, latency).")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case, to expose flaky commands.")
    voice_modes = parser.add_mutually_exclusive_group()
    voice_modes.add_argument("--voice", action="store_true", help="Use file transcription for comparison to earlier runs.")
    voice_modes.add_argument("--live-voice", action="store_true", help="Replay speech through the live listener and time speech to dispatch.")
    parser.add_argument("--draft", action=argparse.BooleanOptionalAction, default=False, help="Enable draft decisions with --live-voice.")
    parser.add_argument("--push-to-talk", action="store_true", help="Simulate holding Right Option during each --live-voice recording.")
    parser.add_argument("--report", type=Path, default=ROOT / ".build/native-matrix.json")
    parser.add_argument("--list", action="store_true", help="List cases and exit.")
    args = parser.parse_args(argv)
    if (args.draft or args.push_to_talk) and not args.live_voice:
        parser.error("--draft and --push-to-talk require --live-voice")
    selected = cases()
    if args.only:
        wanted = set(args.only.split(","))
        selected = [case for case in selected if case.name in wanted or case.scope in wanted]
    if args.voice or args.live_voice:
        selected = [case for case in selected if not case.drift_at]
    if not selected or args.repeat < 1:
        parser.error("Select at least one eligible case and a positive repeat count.")
    if args.list:
        for case in selected:
            print(f"{case.scope}  {case.name:<14} {case.goal}")
        return 0
    if not AX.AXIsProcessTrusted():
        print(f"Grant Accessibility to this terminal/Python host first: {sys.executable}", file=sys.stderr)
        return 2
    if not load_api_key():
        print("Configure TYPESAFE_API_KEY; the matrix uses live Jev.", file=sys.stderr)
        return 2

    model = None
    if args.voice or args.live_voice:
        from app.speech_model import load_speech_model
        model = load_speech_model()
    desktop = RecordingDesktop()
    fixtures = Fixtures()
    rows = []
    try:
        for case in selected:
            for attempt in range(1, args.repeat + 1):
                row = {"case": case.name, "scope": case.scope, "attempt": attempt, "goal": case.goal}
                started = perf_counter()
                try:
                    baseline = case.setup(fixtures)
                    goal = case.goal
                    if args.voice:
                        goal, row["wer"] = speak(case.goal, fixtures.directory, model)
                        row["transcript"] = goal
                    desktop.offered, approvals, prepared = set(), [], frontmost()
                    desktop.drift_at, desktop.drift_applied = case.drift_at, False
                    desktop.drift_error = None
                    desktop.drift_hook = fixtures.move_fixture_caret if case.drift_at else None

                    def approve(action):
                        return grant(action, approvals)

                    if args.live_voice:
                        from scripts.test_stt_e2e import word_error

                        session = live_voice(case, fixtures, model, desktop, approve,
                                             drafts=args.draft, push_to_talk=args.push_to_talk)
                        row["transcripts"] = session["transcripts"]
                        row["drafts"] = session.get("drafts")
                        if len(session["runs"]) != 1 or len(session["transcripts"]) != 1 or session.get("error"):
                            row.update(stage="transcription", reason=session.get("error") or
                                       "Expected one committed transcript and one command run.")
                            continue
                        report = session["runs"][0]
                        row["transcript"] = report["transcript"]
                        row["wer"] = word_error(case.goal, report["transcript"])
                        row["draft"] = report.get("draft")
                        row["speech_end_to_effect_ms"] = report.get("speech_end_to_effect_ms")
                    else:
                        report = run_goal(goal, desktop, approve=approve)
                    if desktop.drift_error:
                        raise RuntimeError(desktop.drift_error)
                    oracle, detail = wait_for(lambda: case.check(fixtures, baseline))
                    result = stage(case, desktop.offered, report, oracle, drift_applied=desktop.drift_applied)
                    # Another app (often the user's own) took focus after setup; that is not a product stage.
                    if result != "pass" and report.get("observed_app") not in {None, prepared}:
                        result = "focus_stolen"
                    row.update(stage=result, prepared_app=prepared, oracle=oracle, oracle_detail=detail,
                               outcome=report["outcome"], reason=report.get("reason"), effect_sent=report["effect_sent"],
                               verified=report["verified"], offered=sorted(desktop.offered), approvals=approvals,
                               # Bounded capability/discovery facts (no document text) explain discovery-stage misses.
                               observed_app=report.get("observed_app"), readiness=report.get("editor_readiness"),
                               operations=[step["decision"]["semantic"]["operation"] for step in report["steps"]],
                               verification=[step.get("verification") for step in report["steps"]],
                               timings_ms=report["timings_ms"])
                except Exception as error:
                    row.update(stage="harness_error", reason=f"{type(error).__name__}: {error}")
                finally:
                    if "stage" in row:
                        row["seconds"] = round(perf_counter() - started, 1)
                        rows.append(row)
                        print(f"{row['stage']:<14} {case.scope}  {case.name:<14} {row.get('outcome', '')} {row.get('reason') or ''}", flush=True)
    except KeyboardInterrupt:
        print("Interrupted; saving the partial matrix.", file=sys.stderr)
    finally:
        fixtures.close()
    summary = {}
    for row in rows:
        entry = summary.setdefault(row["case"], {"scope": row["scope"], "runs": 0, "passed": 0, "stages": {}})
        entry["runs"] += 1
        entry["passed"] += row["stage"] == "pass"
        entry["stages"][row["stage"]] = entry["stages"].get(row["stage"], 0) + 1
    regressions = sorted(name for name, entry in summary.items() if entry["scope"] == "v0.1" and entry["passed"] < entry["runs"])
    latency_regressions = sorted(name for name, entry in summary.items() if entry["scope"] == "latency" and entry["passed"] < entry["runs"])
    _save_report(args.report, {"schema_version": 1, "mode": "live_voice" if args.live_voice else "voice" if args.voice else "text",
                               "python_executable": sys.executable, "summary": summary,
                               "v01_regressions": regressions, "latency_regressions": latency_regressions,
                               **({"draft_enabled": args.draft, "push_to_talk": args.push_to_talk} if args.live_voice else {}),
                               "runs": rows})
    print(f"\n{sum(e['passed'] for e in summary.values())}/{len(rows)} passed; report: {args.report}")
    if regressions:
        print(f"v0.1 regressions: {', '.join(regressions)}")
    if latency_regressions:
        print(f"Latency regressions: {', '.join(latency_regressions)}")
    return 1 if regressions or latency_regressions or any(row['scope'] == 'v0.3' and row['stage'] != 'pass' for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
