# Jev Pilot

Jev Pilot is an early macOS command-line tool that asks Jev to select an operation, a grounded application or editable-field target, and an exact text payload together. Local code validates all three choices, performs native app/focus/text effects, and verifies their results.

## What you can do today

App targets are discovered at startup from `/Applications`, `~/Applications`, `/System/Applications`, and `/System/Library/CoreServices`, including nested utility folders. Embedded helper apps and background-only bundles are excluded. Running apps and familiar aliases such as Chrome and VS Code supplement discovery. Duplicate spoken names stop with the conflicting bundle identifiers; use an identifier to distinguish them. Restart the CLI after installing another app. Jev interprets which application and field the goal refers to.

Website navigation accepts named sites (YouTube, Google, GitHub, Wikipedia, Reddit, Stripe, Gmail), explicit HTTP/HTTPS URLs, and spoken domains such as “stripe dot com.” Web search supports Google and YouTube. Jev selects the current default browser unless you name another installed browser. URLs open through AppKit without address-bar typing, clipboard changes, or Return. Credential-bearing URLs and non-web schemes are rejected.

Search also accepts “search for messi soccer videos on YouTube” and “find me Messi soccer videos on YouTube.” A trailing site and optional browser identify the destination; the exact query span excludes those words. Quotes protect literal site words and typing/Finder queries retain their existing behavior.

```sh
python -m app 'open Cursor'
python -m app 'go to stripe dot com'
python -m app 'open example.com in Chrome'
python -m app 'google best ramen near me'
python -m app 'search YouTube for "lofi hip hop" in Chrome'
python -m app 'search for messi soccer videos on youtube'
```

These commands also work through `--listen`. A browser action verifies the requested URL in the selected foreground browser's accessible window/web area; searches verify the site, path and exact query, allowing extra tracking parameters. This proves the URL was observed, not that the page finished loading. Redirects to a different URL, missing Accessibility permission or unavailable browser URL attributes produce `unverified` after one request, without retrying. Live browser compatibility still needs native acceptance.

App-switching commands perform one activation. Typing commands allow up to four Jev-selected steps to activate the app, focus the intended editor, and insert text exactly once. Text replaces the current selection or inserts at the caret, preserving Unicode and whitespace. Multiline text requires an editor with writable Accessibility selected text. Keyboard fallback rejects control characters, and Terminal typing never submits a command.

Plain named app commands now require one exact installed name/alias: “open Notes” can only target Apple Notes. Creation workflows bind Notes or TextEdit, and Finder searches bind Finder; other installed apps remain available for switching. An explicit observed control-and-type command stays in its current app. App quitting, Terminal new-window creation and Music playlist selection remain unsupported/deferred rather than substituting app activation or unrelated controls.

Explicit Enter is a separate action after exact insertion verifies. Notes and TextEdit text areas verify the resulting newline and caret; other focused editors require confirmation and an observable change. Terminal/iTerm text/menu keyboard commands remain unavailable; their native window controls are separate. The existing insertion implementation is unchanged.

```sh
python -m app 'type hello world and hit enter'
python -m app 'select all and copy'
python -m app 'undo'
python -m app 'close this tab'
python -m app 'scroll down a lot'
python -m app 'go to the top'
```

Text shortcuts require a focused readable editable field; select-all needs nonempty text, and copy/cut need a selection. Copy, cut, paste, undo, redo and find invoke an enabled observed menu command in the current app. Paste can first focus the existing body; it checks bounded plain text locally and verifies exact insertion/caret without exposing clipboard contents to Jev or reports. Browser menus additionally support new/close/reopen/next/previous tab, back/forward/reload, address-bar focus and zoom in/out/reset. Safari also supports choosing an observed tab by name. Tab, Shift-Tab and Escape require safe accessible focus. Cut, closing tabs and Enter outside Notes/TextEdit always need interactive approval, including with `--yes`; paste follows the normal confidence threshold. Unsupported/disabled/missing menus are unavailable.

