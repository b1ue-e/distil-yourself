# Distil Yourself

Distil Yourself is a privacy-conscious, resumable Skill Factory for turning a person's documents, agent sessions, and explicit judgments into small, behaviorally testable agent skills.

The project is currently at the approved-design stage. Runtime implementation has not started.

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

## Repository status

- Design: approved for implementation planning
- Implementation: not started
- CI and release process: not defined
- License: not yet selected
