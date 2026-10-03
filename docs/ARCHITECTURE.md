# Jev Pilot architecture

This document describes the code that runs today and the boundaries guiding its development. The user-facing setup and supported commands are in the [README](../README.md); the long-term voice-control direction is summarized there and detailed in `.codex/roadmap.md` for local development.

## Current runtime

`python -m app` accepts one typed goal and can perform at most one app activation. It does not currently use speech recognition or control elements inside apps.

```text
goal
  → block sensitive wording locally
  → observe foreground/running apps
  → generate installed, explicitly named app candidates + STOP
  → ask Jev to select one candidate
  → validate Jev's answer
  → re-observe and confirm the candidate is still valid
  → apply local safety / ask for approval
  → open or focus one app with AppKit
  → verify foreground identity and report outcome
```

## Responsibilities

| Module | Responsibility |
|---|---|
| `app/cli.py` | Parse the goal and options, request approval when needed, save and print the report. |
| `app/runtime.py` | Orchestrate one bounded run and record stage timings/outcome. |
| `app/desktop.py` | Observe foreground/running applications, generate named-app candidates, and request AppKit activation. AX is only queried for trust/focused-window readiness; it does not inspect or operate controls. |
| `app/decision.py` | Build one TypeSafe Choice request, call Jev, validate its response, and resolve its selected ID. |
| `app/models.py` | Define local candidate and validated decision records. |
| `app/safety.py` | Block selected sensitive goals before provider access and apply the post-choice confidence rule. |
| `app/verification.py` | Poll for the requested app's bundle ID becoming frontmost; never retry an uncertain effect. |
| `app/config.py` | Load the TypeSafe key from the environment or `.env`. |

## Jev's authority boundary

`MacDesktop.candidates()` creates each `ActionCandidate` locally, including its opaque ID, operation (`OPEN_APP` or `FOCUS_APP`), bundle ID, and description. `decision.build_choice()` exposes those options as `Choice.criteria`. Jev returns an ID and decision metadata, not an executable command. `decision.py` checks that the ID and probability keys match the supplied set, validates the numeric values and winner, then returns the original local candidate. The candidate ID only has meaning through that request's local lookup.

The runtime independently checks the foreground has not changed and that the selected app action is still available. Sensitive credential, payment, purchase, and destructive wording is blocked before Jev receives a request. Only app-open/focus actions are executable in this milestone. Confidence below 0.65 prompts for approval; `--yes` only pre-approves that prompt. `STOP` does not execute. Completion is reported only after the requested bundle is observed frontmost.

## Data and failure handling

Jev receives the goal, a compact active-app/Accessibility-readiness summary, and the candidate menu. It does not receive native AX element handles or arbitrary app contents. The key is parsed as data from `.env` (or taken from the environment); it is not included in reports. Reports are local JSON artifacts and do contain the user's goal text.

The report distinguishes outcomes such as blocked, unsupported, stopped, rejected, dry-run, failed, unverified, and completed. Once an OS effect is dispatched, cancellation cannot undo it; if verification is uncertain, the action is not replayed. Offline end-to-end tests inject fake Jev and desktop boundaries, so passing tests do not prove live provider access, macOS permissions, or real activation.

## Intended direction, not current behavior

The long-term goal is voice-powered control of the full desktop through Jev: transcribe speech, understand the requested operation, select a real observed target and exact payload, execute grounded actions, and verify outcomes. Development is intended to proceed through reliable semantic decisions and bounded desktop perception, then tested app controls and multi-step workflows, before adding voice, a bounded queue, and potentially early execution of stable low-risk commands while speech continues.

Those capabilities are not present in `app/` today. Future work must keep executable actions locally grounded, revalidate live targets before effects, apply safety independently of Jev, distinguish typing from submission, and verify results rather than treating dispatch as success. See the README's **In development** section for the concise user-facing roadmap.