Scrolling requires an accessible scroll area with a readable range and remaining room in the requested direction. It prefers the area containing current focus. Up/down/left/right use one positioned wheel event with normal, “a little,” or “a lot” distance. Top/bottom additionally require a writable native scrollbar position. Safari's standard WebKit scrollbars support their normalized 0–1 range when min/max attributes are unsupported, including unique axis-labelled child bars. Native range/window identity is rechecked before dispatch, and position movement must be observed. Browser menus and scrolling ignore unrelated editor/caret drift while preserving window/page/tab guards. Tab selection/closure and zoom require tab or zoom evidence; event delivery alone is insufficient, so some native apps may report `unverified` even when they acted.

Menus, scrolling and window commands can run without an AXFocusedUIElement; text commands still require the exact focused editor. Discovery visits only the requested menu branches and defers webpage content behind browser controls. A generic failure reading optional AXSubrole on a known menu/application root does not discard that menu; secure roles, protected content and other ancestry failures still block.

“Maximize” enters full screen, as requested; “minimize” minimizes the observed window. Both require readable, writable native window attributes and verify the original window's resulting state. Named commands such as “maximize Safari” can first focus that running app, and never operate on another app's window. Window controls also work in Terminal without sending keyboard input or submitting commands.

```sh
python -m app 'maximize this window'
python -m app 'minimize Safari'
```

The CLI continuously transcribes microphone input locally with MLX-Audio's 8-bit Nemotron streaming ASR. Final utterances enter a five-command FIFO and one worker runs the same observe → Jev → guarded effect → verify loop as typed goals. Notes creation, Finder search/selection/opening, and pressing observed Accessibility buttons/menus in other apps use this shared loop. Native compatibility still needs manual acceptance. Terminal submission and coordinate/canvas clicking remain unsupported. Secure, disabled, read-only, oversized, or unverifiable targets are unavailable.

The semantic vocabulary includes app activation, website navigation/web search, field focus, typing, Finder search, Notes creation, generic control presses, unsupported Terminal submission, STOP, and UNSUPPORTED. Jev sees the full vocabulary as context but chooses only currently available operations. One request asks speculative targets per operation, then consumes only the chosen head. Completed prerequisites/controls are withdrawn; typing waits for required creation. Payloads are exact locally extracted spans. Quotes protect literal command words. Goals over 8192 characters or 16 proposed spans fail locally.

Low-confidence choices require confirmation in an interactive terminal. Goals mentioning passwords, payments, purchases, or deletion are blocked before a request is sent to Jev. App switching uses macOS AppKit and does not require Accessibility permission.

After Y/N approval, the CLI restores the exact original process if answering required switching to Terminal, then repeats all target/window/text checks. Changed editors or selections still stop the command.

## Set up

You need an Apple Silicon Mac, Python 3.12 or newer, this repository, and (for Jev actions) a TypeSafe account with a Jev API key. Requests go directly from your Mac to TypeSafe; this project does not proxy them. The first voice run downloads the approximately 756 MB 8-bit Nemotron model into Hugging Face's local cache. The model is under NVIDIA's Open Model License.

From the repository directory, create the conda environment once, then activate it before installing or running Python commands:

```sh
conda create -n jev-pilot python=3.12
conda activate jev-pilot
python -m pip install -r requirements.txt
```

On later visits, activate the same environment again.

## Add your API key

Create a `.env` file in the repository root with this line:

```text
TYPESAFE_API_KEY=your_key_here
```

Replace the placeholder with your key. Do not add quotes. The file is ignored by Git and read as data—not executed as a shell script. Alternatively, set `TYPESAFE_API_KEY` in your shell; an environment value takes precedence over `.env`. This CLI does not use macOS Keychain, so keep the file private and never commit or share it.

## Run your first command

Start with a dry run. It contacts Jev and prints the choice, but does not change which app is frontmost:

```sh
python -m app "switch to Finder" --dry-run
```

Then run an actual app switch:

```sh
python -m app "open Safari"
```

