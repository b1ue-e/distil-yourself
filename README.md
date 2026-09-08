# Distil Yourself

Distil Yourself is a privacy-conscious, resumable Skill Factory for turning a person's documents, agent sessions, and explicit judgments into small, behaviorally testable agent skills.

The approved design now has a runnable local core: guarded workflow checkpoints, authorization validation, dependency-injected Lark and Codex ingestion, a strict evidence/claim model, deterministic capability scoring, one-question selection, explicit adjudication, and a non-installing domain-skill compiler.

## Design

The canonical specification is [Knowledge Distiller Design](docs/specs/knowledge-distiller-design.md).

The proposed workflow:

1. discover candidate capabilities from explicitly authorized sources;
2. distill one repeatable capability at a time;
3. resolve only behavior-changing conflicts with the user;
4. compile a non-executable domain-skill draft;
5. validate it with historical, boundary, trigger, and safety evaluations;
6. require explicit approval before export or lifecycle updates.

The [adapter compatibility matrix](knowledge-distiller/references/adapter-compatibility.md) and [canonical adapter contract](knowledge-distiller/references/adapter-contract.md) define the current boundary: the canonical adapter contract and synthetic conformance harness exist. One exact Lark raw-content normalizer tuple and one exact Codex rollout adapter tuple are available. Trusted, dependency-injected dual-source ingestion exists, but default production source runtimes do not.

## V1 boundaries

V1 does not silently scan accessible data, generate or execute domain scripts, install skills, publish artifacts, or treat account access as permission to ingest content. Private evidence, sealed evaluations, and exportable skill artifacts remain separated. Automatic discovery, the production Lark runtime, production Codex runtime, Claude Code adapter, Trae adapter, sealed evaluation, approval signatures, export, installation, publication, and purge are unavailable.

## Foundation usage

The implementation supports Python 3.9+ and uses only the standard library. Run the test suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Validate a generated domain-skill draft:

```bash
python3 knowledge-distiller/scripts/kd.py validate-draft /absolute/path/to/domain-skill
```

The current compiler emits a fixed two-file bundle and calls the validator with an empty asset allowlist, so it rejects every asset. The CLI has no caller-controlled asset allowlist.

Validate a candidate canonical event graph against the listed synthetic adapter tuple and explicit trust anchors:

```bash
python3 knowledge-distiller/scripts/kd.py validate-event-graph /absolute/path/to/graph.json --expected-owner-id OWNER_ID --expected-source-snapshot-id sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Successful event-graph validation proves only that the graph conforms to an allowlisted exact tuple and the canonical contract. It does not authorize a source read, native tool invocation, directory enumeration, ingestion, or any future access. Native readiness is defined separately by the compatibility gate.

Simulate a guarded workflow transition:

```bash
python3 knowledge-distiller/scripts/kd.py transition \
  --state '{"phase":"init","epoch":0}' \
  --event start-discover \
  --facts '{"has_seed":true}'
```

Successful commands emit structured JSON on stdout. Input-boundary failures,
including encoding, JSON, resource-limit, and unsafe-source errors, exit with
code `2`. Duplicate JSON keys, canonical event-graph contract rejections,
rejected artifacts, and rejected transitions exit with code `3` and emit
structured JSON on stderr.

Create, advance, inspect, or recover a durable local task:

```bash
python3 knowledge-distiller/scripts/kd.py task-init /absolute/path/to/task-workspace
python3 knowledge-distiller/scripts/kd.py task-transition /absolute/path/to/task-workspace \
  --event start-discover \
  --facts '{"has_seed":true}'
python3 knowledge-distiller/scripts/kd.py task-inspect /absolute/path/to/task-workspace
python3 knowledge-distiller/scripts/kd.py task-inspect /absolute/path/to/task-workspace --recover
```

Task directories and regular files use `0700` and `0600` modes. The framed journal verifies CRC32C, sequence, fencing epoch, payload digest, and a SHA-256 record chain. State updates use PREPARE → immutable generation → COMMIT → atomic pointer replacement. Inspection is read-only; `--recover` takes the writer lock and repairs only the cases authorized by the design.

Checkpoint facts are a closed set of booleans and one phase enum. Never place source text, secrets, locators, or free-form notes in them. Persisting a state transition grants no source access and authorizes no external mutation.

The following lines document the embedded API boundary syntax only. They are not directly executable for real ingestion without a production host runtime:

```bash
python3 knowledge-distiller/scripts/kd.py ingest-source /absolute/path/to/task-workspace /absolute/path/to/lark-request.json
python3 knowledge-distiller/scripts/kd.py ingest-source /absolute/path/to/task-workspace /absolute/path/to/codex-request.json
python3 knowledge-distiller/scripts/kd.py validate-knowledge-packet /absolute/path/to/knowledge-packet.json
python3 knowledge-distiller/scripts/kd.py next-critical-question /absolute/path/to/knowledge-packet.json
python3 knowledge-distiller/scripts/kd.py adjudicate-knowledge-packet /absolute/path/to/task-workspace /absolute/path/to/knowledge-packet.json --transaction-id DECISION_ID --expected-generation-id GENERATION_ID
python3 knowledge-distiller/scripts/kd.py compile-capability /absolute/path/to/task-workspace /absolute/path/to/knowledge-packet.json --transaction-id COMPILE_ID --expected-generation-id GENERATION_ID
```

Each ingestion request is private and covers one source selector and pinned revision or closed session range. Both ContentGrant and AuthorityAttestation are validated before every source read. Standalone `kd.py ingest-source` returns `ingestion-runtime-unavailable` before reading the request file. Claims retain the chain `source snapshot → native evidence → redacted span → ContentGrant → AuthorityAttestation`. Ask at most one critical question at a time; a lower-impact uncertainty does not block progress.

Before adjudication, the current user must explicitly confirm the selected capability and every claim that will be published. The CLI validates the `current-user` marker structurally; it does not authenticate the current user. Do not generate or infer user confirmation. Compilation accepts only the exact adjudicated packet bytes, validates and stores a private draft plus manifest, and does not install or export.

Compilation revalidates persisted provenance and recorded grant digest/time bounds; live revocation and issuer authentication remain broker responsibilities. Bounded evidence review is unavailable; an explicit request does not create a supported evidence-output path.

## Foundation limits

This milestone does not implement automatic discovery, a production Lark runtime, a production Codex runtime, the Claude Code adapter, the Trae adapter, a parser sandbox, renewable/distributed leases, sealed evaluation, approval signatures, export/purge brokers, installation, or publication. Passing local validation or writing a checkpoint authorizes none of those operations.

## Repository status

- Design: approved
- Implementation: deterministic dual-source evidence-to-private-draft core
- CI and release process: not defined
- License: not yet selected
