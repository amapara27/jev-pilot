# Jev Pilot architecture

The active runtime is `python -m app`: typed goals or continuous local Nemotron
speech, one factorized Jev request per step, bounded typing preparation, native
app/editor effects, and verified reports. Swift sources are retired references.

```text
goal → sensitive-goal block → native app + bounded AX editor snapshot
  → discovered application/browser/site targets + field targets + exact payload spans
  → one Jev request: operation / target / payload
  → validate each distribution and the combination
  → fresh identity/text/caret check → local safety / approval → fresh check
  → native activation / HTTP URL open / control press / field focus / insertion → outcome verification
  → action history → fresh observation (four typing steps / eight workflow steps)

voice: microphone callback → bounded ordered PCM queue → Nemotron streaming session
  → partial display + optional stable app clause → silence-based EOU suffix → bounded FIFO
  → one goal worker → the same run_goal path → Jev → guarded effect → verification
```

## Responsibilities

| Module | Responsibility |
|---|---|
| `app/cli.py` | Input/options, approval, atomic report output, exit status. |
| `app/runtime.py` | Bounded preparation/insertion flow, fresh checks, safety, timings, reports. |
| `app/desktop.py` | AppKit observation/activation, app/field menus, final native guard before dispatch. |
| `app/app_catalog.py` | Installed bundle discovery, metadata validation and familiar spoken aliases. |
| `app/web_navigation.py` | HTTP URL normalization, search encoding and bounded browser URL observation/matching. |
| `app/text_input.py` | Bounded AX editor discovery, identity continuity, local document/caret facts, UTF-16 replacement, guarded text/focus dispatch. |
| `app/workflows.py` | Bounded Notes/Finder controls, local file identity, guarded menu/query/selection effects. |
| `app/interactions.py` | Explicit command order, focused editor/enabled menu/scroll-range grounding, guarded keys/wheel/AX effects and local outcome evidence. |
| `app/semantics.py` | Operation vocabulary, exact goal spans, combination checks, local action resolution. |
| `app/decision.py` | One TypeSafe call with operation, payload and speculative per-operation targets; validate only the chosen head. |
| `app/models.py` | Executable actions and validated semantic decisions. |
| `app/safety.py` | Sensitive-goal blocking and local confidence approval. |
| `app/verification.py` | Verify foreground, focused identity, or exact resulting text/caret; never replay. |
| `app/config.py` | Environment/`.env` keys parsed as data, excluded from reports. |
| `app/speech_model.py` | Lazily load the pinned MLX-Audio Nemotron model from the local Hugging Face cache. |
| `app/speech_runtime.py` | Capture mono audio, bound backlog, stream one decoder consumer, and commit final text at EOU. |

## Semantic authority and preparation

Explicit KEYBOARD_ACTION/SCROLL targets are collected only for recognized command
goals; ordinary insertion keeps its native implementation and scanner path.
Command parsing shields quoted and dictated/search text, requiring an explicit
connector for unquoted command tails. Ordered next_command filtering and runtime
checks prevent skipped/repeated shortcuts. Typing remains one insertion, followed
only by explicitly requested commands within the eight-step workflow bound.
Completed TYPE_TEXT is withdrawn, and Enter after insertion requires the same
verified editor/value/caret. A typing span containing an explicit command tail
is rejected; quote those words to insert them literally.

Interaction snapshots retain native window/focus/editor handles and baselines
locally. A bounded 256-node/half-second scan collects enabled English menu
commands and scroll areas, preferring the area containing focus. Only requested English menu branches are scanned. Scroll/window-only goals start at the window, and webpage children are deferred behind browser controls. Traversal tracks AXWebArea descendants so webpage tab groups and chart zoom widgets cannot become browser tab/zoom observations. Window observation remains reserved before the node budget is exhausted. Text commands
require a verifiable focused editor, copy/cut require selection, and select-all
requires text. Browser commands require a known browser and an observed menu, but not a focused editor. Missing AXFocusedUIElement suppresses text/Tab/Escape commands only; menus, grounded scrolling and native window controls remain eligible. Safari named-tab selection uses observed AX tab buttons. Browser/menu/scroll commands bind window/page/tab identity without binding an unrelated editor or caret; text commands retain exact editor/focus checks.
Menu shortcuts invoke AXPress; select-all uses a writable native UTF-16 selection
range or enabled Select All menu, avoiding keyboard-layout assumptions.
Enter/Tab/Shift-Tab/Escape use paired PID-directed Quartz events. Enter never executes in Terminal/iTerm. Keyboard
permission preflight and a final PID/window/focus guard precede event dispatch.
Notes selection identity is rechecked independently of editor identity.

