"""Exercise app discovery and web commands through the CLI with native/provider fakes."""

from __future__ import annotations

import io
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from Foundation import NSURL

from app.cli import main
from app.desktop import MacDesktop
from app.text_input import NativeText
from app.verification import verify_action
from test_typing_e2e import NativeAX, NativeQuartz, Workspace, choice_answers
from test_speech_e2e import FakeMicrophone, FakeSpeechModel, FakeStreamingSession


SAFARI = "com.apple.Safari"
CHROME = "com.google.Chrome"
CURSOR = "com.todesktop.cursor"


class WebWorkspace(Workspace):
    """Record native URL/app requests and expose only explicitly installed fixtures."""

    def __init__(self, ax, root):
        super().__init__(ax)
        self.bundle = SAFARI
        self.installed = {SAFARI: root / "Safari.app", CHROME: root / "Chrome.app", CURSOR: root / "Cursor.app",
                          "example.othercursor": root / "Other/Cursor.app"}
        self.requests = []
        self.visible = True
        self.fail = False
        self.observed = None
        self.snapshot_calls = 0
        self.final_drift = False
        self.native_url = False

    def frontmostApplication(self):
        self.snapshot_calls += 1
        if self.final_drift and self.snapshot_calls == 3:
            self.bundle = CHROME
        return self

    def URLForApplicationWithBundleIdentifier_(self, bundle):
        path = self.installed.get(bundle)
        return NSURL.fileURLWithPath_(str(path)) if path and path.exists() else None

    def URLForApplicationToOpenURL_(self, url):
        return self.URLForApplicationWithBundleIdentifier_(SAFARI)

    def openApplicationAtURL_configuration_completionHandler_(self, url, configuration, callback):
        self.requests.append(("app", str(url.path())))
        if not self.fail:
            self.bundle = next(bundle for bundle, path in self.installed.items() if str(path) == str(url.path()))
        callback(None if self.fail else self, None)

    def openURLs_withApplicationAtURL_configuration_completionHandler_(self, urls, application, configuration, callback):
        self.requests.append(("web", str(urls[0].absoluteString())))
        if not self.fail:
            self.bundle = next(bundle for bundle, path in self.installed.items() if str(path) == str(application.path()))
            if self.visible:
                self.ax.window.attrs["AXURL"] = self.observed or str(urls[0].absoluteString())
                if self.native_url:
                    self.ax.window.attrs["AXURL"] = NSURL.URLWithString_(self.ax.window.attrs["AXURL"])
        callback(None if self.fail else self, None)


class WebJev:
    """Select grounded browser/site IDs while preserving exact query spans."""

    def __init__(self, operation, browser=SAFARI, site="google", *, after=None, confidence=0.99, text=None):
        self.operation, self.browser, self.site = operation, browser, site
        self.after, self.confidence, self.text = after, confidence, text
        self.calls = 0

    def system_one(self, state, questions, *, model=None):
        self.calls += 1
        target = self.browser
        if self.operation == "SEARCH_WEB":
            target = f"web:{self.site}:{self.browser}"
        payload = "none"
        if self.operation in {"OPEN_WEBSITE", "SEARCH_WEB"}:
            allowed = state["semantic_options"]["operations"][self.operation]["available_payloads"]
            payload = next((key for key in allowed if self.text is None or questions["payload"].criteria[key]["text"] == self.text), "none")
        elif self.operation == "TYPE_TEXT":
            payload = "span_0"
        answers = choice_answers(questions, {"operation": self.operation, "target": target, "payload": payload}, self.confidence)
        if self.after:
            self.after()
        return SimpleNamespace(choices=answers, model="offline-web", usage=None, request_id="web-test")


