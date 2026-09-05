# Distil Yourself

Distil Yourself is a privacy-conscious, resumable Skill Factory for turning a person's documents, agent sessions, and explicit judgments into small, behaviorally testable agent skills.

The approved design now has a runnable local foundation: a guarded workflow state model, crash-consistent state checkpoints, a closed-policy domain-draft validator, a JSON CLI, and contract/evaluation fixtures for the meta-skill.

## Design

The canonical specification is [Knowledge Distiller Design](docs/specs/knowledge-distiller-design.md).

The proposed workflow:

1. discover candidate capabilities from explicitly authorized sources;
2. distill one repeatable capability at a time;
3. resolve only behavior-changing conflicts with the user;
4. compile a non-executable domain-skill draft;
5. validate it with historical, boundary, trigger, and safety evaluations;
6. require explicit approval before export or lifecycle updates.

The [adapter compatibility matrix](knowledge-distiller/references/adapter-compatibility.md) and [canonical adapter contract](knowledge-distiller/references/adapter-contract.md) define the current boundary: the canonical adapter contract and synthetic conformance harness exist; Lark, Codex, Claude Code, and Trae native adapters remain blocked and unimplemented.

## V1 boundaries

V1 does not silently scan accessible data, generate or execute domain scripts, install skills, publish artifacts, or treat account access as permission to ingest content. Private evidence, sealed evaluations, and exportable skill artifacts remain separated.

## Foundation usage

The implementation supports Python 3.9+ and uses only the standard library. Run the test suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Validate a generated domain-skill draft:

```bash
python3 knowledge-distiller/scripts/kd.py validate-draft /absolute/path/to/domain-skill
```

The Foundation CLI has no caller-controlled asset allowlist, so it rejects every asset. A future compiler may call the library validator with its own reviewed, compiler-controlled template digests.

Validate a candidate canonical event graph against the listed synthetic adapter tuple and explicit trust anchors:

```bash
python3 knowledge-distiller/scripts/kd.py validate-event-graph /absolute/path/to/graph.json --expected-owner-id OWNER_ID --expected-source-snapshot-id sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Successful event-graph validation proves only that the graph conforms to the listed synthetic tuple and canonical contract. It does not authorize a source read, does not authorize native tool invocation, and does not make any native adapter supported.

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

## Foundation limits

This milestone does not implement the trusted request broker, parser sandbox, renewable/distributed leases, sealed evaluator, signed approval subjects, export/purge brokers, installation, or publication. Passing local validation or writing a checkpoint authorizes none of those operations.

## Repository status

- Design: approved
- Implementation: deterministic Foundation plus durable local checkpoints
- CI and release process: not defined
- License: not yet selected