Use a supported installed app name. `--yes` approves low-confidence existing actions, but generic control presses requiring confirmation still need an interactive answer. It never bypasses blocks, identity checks or verification.

Use `--auto-approve-basic` to skip confidence prompts specifically for app opening/focus, website navigation, and supported web/Finder searches. Other confidence prompts and all safety blocks, target checks, verification, and dangerous-action approvals remain active.

The command prints a schema-version-3 JSON result and saves it to `.build/python-cli-last.json` by default. It includes semantic decisions, exact payload offsets, per-factor distributions, preparation steps, and verification. The lowest factor confidence drives approval. Reports contain your goal/payload and bounded app/field metadata, but no existing document values or native handles. Review them before sharing. Choose another destination with `--report .build/my-run.json`.

If an app opens without typing, inspect individual steps: overall `effect_sent` can be true while typing sends nothing. `TYPE_TEXT` requires a field target; app IDs are excluded from its head. Typing-only preparation has four steps, other workflows eight. Each dispatched step has `verification.reason` and post-action `verification.editor_readiness`; top-level readiness is pre-decision. Notes creation and editor focus are separate, while insertion still requires an empty new body/caret. Probability rejections identify the consumed factor and raw total.

After granting Accessibility, open a disposable TextEdit document and try:

```sh
python -m app 'type "hello 🦊" in TextEdit' --dry-run
python -m app 'type "hello 🦊" in TextEdit'
```

Quoted payloads preserve exact text. Jev chooses a real editable field; typing requires a readable value and UTF-16 caret/selection. The CLI checks that both are unchanged and verifies the complete resulting text/caret. Failed or uncertain insertion is never repeated. A dry run stops before the first effect; it does not simulate subsequent preparation steps.

The baseline reader accepts native attributed strings as text. If AXValue is unavailable, a successful zero character count proves an empty body; nonempty text requires a bounded AXStringForRange read whose UTF-16 length matches the native count. Missing or inconsistent evidence keeps typing unavailable. Reports include value error/type/source, query/menu discovery counts and focused-element availability, without document or clipboard contents.

## Try Notes and Finder workflows

With Accessibility enabled, use an unlocked Notes folder and disposable files:

Workflow activation waits for a usable focused Accessibility window before the
next Jev decision, polling for up to three seconds and continuing as soon as ready.
If the window remains unavailable, the run stops with a readiness error; it does
not retry the activation or proceed to creation/typing.

```sh
python -m app 'create a new note in Notes and type "hello world"'
python -m app 'create a new document in TextEdit and type "hello world"'
python -m app 'search Finder for "Jev disposable resume" and open it'
```

Jev chooses each prerequisite against fresh state and compact action history. Notes can prove creation through changed selected-note identity before its body is focused, then focus and type separately. It keeps that note/editor pinned and refuses insertion into a nonempty or changed note. Indistinguishable creation pauses instead of making another note. Finder keeps exact query, URL, file and destination checks; duplicate names and missing evidence pause.

After verified creation, a focused new body can wait up to three seconds for readable text and a valid caret before the next Jev call. Note/editor/window drift and cancellation stop that wait; an unreadable body produces an explicit incomplete result without repeating creation. An unfocused body still uses its separate focus step.

Finder prioritizes the File → Find menu before dense window content. Search fields may omit AXEnabled only when AXValue is writable. Search focuses that query, sets its exact text and sends paired Return after checking the foreground/window/query again. Verification establishes query entry and submission dispatch, not result readiness or file opening.

Other foreground accessible apps expose real AXPress buttons/menus through `PRESS_CONTROL`. A control press needs an observed selection/expansion, editor, or local-document change; it is never blindly assumed successful. A prepared editor is pinned through typing. Routine controls need 82% confidence; unknown controls need interactive approval, destructive labels and Terminal controls are blocked. URL/file controls cannot bypass Finder identity checks. Generic control-only completion has `verified_actions` scope, not proof of arbitrary goal completion. These are grounded accessible controls, not universal desktop automation.

