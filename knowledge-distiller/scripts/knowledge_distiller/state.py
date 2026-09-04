"""Pure guarded transitions for a Knowledge Distiller task."""

from dataclasses import dataclass, fields, replace
from enum import Enum
from typing import Optional


class Mode(str, Enum):
    DISCOVER = "discover"
    DISTILL = "distill"
    UPDATE = "update"


class Phase(str, Enum):
    INIT = "init"
    SCOUT = "scout"
    SOURCE_REVIEW = "source-review"
    INGEST = "ingest"
    MAP = "map"
    CAPABILITY_REVIEW = "capability-review"
    EXTRACT = "extract"
    CLAIM_REVIEW = "claim-review"
    COMPILE = "compile"
    EVALUATE = "evaluate"
    REVISION_REVIEW = "revision-review"
    VERSION_REVIEW = "version-review"
    EXPORT_REVIEW = "export-review"
    SUSPENDED = "suspended"
    SUSPENDED_EXHAUSTED = "suspended-exhausted"
    AUTH_STALE = "auth-stale"
    DONE = "done"
    DONE_APPROVED = "done-approved"
    DONE_PARTIAL = "done-partial"
    CANCELLED = "cancelled"
    FAILED_PERMANENT = "failed-permanent"


class Event(str, Enum):
    START_DISCOVER = "start-discover"
    START_DISTILL = "start-distill"
    START_UPDATE = "start-update"
    START_REPAIR = "start-repair"
    SCOUT_COMPLETED = "scout-completed"
    SOURCES_REJECTED = "sources-rejected"
    CONTENT_GRANTED = "content-granted"
    SOURCES_SNAPSHOTTED = "sources-snapshotted"
    CAPABILITY_MAP_READY = "capability-map-ready"
    CAPABILITY_SELECTED = "capability-selected"
    EVIDENCE_EXTRACTED = "evidence-extracted"
    CLAIMS_ADJUDICATED = "claims-adjudicated"
    DRAFT_COMPILED = "draft-compiled"
    EVALUATION_PASSED = "evaluation-passed"
    EVALUATION_FAILED = "evaluation-failed"
    REVISION_ACCEPTED = "revision-accepted"
    REVISION_REJECTED = "revision-rejected"
    VERSION_APPROVED = "version-approved"
    VERSION_REJECTED = "version-rejected"
    EXPORT_COMPLETED = "export-completed"
    EXPORT_DEFERRED = "export-deferred"
    PAUSE = "pause"
    RESUME = "resume"
    BUDGET_EXHAUSTED = "budget-exhausted"
    BUDGET_EXTENDED = "budget-extended"
    AUTH_INVALID = "auth-invalid"
    AUTH_RESTORED = "auth-restored"
    CANCEL = "cancel"
    FAIL_PERMANENTLY = "fail-permanently"


TERMINAL_PHASES = frozenset(
    {
        Phase.DONE,
        Phase.DONE_APPROVED,
        Phase.DONE_PARTIAL,
        Phase.CANCELLED,
        Phase.FAILED_PERMANENT,
    }
)

WORK_PHASES = frozenset(
    {
        Phase.SCOUT,
        Phase.SOURCE_REVIEW,
        Phase.INGEST,
        Phase.MAP,
        Phase.CAPABILITY_REVIEW,
        Phase.EXTRACT,
        Phase.CLAIM_REVIEW,
        Phase.COMPILE,
        Phase.EVALUATE,
        Phase.REVISION_REVIEW,
        Phase.VERSION_REVIEW,
        Phase.EXPORT_REVIEW,
    }
)
RESUMABLE_PHASES = frozenset(
    {Phase.SUSPENDED, Phase.SUSPENDED_EXHAUSTED, Phase.AUTH_STALE}
)
REVISION_TARGETS = frozenset(
    {Phase.EXTRACT, Phase.CLAIM_REVIEW, Phase.COMPILE, Phase.EVALUATE}
)


@dataclass(frozen=True)
class TaskState:
    mode: Optional[Mode] = None
    phase: Phase = Phase.INIT
    epoch: int = 0
    prior_phase: Optional[Phase] = None

    def __post_init__(self) -> None:
        if self.mode is not None and not isinstance(self.mode, Mode):
            raise ValueError("mode must be a Mode or None")
        if not isinstance(self.phase, Phase):
            raise ValueError("phase must be a Phase")
        if self.prior_phase is not None and not isinstance(self.prior_phase, Phase):
            raise ValueError("prior_phase must be a Phase or None")
        if type(self.epoch) is not int or self.epoch < 0:
            raise ValueError("epoch must be non-negative")
        if self.phase is Phase.INIT and self.mode is not None:
            raise ValueError("init state must not have a mode")
        if self.phase not in {Phase.INIT, Phase.CANCELLED, Phase.FAILED_PERMANENT}:
            if self.mode is None:
                raise ValueError(f"phase {self.phase.value} requires mode")
        if self.phase in {Phase.SCOUT, Phase.MAP} and self.mode is not Mode.DISCOVER:
            raise ValueError(f"phase {self.phase.value} requires discover mode")
        if self.phase in RESUMABLE_PHASES:
            if self.prior_phase not in WORK_PHASES:
                raise ValueError("resumable state requires an active prior_phase")
            if self.prior_phase in {Phase.SCOUT, Phase.MAP} and self.mode is not Mode.DISCOVER:
                raise ValueError(f"prior_phase {self.prior_phase.value} requires discover mode")
        elif self.prior_phase is not None:
            raise ValueError("prior_phase is valid only for resumable states")