class WebPipelineTests(unittest.TestCase):
    """Cover dispatch, verification, ambiguity, URL safety, drift and non-retry failures."""

    def test_app_and_web_commands(self):
        cases = {}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def bundle(name, identifier, folder=root):
                path = folder / f"{name}.app" / "Contents"
                path.mkdir(parents=True, exist_ok=True)
                (path / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier, "CFBundleName": name,
                                                                "CFBundlePackageType": "APPL"}))

            for name, identifier in (("Safari", SAFARI), ("Chrome", CHROME), ("Cursor", CURSOR)):
                bundle(name, identifier)
            bundle("Nested Tool", "example.nested", root / "Utilities")
            bundle("Hidden Helper", "example.helper", root / "Cursor.app" / "Contents")

            def setup():
                ax = NativeAX(bundle=SAFARI)
                ax.editor.attrs.pop("AXValue")
                workspace = WebWorkspace(ax, root)
                desktop = MacDesktop(workspace=workspace, text=NativeText(ax=ax, quartz=NativeQuartz(ax), equal=lambda a, b: a is b),
                                     application_roots=(root,))
                return desktop, workspace, ax

            def run(name, goal, client, *, mutate=None, options=()):
                desktop, workspace, ax = setup()
                if mutate:
                    mutate(desktop, workspace, ax, client)
                output = io.StringIO()
                report_path = root / "report.json"
                with patch("app.runtime.verify_action", side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0, **kwargs)):
                    main([goal, *options, "--report", str(report_path)], desktop=desktop, client=client, output=output)
                report = json.loads(report_path.read_text())
                self.assertEqual(ax.effects, [])
                self.assertEqual(desktop.text.quartz.events, [])
                cases[name] = {"goal": goal, "outcome": report["outcome"], "requests": workspace.requests,
                               "provider_calls": client.calls, "verified": report["verified"],
                               "reason": report.get("reason"), "steps": report["steps"]}
                return report, workspace

            for name, goal, operation, browser, site, expected in (
                ("cursor", "open Cursor", "OPEN_APP", CURSOR, "google", None),
                ("youtube", "go to youtube", "OPEN_WEBSITE", SAFARI, "google", "https://www.youtube.com/"),
                ("spoken_domain", "go to stripe dot com", "OPEN_WEBSITE", SAFARI, "google", "https://stripe.com/"),
                ("spoken_period", "go to stripe dot com.", "OPEN_WEBSITE", SAFARI, "google", "https://stripe.com/"),
                ("quoted_url", 'go to "https://example.com/a?x=1&y=2#anchor"', "OPEN_WEBSITE", SAFARI, "google", "https://example.com/a?x=1&y=2#anchor"),
                ("unicode_domain", "go to café.com", "OPEN_WEBSITE", SAFARI, "google", "https://xn--caf-dma.com/"),
                ("explicit_browser", "open example.com in Chrome", "OPEN_WEBSITE", CHROME, "google", "https://example.com/"),
                ("google", "google best ramen near me", "SEARCH_WEB", SAFARI, "google", "https://www.google.com/search?q=best+ramen+near+me"),
                ("youtube_search", "search youtube for lofi hip hop", "SEARCH_WEB", SAFARI, "youtube", "https://www.youtube.com/results?search_query=lofi+hip+hop"),
                ('youtube_suffix', 'search for messi soccer videos on youtube', 'SEARCH_WEB', SAFARI, 'youtube', 'https://www.youtube.com/results?search_query=messi+soccer+videos'),
                ('youtube_find_suffix', 'Find me Messi soccer videos on YouTube.', 'SEARCH_WEB', SAFARI, 'youtube', 'https://www.youtube.com/results?search_query=Messi+soccer+videos'),
                ('youtube_quoted_suffix', 'search for "café 🦊 soccer" on YouTube in Chrome', 'SEARCH_WEB', CHROME, 'youtube', 'https://www.youtube.com/results?search_query=caf%C3%A9+%F0%9F%A6%8A+soccer'),
                ("literal_commands", "google how to create a new note and type hello", "SEARCH_WEB", SAFARI, "google", "https://www.google.com/search?q=how+to+create+a+new+note+and+type+hello"),
                ("literal_keyboard_words", "google how to copy and paste and scroll down", "SEARCH_WEB", SAFARI, "google", "https://www.google.com/search?q=how+to+copy+and+paste+and+scroll+down"),
                ("unicode_query", 'search Google for "café 🦊 & a+b" in Chrome', "SEARCH_WEB", CHROME, "google", "https://www.google.com/search?q=caf%C3%A9+%F0%9F%A6%8A+%26+a%2Bb"),
            ):
                client = WebJev(operation, browser, site)
                if operation == 'SEARCH_WEB':
                    client.text = next(iter(parse_qs(urlsplit(expected).query).values()))[0]
                report, workspace = run(name, goal, client)
                self.assertEqual(report["outcome"], "completed", report)
                self.assertEqual(client.calls, 1)
                self.assertEqual(len(workspace.requests), 1)
                if expected:
                    self.assertEqual(workspace.requests[0], ("web", expected))

            for name, goal in (("script_url", "go to javascript:alert(1)"), ("file_url", "go to file:///tmp/a"),
                               ("credentials", "go to https://user:secret@example.com"), ("invalid_port", "go to https://example.com:bad"),
                               ("missing_site", "go to unknownsite")):
                report, workspace = run(name, goal, WebJev("OPEN_WEBSITE"))
                self.assertEqual(report["outcome"], "failed")
                self.assertEqual(workspace.requests, [])

            for name, mutate, options, confidence, outcome in (
                ("dry_run", None, ("--dry-run",), 0.99, "dry_run"),
                ("approval_rejected", None, (), 0.3, "rejected"),
                ("basic_auto_approval", None, ('--auto-approve-basic',), 0.3, 'completed'),
                ("url_not_observed", lambda d, w, a, c: setattr(w, "visible", False), (), 0.99, "unverified"),
                ("native_failure", lambda d, w, a, c: setattr(w, "fail", True), (), 0.99, "failed"),
                ("no_ax", lambda d, w, a, c: setattr(a, "trusted", False), (), 0.99, "unverified"),
                ("foreground_drift", lambda d, w, a, c: setattr(c, "after", lambda: setattr(w, "bundle", CHROME)), (), 0.99, "failed"),
                ("final_native_drift", lambda d, w, a, c: setattr(w, "final_drift", True), (), 0.99, "failed"),
                ("removed_browser", lambda d, w, a, c: setattr(c, "after", lambda: w.installed.pop(SAFARI)), (), 0.99, "failed"),
            ):
                report, workspace = run(name, "go to youtube", WebJev("OPEN_WEBSITE", confidence=confidence), mutate=mutate, options=options)
                self.assertEqual(report["outcome"], outcome, report)
                self.assertEqual(len(workspace.requests), int(name in {"url_not_observed", "native_failure", "no_ax", 'basic_auto_approval'}))
                if name == "final_native_drift":
                    self.assertFalse(report["effect_sent"])

            for name, observed, outcome in (
                ("search_tracking", "https://www.google.com/search?q=ramen&source=browser", "completed"),
                ("wrong_query", "https://www.google.com/search?q=pizza", "unverified"),
                ("duplicate_query", "https://www.google.com/search?q=ramen&q=pizza", "unverified"),
                ("wrong_site", "https://example.com/search?q=ramen", "unverified"),
            ):
                report, workspace = run(name, "google ramen", WebJev("SEARCH_WEB"),
                                        mutate=lambda d, w, a, c: setattr(w, "observed", observed))
                self.assertEqual(report["outcome"], outcome)
                self.assertEqual(len(workspace.requests), 1)

            def ready_editor(desktop, workspace, ax, client):
                ax.editor.attrs.update(AXValue="", AXSelectedTextRange=(0, 0))

            report, workspace = run("web_cannot_type_into_document", "google ramen", WebJev("TYPE_TEXT", "field_0"), mutate=ready_editor)
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(workspace.requests, [])
            report, workspace = run('suffix_site_is_not_query_text', 'search for messi soccer videos on youtube',
                                    WebJev('SEARCH_WEB', site='youtube', text='messi soccer videos on youtube'))
            self.assertEqual(report['outcome'], 'failed')
            self.assertEqual(workspace.requests, [])

            def web_area(desktop, workspace, ax, client):
                from test_typing_e2e import Node
                area = Node(AXRole="AXWebArea", AXURL=NSURL.URLWithString_("https://www.youtube.com/"))
                ax.window.attrs["AXChildren"] = [area]
                workspace.visible = False

            report, workspace = run("web_area_url", "go to youtube", WebJev("OPEN_WEBSITE"), mutate=web_area)
            self.assertEqual(report["outcome"], "completed")
            report, workspace = run("native_cfurl", "go to youtube", WebJev("OPEN_WEBSITE"),
                                    mutate=lambda d, w, a, c: setattr(w, "native_url", True))
            self.assertEqual(report["outcome"], "completed")

            class SearchSession(FakeStreamingSession):
                def step(self, **kwargs):
                    return [text.replace("Switch to Terminal", "search youtube for lofi hip hop") for text in super().step(**kwargs)]

            class SearchSpeech(FakeSpeechModel):
                def create_streaming_session(self, **kwargs):
                    session = SearchSession()
                    self.sessions.append(session)
                    return session

            desktop, workspace, ax = setup()
            voice_report = root / "voice.json"
            status = main(["--listen", "--silence-ms", "200", "--report", str(voice_report)], desktop=desktop,
                          client=WebJev("SEARCH_WEB", site="youtube"), output=io.StringIO(),
                          speech_model=SearchSpeech(), audio_source_factory=FakeMicrophone)
            self.assertEqual(status, 0)
            voice = json.loads(voice_report.read_text())
            self.assertEqual(voice["runs"][0]["outcome"], "completed")
            self.assertEqual(len(workspace.requests), 1)
            cases["voice_search"] = voice

            desktop, workspace, ax = setup()
            menu = desktop.semantic_menu("open Cursor", desktop.snapshot())
            self.assertIn(CURSOR, menu.targets)
            self.assertTrue(menu.targets[SAFARI]["default_browser"])
            self.assertEqual(desktop.application_aliases()["cursor"], CURSOR)
            self.assertNotIn("example.helper", {item[1] for item in desktop.applications})
            self.assertIn("example.nested", {item[1] for item in desktop.applications})
            bundle("Cursor", "example.othercursor", root / "Other")
            report, workspace = run("ambiguous_app", "open Cursor", WebJev("OPEN_APP", CURSOR))
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(workspace.requests, [])
            self.assertEqual(cases["ambiguous_app"]["provider_calls"], 0)
            report, workspace = run("quoted_ambiguous_app", 'open "Cursor"', WebJev("OPEN_APP", CURSOR))
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(cases["quoted_ambiguous_app"]["provider_calls"], 0)
            report, workspace = run("ambiguous_name_in_query", "google how to open Cursor", WebJev("SEARCH_WEB"))
            self.assertEqual(report["outcome"], "completed")

        artifact = Path(__file__).resolve().parents[1] / ".build/web-e2e.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps({"evidence": "offline native/provider boundaries", "scenarios": cases}, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()