Replaced pressable controls can trigger at most two fresh Jev choices before any effect, in the same app/window. This does not transfer approval or retry sent effects. Text/caret/file/app drift pauses. Verification polls within three seconds and continues immediately on evidence, without fixed inter-action waits.

## Try local speech

Install `requirements.txt` in the conda environment (or virtual environment) used
to run the CLI. First, test the microphone and model without Jev or Accessibility
access:

```sh
python -m app --transcribe-only
```

Speak naturally and pause for about 650 ms to commit each utterance. Press
Control-C to stop; unfinished partial speech is discarded and the active goal
stops before its next effect. A native effect already sent cannot be undone.
Lower the endpoint delay for faster turn-taking or raise it to avoid splitting
pauses:

```sh
python -m app --transcribe-only --silence-ms 450
```

If quiet speech is not detected, lower `--speech-threshold` slightly (default
`0.012`); raise it if room noise keeps starting utterances.

To route each completed transcript into Jev and the existing native executor:

```sh
python -m app --listen
```

For continuous command testing without routine open/search prompts:

```sh
conda activate jev-pilot
python -m app --listen --auto-approve-basic
```

Speak one command, wait for its result, then speak the next. The same microphone/runtime stays active after successful, rejected, blocked, failed and unverified commands. After a failed command, already queued follow-ups are recorded as not run; newly spoken commands resume against fresh state. Reports checkpoint after every command. Say “stop listening,” “stop pilot,” or “quit pilot,” or press Control-C to stop. Cancellation cannot undo a sent effect. The final exit status still records any session failure. Audio/goal overload, capture errors and failed speculative prefixes stop the session.

To try app activation while you are still speaking:

```sh
python -m app --listen --stream-prefixes
```

Say “open Finder and search for Jev disposable resume and open it.” An explicitly
delimited opening/focusing clause held unchanged for 400 ms may queue before EOU;
Jev selects its action. The final transcript queues only the remaining suffix,
with the intended app rechecked at dequeue. Typing and file opening wait for EOU.
A revised committed prefix stops the session; completed effects remain completed.
Only the first known-app clause streams in this initial version, so live latency
depends on when Nemotron emits that clause and on Jev's request time.

Keep `.env` configured with `TYPESAFE_API_KEY`; grant Microphone permission to
the terminal/Python host and Accessibility permission for typing. Low-confidence
actions still require interactive approval; `--yes` approves them for that
invocation. A failed action discards queued follow-ups while listening continues
for a new instruction. Reports are written to `.build/python-cli-last.json` after each goal.

## Accessibility for typing and workflows

Typing, field focus and Notes/Finder controls require Accessibility. App switching alone does not. Request access with:

```sh
python -m app --request-accessibility
```

Grant access to the terminal or Python host macOS identifies under **System Settings → Privacy & Security → Accessibility**, then restart that host if needed. Rerun the check from that same terminal until it prints `accessibility_trusted: true`. Microphone permission is separate. The command and normal reports print the exact Python executable to identify a changed conda/terminal host. Recognized typing and Notes/Finder commands fail before contacting Jev when access is missing; ordinary app switching still works.

## Remove the retired speech model cache

The retired Swift app cached FluidAudio Parakeet EOU models in `~/Library/Application Support/FluidAudio/Models/parakeet-eou-streaming/`. If you no longer use them, quit the Swift app and move this folder to Trash using Finder. The nested `parakeet-eou-streaming/` folder can contain separate `160ms`, `320ms`, and `1280ms` downloads, including source and compiled Core ML models. The Python CLI does not use this cache; starting speech in the retired app can download it again.

Other retired app data lives in `~/Library/Caches/ai.typesafe.jev-pilot/` (including Core ML compilation caches) and `~/Library/Application Support/Jev Pilot/` (saved Swift-app command history). Move these folders to Trash to clear that data. The old app bundle was built at `.build/Jev Pilot.app`; check whether it still exists before removing it. `.build/ModuleCache/` is regeneratable build cache, while the rest of `.build/` also contains current Python CLI reports and native test fixtures.

