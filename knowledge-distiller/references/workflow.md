# Workflow Reference

Use the state engine as the source of truth for legal phase changes. Conversation text never advances state by itself.

## Modes

| Mode | Entry requirement | Purpose |
|---|---|---|
| discover | Theme or seed | Build a capability map and select one capability |
| distill | Named capability | Extract and compile one capability |
| update | Approved parent version | Produce a provenance-linked revision |

Resume is an operation on a suspended state, not a fourth mode. It revalidates inputs and continues the recorded mode.

## Core phases

`init → scout → source-review → ingest → map/capability-review → extract → claim-review → compile → evaluate → version-review → export-review`

The engine also represents `revision-review`, `suspended`, `suspended-exhausted`, `auth-stale`, `done`, `done-approved`, `done-partial`, `cancelled`, and `failed-permanent`.

The foundation implements transition validation and local durable task-state persistence, but no source or export side effects. A successful transition means only that the requested phase change is legal under supplied typed facts and was checkpointed locally.

## Durable checkpoints

Choose one explicit task workspace; do not infer a global location. Initialize it once with `task-init`, inspect it with `task-inspect`, and advance it only with `task-transition`. The workspace contains an owner-only framed event log and immutable state generations. `current-generation` is a derived pointer, not the authority.

Read-only inspection never changes the fencing epoch. `task-inspect --recover` acquires the single-writer lock, truncates only an incomplete final frame, quarantines an uncommitted prepared generation, advances the fencing epoch, and repairs a missing or stale pointer from the latest verified COMMIT. Checksum, hash-chain, sequence, manifest, state, lineage, permission, link, and uncommitted-pointer failures are corruption; stop instead of reconstructing or guessing.

Transition facts are the fixed booleans and enum defined by the state engine. Do not place source text, excerpts, locators, participant data, secrets, or free-form notes into facts or control-file paths. Checkpoint completion does not create a grant, authority attestation, approval, or mutation consent.

## Interaction rules

- Ask one blocking question at a time.
- Record a lower-impact uncertainty without interrupting progress.
- Route invalidated evidence to extraction, claim changes to claim review, draft changes to compilation, and evaluation-only changes to evaluation.
- Treat pause, exhaustion, and stale authorization as resumable; treat done, cancellation, and permanent failure as terminal.
- Never infer approval from elapsed time, prior conversations, or source content.