Directional scrolling uses one pixel-unit wheel event positioned within an
observed area with readable remaining range. Top/bottom use writable AX scrollbar
positions. Range, bounds, native scrollbar/window/focus and editor context are
freshly validated. Verification requires movement in the requested direction
or exact boundary position; no event replay. Safari standard WebKit AXScrollBar values use the documented normalized 0–1 range when both min/max attributes are unsupported. Unique axis-labelled child scrollbars are accepted. Paste reads bounded plain text only locally, checking stable changeCount before/after capture and before dispatch; neither clipboard contents nor native handles enter Jev state or reports. Copy/cut use changeCount plus unchanged/exactly cut editor evidence. TextEdit/Notes
Enter verifies exact newline/caret; selection verifies the complete UTF-16 range.
Paste verifies exact expected insertion and UTF-16 caret; undo/redo require changed editor values. Safari tab selection and closure require same-window native selection/count evidence or confirmed removal of the original window from AXWindows. Next/previous tab requires the adjacent selected tab in the same native tab order, including wraparound; a single unchanged tab cannot prove switching. Lost Accessibility permission/window evidence cannot prove closure. Zoom percentages must change in the requested direction on the same page/tab; reset availability can prove reset-to-default only, with proportional same-element geometry as the other zoom evidence. A successful selection can withdraw its action target, so verification uses another observation of the same native window. Tab creation/reopening needs count growth, history navigation needs a URL change, and reload needs busy/page identity evidence. Focus changes alone never prove these browser actions. Address-bar/find/key focus actions retain their specific focus evidence. Unobservable effects stay
unverified even if the event was sent.

--auto-approve-basic changes only confidence prompts for app activation, HTTP
navigation and supported searches. It cannot bypass blocks, fresh identity checks
or verification. Cut/close-tab and generic editor Enter always confirm, including with --yes; paste uses the normal confidence threshold. Terminal Y/N approval restores the exact original NSRunningApplication process with a bounded foreground wait, then runtime repeats all window/editor/text/selection checks; approval never transfers to another target. Continuous voice checkpoints after each run and survives
per-goal failures; queued follow-ups behind a failure are discarded. New utterances
resume with fresh context. Voice approval polls the terminal's input readiness with a 50 ms bound rather than blocking in input(); spoken stop or capture failure cancels a pending prompt without a keyboard answer. The approval reader checks cancellation before restoration; the runtime checks again immediately after approval returns, before recording rejection or validating/dispatching further actions, and records cancelled rather than rejected. Typed-only approval retains its existing input() path. Spoken stop and Control-C cancel before the next effect;
capture errors, overload and failed committed prefixes still stop the session.
The voice report retains failures and the process exit status reflects them.

Installed app discovery runs at desktop construction, reading standard install
folders and nested utility folders without descending into app/framework/helper
packages. Background-only apps are excluded; duplicate bundles merge aliases.
Catalog candidates must still resolve through NSWorkspace at every fresh check.
Running apps and familiar aliases supplement discovery. Ambiguous command-side
app names fail before Jev; literal query/typing words do not authorize app actions.
Speech-prefix aliases come from the same catalog and exclude collisions.

OPEN_WEBSITE selects a browser app and an exact named-site/domain/HTTP span.
SEARCH_WEB selects a Google/YouTube site-and-browser target and an exact query.
Jev receives native default-browser facts; neither operation needs address-bar
typing or Return. Local normalization rejects credentials, non-HTTP schemes,
invalid domains/ports and control characters. Search uses URL encoding, and
command-like query words stay literal. Finder SEARCH remains separate.
Suffix-site search/find/locate forms propose an exact query span ending before
“on/in YouTube/Google,” with optional browser context. Explicit Finder and prior
typing remain shielded. Required effects classify these forms as SEARCH_WEB.
The local resolver and eligible payload Choices reject inclusion of the site
suffix, rather than silently rewriting a model-selected span.
Web-only goals withdraw editor, Finder and control effects from Jev's executable
operation Choice; a mistaken typing answer cannot insert a query into a document.
The executor rechecks foreground identity and browser installation immediately
before NSWorkspace's URL request. One failed/unverified request is never replayed.

