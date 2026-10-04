# Jev Pilot architecture

The active runtime is `python -m app`: text goals, one factorized Jev request per
step, bounded typing preparation, native app/editor effects, and verified reports.
Swift sources are retired references, not a fallback runtime.

```text
goal → sensitive-goal block → native app + bounded AX editor snapshot
  → installed application targets + field targets + exact payload spans
  → one Jev request: operation / target / payload
  → validate each distribution and the combination
  → fresh identity/text/caret check → local safety / approval → fresh check
  → native activation / field focus / exact insertion → outcome verification
  → continue only for explicit typing prerequisites, at most four steps
```

## Responsibilities

| Module | Responsibility |
|---|---|
| `app/cli.py` | Input/options, approval, atomic report output, exit status. |
| `app/runtime.py` | Bounded preparation/insertion flow, fresh checks, safety, timings, reports. |
| `app/desktop.py` | AppKit observation/activation, app/field menus, final native guard before dispatch. |
| `app/text_input.py` | Bounded AX editor discovery, identity continuity, local document/caret facts, UTF-16 replacement, guarded text/focus dispatch. |
| `app/semantics.py` | Operation vocabulary, exact goal spans, combination checks, local action resolution. |
| `app/decision.py` | Three Choices in one TypeSafe call per step; strict response validation. |
| `app/models.py` | Executable actions and validated semantic decisions. |
| `app/safety.py` | Sensitive-goal blocking and local confidence approval. |
| `app/verification.py` | Verify foreground, focused identity, or exact resulting text/caret; never replay. |
| `app/config.py` | Environment/`.env` keys parsed as data, excluded from reports. |

## Semantic authority and preparation

All operations remain visible independently of local verb recognition. Installed
catalog apps plus the current foreground app are targets. TextEdit is in the
catalog. AX fields have a separate `field` scope and request-local identity IDs;
application targets cannot execute TYPE_TEXT. Local parsing proposes exact quote
interiors and unquoted suffix boundaries, not the operation. Limits remain 8192
goal characters and 16 span proposals; overflow fails before provider access.

Each step sends operation/target/payload Choices with shared `semantic_options`.
Operation criteria list their currently compatible targets; target criteria list
available operations. This grounds independent answers in the same next-step
constraints without locally classifying the user's operation.
Every answer requires known IDs, exact probability keys, finite [0,1] values,
total within 0.01 of one, valid confidence, and a maximum-probability winner.
Normalize accepted totals to one without changing factor confidence. Record raw
total/count/normalization per factor, including factor-specific rejection details.
Minimum factor confidence drives approval; independent factors are not a joint
probability. Incompatible choices reject the whole decision without fallback effects.

OPEN_APP/FOCUS_APP retain their one-action behavior. Explicit
OPEN_APP_FOR_TEXT/FOCUS_APP_FOR_TEXT request activation followed by fresh editor
observation; FOCUS_FIELD requests one native focus effect followed by another Jev
choice. Typing prerequisites may carry an exact payload span without inserting
it, or explicitly choose none. A carried span is bound to the preparation sequence
and cannot change before insertion. TYPE_TEXT requires a focused field with a
readable value and valid UTF-16 selection plus an exact span. App-level TYPE_TEXT
can be described semantically but has no executable target. SEARCH/CREATE_NOTE/
RUN_COMMAND remain unsupported. No executor submits commands or presses Return.

If Jev selects application-level TYPE_TEXT while that app is frontmost and has
an observed typeable or focusable editor, record a `reselect_field` step with no
effect. Bind its exact payload and ask Jev again using only that app's fresh field
targets plus none. This consumes the same four-step budget; Jev must still choose
FOCUS_FIELD when needed, then TYPE_TEXT. Never map the app directly to an editor.
A changed foreground app, changed payload, missing usable editors, or repeated app
ID cannot authorize insertion. Other incompatible choices still reject normally.

