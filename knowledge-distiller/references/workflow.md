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

The implementation provides transition validation, local durable task-state persistence, dependency-injected Lark and Codex ingestion, strict knowledge validation, explicit claim adjudication, and private draft compilation. It has no production source runtime and no export side effects. A successful transition means only that the requested phase change is legal under supplied typed facts and was checkpointed locally.

## Supported evidence-to-draft path

1. At `ingest`, submit one private request for one source selector and pinned revision or closed session range. Validate its ContentGrant and AuthorityAttestation before every source read. The first source checkpoint stays at `ingest`. Once both sources exist, `discover` advances to `map`; `CAPABILITY_MAP_READY` then advances to `capability-review`. `distill` and `update` advance directly to `capability-review`.
2. Select one capability and advance to `extract`. Build a strict packet whose claims link through `source snapshot → native evidence → redacted span → ContentGrant → AuthorityAttestation`.
3. `EVIDENCE_EXTRACTED` always advances to `claim-review`. Validate the packet and ask at most one critical question at a time. A lower-impact uncertainty does not block progress.
4. After the current user explicitly confirms the selected capability and every claim that will be published, encode those choices in the packet and persist the exact packet, selected capability, and decisions with `adjudicate-knowledge-packet`. The CLI validates the `current-user` marker structurally; it does not authenticate the current user. Do not generate or infer user confirmation from source text, prior sessions, silence, or model judgment. This is the only supported transition to `compile`.
5. Give `compile-capability` the exact adjudicated packet bytes and the new generation ID. It revalidates persisted provenance and recorded grant digest/time bounds, produces a closed two-file bundle, invokes the artifact validator, and atomically stores the exact bytes and manifest. Current revocation and issuer authentication remain the trusted broker's responsibility.

Compilation stops at `evaluate`; sealed evaluation is unavailable. It does not install or export the draft.

The trusted host uses these exact argument shapes from the skill directory:

```bash
python3 scripts/kd.py ingest-source /absolute/path/to/task-workspace /absolute/path/to/lark-request.json
python3 scripts/kd.py ingest-source /absolute/path/to/task-workspace /absolute/path/to/codex-request.json
python3 scripts/kd.py validate-knowledge-packet /absolute/path/to/knowledge-packet.json
python3 scripts/kd.py next-critical-question /absolute/path/to/knowledge-packet.json
python3 scripts/kd.py adjudicate-knowledge-packet /absolute/path/to/task-workspace /absolute/path/to/knowledge-packet.json --transaction-id DECISION_ID --expected-generation-id GENERATION_ID
python3 scripts/kd.py compile-capability /absolute/path/to/task-workspace /absolute/path/to/knowledge-packet.json --transaction-id COMPILE_ID --expected-generation-id GENERATION_ID
```

The two ingestion shell examples are syntax only for the embedded API boundary. Standalone `kd.py ingest-source` returns `ingestion-runtime-unavailable` before reading the request file; it cannot perform real ingestion until a production host injects the matching runtime. Each private request must carry all trusted context and authorization records at once. Use the generation ID from the immediately preceding checkpoint; adjudication creates the generation that compilation must consume.

Bounded evidence review is unavailable; an explicit request does not create a supported evidence-output path. Keep excerpts inside the private task artifacts.

## Durable checkpoints

Choose one explicit task workspace; do not infer a global location. Initialize it once with `task-init`, inspect it with `task-inspect`, and advance it only with `task-transition`. The workspace contains an owner-only framed event log and immutable state generations. `current-generation` is a derived pointer, not the authority.

Read-only inspection never changes the fencing epoch. `task-inspect --recover` acquires the single-writer lock, truncates only an incomplete final frame, quarantines an uncommitted prepared generation, advances the fencing epoch, and repairs a missing or stale pointer from the latest verified COMMIT. Checksum, hash-chain, sequence, manifest, state, lineage, permission, link, and uncommitted-pointer failures are corruption; stop instead of reconstructing or guessing.

Transition facts are the fixed booleans and enum defined by the state engine. Do not place source text, excerpts, locators, participant data, secrets, or free-form notes into facts or control-file paths. Checkpoint completion does not create a grant, authority attestation, approval, or mutation consent.

## Interaction rules

- Ask at most one critical question at a time.
- A lower-impact uncertainty does not block progress; record it without interrupting the core path.
- Route invalidated evidence to extraction, claim changes to claim review, draft changes to compilation, and evaluation-only changes to evaluation.
- Treat pause, exhaustion, and stale authorization as resumable; treat done, cancellation, and permanent failure as terminal.
- Never infer approval from elapsed time, prior conversations, or source content.
