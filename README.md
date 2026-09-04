# Distil Yourself

Distil Yourself is a privacy-conscious, resumable Skill Factory for turning a person's documents, agent sessions, and explicit judgments into small, behaviorally testable agent skills.

The approved design now has a runnable Foundation milestone: a guarded workflow state model, a closed-policy domain-draft validator, a small JSON CLI, and contract/evaluation fixtures for the meta-skill.

## Design

The canonical specification is [Knowledge Distiller Design](docs/specs/knowledge-distiller-design.md).

The proposed workflow:

1. discover candidate capabilities from explicitly authorized sources;
2. distill one repeatable capability at a time;
3. resolve only behavior-changing conflicts with the user;
4. compile a non-executable domain-skill draft;
5. validate it with historical, boundary, trigger, and safety evaluations;
6. require explicit approval before export or lifecycle updates.

Initial source adapters are planned for Lark documents and Codex, Claude Code, and Trae sessions.

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

Simulate a guarded workflow transition:

```bash
python3 knowledge-distiller/scripts/kd.py transition \
  --state '{"phase":"init","epoch":0}' \
  --event start-discover \
  --facts '{"has_seed":true}'
```

Successful commands emit structured JSON on stdout. Invalid input exits with code `2`; a rejected artifact or transition exits with code `3` and emits structured JSON on stderr.

## Foundation limits

This milestone does not implement network or local-session source adapters, the trusted request broker, parser sandbox, persistent event log/checkpoints, sealed evaluator, signed approval subjects, export/purge brokers, installation, or publication. Passing local validation authorizes none of those operations.

## Repository status

- Design: approved
- Implementation: deterministic Foundation milestone
- CI and release process: not defined
- License: not yet selected
