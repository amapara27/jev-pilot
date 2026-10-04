# Jev Pilot

Jev Pilot is an early macOS command-line tool that asks Jev to select an operation, a grounded application or editable-field target, and an exact text payload together. Local code validates all three choices, performs native app/focus/text effects, and verifies their results.

## What you can do today

Supported app targets are Finder, Terminal, Visual Studio Code, Safari, Google Chrome, System Settings, Notes, and TextEdit. The current foreground app is also available, so typing can work in other apps with accessible editors. Jev interprets which application and field the goal refers to.

App-switching commands perform one activation. Typing commands allow up to four Jev-selected steps to activate the app, focus the intended editor, and insert text exactly once. Text replaces the current selection or inserts at the caret, preserving Unicode and whitespace. Multiline text requires an editor with writable Accessibility selected text. Keyboard fallback rejects control characters, and Terminal typing never submits a command.

The CLI does not create Notes documents, submit Terminal commands, click buttons, listen to the microphone, or handle arbitrary multi-command workflows. A Notes typing request needs an existing editable note. Secure, disabled, read-only, oversized, or unverifiable fields are unavailable. If preparation stops early, the outcome is `incomplete`; completed effects are not undone.

The semantic menu includes app opening/focusing, text entry, search, Notes creation, Terminal submission, STOP, and UNSUPPORTED. Jev sees all operations even when local parsing does not recognize a verb. Text payloads are chosen from exact locally extracted spans. Quotes protect literal command words; unquoted suffixes can have multiple plausible boundaries. Goals over 8192 characters or more than 16 proposed spans fail locally.

Low-confidence choices require confirmation in an interactive terminal. Goals mentioning passwords, payments, purchases, or deletion are blocked before a request is sent to Jev. App switching uses macOS AppKit and does not require Accessibility permission.

## Set up

You need a Mac, Python 3.12 or newer, this repository, and a TypeSafe account with a Jev API key. Requests go directly from your Mac to TypeSafe; this project does not proxy them.

From the repository directory, create and activate a virtual environment, then install dependencies:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If you use conda instead, create and activate the project environment before installing or running Python commands:

```sh
conda create -n jev-pilot python=3.12
conda activate jev-pilot
python -m pip install -r requirements.txt
```

On later visits, activate the same environment again. Do not set up both environments unless you have a reason to.

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

Use an installed app name. Run `python -m app --help` to see all options. `--yes` approves low-confidence app, field-focus, and typing actions for that invocation; it does not bypass safety or fresh-target checks.

The command prints a schema-version-3 JSON result and saves it to `.build/python-cli-last.json` by default. It includes semantic decisions, exact payload offsets, per-factor distributions, preparation steps, and verification. The lowest factor confidence drives approval. Reports contain your goal/payload and bounded app/field metadata, but no existing document values or native handles. Review them before sharing. Choose another destination with `--report .build/my-run.json`.

If an app opens without typing, inspect the individual steps: opening can set the overall `effect_sent` to true even when the typing step sends nothing. `TYPE_TEXT` needs a `field_*` target; an application target such as `com.apple.Notes` cannot insert text. If Jev selects the active app despite usable observed fields, a `reselect_field` step sends no effect and asks Jev again using only that app’s fields. This consumes one of the four steps; an unfocused editor still requires `FOCUS_FIELD` before insertion. `editor_readiness` gives discovery counts and exclusion reasons. Discovery prioritizes editor panes over broad note lists within its bounded scan. Notes requires an existing unlocked note; automatic note creation is not implemented. Probability rejections include the failing factor and raw total under `rejected_decision`.

After granting Accessibility, open a disposable TextEdit document and try:

```sh
python -m app 'type "hello 🦊" in TextEdit' --dry-run
python -m app 'type "hello 🦊" in TextEdit'
```

Quoted payloads preserve exact text. Jev chooses a real editable field; typing requires a readable value and UTF-16 caret/selection. The CLI checks that both are unchanged before insertion and verifies the complete resulting text/caret. Failed or uncertain insertion is never repeated. Search, note creation, and command submission still report `unsupported`. A dry run sends the goal, app/field metadata, and span proposals to TypeSafe, but stops before the first effect; it does not simulate subsequent preparation steps.

## Accessibility for typing

Typing and field focus require Accessibility. App switching alone does not. Request access with:

```sh
python -m app --request-accessibility
```

Grant access to the terminal or Python host macOS identifies under **System Settings → Privacy & Security → Accessibility**, then restart that host if needed. The command prints the exact Python executable to help identify it. Keyboard fallback also checks event-posting permission. A `false` status prevents typing; it does not prevent ordinary app switching.

## In development

The long-term goal is **voice-powered control of the Mac desktop through Jev**: speak naturally, let Jev understand what you mean and choose the next grounded action, then have Jev Pilot carry it out and verify the result. That means progressing from today's single app switch toward voice transcription, selecting an operation and its real on-screen target, handling exact dictated text, and chaining safe actions across desktop apps. Local safety checks, fresh target validation, and outcome verification remain part of that design—Jev chooses among actions the app can actually ground; it does not generate arbitrary executable commands.

The present CLI is text-only. Semantic factoring, bounded AX editor discovery, exact typing, and app/field preparation are implemented. Native app compatibility still needs acceptance after granting permissions. The next milestones add Notes/Finder workflow controls and prove complete workflows, then speech, queued commands, and eventually stable-prefix execution.

These are plans, not current features. The roadmap is maintained in [`.codex/roadmap.md`](.codex/roadmap.md); it may not be present in a fresh clone because `.codex/` is locally ignored. The current implementation is summarized in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Development checks

With your Python environment active, run the offline pipeline checks:

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
```

Offline checks leave `.build/python-e2e.json` and `.build/typing-e2e.json`, exercising the production pipeline with fake Jev/native API boundaries. They do not prove live permissions, provider compatibility, or application behavior.

Run native acceptance with a disposable labeled TextEdit document:

```sh
python -m scripts.test_typing_native
```

This creates a disposable document, restores your starting app, and runs `open TextEdit and type ...` through the production CLI with scripted choices and real native effects. Success requires verified activation and exact insertion (`workflow_verified=true`); it does not assume a focused editor before running the command. It writes `.build/typing-native.json`. Add `--live-jev` to use TypeSafe choices. Missing permission or readiness is a failure, not a skipped test. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

The previous Swift app is retired. Any retained Swift source is reference material, not a dependency of the Python CLI.

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
