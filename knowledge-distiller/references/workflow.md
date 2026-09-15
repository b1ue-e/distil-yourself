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

The implementation provides transition validation, local durable task-state persistence, dependency-injected Lark and Codex ingestion, one narrow standalone local Codex runtime, one production-disabled synthetic Lark command boundary, strict knowledge validation, explicit claim adjudication, and private draft compilation. It has no general production source runtime and no export side effects. A successful transition means only that the requested phase change is legal under supplied typed facts and was checkpointed locally.

## Supported evidence-to-draft path

1. At `ingest`, submit one private request for one source selector and pinned revision or closed session range. Validate its ContentGrant and AuthorityAttestation before every source read. The first source checkpoint stays at `ingest`. Once both sources exist, `discover` advances to `map`; `CAPABILITY_MAP_READY` then advances to `capability-review`. `distill` and `update` advance directly to `capability-review`.
2. Select one capability and advance to `extract`. Build a strict packet whose claims link through `source snapshot → native evidence → redacted span → ContentGrant → AuthorityAttestation`.
3. `EVIDENCE_EXTRACTED` always advances to `claim-review`. Validate the packet and ask at most one critical question at a time. A lower-impact uncertainty does not block progress.
4. After the current user explicitly confirms the selected capability and every claim that will be published, encode those choices in the packet and persist the exact packet, selected capability, and decisions with `adjudicate-knowledge-packet`. The CLI validates the `current-user` marker structurally; it does not authenticate the current user. Do not generate or infer user confirmation from source text, prior sessions, silence, or model judgment. This is the only supported transition to `compile`.
5. Give `compile-capability` the exact adjudicated packet bytes and the new generation ID. It revalidates persisted provenance and recorded grant digest/time bounds, produces a closed two-file bundle, invokes the artifact validator, and atomically stores the exact bytes and manifest. Current revocation and issuer authentication remain the trusted broker's responsibility.

Compilation stops at `evaluate`; sealed evaluation is unavailable. It does not install or export the draft.

## Standalone synthetic Lark ingestion

From the repository root, the canonical command is:

```bash
python3 knowledge-distiller/scripts/kd.py ingest-lark-document TASK_PATH REQUEST.json --redaction-key-file KEY --allow-live-read
```

`--allow-live-read` records explicit intent for this invocation. The checked-in profile is `synthetic-only`, so every production invocation returns the code-only error `lark-live-disabled` before resolving or running an executable and before network access. The flag does not authorize a real Lark read. Tests can exercise the complete runtime only through a private synthetic gate that is unavailable to callers.

`REQUEST.json` is a closed JSON object with exactly these five fields; duplicate, unknown, or missing fields are rejected:

| Field | Contract |
| --- | --- |
| `schema_version` | Exactly `knowledge-distiller.local-lark-ingestion-request/v1` |
| `transaction_id` | `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` |
| `expected_generation_id` | `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` and equal to the current task generation |
| `document_selector` | One exact Docx token or canonical Docx URL as defined below |
| `derived_processing_until` | Built-in integer Unix time strictly later than runtime time and no more than 90 days later; booleans and values outside `0..2^63-1` are rejected |

A synthetic, structurally valid example is:

```json
{
  "schema_version": "knowledge-distiller.local-lark-ingestion-request/v1",
  "transaction_id": "ingest-lark-1",
  "expected_generation_id": "g-current",
  "document_selector": "doxcn1234567890AbCdEfGhIjKl",
  "derived_processing_until": 4102444800
}
```

The displayed token is a test-only synthetic value, not a real document locator. A selector is either an exact 27-character ASCII alphanumeric token or a literal lower-case `https` URL of the form `https://tenant.{larkoffice.com|larksuite.com|feishu.cn}/docx/TOKEN`. Each DNS label is 1–63 lower-case ASCII characters with no edge hyphen, the full host is at most 253 characters, and at least one tenant label is required. Wiki URLs are unsupported. Query, fragment, explicit port, userinfo, redirect, shortcut, percent encoding, trailing slash, non-Docx path, Unicode, and case-normalized equivalents are rejected; the URL is parsed locally and never followed.

After task-slot, key, deadline, and live-profile gates, the designed owner-only observational protocol is: verify `lark-cli auth status --json --verify`; observe exact token, Docx type, revision, and Drive `owner_id`; require the verified user `openId` to equal `owner_id`; acquire the task writer lease; reverify the same `openId`; read raw content; observe the same typed token/revision/owner tuple again; then normalize, redact, and atomically commit. Equality of the before/after `LarkObservation` proves only that no change was observed across the raw read. It is not an atomic or cryptographic snapshot, does not bind returned bytes to a revision, and does not prove replica consistency or continued freshness.

The raw-content adapter still emits one unresolved, claim-ineligible synthetic block because this endpoint has no native block graph or per-block author. Only opaque selector/principal/account digests and the existing private authorization, evidence, and provenance records may persist; raw selector tokens, `openId`, `owner_id`, titles, content, redaction keys, and upstream diagnostics must not appear in public output, journals, or control records. See [authorization.md](authorization.md) for the owner evidence and [adapter-compatibility.md](adapter-compatibility.md) for the pinned profile and transport limits.

## Standalone local Codex ingestion

From the repository root, the exact supported command is:

```bash
python3 knowledge-distiller/scripts/kd.py ingest-codex-session \
  /absolute/path/to/task-workspace \
  /absolute/path/to/private-codex-request.json \
  --redaction-key-file /absolute/path/to/redaction.key
```

The private request uses `knowledge-distiller.local-codex-ingestion-request/v1` and selects one explicit session file and exact byte-0 prefix. Only `Codex / 1.0.0 / 0.153.0 / rollout-jsonl-v1` is supported. The runtime derives identity from the effective UID; the opened source must have the same UID. The key must be a stable, single-link regular file with exact `0600` mode and contain 32–64 raw bytes. Key generation and lifecycle are outside this milestone.

The request is intentionally closed: the caller never supplies UID, owner, issuer, grant, attestation, adapter version, or native schema. See [authorization.md](authorization.md) for the read decision and private binding semantics. Discovery, sibling reads, arbitrary versions, Lark standalone access, automatic extraction, evaluation, export, installation, and publication are unavailable. The separate synthetic Lark command remains production-disabled.

The trusted host uses these exact argument shapes from the skill directory:

```bash
python3 scripts/kd.py ingest-source /absolute/path/to/task-workspace /absolute/path/to/lark-request.json
python3 scripts/kd.py ingest-source /absolute/path/to/task-workspace /absolute/path/to/codex-request.json
python3 scripts/kd.py validate-knowledge-packet /absolute/path/to/knowledge-packet.json
python3 scripts/kd.py next-critical-question /absolute/path/to/knowledge-packet.json
python3 scripts/kd.py adjudicate-knowledge-packet /absolute/path/to/task-workspace /absolute/path/to/knowledge-packet.json --transaction-id DECISION_ID --expected-generation-id GENERATION_ID
python3 scripts/kd.py compile-capability /absolute/path/to/task-workspace /absolute/path/to/knowledge-packet.json --transaction-id COMPILE_ID --expected-generation-id GENERATION_ID
```

The two `ingest-source` shell examples are syntax only for the embedded API boundary. Standalone `kd.py ingest-source` returns `ingestion-runtime-unavailable` before reading the request file; it cannot perform real ingestion until a host injects the matching runtime. Each private request must carry all trusted context and authorization records at once. Use the generation ID from the immediately preceding checkpoint; adjudication creates the generation that compilation must consume.

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
