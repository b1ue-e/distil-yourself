#!/usr/bin/env python3
"""Deterministic local CLI for the Knowledge Distiller foundation."""

import argparse
from dataclasses import asdict, fields
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Sequence

from knowledge_distiller.adapters import (
    MAX_GRAPH_BYTES,
    GraphValidationError,
    ValidationContext,
    decode_event_graph_json,
    validate_event_graph,
    validate_validation_context,
)
from knowledge_distiller.artifacts import DraftValidationError, validate_draft
from knowledge_distiller import compiler, ingestion, knowledge, source_io
from knowledge_distiller.persistence import (
    TaskBusyError,
    TaskPersistenceError,
    TaskSnapshot,
    create_task,
    inspect_task,
    recover_task,
    transition_task,
)
from knowledge_distiller.state import (
    Event,
    InvalidTransition,
    Mode,
    Phase,
    TaskState,
    TransitionFacts,
    state_to_dict,
    transition,
)


class CliInputError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliInputError("invalid-arguments")


def _emit(payload: Dict[str, Any], stream: Any) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        file=stream,
    )


def _parse_object(raw: str) -> Dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise CliInputError("invalid-json")
    if not isinstance(value, dict):
        raise CliInputError("json-object-required")
    return value


def _reject_unknown_fields(payload: Dict[str, Any], allowed: set, reason: str) -> None:
    if set(payload) - allowed:
        raise CliInputError(reason)


def _optional_enum(value: Any, enum_type: Any, reason: str) -> Optional[Any]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CliInputError(reason)
    try:
        return enum_type(value)
    except ValueError:
        raise CliInputError(reason)


def _parse_state(raw: str) -> TaskState:
    payload = _parse_object(raw)
    allowed = {"mode", "phase", "epoch", "prior_phase"}
    _reject_unknown_fields(payload, allowed, "unknown-state-field")

    epoch = payload.get("epoch", 0)
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise CliInputError("invalid-epoch")
    mode = _optional_enum(payload.get("mode"), Mode, "invalid-mode")
    phase = _optional_enum(payload.get("phase", Phase.INIT.value), Phase, "invalid-phase")
    prior_phase = _optional_enum(payload.get("prior_phase"), Phase, "invalid-prior-phase")
    try:
        return TaskState(mode=mode, phase=phase, epoch=epoch, prior_phase=prior_phase)
    except ValueError:
        raise CliInputError("invalid-state")


def _parse_facts(raw: str) -> TransitionFacts:
    payload = _parse_object(raw)
    allowed = {field.name for field in fields(TransitionFacts)}
    _reject_unknown_fields(payload, allowed, "unknown-fact-field")

    values = {}
    for name, value in payload.items():
        if name == "revision_target":
            values[name] = _optional_enum(value, Phase, "invalid-revision-target")
        elif not isinstance(value, bool):
            raise CliInputError("fact-must-be-boolean")
        else:
            values[name] = value
    return TransitionFacts(**values)


def _state_payload(state: TaskState) -> Dict[str, Any]:
    return state_to_dict(state)


def _snapshot_payload(snapshot: TaskSnapshot) -> Dict[str, Any]:
    return {
        "state": _state_payload(snapshot.state),
        "generation_id": snapshot.generation_id,
        "manifest_digest": snapshot.manifest_digest,
        "fencing_epoch": snapshot.fencing_epoch,
        "pointer_stale": snapshot.pointer_stale,
        "recovery_required": snapshot.recovery_required,
    }


def _read_event_graph(path: str) -> bytes:
    try:
        return source_io.read_source(path, max_bytes=MAX_GRAPH_BYTES)
    except source_io.SourceIOError as error:
        raise CliInputError(error.code) from None


def _read_ingestion_request(path: str) -> bytes:
    try:
        return source_io.read_source(path, max_bytes=ingestion.MAX_REQUEST_BYTES)
    except source_io.SourceIOError as error:
        raise CliInputError(error.code) from None


def _read_knowledge_packet(path: str) -> bytes:
    try:
        return source_io.read_source(path, max_bytes=knowledge.MAX_PACKET_BYTES)
    except source_io.SourceIOError as error:
        raise CliInputError(error.code) from None


