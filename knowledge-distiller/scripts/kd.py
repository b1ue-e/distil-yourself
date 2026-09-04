#!/usr/bin/env python3
"""Deterministic local CLI for the Knowledge Distiller foundation."""

import argparse
from dataclasses import asdict, fields
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Sequence

from knowledge_distiller.artifacts import DraftValidationError, validate_draft
from knowledge_distiller.state import (
    Event,
    InvalidTransition,
    Mode,
    Phase,
    TaskState,
    TransitionFacts,
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
    return {
        "mode": state.mode.value if state.mode is not None else None,
        "phase": state.phase.value,
        "epoch": state.epoch,
        "prior_phase": state.prior_phase.value if state.prior_phase is not None else None,
    }


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="kd.py")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-draft")
    validate.add_argument("path")

    state_transition = commands.add_parser("transition")
    state_transition.add_argument("--state", required=True)
    state_transition.add_argument("--event", required=True)
    state_transition.add_argument("--facts", required=True)
    return parser


def _run(arguments: Sequence[str]) -> Dict[str, Any]:
    options = _build_parser().parse_args(arguments)
    if options.command == "validate-draft":
        manifest = validate_draft(Path(options.path))
        return {"ok": True, "manifest": [asdict(record) for record in manifest]}

    state = _parse_state(options.state)
    facts = _parse_facts(options.facts)
    try:
        event = Event(options.event)
    except ValueError:
        raise CliInputError("invalid-event")
    result = transition(state, event, facts)
    return {"ok": True, "state": _state_payload(result)}


def main(arguments: Optional[Sequence[str]] = None) -> int:
    try:
        payload = _run(sys.argv[1:] if arguments is None else arguments)
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