@dataclass(frozen=True)
class TransitionFacts:
    has_seed: bool = False
    selected_capability: bool = False
    authorized_snapshots: bool = False
    discovery_grant: bool = False
    metadata_grant: bool = False
    content_grant: bool = False
    authority_valid: bool = False
    sources_ready: bool = False
    capability_map_ready: bool = False
    evidence_complete: bool = False
    high_impact_conflict: bool = False
    claims_resolved: bool = False
    draft_valid: bool = False
    evaluation_passed: bool = False
    revision_budget_remaining: bool = False
    revision_target: Optional[Phase] = None
    parent_approved: bool = False
    subject_valid: bool = False
    state_resolved: bool = False
    revocation_verified: bool = False
    version_approved: bool = False
    mutation_consent: bool = False
    export_verified: bool = False
    resume_valid: bool = False
    budget_extended: bool = False
    auth_restored: bool = False
    purge_complete: bool = False

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "revision_target":
                if value is not None and not isinstance(value, Phase):
                    raise ValueError("revision_target must be a Phase or None")
            elif type(value) is not bool:
                raise ValueError(f"{field.name} must be a bool")


class InvalidTransition(ValueError):
    """Raised when an event is illegal or its explicit guard is false."""


def _require(facts: TransitionFacts, *names: str) -> None:
    missing = [name for name in names if not getattr(facts, name)]
    if missing:
        raise InvalidTransition("required facts are false: " + ", ".join(missing))


def _at(state: TaskState, phase: Phase, event: Event) -> None:
    if state.phase is not phase:
        raise InvalidTransition(
            f"event {event.value} requires phase {phase.value}, got {state.phase.value}"
        )