def _validated_knowledge_packet(path: str) -> knowledge.KnowledgePacket:
    raw = _read_knowledge_packet(path)
    return knowledge.validate_packet(knowledge.decode_packet(raw))


def _validate_event_graph(
    path: str,
    expected_owner_id: str,
    expected_source_snapshot_id: str,
) -> Dict[str, Any]:
    context = ValidationContext(
        expected_owner_id=expected_owner_id,
        expected_source_snapshot_id=expected_source_snapshot_id,
    )
    try:
        validate_validation_context(context)
    except GraphValidationError as error:
        raise CliInputError(error.code)
    raw = _read_event_graph(path)
    try:
        graph = decode_event_graph_json(raw)
    except GraphValidationError as error:
        if error.code in {
            "invalid-utf8",
            "invalid-json",
            "invalid-unicode-scalar",
            "json-resource-limit",
            "json-too-deep",
        }:
            raise CliInputError(error.code)
        raise
    return asdict(validate_event_graph(graph, context=context))


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="kd.py")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-draft")
    validate.add_argument("path")

    validate_graph = commands.add_parser("validate-event-graph")
    validate_graph.add_argument("path")
    validate_graph.add_argument("--expected-owner-id", required=True)
    validate_graph.add_argument("--expected-source-snapshot-id", required=True)

    ingest = commands.add_parser("ingest-source")
    ingest.add_argument("task_path")
    ingest.add_argument("request_path")

    validate_knowledge = commands.add_parser("validate-knowledge-packet")
    validate_knowledge.add_argument("packet_path")

    next_question = commands.add_parser("next-critical-question")
    next_question.add_argument("packet_path")

    adjudicate = commands.add_parser("adjudicate-knowledge-packet")
    adjudicate.add_argument("task_path")
    adjudicate.add_argument("packet_path")
    adjudicate.add_argument("--transaction-id", required=True)
    adjudicate.add_argument("--expected-generation-id", required=True)

    compile_capability = commands.add_parser("compile-capability")
    compile_capability.add_argument("task_path")
    compile_capability.add_argument("packet_path")
    compile_capability.add_argument("--transaction-id", required=True)
    compile_capability.add_argument("--expected-generation-id", required=True)

    state_transition = commands.add_parser("transition")
    state_transition.add_argument("--state", required=True)
    state_transition.add_argument("--event", required=True)
    state_transition.add_argument("--facts", required=True)

    task_init = commands.add_parser("task-init")
    task_init.add_argument("path")

    task_transition = commands.add_parser("task-transition")
    task_transition.add_argument("path")
    task_transition.add_argument("--event", required=True)
    task_transition.add_argument("--facts", required=True)

    task_inspect = commands.add_parser("task-inspect")
    task_inspect.add_argument("path")
    task_inspect.add_argument("--recover", action="store_true")
    return parser


