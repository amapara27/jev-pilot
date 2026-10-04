# Jev Pilot

Jev Pilot is an early macOS command-line tool that asks Jev to select an operation, a grounded application target, and an exact text payload together. Local code validates all three choices. Opening and focusing apps can execute today; text entry and app-control choices are reported without sending an effect.

## What you can do today

Supported app targets are Finder, Terminal, Visual Studio Code, Safari, Google Chrome, System Settings, and Notes. Each installed app is offered to Jev, including the current app; Jev interprets which target the goal refers to. If an app-switching goal is already complete, Jev can select STOP.

Each command handles one decision and at most one app activation. It does not listen to your microphone, type or click inside apps, or complete multi-step requests. For example, `open Safari and click Run` may select opening Safari as a prerequisite; `completed` verifies only that selected activation, not the entire goal. It will not click Run.

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

Use an app name from the supported list above, and make sure that app is installed. Run `python -m app --help` to see all options. `--yes` automatically approves a low-confidence app activation for that invocation; it does not bypass the sensitive-goal block.

The command prints a schema-version-2 JSON result and saves it to `.build/python-cli-last.json` by default. It includes the selected operation/target/payload, exact source offsets, three probability distributions, and any executable app action. The lowest factor confidence drives approval. The report contains your goal text and app metadata, so review it before sharing. Choose another destination with `--report .build/my-run.json`.

You can inspect semantic text choices with `python -m app 'type "hello 🦊" in Notes' --dry-run`. Text entry, search, note creation, and command submission report `unsupported` until native executors exist, even with `--yes`; the validated semantic choice still appears in the report. A dry run sends the goal, compact app metadata, and span proposals to TypeSafe, but sends no desktop effect.

## Accessibility (optional)

Accessibility is not needed for current app switching, but you can check or request permission in preparation for future control features:

```sh
python -m app --request-accessibility
```

If macOS prompts, grant access to the terminal or Python host it identifies under **System Settings → Privacy & Security → Accessibility**. A `false` status does not prevent current app-switching commands from working.

## In development

The long-term goal is **voice-powered control of the Mac desktop through Jev**: speak naturally, let Jev understand what you mean and choose the next grounded action, then have Jev Pilot carry it out and verify the result. That means progressing from today's single app switch toward voice transcription, selecting an operation and its real on-screen target, handling exact dictated text, and chaining safe actions across desktop apps. Local safety checks, fresh target validation, and outcome verification remain part of that design—Jev chooses among actions the app can actually ground; it does not generate arbitrary executable commands.

The present CLI is text-only. The first roadmap milestone—one-request operation, target, and exact payload selection—is implemented, with local compatibility validation. The next milestones add bounded desktop perception and app controls; prove complete workflows; then add speech, queued commands, and eventually early execution of stable commands while the user is still speaking. Exact ordering may change as native reliability is measured.

These are plans, not current features. The roadmap is maintained in [`.codex/roadmap.md`](.codex/roadmap.md); it may not be present in a fresh clone because `.codex/` is locally ignored. The current implementation is summarized in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Development checks

With your Python environment active, run the offline pipeline checks:

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
```

The tests use fake Jev and desktop boundaries and leave `.build/python-e2e.json`. They exercise factor validation, compatibility, exact spans, safety, fresh checks before/after approval, effects, and verification. They do not prove live provider access, macOS permissions, or real app activation. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system details.

The previous Swift app is retired. Any retained Swift source is reference material, not a dependency of the Python CLI.