def transition(state: TaskState, event: Event, facts: TransitionFacts) -> TaskState:
    """Return the next immutable state or reject the requested transition."""

    if state.phase in TERMINAL_PHASES:
        raise InvalidTransition(f"phase {state.phase.value} is terminal")

    if event is Event.CANCEL:
        return replace(state, phase=Phase.CANCELLED, prior_phase=None)
    if event is Event.FAIL_PERMANENTLY:
        return replace(state, phase=Phase.FAILED_PERMANENT, prior_phase=None)
    if event is Event.PAUSE:
        if state.phase not in WORK_PHASES:
            raise InvalidTransition(f"cannot pause phase {state.phase.value}")
        return replace(state, phase=Phase.SUSPENDED, prior_phase=state.phase)
    if event is Event.BUDGET_EXHAUSTED:
        if state.phase not in WORK_PHASES:
            raise InvalidTransition(f"cannot exhaust phase {state.phase.value}")
        return replace(state, phase=Phase.SUSPENDED_EXHAUSTED, prior_phase=state.phase)
    if event is Event.AUTH_INVALID:
        if state.phase not in WORK_PHASES:
            raise InvalidTransition(f"cannot stale authorization in phase {state.phase.value}")
        return replace(state, phase=Phase.AUTH_STALE, prior_phase=state.phase)

    if event is Event.START_DISCOVER:
        _at(state, Phase.INIT, event)
        _require(facts, "has_seed")
        return TaskState(mode=Mode.DISCOVER, phase=Phase.SCOUT, epoch=state.epoch)
    if event is Event.START_DISTILL:
        _at(state, Phase.INIT, event)
        _require(facts, "selected_capability")
        phase = Phase.CAPABILITY_REVIEW if facts.authorized_snapshots else Phase.SOURCE_REVIEW
        return TaskState(mode=Mode.DISTILL, phase=phase, epoch=state.epoch)
    if event is Event.START_UPDATE:
        _at(state, Phase.INIT, event)
        _require(facts, "parent_approved", "subject_valid", "state_resolved")
        return TaskState(mode=Mode.UPDATE, phase=Phase.SOURCE_REVIEW, epoch=state.epoch)
    if event is Event.START_REPAIR:
        _at(state, Phase.INIT, event)
        _require(facts, "parent_approved", "subject_valid", "revocation_verified")
        return TaskState(
            mode=Mode.UPDATE,
            phase=Phase.AUTH_STALE,
            epoch=state.epoch + 1,
            prior_phase=Phase.SOURCE_REVIEW,
        )

    if event is Event.SCOUT_COMPLETED:
        _at(state, Phase.SCOUT, event)
        _require(facts, "discovery_grant", "metadata_grant")
        return replace(state, phase=Phase.SOURCE_REVIEW)
    if event is Event.SOURCES_REJECTED:
        _at(state, Phase.SOURCE_REVIEW, event)
        return replace(state, phase=Phase.DONE_PARTIAL)
    if event is Event.CONTENT_GRANTED:
        _at(state, Phase.SOURCE_REVIEW, event)
        _require(facts, "content_grant", "authority_valid")
        return replace(state, phase=Phase.INGEST)
    if event is Event.SOURCES_SNAPSHOTTED:
        _at(state, Phase.INGEST, event)
        _require(facts, "sources_ready")
        next_phase = Phase.MAP if state.mode is Mode.DISCOVER else Phase.CAPABILITY_REVIEW
        return replace(state, phase=next_phase)
    if event is Event.CAPABILITY_MAP_READY:
        _at(state, Phase.MAP, event)
        _require(facts, "capability_map_ready")
        return replace(state, phase=Phase.CAPABILITY_REVIEW)
    if event is Event.CAPABILITY_SELECTED:
        _at(state, Phase.CAPABILITY_REVIEW, event)
        _require(facts, "selected_capability")
        return replace(state, phase=Phase.EXTRACT)
    if event is Event.EVIDENCE_EXTRACTED:
        _at(state, Phase.EXTRACT, event)
        _require(facts, "evidence_complete")
        phase = Phase.CLAIM_REVIEW if facts.high_impact_conflict else Phase.COMPILE
        return replace(state, phase=phase)
    if event is Event.CLAIMS_ADJUDICATED:
        _at(state, Phase.CLAIM_REVIEW, event)
        _require(facts, "claims_resolved")
        return replace(state, phase=Phase.COMPILE)
    if event is Event.DRAFT_COMPILED:
        _at(state, Phase.COMPILE, event)
        _require(facts, "draft_valid")
        return replace(state, phase=Phase.EVALUATE)
    if event is Event.EVALUATION_PASSED:
        _at(state, Phase.EVALUATE, event)
        _require(facts, "evaluation_passed")
        return replace(state, phase=Phase.VERSION_REVIEW)
    if event is Event.EVALUATION_FAILED:
        _at(state, Phase.EVALUATE, event)
        if facts.revision_budget_remaining:
            return replace(state, phase=Phase.REVISION_REVIEW)
        return replace(state, phase=Phase.SUSPENDED_EXHAUSTED, prior_phase=Phase.EVALUATE)
    if event is Event.REVISION_ACCEPTED:
        _at(state, Phase.REVISION_REVIEW, event)
        if facts.revision_target not in REVISION_TARGETS:
            raise InvalidTransition("revision_target must name the earliest invalidated phase")
        return replace(
            state,
            phase=facts.revision_target,
            epoch=state.epoch + 1,
            prior_phase=None,
        )
    if event is Event.REVISION_REJECTED:
        _at(state, Phase.REVISION_REVIEW, event)
        return replace(state, phase=Phase.DONE_PARTIAL)
    if event is Event.VERSION_APPROVED:
        _at(state, Phase.VERSION_REVIEW, event)
        _require(facts, "version_approved", "subject_valid")
        return replace(state, phase=Phase.EXPORT_REVIEW)
    if event is Event.VERSION_REJECTED:
        _at(state, Phase.VERSION_REVIEW, event)
        return replace(state, phase=Phase.REVISION_REVIEW)
    if event is Event.EXPORT_COMPLETED:
        _at(state, Phase.EXPORT_REVIEW, event)
        _require(facts, "mutation_consent", "export_verified")
        return replace(state, phase=Phase.DONE)
    if event is Event.EXPORT_DEFERRED:
        _at(state, Phase.EXPORT_REVIEW, event)
        return replace(state, phase=Phase.DONE_APPROVED)
    if event is Event.RESUME:
        _at(state, Phase.SUSPENDED, event)
        _require(facts, "resume_valid")
        return replace(state, phase=state.prior_phase, prior_phase=None)
    if event is Event.BUDGET_EXTENDED:
        _at(state, Phase.SUSPENDED_EXHAUSTED, event)
        _require(facts, "resume_valid", "budget_extended")
        return replace(state, phase=state.prior_phase, prior_phase=None)
    if event is Event.AUTH_RESTORED:
        _at(state, Phase.AUTH_STALE, event)
        _require(facts, "auth_restored", "purge_complete")
        if facts.revision_target not in REVISION_TARGETS | {
            Phase.SCOUT,
            Phase.SOURCE_REVIEW,
            Phase.INGEST,
            Phase.MAP,
            Phase.CAPABILITY_REVIEW,
        }:
            raise InvalidTransition("revision_target must name the earliest invalidated phase")
        if facts.revision_target in {Phase.SCOUT, Phase.MAP} and state.mode is not Mode.DISCOVER:
            raise InvalidTransition(
                f"revision_target {facts.revision_target.value} is incompatible with mode "
                f"{state.mode.value}"
            )
        return replace(
            state,
            phase=facts.revision_target,
            epoch=state.epoch + 1,
            prior_phase=None,
        )

    raise InvalidTransition(f"event {event.value} is not legal from {state.phase.value}")