## In development

The long-term goal is **voice-powered control of the Mac desktop through Jev**: speak naturally, let Jev understand what you mean and choose the next grounded action, then have Jev Pilot carry it out and verify the result. That means progressing from today's single app switch toward voice transcription, selecting an operation and its real on-screen target, handling exact dictated text, and chaining safe actions across desktop apps. Local safety checks, fresh target validation, and outcome verification remain part of that design—Jev chooses among actions the app can actually ground; it does not generate arbitrary executable commands.

Semantic factoring, exact typing, local speech/FIFO, scoped Notes/Finder workflows, and opt-in stable app clauses are implemented. The user has manually tested speech and confirmed Notes typing. Native workflow acceptance, measured accuracy/latency, richer command grouping and Terminal's explicit submission boundary remain.

The roadmap separates implemented work from remaining acceptance in [`.codex/roadmap.md`](.codex/roadmap.md); `.codex/` is locally ignored. Current implementation is summarized in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Development checks

With your conda environment active, run the offline pipeline checks:

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
```

Offline checks leave `.build/python-e2e.json`, `.build/typing-e2e.json`, and `.build/voice-e2e.json`, exercising production routing with fake Jev/native/speech boundaries. They do not prove model accuracy, live microphone capture, provider compatibility, or application behavior.

`.build/web-e2e.json` covers app discovery/ambiguity, spoken domains, explicit browsers, Unicode/encoded queries, literal query words, voice routing, rejected URLs, stale targets, native failure and exact URL-observation boundaries. Native, provider and audio services are fake; real app discovery/default-browser lookup have separate read-only acceptance.

`.build/request-e2e.json` covers large-catalog request size and safe API failures. Target facts are sent once instead of repeated across every question. Jev context-limit rejections report their cause without raw error bodies; Choice menus above 255 options fail locally without truncating the app list. For provider acceptance with a synthetic catalog and no desktop effects, run `JEV_LIVE_JEV=1 python -m unittest discover -s tests -p 'test_request_e2e.py' -k test_live_catalog_request -v`; this writes `.build/live-request-e2e.json`.

Compatibility lists now live only in their target Choices; shared state keeps operation definitions and usable target facts once. Withdrawn operation heads carry only `none`. This avoids sending unrelated editor/control metadata for pure web commands while preserving the local catalog and native guards. `.build/dense-request-e2e.json` exercises 263 installed apps plus 36 long control labels. `JEV_LIVE_JEV=1 python -m unittest discover -s tests -p 'test_request_e2e.py' -k test_live -v` additionally checks the exact Messi query against real Jev in a synthetic desktop dry run, writing `.build/live-search-request-e2e.json`.

New checks also leave `.build/workflow-e2e.json`, `.build/workflow-failures-e2e.json`
and `.build/streaming-command-e2e.json`. They cover Notes/Finder effects, stale
identity, duplicate names, approval/cancellation, missing proof, and an app effect
before EOU with no prefix replay. `python -m compileall -q app scripts tests`
checks the Python source; the retired Swift build is not part of this CLI.

`.build/interactions-e2e.json`, `.build/keyboard-menu-e2e.json`, `.build/interaction-failures-e2e.json` and `.build/continuous-voice-e2e.json` cover explicit Enter, ordered shortcuts, clipboard approval, focus/text drift, missing scroll ranges, secure fields, event permissions and multiple speech commands with failure recovery. These use synthetic native/audio/provider services. `JEV_LIVE_JEV=1 python -m unittest discover -s tests -p 'test_interactions_e2e.py' -k test_live_input_progression -v` checks real Jev progression with scripted approval and synthetic desktop effects, writing `.build/live-interactions-e2e.json`; it does not prove actual app compatibility.

`tests/test_reported_regressions_e2e.py` writes `.build/reported-*-e2e.json` for the reported Notes routing, paste, Safari tabs/zoom/focus, scrolling, Finder and approval failures. Its opt-in live case writes `.build/live-reported-regressions-e2e.json` with real Jev and synthetic native services. The user confirmed ordinary typing/web/search/copy, standalone Notes creation, TextEdit document creation, Music play and spoken stop on 2026-10-12; the new repairs still need native retesting. This tool host reports Accessibility trust false.

`tests/test_native_discovery_e2e.py` reproduces the subsequent logs: unreadable new-note bodies, missing Safari focus and menu ancestry failures. Artifacts `.build/native-*-e2e.json` also cover paste/cut, Finder, native fullscreen/minimize, stale windows, protected content and failed/ignored setters. The opt-in `.build/live-native-discovery-e2e.json` proves real Jev progression with synthetic native services. These artifacts do not establish actual app compatibility.

`.build/workflow-progression-e2e.json`, `.build/workflow-recovery-e2e.json` and
`.build/generic-workflow-e2e.json` cover separate creation/focus/typing, delayed
and reused editors, native-dispatch note drift, scoped heads, controls in other
apps and bounded recovery. Ordinary tests use fake provider/native services.
To explicitly test real Jev decisions with synthetic native services (no desktop
changes), configure your key and run:

```sh
JEV_LIVE_JEV=1 python -m unittest discover -s tests -p 'test_workflow_e2e.py' -k test_live_jev -v
```

This uses the API and writes `.build/live-workflow-e2e.json`; it does not prove
native app compatibility or speech quality.

`.build/workflow-routing-regression.json` replays the reported workflow/payload
tuple from typed arguments and live audio callbacks, through app activation, note
creation and exact insertion. Preparation may carry the future text without
entering it; changed prepared spans are rejected. The corresponding boundaries
artifact covers permission preflight before Jev and app switching without AX.

`.build/workflow-readiness-e2e.json` covers delayed window availability and
persistent Accessibility window errors through open/focus → creation → typing,
plus ordinary app-only switching. Native/provider boundaries are faked.

For real model transcription accuracy, set `JEV_STT_AUDIO` to an audio file and
`JEV_STT_EXPECTED` to its reference transcript, then run:

```sh
python -m scripts.test_stt_e2e
```

The gated full speech → live Jev → disposable native TextEdit test is
`python -m scripts.test_voice_native`; it requires `JEV_STT_AUDIO`,
`JEV_STT_EXPECTED`, and `TYPESAFE_API_KEY`, plus Microphone and Accessibility
permissions. Supply audio whose spoken command explicitly targets the disposable
TextEdit fixture; inspect the report before repeating any effect.

Run native acceptance with a disposable labeled TextEdit document:

```sh
python -m scripts.test_typing_native
```

This creates a disposable document, restores your starting app, and runs `open TextEdit and type ...` through the production CLI with scripted choices and real native effects. Success requires verified activation and exact insertion (`workflow_verified=true`); it does not assume a focused editor before running the command. It writes `.build/typing-native.json`. Add `--live-jev` to use TypeSafe choices. Missing permission or readiness is a failure, not a skipped test. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

The previous Swift app is retired. Any retained Swift source is reference material, not a dependency of the Python CLI. The active STT provider is MLX-Audio Nemotron, not FluidAudio Parakeet; the old FluidAudio model cache can be removed separately as described above.

To diagnose discovery from your permitted terminal without activating an app,
typing, or calling Jev, keep the affected document open and run:

```sh
python -m scripts.test_typing_native --inspect Notes
python -m scripts.test_typing_native --inspect TextEdit
```

These print capability/error facts and omit document contents and field labels.
`text_field_checks` distinguishes a disabled field, invalid native Boolean, and
an AXEnabled read error; `ancestry_rejections` identifies protected/failed ancestry.
Offline test success does not verify native editor compatibility.

Notes can omit `AXEnabled` on its focused document body (`-25205`, attribute
unsupported). The scanner uses positive native write support for that specific
case, while still rejecting explicit disabled values and other read errors.
A focused body with this error was the confirmed cause of the reported Notes
field exclusion; a scan-limit flag did not mean that body was never reached.
