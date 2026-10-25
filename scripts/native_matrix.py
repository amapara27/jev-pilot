"""Run commands against disposable real-app fixtures and check each result independently.

Run from an Accessibility-trusted Terminal: python -m scripts.native_matrix [--voice] [--only copy,zoom] [--repeat 3]
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
from threading import Lock, Thread
from time import monotonic, perf_counter, sleep
from typing import Callable
from urllib.parse import parse_qs, urlparse

import ApplicationServices as AX
from AppKit import NSRunningApplication

from app.cli import _save_report
from app.config import load_api_key
from app.desktop import MacDesktop
from app.runtime import run_goal
from app.safety import requires_confirmation


ROOT = Path(__file__).resolve().parents[1]
# The page reports its own load, scroll and viewport facts, so web checks never rely on Jev Pilot's observations.
PAGE = """<!doctype html><title>Jev matrix {path}</title>
<body style="margin:0;height:{height}px;background:linear-gradient(#fff,#ccd)"><h1>{path}</h1><script>
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

def osa(script: str) -> str:
    """Run AppleScript for fixture setup and oracle reads; never for the command under test."""

    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"AppleScript failed: {result.stderr.strip()}")
    return result.stdout.strip()


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
        wait_for(lambda: (self.text() is not None, None))
        osa(f'tell application "TextEdit" to set text of document "{self.document.name}" to {json.dumps(text)}')
        sleep(0.3)
        if select_all:
            keys('keystroke "a" using command down')
        elif caret_end:
            keys("key code 125 using command down")

    def text(self) -> str | None:
        return osa(f'tell application "TextEdit" to get text of document "{self.document.name}"')

    def selected_text(self) -> str | None:
        return next((ax(node, "AXSelectedText") for node in ax_nodes("com.apple.TextEdit")
                     if ax(node, "AXRole") == "AXTextArea"), None)

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


SAMPLE = "Jev matrix sample text"


def cases() -> list[Case]:
    def same(value, expected):
        return value == expected, value

    return [
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
    ]


class RecordingDesktop(MacDesktop):
    """Production desktop that also records which targets each Jev request was offered."""

    offered: set[str]

    def semantic_menu(self, goal, state):
        menu = super().semantic_menu(goal, state)
        for facts in menu.targets.values():
            if facts.get("command"):
                self.offered.add(facts["command"])
            self.offered.update(f"finder_{kind}" for kind in facts.get("workflow_controls", {}))
        return menu


def stage(case: Case, offered: set[str], report: dict, oracle: bool) -> str:
    """Name the first failing stage: discovery → selection → blocked → dispatch → verification."""

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


def speak(goal: str, directory: Path, model) -> tuple[str, dict]:
    """Synthesize the goal with macOS speech and transcribe it with the production ASR path."""

    from scripts.test_stt_e2e import word_error
    from app.speech_runtime import transcribe_audio_file

    audio = directory / "goal.wav"
    subprocess.run(["say", "-o", str(audio), "--data-format=LEI16@16000", goal.replace('"', "")], check=True)
    transcript = transcribe_audio_file(model, str(audio))
    return transcript, word_error(goal, transcript)


def grant(action, approvals: list) -> bool:
    """Fixtures are disposable, so approvals are granted and recorded, except unfamiliar control presses:
    those can reach things outside the fixtures (Finder's sidebar Eject, Rename, Duplicate), so the case stays blocked."""

    granted = not (action.kind == "PRESS_CONTROL" and requires_confirmation(action))
    approvals.append({"kind": action.kind, "mandatory": requires_confirmation(action), "granted": granted,
                      "label": action.parameters.get("label")})
    return granted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="Comma-separated case names, or v0.1 / v0.2.")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case, to expose flaky commands.")
    parser.add_argument("--voice", action="store_true", help="Speak each goal with `say` and run the Nemotron transcript instead.")
    parser.add_argument("--report", type=Path, default=ROOT / ".build/native-matrix.json")
    parser.add_argument("--list", action="store_true", help="List cases and exit.")
    args = parser.parse_args(argv)
    selected = cases()
    if args.only:
        wanted = set(args.only.split(","))
        selected = [case for case in selected if case.name in wanted or case.scope in wanted]
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
    if args.voice:
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
                    if model is not None:
                        goal, row["wer"] = speak(case.goal, fixtures.directory, model)
                        row["transcript"] = goal
                    desktop.offered, approvals, prepared = set(), [], frontmost()
                    report = run_goal(goal, desktop, approve=lambda action: grant(action, approvals))
                    oracle, detail = wait_for(lambda: case.check(fixtures, baseline))
                    result = stage(case, desktop.offered, report, oracle)
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
    _save_report(args.report, {"schema_version": 1, "mode": "voice" if args.voice else "text",
                               "python_executable": sys.executable, "summary": summary,
                               "v01_regressions": regressions, "runs": rows})
    print(f"\n{sum(e['passed'] for e in summary.values())}/{len(rows)} passed; report: {args.report}")
    if regressions:
        print(f"v0.1 regressions: {', '.join(regressions)}")
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
