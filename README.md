# Jev Pilot

Control your Mac with spoken commands. Jev Pilot listens through your microphone, transcribes speech locally, asks Jev to choose an available action, and checks the result before moving on.

This is an early macOS command-line tool. Start a listening session in Terminal, then use your voice in the apps you want to control.

## Features (v0.1)

- **Open and switch apps:** use installed app names, including familiar aliases such as Chrome and VS Code.
- **Visit websites:** say a site name, URL, or domain such as “stripe dot com.”
- **Search the web:** search Google or YouTube, optionally in a named browser.
- **Dictate text:** insert text into an editable field, with Enter only when you explicitly request it.
- **Create documents:** create a note in Apple Notes or a document in TextEdit, then dictate its contents.
- **Control windows:** enter full screen and minimize the current window or a named running app.
- **Keep listening:** run multiple commands in one session, with an option to skip routine open/search confirmation prompts.

App opening/switching, website navigation, Google/YouTube searches, typing, supported document creation, window controls, and continuous listening are in scope for v0.1 following manual testing. Clipboard editing, browser controls, scrolling, and Finder search are deferred to v0.2; see the list below. More complex workflows may stop when a required control or result cannot be observed.

## Set up

You need an Apple Silicon Mac, conda, Python 3.12 or newer, and a TypeSafe account with a Jev API key. Internet access is required for Jev decisions and the initial speech-model download; transcription runs locally afterward.

From the repository directory:

```sh
conda create -n jev-pilot python=3.12
conda activate jev-pilot
python -m pip install -e .
```

If the environment already exists, just activate it and run the editable install above, which installs the `jev-pilot` command and its dependencies. The first voice run downloads the Nemotron speech model and may take a while.

Create your configuration directory:

```sh
mkdir -p "$HOME/Library/Application Support/Jev Pilot"
```

Create `~/Library/Application Support/Jev Pilot/.env` containing:

```text
TYPESAFE_API_KEY=your_key_here
```

Replace the placeholder with your key and keep the file private. You can also set `TYPESAFE_API_KEY` in your shell. Key precedence is the environment variable, a source checkout’s `.env`, then the user configuration file above. Installed use does not load `.env` files from the current directory or site-packages.

### Grant permissions

Allow **Microphone** access for the terminal/Python host macOS identifies when you first start listening. Check **System Settings → Privacy & Security → Microphone** if prompted access was denied.

Request **Accessibility** access for desktop controls:

```sh
jev-pilot --request-accessibility
```

Enable the identified host under **System Settings → Privacy & Security → Accessibility**. Restart that host if needed, activate the conda environment again, and rerun the command from the same terminal until it prints `accessibility_trusted: true`. App opening/switching alone does not require Accessibility; most other controls do.

## Start listening

On each visit, open Terminal and run:

```sh
conda activate jev-pilot
jev-pilot --listen --auto-approve-basic
```

This skips confidence prompts for app opening/switching, website navigation, and web searches. Other approvals and safety checks stay active. To keep routine confidence prompts too, use `jev-pilot --listen`.

To skip routine confidence prompts across supported commands, including typing, document creation, and window controls, use:

```sh
jev-pilot --listen --yes
```

**These actions always ask for approval, even with `--yes`:**

- Cutting text.
- Closing a browser tab.
- Pressing Enter/Return outside Notes or TextEdit.
- Using an unfamiliar control whose effect is not classified as routine.

Existing safety blocks and target/result checks remain active. Password, payment, purchase, and deletion requests remain blocked.
The cut and close-tab safeguards also apply to their experimental handlers, which are deferred to v0.2.

1. Say a command, such as **“open Safari.”**
2. Pause briefly to finish the utterance; the default pause is about 650 ms.
3. Wait for the result and **“Ready for the next command.”**
4. Say the next command without restarting the listener.

The terminal shows partial speech, the final transcript, and the command result. If a `Y / n` prompt appears, enter `y` to approve or `n` to decline. Approval restores the original app and checks the target again before acting.

A failed or declined command normally leaves listening active. Follow-up commands already queued behind a failure are discarded; speak a fresh command after the result. Capture errors or queue overload can end the session, so wait between commands while testing.

### Stop listening

Say **“stop listening,” “stop pilot,”** or **“quit pilot.”** You can also press **Control-C** in Terminal.

Spoken stop also cancels a pending approval prompt without requiring a keyboard answer.

Stopping discards unfinished speech and pending commands, and cancels the current command before its next action. It cannot undo an action already sent. Run the start command again to begin another session.

## v0.1 example spoken commands

Start with app opening and web searches, then try dictation in a disposable document.