There are at most four steps per run. Each prerequisite must verify before the
next request; previous verified operations are provided to Jev. A completed
insertion terminates the run immediately, preventing a second insertion. STOP
after preparation reports incomplete, and exhausted preparation is incomplete.
Completed preparation effects are never undone. Ordinary app activation does not
finish a pending typing sequence. This is bounded typing preparation, not a general
multi-command or Notes-creation workflow controller.

## Native text boundary

AX discovery retains a deeply focused editor first, then traverses at most 256
nodes / 32 editable fields with a two-second scan budget in the focused window.
Container traversal is depth-first; table/list/outline/browser/collection children
are deferred behind sibling editor panes. Prefer AXVisibleChildren in collections
and scroll areas, falling back to AXChildren when unsupported. Each pending queue
is capped at 256 handles. Inspect a directly focused text role first; a focused
sidebar row/table does not bring its subtree ahead of the window's document pane.
Do not descend into paragraph/attachment children of text fields. AXIdentifier is
a bounded label fallback, allowing Jev to distinguish Note Body Text View from search.
Secure/password roles and
protected ancestors are rejected before reading values. Disabled and read-only
fields are excluded. Native CFEqual identity maps adjacent snapshots; fresh handles
alone execute. Available AXWindow identity must match the observed focused window.
Only the focused editor retains existing document value/selection locally; values
above 65,536 Python characters or missing/invalid selections prevent insertion.

Immediately before effects, compare app PID, window/element identity, role/label,
write capabilities, focus, exact value and selection. Repeat after approval and
once more in the native executor. Caret offsets count UTF-16 units, not Python
code points. Split-surrogate selections are rejected. Replacement touches only the
selection via writable AXSelectedText; AXValue is never rewritten wholesale.
If that attribute is not writable, use preflighted paired Quartz Unicode events,
20 UTF-16 units per batch without splitting surrogate pairs. Guard foreground PID
and focused native identity between batches without copying document text. Events
are addressed to the selected PID with zero modifiers. No clipboard or Return/Tab
key is used. A failed setter never falls back to keyboard events.

Multiline text is supported through AXSelectedText on AXTextArea. Single-line fields
and keyboard fallback reject control/multiline characters. Terminal is type-only:
require the focused AXTextArea caret at the exact buffer end with no selection;
reject control characters and Unicode line separators through either dispatch path.
Fields without a verifiable baseline remain unsupported rather than guessed.

Verification requires the complete expected value on the same editor/window and
the collapsed caret at selection start + inserted UTF-16 length. This also handles
replacement with identical text. Substring presence is never sufficient. Partial,
failed, or unverified insertions are not retried; reports distinguish dispatch
that never started from uncertain/partial effects.

## Data, reports, and evidence

Reported failure cases to reproduce: a valid Notes activation followed by a
probability-total rejection must retain the failing factor and raw total; rounding
within 0.01 may normalize without changing confidence, larger errors must reject.
An active Notes window with no field targets must report discovery/readiness
counts so missing editors can be distinguished from missing text/caret/write support.

Provider state contains the goal, compact app/readiness facts, bounded field labels
(up to 240 characters), roles/focus/capabilities, span proposals, and prior verified
operation names. Existing AX document values, selected text, and native handles
stay local. Labels may themselves contain app-provided text. Schema-3 reports
contain semantic decisions, exact dictated payloads, per-factor metadata, step
outcomes, aggregate timings, and completion scope. `single_insertion` verifies
one insertion; `selected_action` verifies an ordinary app activation. Neither
claims arbitrary multi-command goal completion. Review reports before sharing.
Rejected combinations include validated choice IDs, factor confidences and
distributions under `rejected_decision`, without raw provider bodies.
`editor_readiness` reports focused-window AX error, field/insertion counts,
excluded/unready field reasons, traversal/deferred collection counts, dropped
pending handles and limits, without document text or labels. The observed Notes
failure had a verified app preparation, then no field targets and an unsupported
application-level TYPE_TEXT; no text was sent. The latest saved report reached
exactly 256 visited nodes with scan_limit_reached=true and no excluded text fields.
Read-only native inspection confirmed a writable Note Body Text View beside the
broad note table. Breadth-first traversal could exhaust its budget in list rows
before reaching a nested body; deferred collection traversal fixes this case.
Regression scenarios cover 170-note trees, both pane orders, visible-children
support or absence, and focus on row/table/window/body. Native Python insertion
remains unverified on this tool host; native UI inspection alone does not prove it.

