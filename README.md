# Jev Pilot

Jev Pilot is an early macOS command-line tool that uses natural-language text to open or switch to a supported app. You give it a goal, local code builds a short list of allowed app actions, Jev chooses one, and Jev Pilot checks that the app actually came to the foreground.

## What you can do today

Supported app targets are Finder, Terminal, Visual Studio Code, Safari, Google Chrome, System Settings, and Notes. The selected app must be installed. If it is already frontmost, there is nothing to do.

Each command handles one decision and at most one app activation. It does not listen to your microphone, type or click inside apps, or complete multi-step requests. For example, `open Safari and click Run` only offers the Safari app action; it will not click Run.

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

The command prints a JSON result and saves it to `.build/python-cli-last.json` by default. The report contains your goal text, so review it before sharing. Choose another destination with `--report .build/my-run.json`.

## Accessibility (optional)

Accessibility is not needed for current app switching, but you can check or request permission in preparation for future control features:

```sh
python -m app --request-accessibility
```

If macOS prompts, grant access to the terminal or Python host it identifies under **System Settings → Privacy & Security → Accessibility**. A `false` status does not prevent current app-switching commands from working.

## In development

The long-term goal is **voice-powered control of the Mac desktop through Jev**: speak naturally, let Jev understand what you mean and choose the next grounded action, then have Jev Pilot carry it out and verify the result. That means progressing from today's single app switch toward voice transcription, selecting an operation and its real on-screen target, handling exact dictated text, and chaining safe actions across desktop apps. Local safety checks, fresh target validation, and outcome verification remain part of that design—Jev chooses among actions the app can actually ground; it does not generate arbitrary executable commands.

This is the direction, not current functionality. The present CLI is text-only and only opens or focuses a supported app. The roadmap's next milestones build a reliable Jev decision model for intent, target, and payload; add bounded desktop perception and app controls; prove complete workflows; then add speech, queued commands, and eventually early execution of stable commands while the user is still speaking. Exact ordering may change as native reliability is measured.

These are plans, not current features. The roadmap is maintained in [`.codex/roadmap.md`](.codex/roadmap.md); it may not be present in a fresh clone because `.codex/` is locally ignored. The current implementation is summarized in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Development checks

With your Python environment active, run the offline pipeline checks:

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
```

The tests use fake Jev and desktop boundaries. They exercise the CLI pipeline but do not prove live provider access, macOS permissions, or real app activation. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system details.

The previous Swift app is retired. Any retained Swift source is reference material, not a dependency of the Python CLI.