| What you want | Say |
|---|---|
| Open an installed app | “open Cursor” · “open Notes” · “open Music” |
| Switch apps | “switch to Chrome” · “switch to Finder” |
| Visit a site | “go to YouTube” · “go to stripe dot com” |
| Choose a browser | “open example.com in Chrome” |
| Search Google | “google best ramen near me” |
| Search YouTube | “search for Messi soccer videos on YouTube” |
| Search in a named browser | “search YouTube for lofi hip hop in Safari” |
| Dictate into the focused field | “type hello world” |
| Dictate, then press Enter | “type hello world and hit enter” |
| Create and fill a note | “create a new note in Notes and type hello world” |
| Create and fill a document | “create a new document in TextEdit and type hello world” |
| Select text | “select all” |
| Enter full screen | “maximize this window” · “maximize Safari” |
| Minimize a window | “minimize this window” · “minimize Safari” |
| End the session | “stop listening” |

Text controls require an editable field; select-all needs text. Typing replaces selected text or inserts at the caret. Named window commands target a running app; “maximize” means enter full screen.

Use short, explicit commands. For longer workflows, start with Notes or TextEdit and name the app. Arbitrary multi-app workflows are not supported. If speech is misheard, wait for the result and give a fresh instruction; an action already performed is not automatically undone.

## Deferred to v0.2

The following failed current manual testing and are outside v0.1's supported scope:

- Copy and paste.
- Cut and paste.
- Undo and redo.
- Creating, switching, and closing browser tabs.
- Browser history: back and forward.
- Reload.
- Browser zoom.
- Scrolling.
- Scroll boundaries: go to the top or bottom.
- Finder search.
- Finder scrolling.

Existing experimental handlers may still accept these commands. They need real-app fixes and acceptance testing before being advertised as supported. Google and YouTube searches and opening websites remain in v0.1.

## Approvals and current limits

Cut, closing a tab, Enter outside Notes/TextEdit, and unfamiliar controls require approval even with `--yes`. Without `--yes`, low-confidence choices can also prompt. `--auto-approve-basic` only relaxes routine open/search prompts; `--yes` extends this to other supported routine commands, including known document-creation controls. Neither option bypasses target checks or verification. Goals involving passwords, payments, purchases, or deletion are blocked.

An `unsupported` result means no suitable action was available. An `unverified` result means an action may have happened, but Jev Pilot could not confirm it. Check the app before repeating a command, especially note creation, typing, or cutting text.

Current limits include:

- Menus must be accessible and use supported English labels. Browser tab/navigation compatibility varies; Helium and other browser adapters remain future work.
- Quitting apps, creating Terminal windows, submitting Terminal commands, and choosing a Music playlist are not supported.
- Secure, read-only, or unreadable text fields cannot be edited.
- Finder search and scrolling are deferred to v0.2. Existing search verification covers query entry rather than loaded results; duplicate or inaccessible file results cannot be safely opened.

Speech transcription runs on your Mac. Jev requests send your spoken goal and bounded app/control metadata to TypeSafe. Existing document values and clipboard contents stay local. Session reports contain transcripts and dictated text, so review them before sharing.

## Troubleshooting

- **Check speech without desktop actions:** run `jev-pilot --transcribe-only`, speak a few phrases, and press Control-C to stop. This mode does not call Jev or require an API key or Accessibility permission.
- **Commands split too early:** increase the pause, for example `jev-pilot --listen --auto-approve-basic --silence-ms 900`. Lower it for faster turn-taking.
- **Quiet speech is missed:** lower `--speech-threshold` slightly from its default `0.012`; raise it if background noise triggers utterances.
- **Desktop controls are unavailable:** check Accessibility from the same terminal and conda environment used to listen. An app must also expose the relevant control or editable field.
- **App names are ambiguous or missing:** use the exact installed name. Restart the listener after installing an app. Duplicate names require a bundle identifier to distinguish them.
- **An app opens but a workflow stops:** inspect the result before trying again; opening, creation, focus, and typing are separate steps.
- **Clipboard editing, browser controls, scrolling, or Finder search do not work:** these are known failures deferred to v0.2. If an experimental command returns `unverified`, check the app before repeating it.
- **Need to share a failure:** the latest session report is saved after each command to `~/Library/Application Support/Jev Pilot/Reports/python-cli-last.json`. Use `--report .build/my-session.json` to choose a different file. Remove private transcript/dictation content before sharing.

## Local development

The active runtime is the Python CLI in `app/`; retained Swift sources are historical references. `python -m pip install -e .` installs the source checkout as the `jev-pilot` command; `python -m app` remains available. Package version and build settings live in `pyproject.toml`, which reads dependencies from `requirements.txt`. Activate the environment and run the offline checks:

```sh
conda activate jev-pilot
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q app scripts tests
```

Tests produce reports under `.build/` and use simulated desktop, speech, and provider services. They do not establish real-app compatibility. Opt-in provider tests use `JEV_LIVE_JEV=1`, require your API key, and make real API requests with simulated desktop actions.

For real desktop acceptance, `python -m scripts.test_typing_native` creates and edits a disposable TextEdit document; it requires Accessibility. For a read-only Notes/TextEdit diagnostic, use `python -m scripts.test_typing_native --inspect Notes` or `--inspect TextEdit`.

See [the architecture guide](docs/ARCHITECTURE.md) for implementation details. Restart the listener after changing code.
