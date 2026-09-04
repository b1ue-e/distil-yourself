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

The foundation implements transition validation but not persistence or external side effects. A successful transition means only that the requested phase change is legal under supplied facts.

## Interaction rules

- Ask one blocking question at a time.
- Record a lower-impact uncertainty without interrupting progress.
- Route invalidated evidence to extraction, claim changes to claim review, draft changes to compilation, and evaluation-only changes to evaluation.
- Treat pause, exhaustion, and stale authorization as resumable; treat done, cancellation, and permanent failure as terminal.
- Never infer approval from elapsed time, prior conversations, or source content.