Browser URL observation stays local: window AXURL/AXDocument or one unique
AXWebArea URL, with 128 nodes/one second and no arbitrary link/page-text reads.
AXURL's documented CFURL/NSURL values and string-valued browser attributes are supported.
Verification requires the chosen foreground browser and exact normalized URL;
search accepts extra parameters while requiring the same site/path/exact query.
Missing AX permission/URL evidence or redirects remain unverified; OS handoff
alone does not prove navigation, search results or page-load completion.
Read-only native acceptance found 251 installed targets and Safari as the default
browser on 2026-10-11; this host has no AX permission. `.build/web-e2e.json`
proves the offline CLI/voice paths and failure boundaries, not live browser compatibility.

All operations remain visible independently of local verb recognition. Installed
catalog apps plus the current foreground app are targets. TextEdit is in the
catalog. AX fields have a separate `field` scope and request-local identity IDs;
application targets cannot execute TYPE_TEXT. Local parsing proposes exact quote
interiors and unquoted suffix boundaries, not the operation. Limits remain 8192
goal characters and 16 span proposals; overflow fails before provider access.

Each step sends operation, payload and target_OPERATION Choices in one request.
Targets are restricted to each operation's grounded candidates. Only the selected
operation's target head authorizes input; unused answers are ignored, but the
response must have exactly the requested head names. Shared semantic_options retains
operation definitions and executable target facts once. Schema-3 reports retain canonical operation/target/payload
factors. Only currently available operations enter the operation Choice. Withdraw
completed one-shot prerequisites and verified control IDs; withhold typing until
required creation/control preparation verifies. After unique editor preparation,
scope subsequent target menus to that field. Each consumed answer requires known IDs,
exact probability keys, finite [0,1] values,
total within 0.01 of one, valid confidence, and a maximum-probability winner.
Normalize accepted totals to one without changing factor confidence. Record raw
total/count/normalization per factor, including factor-specific rejection details.
Minimum factor confidence drives approval; independent factors are not a joint
probability. Incompatible choices reject the whole decision without fallback effects.

Operation and target question criteria contain allowed IDs with null descriptions;
operation definitions and target facts live once in shared semantic_options.
Compatibility lists exist only in target Choices; withdrawn heads keep `none`.
Shared target facts cover only targets used by currently executable operations,
excluding irrelevant controls/editors for pure web goals. The native catalog/menu
and report metadata remain complete; this does not truncate installed apps.
The payload Choice is the union of executable operations' allowed payloads.
Redundant bundle IDs
and native PID lists are omitted only from provider application facts; local
resolution, identity checks and schema-3 reports retain the complete metadata.
Repeating a 263-app catalog across speculative heads caused HTTP 400 context
overflow. Jev 1.13 permits 64k total input tokens and 32k for state plus the longest
question; each Choice permits 255 options. Apply that bound after removing withheld operations and their target heads. For typing with more than 255 installed application targets, bind the exact named app context (or the current app for unnamed typing) before building choices. Keep the complete catalog locally and preserve the existing smaller-catalog typing choices. Pure web requests withdraw non-web effects; query extraction and URL generation are unchanged. Remaining oversized executable Choice menus fail locally without truncation. Recognized max_tokens_exceeded error codes produce a static
context-limit reason; raw provider bodies remain excluded from reports.
`.build/request-e2e.json` checks serialization growth, unchanged target selection
and safe failures. Live synthetic reproduction rejected the old 362,749-byte
request and accepted the compact 121,189-byte Notes dry run (40,190 input tokens).
`.build/request-limit-reproduction.json` and `.build/live-request-e2e.json` retain
that evidence without establishing native app compatibility.

The later Music foreground report contained 299 targets and rejected both the
Messi suffix-search phrase and Open Safari before effects. The remaining duplicated
compatibility lists and unused target heads were removed. Dense-catalog E2E covers
263 apps plus 36 long controls; opt-in live checks accept that app request and the
exact suffix search. A first live suffix search included the site in the query;
strict local span constraints now make that tuple unavailable. These checks use
synthetic native state, so they establish provider acceptance rather than app behavior.