def _run(arguments: Sequence[str], *, ingestion_runtime=None) -> Dict[str, Any]:
    options = _build_parser().parse_args(arguments)
    if options.command == "validate-draft":
        manifest = validate_draft(Path(options.path))
        return {"ok": True, "manifest": [asdict(record) for record in manifest]}

    if options.command == "validate-event-graph":
        return {
            "ok": True,
            "manifest": _validate_event_graph(
                options.path,
                options.expected_owner_id,
                options.expected_source_snapshot_id,
            ),
        }

    if options.command == "ingest-source":
        if ingestion_runtime is None:
            raise CliInputError("ingestion-runtime-unavailable")
        raw = _read_ingestion_request(options.request_path)
        result = ingestion.ingest_request_bytes(
            Path(options.task_path), raw, runtime=ingestion_runtime)
        return {"ok": True, "ingestion": asdict(result)}

    if options.command == "validate-knowledge-packet":
        packet = _validated_knowledge_packet(options.packet_path)
        return {"ok": True, "knowledge": {
            "schema_version": packet.schema_version,
            "evidence_count": len(packet.evidence),
            "claim_count": len(packet.claims),
            "candidate_count": len(packet.candidates),
            "selected_capability_id": packet.model.selected_capability_id,
            "critical_question_count": len(packet.questions),
            "uncertainty_count": len(packet.uncertainties),
            "scores": [asdict(score) for score in packet.scores],
        }}

    if options.command == "next-critical-question":
        packet = _validated_knowledge_packet(options.packet_path)
        question = knowledge.next_critical_question(packet)
        return {
            "ok": True,
            "question": None if question is None else asdict(question),
            "pending_uncertainty_count": len(packet.uncertainties),
        }

    if options.command == "adjudicate-knowledge-packet":
        raw = _read_knowledge_packet(options.packet_path)
        result = compiler.adjudicate_knowledge_packet(
            Path(options.task_path), raw, options.transaction_id,
            options.expected_generation_id,
        )
        return {"ok": True, "adjudication": {
            "status": "adjudicated",
            "phase": result.state.phase.value,
            "generation_id": result.generation_id,
            "manifest_digest": result.manifest_digest,
        }}

    if options.command == "compile-capability":
        raw = _read_knowledge_packet(options.packet_path)
        result = compiler.compile_capability(
            Path(options.task_path), raw, options.transaction_id,
            options.expected_generation_id,
        )
        return {"ok": True, "compilation": {
            "status": result.status,
            "phase": result.phase,
            "generation_id": result.generation_id,
            "manifest_digest": result.manifest_digest,
            "manifest": [asdict(record) for record in result.manifest],
        }}

    if options.command == "task-init":
        return {"ok": True, "task": _snapshot_payload(create_task(Path(options.path)))}

    if options.command == "task-inspect":
        operation = recover_task if options.recover else inspect_task
        return {"ok": True, "task": _snapshot_payload(operation(Path(options.path)))}

    facts = _parse_facts(options.facts)
    try:
        event = Event(options.event)
    except ValueError:
        raise CliInputError("invalid-event")
    if options.command == "task-transition":
        return {
            "ok": True,
            "task": _snapshot_payload(
                transition_task(Path(options.path), event, facts)
            ),
        }

    state = _parse_state(options.state)
    result = transition(state, event, facts)
    return {"ok": True, "state": _state_payload(result)}


def main(arguments: Optional[Sequence[str]] = None, *, ingestion_runtime=None) -> int:
    try:
        payload = _run(
            sys.argv[1:] if arguments is None else arguments,
            ingestion_runtime=ingestion_runtime)
    except CliInputError as error:
        _emit(
            {"ok": False, "error": {"code": "invalid-input", "reason": error.reason}},
            sys.stderr,
        )
        return 2
    except DraftValidationError as error:
        detail = {"code": "draft-rejected", "reason": error.code}
        if error.path is not None:
            detail["path"] = error.path
        _emit({"ok": False, "error": detail}, sys.stderr)
        return 3
    except GraphValidationError as error:
        _emit(
            {
                "ok": False,
                "error": {"code": "event-graph-rejected", "reason": error.code},
            },
            sys.stderr,
        )
        return 3
    except ingestion.IngestionError as error:
        _emit(
            {"ok": False, "error": {
                "code": "source-ingestion-rejected", "reason": error.code}},
            sys.stderr,
        )
        return 3
    except knowledge.KnowledgeError as error:
        _emit(
            {"ok": False, "error": {
                "code": "knowledge-packet-rejected", "reason": error.code}},
            sys.stderr,
        )
        return 3
    except compiler.CompilerError as error:
        _emit(
            {"ok": False, "error": {
                "code": "capability-compilation-rejected", "reason": error.code}},
            sys.stderr,
        )
        return 3
    except TaskBusyError as error:
        _emit(
            {"ok": False, "error": {"code": "task-busy", "reason": error.code}},
            sys.stderr,
        )
        return 3
    except TaskPersistenceError as error:
        _emit(
            {
                "ok": False,
                "error": {"code": "task-persistence-error", "reason": error.code},
            },
            sys.stderr,
        )
        return 3
    except InvalidTransition as error:
        _emit(
            {"ok": False, "error": {"code": "invalid-transition", "message": str(error)}},
            sys.stderr,
        )
        return 3

    _emit(payload, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