Confidence below 0.65 requires approval. `--yes` approves low-confidence native
app/focus/typing actions only; it does not bypass blocks or fresh validation.
Sensitive goal wording is blocked before Jev. `--dry-run` still sends bounded
state to TypeSafe but stops before the first effect, including preparation.

Offline production-pipeline checks leave `.build/python-e2e.json` and
`.build/typing-e2e.json`. The typing checks fake only Jev and native API boundaries,
exercising production discovery, semantics, safety, dispatch, and verification.
`python -m scripts.test_typing_native` creates a uniquely named disposable
TextEdit fixture, restores the starting foreground app, then sends `open TextEdit
and type ...` through the production CLI. Scripted Jev chooses activation, any
necessary field focus, and insertion from fresh menus; `--live-jev` uses TypeSafe.
Only TextEdit activation and the identified fixture's body may receive runtime
effects. Match its unique title and AXDocument URL when present; require the exact
fixture baseline before insertion. Success requires verified app preparation and
exact insertion (`workflow_verified=true`), not an already-focused-editor preflight.
It writes `.build/typing-native.json` and fails on missing permissions/readiness.
`--inspect Notes` or `--inspect TextEdit` reads a running app's editor capability/
error facts without activation, effects, provider calls, or printing document text.
The command harness is independently checked with fake native boundaries in
`.build/native-harness-e2e.json`; this artifact is offline evidence only. This development Python host currently
has no AX permission: native insertion remains unverified. User-run reports show
their CLI host has permission; permission attribution differs between hosts.

Native discovery reports `text_field_checks` (at most 32 records) with roles,
focus, AXEnabled error/type/Boolean result, and write/focus capabilities; ancestry
failures retain attribute-specific error counts. Existing values, selections,
labels and handles are omitted from these diagnostics. The user’s native Notes
inspection confirmed a focused AXTextArea was discarded because AXEnabled returned
kAXErrorAttributeUnsupported (-25205). Only that specific unsupported attribute
may defer to positive AXSelectedText/AXValue writability or AXEditable=true. Explicit
false/invalid enabled values, NoValue (-25212), and other read errors still reject.
Terminal’s special text-area allowance is not write evidence for this exception.
Secure ancestry, window identity, and exact focused text/caret checks still apply.
Diagnostics mark availability_check=write_support for accepted unsupported cases.
Native true accepts Boolean or integer 1 for AXEnabled/AXEditable. E2E regressions
cover unsupported-enabled focused/unfocused Notes/TextEdit fields, the native
command harness, and read-only/secure/no-write/error rejection. The Notes editor
was reached directly before rejection; scan_limit_reached=true describes remaining
list traversal, not the cause of this editor’s absence. The user confirmed
successful Notes typing after this fix on 2026-10-04. Formal native-harness and
TextEdit acceptance remain pending; this tool’s Python host remains untrusted.

## Remaining roadmap

Prove real app/editor compatibility after granting native permissions. Notes
creation, Finder search/open-result workflows, Terminal submission, general
multi-step goals, speech, queues, and streaming remain future work.

User confirmation (2026-10-04): Notes typing worked after the AXEnabled
attribute-unsupported compatibility fix. This establishes a successful user-run
Notes workflow. The formal native-harness result and TextEdit acceptance remain
pending independent confirmation.