OPEN_APP/FOCUS_APP are one-action only when no recognized effect remains. Explicit
OPEN_APP_FOR_TEXT/FOCUS_APP_FOR_TEXT request activation followed by fresh editor
observation; FOCUS_FIELD requests one native focus effect followed by another Jev
choice. Typing and workflow prerequisites may carry an exact payload span without inserting
it, or explicitly choose none. A carried span is bound to the preparation sequence
and cannot change before insertion. TYPE_TEXT requires a focused field with a
readable value and valid UTF-16 selection plus an exact span. App-level TYPE_TEXT
is rejected, not corrected into an editor. Scoped SEARCH and
CREATE_NOTE execute only with current native controls. RUN_COMMAND remains
unsupported. No executor submits Terminal commands; explicit guarded Enter remains separate from insertion.

OPEN/FOCUS_APP_FOR_WORKFLOW, CREATE_NOTE and PREPARE_SEARCH accept a carried future
span, exactly like typing preparation; native parameters for those effects contain
no text. Only TYPE_TEXT/SEARCH dispatch the payload. This fixes the observed live
rejection of OPEN_APP_FOR_WORKFLOW + Notes + span_0. “Open a new note” establishes
the same creation requirement as “create a new note”; quoted payloads are shielded.
Recognized typing/Notes/Finder goals with AX trust false fail before Jev, reporting
python_executable and permission instructions. Plain app activation still works
without AX permission. Microphone permission alone does not authorize AX effects.

Both continue_typing and continue_workflow activation must observe the selected
foreground app and a usable focused Accessibility window, except Finder search
preparation may use an observed enabled Find menu when AXFocusedWindow is absent
with success or AXNoValue. Only PREPARE_SEARCH can execute in this windowless state;
recheck foreground PID, trust and absent window before pressing Find, then require
a real writable query. Never compare nil native handles with CFEqual. Verification polls
within its existing three-second bound, returning immediately when ready. Persistent
AX window errors stop preparation before the next provider request, with a specific
readiness reason; the already-sent activation is not replayed. App-only activation
still needs foreground alone. .build/workflow-readiness-e2e.json covers delayed
window readiness, persistent -25204 errors and ordinary open/focus through the CLI.

Jev receives the last eight compact action outcomes plus verified/remaining
operations. A replaced pressable control in the same app/native window permits at
most two fresh Jev choices before dispatch, within the normal step budget. Never
inherit approval, recover text/caret/file/app drift, or replay a sent effect.

Typing-only runs have at most four steps. Each prerequisite must verify before the
next request; previous verified operations are provided to Jev. A completed
insertion terminates ordinary typing runs; explicit command tails continue without another insertion. STOP
after preparation reports incomplete, and exhausted preparation is incomplete.
Completed preparation effects are never undone. Ordinary app activation does not
finish pending typing. Recognized Notes/Finder workflows have eight steps, with
required operations derived from supported command forms while shielding quoted
and typed payloads. Jev chooses every step. A skipped creation/search prerequisite
is withheld, STOP is incomplete, repeated workflow effects are denied, and one
insertion terminates workflows without explicit keyboard/scroll tails. These are scoped workflows, not general planning.

## Notes and Finder controls

`NativeWorkflows` scans the active accessible app, at most 256 nodes and one second,
using visible children and CFEqual identity across adjacent observations. Exact
English New Note, Find and Open menu controls require AXPress and availability;
prefer a unique menu item to a redundant toolbar button. Changed selected-note
identity with one writable body proves creation even before focus. Alternatively
require a newly observed focused empty editor, or an empty body after nonempty
baseline/window change. Bind subsequent focus/typing to that note/editor/window;
the executor rechecks selected note identity. Require empty body/caret (0,0)
before insertion. An indistinguishable old empty note pauses; creation is not replayed.

The scanner also grounds AXPress buttons/menus in other accessible apps, deferring
broad collections behind controls. PRESS_CONTROL verifies target selection/expansion,
a new editor, or changed local document URL. AXPress success alone is not proof.
Bind a uniquely prepared editor through focus/insertion; reject repeated control IDs.
Routine labels need 82%; unknown labels require interactive approval even with
--yes. Destructive labels and Terminal controls are denied. URL/filename-bearing
controls cannot bypass scoped file operations. Control-only runs end on Jev STOP
with verified_actions scope: observable effects, not arbitrary goal completion.

Each dispatched step records bounded post-action reason/readiness, not values,
carets or handles. Poll within three seconds, advance immediately on evidence and
check cancellation each observation. All workflows use the same run_goal loop.

