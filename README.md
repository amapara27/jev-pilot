# Jev Pilot

Jev Pilot is an early macOS command-line tool that asks Jev to select an operation, a grounded application or editable-field target, and an exact text payload together. Local code validates all three choices, performs native app/focus/text effects, and verifies their results.

## What you can do today

Supported app targets are Finder, Terminal, Visual Studio Code, Safari, Google Chrome, System Settings, Notes, and TextEdit. The current foreground app is also available, so typing can work in other apps with accessible editors. Jev interprets which application and field the goal refers to.

App-switching commands perform one activation. Typing commands allow up to four Jev-selected steps to activate the app, focus the intended editor, and insert text exactly once. Text replaces the current selection or inserts at the caret, preserving Unicode and whitespace. Multiline text requires an editor with writable Accessibility selected text. Keyboard fallback rejects control characters, and Terminal typing never submits a command.

The CLI continuously transcribes microphone input locally with MLX-Audio's 8-bit Nemotron streaming ASR. Final utterances enter a five-command FIFO and one worker runs the same observe → Jev → guarded effect → verify loop as typed goals. Notes creation, Finder search/selection/opening, and pressing observed Accessibility buttons/menus in other apps use this shared loop. Native compatibility still needs manual acceptance. Terminal submission and coordinate/canvas clicking remain unsupported. Secure, disabled, read-only, oversized, or unverifiable targets are unavailable.

The semantic vocabulary includes app activation, field focus, typing, search, Notes creation, generic control presses, unsupported Terminal submission, STOP, and UNSUPPORTED. Jev sees the full vocabulary as context but chooses only currently available operations. One request asks speculative targets per operation, then consumes only the chosen head. Completed prerequisites/controls are withdrawn; typing waits for required creation. Payloads are exact locally extracted spans. Quotes protect literal command words. Goals over 8192 characters or 16 proposed spans fail locally.

Low-confidence choices require confirmation in an interactive terminal. Goals mentioning passwords, payments, purchases, or deletion are blocked before a request is sent to Jev. App switching uses macOS AppKit and does not require Accessibility permission.

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

The command prints a schema-version-3 JSON result and saves it to `.build/python-cli-last.json` by default. It includes semantic decisions, exact payload offsets, per-factor distributions, preparation steps, and verification. The lowest factor confidence drives approval. Reports contain your goal/payload and bounded app/field metadata, but no existing document values or native handles. Review them before sharing. Choose another destination with `--report .build/my-run.json`.

If an app opens without typing, inspect individual steps: overall `effect_sent` can be true while typing sends nothing. `TYPE_TEXT` requires a field target; app IDs are excluded from its head. Typing-only preparation has four steps, other workflows eight. Each dispatched step has `verification.reason` and post-action `verification.editor_readiness`; top-level readiness is pre-decision. Notes creation and editor focus are separate, while insertion still requires an empty new body/caret. Probability rejections identify the consumed factor and raw total.

After granting Accessibility, open a disposable TextEdit document and try:

```sh
python -m app 'type "hello 🦊" in TextEdit' --dry-run
python -m app 'type "hello 🦊" in TextEdit'
```

Quoted payloads preserve exact text. Jev chooses a real editable field; typing requires a readable value and UTF-16 caret/selection. The CLI checks that both are unchanged and verifies the complete resulting text/caret. Failed or uncertain insertion is never repeated. A dry run stops before the first effect; it does not simulate subsequent preparation steps.

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
invocation. A failed action pauses the queue, and later committed utterances are
reported as not run. Reports are written to `.build/python-cli-last.json`.

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

New checks also leave `.build/workflow-e2e.json`, `.build/workflow-failures-e2e.json`
and `.build/streaming-command-e2e.json`. They cover Notes/Finder effects, stale
identity, duplicate names, approval/cancellation, missing proof, and an app effect
before EOU with no prefix replay. `python -m compileall -q app scripts tests`
checks the Python source; the retired Swift build is not part of this CLI.

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