SEARCH focuses only an identified writable Finder query, capped at 4096 characters, sets its exact value and sends paired PID-directed Return after rechecking foreground/window/query; it never rewrites a document or submits Terminal input. Finder prioritizes File → Find before dense window content, accepts supported search text/combobox fields when AXEnabled is unsupported and AXValue is writable, and rejects explicit disabled/error states. PREPARE_SEARCH exposes that field through Find. Query equality verifies query entry plus submission dispatch, not result readiness or opening. SELECT_ITEM
requires a visible selectable item with a resolvable local URL. Deduplicate AX
representations of one URL; disable distinct same-name files. OPEN_ITEM requires
exactly one native collection selection and the current Open menu. File device/inode/size/mtime and
native item/window identity must remain unchanged. Verify the destination's
AXDocument/AXURL, including app changes. Missing identity or destination evidence
pauses; there is no coordinate/path guessing or retry. Known executable/package/
link extensions are blocked. New workflow effects require 82% confidence or
approval; existing app/focus/typing keeps 65%.

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
Only the focused editor retains existing document value/selection locally. Prefer AXValue strings; bridge NSAttributedString through string(). If AXValue is unavailable, a successful integer AXNumberOfCharacters=0 proves an empty body. Nonempty fallback requires bounded AXStringForRange over the complete native range and matching UTF-16 length. Oversized/inconsistent/unreadable text and missing/invalid selections still prevent insertion. The insertion method itself is unchanged.

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
one insertion; `selected_action` verifies ordinary activation. `verified_workflow`
requires every recognized workflow effect and every ordered explicit input command; records verified_operations.
None claims arbitrary free-form goal completion. Review reports before sharing.
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

Typing/app confidence below 0.65 and workflow/interaction confidence below 0.82 require approval. `--yes` approves routine confidence prompts, including known document-creation controls. The shared safety.requires_confirmation predicate keeps unfamiliar control presses and interaction targets requiring confirmation interactive in both text and voice modes. --auto-approve-basic applies only to app activation/navigation/search. Neither bypasses blocks or fresh validation. Synthetic text/voice scenarios in .build/yes-workflow-approvals-e2e.json and .build/yes-risky-approvals-e2e.json cover low-confidence creation/typing, unknown/destructive controls, and mandatory cut/close-tab/generic-Enter approvals.
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

## Continuous speech boundary

Voice mode is opt-in (`--listen`); `--transcribe-only` exercises microphone and
ASR without an API key, Jev request, Accessibility snapshot, or desktop effect.
The ordinary positional text-goal route remains unchanged. The 8-bit Nemotron
model is loaded lazily on Apple Silicon and its first use downloads to the
standard Hugging Face cache; subsequent recognition is local. The runtime uses
MLX-Audio's `create_streaming_session()` interface, not its separate speaker-
diarization session.

The PortAudio callback copies mono float32 frames into a bounded queue and does
no model work. A single ordered consumer feeds audio and advances the decoder;
partials are display-only by default. A small RMS gate commits an utterance after sustained
silence, flushes one final result, and creates the next decoder session while
capture continues. Input and goal queues are bounded; overflow is an explicit
error, never silent data loss. Final transcripts enter a five-goal FIFO. One
execution worker calls the existing `run_goal()` on the main/AppKit thread
with transcript text only; desktop state is observed fresh when a queued goal
starts. ASR decoding runs alongside it. A non-success outcome discards queued
follow-ups and records them as not run; listening continues for new goals. Stop/Ctrl-C discards the
current partial utterance and cancels the active run at its next pre-effect
boundary; already-sent native effects cannot be undone.

`--silence-ms` controls the local endpointing threshold. This is a latency/noise
tradeoff, not a learned VAD. The Nemotron session uses its automatic language
selection. The user reports live speech testing; quantified WER and latency remain
separate acceptance evidence. Offline checks fake model/audio boundaries.

`--listen --stream-prefixes` enables `StreamingCommands`: one explicit known-app
opening/focusing clause followed by a connector must remain unchanged for 400 ms.
It enters the same bounded FIFO before EOU; Jev may only select OPEN_APP/FOCUS_APP
for that exact app (or STOP if already active). No partial typing or file opening.
At EOU, remove only the exact committed prefix and connector and queue its exact
suffix with intended app context. Re-observe that context at dequeue. A changed
committed prefix/boundary stops capture and future effects; sent effects remain.
Offline E2E proves early dispatch/deduplication, not real ASR prefix latency.

## Remaining roadmap

Notes/Finder workflows and opt-in first app clauses are implemented with offline
artifacts `.build/workflow-e2e.json`, `.build/workflow-failures-e2e.json`, and
`.build/streaming-command-e2e.json`. Native acceptance on 2026-10-11 still fails
before effects: this Codex Python host is not AX-trusted. Validate new controls
from the user's permitted terminal. Terminal submission, richer app controls,
general planning, broader streaming and measured latency remain future work.

The user's subsequent voice report also has AX trust false. Routing regressions
are covered by `.build/workflow-routing-regression.json` (typed/voice, open/focus,
carried span through creation and one insertion) and
`.build/workflow-routing-boundaries.json` (no-access preflight, payload drift and
app-only execution without AX). These artifacts fake native/provider boundaries.

User confirmation (2026-10-04): Notes typing worked after the AXEnabled
attribute-unsupported compatibility fix. This establishes a successful user-run
Notes workflow. The formal native-harness result and TextEdit acceptance remain
pending independent confirmation.

## 2026-10-12 reported command repairs

Plain named app requests bind one exact installed name/alias. Notes creation, TextEdit new-document creation and Finder search constrain executable operation/target heads to their app while preserving the full installed catalog locally. Explicit observed control-and-type commands stay in their foreground app. SemanticMenu carries the scope through target pruning and validates it independently; Jev still selects each effect. Plain opening of an already-active app can verify STOP with fresh foreground identity. App quitting and Terminal new-window creation have no executor and cannot substitute generic presses or activation. Existing text insertion and web navigation implementations are unchanged.

The user confirmed web/search/typing/copy, standalone Notes creation, TextEdit create/type, Music play and spoken stop. Reported repair artifacts exercise production routing with synthetic native services; live Jev progression is separate from native acceptance. This tool host remains AX-untrusted. Music playlist selection and Helium/other-browser tab support are deferred.

## Subsequent native discovery feedback

The user's later logs show new-note creation verified by selection identity, followed by a focused writable body with unreadable value and no TYPE_TEXT choice; Finder had no query/Find target after focus, and Safari scrolling had no interaction targets. These logs establish missing capabilities, not successful repaired native behavior. New E2E scenarios replay nil/attributed/range text, known menu/application-root optional AXSubrole failures and absent Safari focus. Only those structural roles permit generic subrole failure; secure/protected/parent/role errors and text subrole failures retain strict rejection. Protected checks precede all text reads.

When a verified created note's focused body lacks readable text/caret, runtime observes it for up to three seconds before another Jev call. Keep its app/window/editor/selection identity pinned and stop on drift, cancellation or timeout; do not repeat creation. An unfocused body still follows the separate FOCUS_FIELD step. Readiness waiting cannot change or normalize the dictated payload.

The user's maximize preference is full screen. Minimize/fullscreen use readable writable AXMinimized/AXFullScreen on one observed window and verify that original window's resulting state through AXWindows. Named commands may focus a running exact app before the window effect; they cannot target another app's window or launch an unrequested app. Native window attributes are permitted in Terminal/iTerm while keyboard/menu input and submission remain blocked. Ordered explicit window tails follow one verified insertion; quoted command words remain literal.

Reports add bounded value error/type/source, ancestry failure roles, workflow/query discovery and interaction/window capability checks. Document/clipboard text and native handles remain local. Pasteboard text is captured only for explicitly requested paste. Tests write native-*-e2e.json and opt-in live-native-discovery-e2e.json with synthetic native services; tool-host AX trust remains false, so actual application acceptance remains unverified.

NativeText.application is shared by discovery, dispatch guards and verification.
If a PID-created application's role or focused-window read reports InvalidUIElement,
it may use the system-focused application only after AXUIElementGetPid matches the
requested PID and AXRole is AXApplication. Trust and foreground guards still apply;
other errors do not authorize recovery. Bounded application_resolution diagnostics
record source and AX error codes without native handles. No private AX flags are set.

Finder standard AXScrollBar positions use normalized 0–1 only when both min/max
attributes are unsupported, as with Safari; other errors and invalid values still
reject the target. Bounded scrollbar_checks report attribute errors, value type,
range source and write capability without values. Nontext interaction goals cannot
offer FOCUS_FIELD. Missing scroll areas/ranges remain unsupported; movement and
boundary verification are unchanged. Native-control E2E artifacts replay invalid
application references, windowless Finder search, normalized bars and unavailable
scroll targets through the production runtime with synthetic native effects.
