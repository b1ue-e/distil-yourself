"""Strict, deterministic knowledge contracts for capability compilation."""

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from . import adapters, privacy


SCHEMA_VERSION = "knowledge-distiller.knowledge-packet/v1"
MAX_PACKET_BYTES = 4 * 1024 * 1024
SECTIONS = (
    "triggers", "non_triggers", "goals", "non_goals", "inputs", "outputs",
    "invariants", "cues", "decision_rules", "workflow", "exceptions",
    "failures", "examples", "dependencies",
)
SNAPSHOT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SPAN_DIGEST = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
OPAQUE_IDENTIFIER_SUFFIX = re.compile(
    r"[a-z0-9](?:[a-z0-9_-]{0,248}[a-z0-9])?\Z"
)
SOURCE_KINDS = frozenset({"document", "session"})
EVIDENCE_TYPES = frozenset({"observation", "owner-statement", "inference"})
CLAIM_TYPES = EVIDENCE_TYPES
FRESHNESS = frozenset({"current", "recent", "historical", "unknown"})
SENSITIVITIES = frozenset(
    {"private-evidence", "restricted", "publishable-guidance"}
)
IMPACTS = frozenset({"low", "medium", "high"})
RULE_KINDS = frozenset({"guidance", "hard-invariant"})
DECISION_OUTCOMES = frozenset({"confirmed", "rejected", "superseded"})
QUESTION_REASONS = frozenset({
    "new-authority-required", "hard-constraint-unsupported",
    "critical-branch-blocked", "competing-rules",
})
QUESTION_PRIORITY = {
    "new-authority-required": 0,
    "hard-constraint-unsupported": 1,
    "critical-branch-blocked": 2,
    "competing-rules": 3,
}
KNOWLEDGE_ERROR_CODES = frozenset({
    "candidate-set-mismatch", "claim-evidence-overlap",
    "claim-provenance-mismatch", "critical-uncertainty",
    "duplicate-claim-decision", "duplicate-identifier", "duplicate-json-key",
    "duplicate-value", "graph-too-large", "invalid-claim-decision",
    "invalid-claim-status", "invalid-count", "invalid-enum", "invalid-json",
    "invalid-number", "invalid-snapshot-id", "invalid-span-id", "invalid-text",
    "invalid-type", "invalid-unicode-scalar", "invalid-utf8",
    "json-resource-limit", "json-too-deep", "missing-field",
    "noncritical-question", "packet-too-large", "unknown-capability",
    "question-private-content",
    "unknown-claim", "unknown-decision", "unknown-evidence", "unknown-field",
    "unsupported-recommendation", "unsupported-schema",
})


class KnowledgeError(ValueError):
    """A bounded diagnostic that never includes packet values."""

    def __init__(self, code: str) -> None:
        self.code = (
            code if type(code) is str and code in KNOWLEDGE_ERROR_CODES
            else "knowledge-invalid"
        )
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class EvidenceRecord:
    evidence_id: str
    source_kind: str
    source_snapshot_id: str
    redacted_span_ids: Tuple[str, ...]
    evidence_type: str
    excerpt: str
    applicability: Tuple[str, ...]
    confidence: int
    freshness: str
    freshness_reason: str
    sensitivity: str


@dataclass(frozen=True, repr=False)
class Claim:
    claim_id: str
    claim_type: str
    statement: str
    redacted_span_ids: Tuple[str, ...]
    support_evidence_ids: Tuple[str, ...]
    contradiction_evidence_ids: Tuple[str, ...]
    confidence: int
    freshness: str
    freshness_reason: str
    sensitivity: str
    impact: str
    rule_kind: str
    status: str


@dataclass(frozen=True, repr=False)
class ClaimDecision:
    decision_id: str
    claim_id: str
    outcome: str
    decided_by: str
    rationale: str
    superseded_by: Optional[str]


@dataclass(frozen=True, repr=False)
class CapabilityCandidate:
    capability_id: str
    name: str
    purpose: str
    triggers: Tuple[str, ...]
    outcome: str
    evaluation_scenarios: Tuple[str, ...]
    recurrence_count: int
    decision_impact: str
    evidence_ids: Tuple[str, ...]


@dataclass(frozen=True)
class CapabilityScore:
    capability_id: str
    recurrence: int
    decision_impact: int
    evidence_coverage: int
    testability: int
    total: int
    evidence_ids: Tuple[str, ...]
    tie_break_rationale: str


@dataclass(frozen=True, repr=False)
class CapabilityModel:
    model_id: str
    selected_capability_id: str
    selected_by: str
    candidate_ids: Tuple[str, ...]
    sections: Mapping[str, Tuple[str, ...]]


@dataclass(frozen=True, repr=False)
class QuestionAlternative:
    alternative_id: str
    label: str
    evidence_ids: Tuple[str, ...]
    behavioral_impact: str


@dataclass(frozen=True, repr=False)
class CriticalQuestion:
    question_id: str
    reason: str
    prompt: str
    alternatives: Tuple[QuestionAlternative, ...]
    evidence_ids: Tuple[str, ...]
    behavioral_impact: str
    confidence: int
    recommendation: Optional[str]


@dataclass(frozen=True, repr=False)
class Uncertainty:
    uncertainty_id: str
    summary: str
    evidence_ids: Tuple[str, ...]
    behavioral_impact: str


@dataclass(frozen=True, repr=False)
class KnowledgePacket:
    schema_version: str
    evidence: Tuple[EvidenceRecord, ...]
    claims: Tuple[Claim, ...]
    decisions: Tuple[ClaimDecision, ...]
    candidates: Tuple[CapabilityCandidate, ...]
    model: CapabilityModel
    questions: Tuple[CriticalQuestion, ...]
    uncertainties: Tuple[Uncertainty, ...]
    scores: Tuple[CapabilityScore, ...]


def _reject(code: str) -> None:
    raise KnowledgeError(code)


def _object(value: Any, fields: Iterable[str]) -> Dict[str, Any]:
    if type(value) is not dict:
        _reject("invalid-type")
    expected = set(fields)
    if set(value) - expected:
        _reject("unknown-field")
    if expected - set(value):
        _reject("missing-field")
    return value


def _array(value: Any, minimum: int = 0, maximum: int = 4096) -> List[Any]:
    if type(value) is not list:
        _reject("invalid-type")
    if len(value) < minimum or len(value) > maximum:
        _reject("invalid-count")
    return value


def _text(value: Any, minimum: int = 1, maximum: int = 4096) -> str:
    if type(value) is not str:
        _reject("invalid-type")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        _reject("invalid-text")
    if size < minimum or size > maximum:
        _reject("invalid-text")
    return value


def _identifier(value: Any, prefix: str) -> str:
    identifier = _text(value, 3, 256)
    if (not identifier.startswith(prefix + "-")
            or OPAQUE_IDENTIFIER_SUFFIX.fullmatch(
                identifier[len(prefix) + 1:]) is None):
        _reject("invalid-text")
    return identifier


def _enum(value: Any, choices: Set[str]) -> str:
    if type(value) is not str or value not in choices:
        _reject("invalid-enum")
    return value


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        _reject("invalid-number")
    return value


def _optional_identifier(value: Any, prefix: str) -> Optional[str]:
    return None if value is None else _identifier(value, prefix)


def _string_array(value: Any, minimum: int = 1) -> Tuple[str, ...]:
    result = tuple(_text(item) for item in _array(value, minimum))
    if len(set(result)) != len(result):
        _reject("duplicate-value")
    return result


def _identifier_array(
    value: Any,
    minimum: int = 1,
    prefix: str = "",
) -> Tuple[str, ...]:
    result = tuple(_identifier(item, prefix) for item in _array(value, minimum))
    if len(set(result)) != len(result):
        _reject("duplicate-identifier")
    return result


def _snapshot_digest(value: Any) -> str:
    if type(value) is not str or SNAPSHOT_DIGEST.fullmatch(value) is None:
        _reject("invalid-snapshot-id")
    return value


def _span_digest(value: Any) -> str:
    if type(value) is not str or SPAN_DIGEST.fullmatch(value) is None:
        _reject("invalid-span-id")
    return value


def _unique(identifier: str, seen: Set[str]) -> None:
    if identifier in seen:
        _reject("duplicate-identifier")
    seen.add(identifier)


def decode_packet(raw: bytes) -> Any:
    """Decode JSON through the hardened, duplicate-key-safe boundary."""

    if type(raw) is not bytes:
        _reject("invalid-type")
    if len(raw) > MAX_PACKET_BYTES:
        _reject("packet-too-large")
    try:
        return adapters.decode_event_graph_json(raw)
    except adapters.GraphValidationError as error:
        raise KnowledgeError(error.code) from None


def _validate_evidence(raw: Any) -> EvidenceRecord:
    value = _object(raw, {
        "evidence_id", "source_kind", "source_snapshot_id", "redacted_span_ids",
        "evidence_type", "excerpt", "applicability", "confidence", "freshness",
        "freshness_reason", "sensitivity",
    })
    spans = _string_array(value["redacted_span_ids"])
    for span_id in spans:
        _span_digest(span_id)
    return EvidenceRecord(
        evidence_id=_identifier(value["evidence_id"], "ev"),
        source_kind=_enum(value["source_kind"], SOURCE_KINDS),
        source_snapshot_id=_snapshot_digest(value["source_snapshot_id"]),
        redacted_span_ids=spans,
        evidence_type=_enum(value["evidence_type"], EVIDENCE_TYPES),
        excerpt=_text(value["excerpt"], 8, 16 * 1024),
        applicability=_string_array(value["applicability"]),
        confidence=_integer(value["confidence"], 0, 3),
        freshness=_enum(value["freshness"], FRESHNESS),
        freshness_reason=_text(value["freshness_reason"]),
        sensitivity=_enum(value["sensitivity"], SENSITIVITIES),
    )


def _validate_claim(raw: Any) -> Claim:
    value = _object(raw, {
        "claim_id", "claim_type", "statement", "redacted_span_ids",
        "support_evidence_ids", "contradiction_evidence_ids", "confidence",
        "freshness", "freshness_reason", "sensitivity", "impact", "rule_kind",
        "status",
    })
    if value["status"] != "proposed":
        _reject("invalid-claim-status")
    spans = _string_array(value["redacted_span_ids"])
    for span_id in spans:
        _span_digest(span_id)
    support = _identifier_array(value["support_evidence_ids"], prefix="ev")
    contradiction = _identifier_array(
        value["contradiction_evidence_ids"], 0, "ev")
    if set(support) & set(contradiction):
        _reject("claim-evidence-overlap")
    return Claim(
        claim_id=_identifier(value["claim_id"], "cl"),
        claim_type=_enum(value["claim_type"], CLAIM_TYPES),
        statement=_text(value["statement"], 1, 16 * 1024),
        redacted_span_ids=spans,
        support_evidence_ids=support,
        contradiction_evidence_ids=contradiction,
        confidence=_integer(value["confidence"], 0, 3),
        freshness=_enum(value["freshness"], FRESHNESS),
        freshness_reason=_text(value["freshness_reason"]),
        sensitivity=_enum(value["sensitivity"], SENSITIVITIES),
        impact=_enum(value["impact"], IMPACTS),
        rule_kind=_enum(value["rule_kind"], RULE_KINDS),
        status="proposed",
    )


def _validate_decision(raw: Any) -> ClaimDecision:
    value = _object(raw, {
        "decision_id", "claim_id", "outcome", "decided_by", "rationale",
        "superseded_by",
    })
    if value["decided_by"] != "current-user":
        _reject("invalid-claim-decision")
    outcome = _enum(value["outcome"], DECISION_OUTCOMES)
    superseded_by = _optional_identifier(value["superseded_by"], "dec")
    if (outcome == "superseded") != (superseded_by is not None):
        _reject("invalid-claim-decision")
    return ClaimDecision(
        decision_id=_identifier(value["decision_id"], "dec"),
        claim_id=_identifier(value["claim_id"], "cl"),
        outcome=outcome,
        decided_by="current-user",
        rationale=_text(value["rationale"]),
        superseded_by=superseded_by,
    )


def _validate_candidate(raw: Any) -> CapabilityCandidate:
    value = _object(raw, {
        "capability_id", "name", "purpose", "triggers", "outcome",
        "evaluation_scenarios", "recurrence_count", "decision_impact",
        "evidence_ids",
    })
    return CapabilityCandidate(
        capability_id=_identifier(value["capability_id"], "cap"),
        name=_text(value["name"], 1, 256),
        purpose=_text(value["purpose"]),
        triggers=_string_array(value["triggers"]),
        outcome=_text(value["outcome"]),
        evaluation_scenarios=_string_array(value["evaluation_scenarios"]),
        recurrence_count=_integer(value["recurrence_count"], 1, 1_000_000),
        decision_impact=_enum(value["decision_impact"], IMPACTS),
        evidence_ids=_identifier_array(value["evidence_ids"], prefix="ev"),
    )


def _validate_model(raw: Any) -> CapabilityModel:
    value = _object(raw, {
        "model_id", "selected_capability_id", "selected_by", "candidate_ids",
        "sections",
    })
    if value["selected_by"] != "current-user":
        _reject("invalid-claim-decision")
    sections = _object(value["sections"], set(SECTIONS))
    return CapabilityModel(
        model_id=_identifier(value["model_id"], "model"),
        selected_capability_id=_identifier(
            value["selected_capability_id"], "cap"),
        selected_by="current-user",
        candidate_ids=_identifier_array(value["candidate_ids"], prefix="cap"),
        sections=MappingProxyType({
            section: _identifier_array(sections[section], prefix="cl")
            for section in SECTIONS
        }),
    )


def _validate_question(raw: Any) -> CriticalQuestion:
    value = _object(raw, {
        "question_id", "reason", "prompt", "alternatives", "evidence_ids",
        "behavioral_impact", "confidence", "recommendation",
    })
    alternatives: List[QuestionAlternative] = []
    alternative_ids: Set[str] = set()
    for raw_alternative in _array(value["alternatives"], 2, 4):
        alternative = _object(raw_alternative, {
            "alternative_id", "label", "evidence_ids", "behavioral_impact",
        })
        alternative_id = _identifier(alternative["alternative_id"], "a")
        _unique(alternative_id, alternative_ids)
        alternatives.append(QuestionAlternative(
            alternative_id=alternative_id,
            label=_text(alternative["label"]),
            evidence_ids=_identifier_array(
                alternative["evidence_ids"], 0, "ev"),
            behavioral_impact=_enum(alternative["behavioral_impact"], IMPACTS),
        ))
    question_evidence = _identifier_array(value["evidence_ids"], prefix="ev")
    recommendation = _optional_identifier(value["recommendation"], "a")
    if recommendation is not None:
        matches = [item for item in alternatives if item.alternative_id == recommendation]
        if (not matches or not matches[0].evidence_ids
                or not set(matches[0].evidence_ids).issubset(question_evidence)):
            _reject("unsupported-recommendation")
    behavioral_impact = _enum(value["behavioral_impact"], IMPACTS)
    if behavioral_impact != "high":
        _reject("noncritical-question")
    return CriticalQuestion(
        question_id=_identifier(value["question_id"], "q"),
        reason=_enum(value["reason"], QUESTION_REASONS),
        prompt=_text(value["prompt"]),
        alternatives=tuple(alternatives),
        evidence_ids=question_evidence,
        behavioral_impact=behavioral_impact,
        confidence=_integer(value["confidence"], 0, 3),
        recommendation=recommendation,
    )


def _validate_uncertainty(raw: Any) -> Uncertainty:
    value = _object(raw, {
        "uncertainty_id", "summary", "evidence_ids", "behavioral_impact",
    })
    impact = _enum(value["behavioral_impact"], IMPACTS)
    if impact == "high":
        _reject("critical-uncertainty")
    return Uncertainty(
        uncertainty_id=_identifier(value["uncertainty_id"], "u"),
        summary=_text(value["summary"]),
        evidence_ids=_identifier_array(value["evidence_ids"], prefix="ev"),
        behavioral_impact=impact,
    )


def _score(candidate: CapabilityCandidate) -> CapabilityScore:
    recurrence = 0 if candidate.recurrence_count == 1 else (
        1 if candidate.recurrence_count == 2 else (
            2 if candidate.recurrence_count <= 4 else 3
        )
    )
    impact = {"low": 1, "medium": 2, "high": 3}[candidate.decision_impact]
    coverage = min(3, len(candidate.evidence_ids))
    testability = min(3, len(candidate.evaluation_scenarios))
    return CapabilityScore(
        capability_id=candidate.capability_id,
        recurrence=recurrence,
        decision_impact=impact,
        evidence_coverage=coverage,
        testability=testability,
        total=recurrence + impact + coverage + testability,
        evidence_ids=candidate.evidence_ids,
        tie_break_rationale=(
            "Rank by total, then decision impact, then testability, then stable capability ID."
        ),
    )


def _require_references(packet: KnowledgePacket) -> None:
    evidence = {item.evidence_id: item for item in packet.evidence}
    claims = {item.claim_id: item for item in packet.claims}
    decisions = {item.decision_id: item for item in packet.decisions}
    candidates = {item.capability_id: item for item in packet.candidates}

    outward_identifiers = (
        tuple(evidence)
        + tuple(candidates)
        + tuple(question.question_id for question in packet.questions)
        + tuple(
            alternative.alternative_id
            for question in packet.questions
            for alternative in question.alternatives
        )
    )
    private_excerpts = tuple(item.excerpt for item in packet.evidence)
    try:
        identifier_leak = privacy.contains_private_fragment(
            outward_identifiers, private_excerpts)
    except privacy.PrivacyBudgetExceeded:
        _reject("json-resource-limit")
    if (identifier_leak or any(
            privacy.contains_forbidden_marker(item)
            for item in outward_identifiers)):
        _reject("question-private-content")

    for claim in packet.claims:
        references = claim.support_evidence_ids + claim.contradiction_evidence_ids
        if any(identifier not in evidence for identifier in references):
            _reject("unknown-evidence")
        available_spans = {
            span_id for identifier in claim.support_evidence_ids
            for span_id in evidence[identifier].redacted_span_ids
        }
        if not set(claim.redacted_span_ids).issubset(available_spans):
            _reject("claim-provenance-mismatch")

    decided_claims: Set[str] = set()
    for decision in decisions.values():
        if decision.claim_id not in claims:
            _reject("unknown-claim")
        if decision.claim_id in decided_claims:
            _reject("duplicate-claim-decision")
        decided_claims.add(decision.claim_id)
        if (decision.superseded_by is not None
                and decision.superseded_by not in decisions):
            _reject("unknown-decision")

    for candidate in candidates.values():
        if any(identifier not in evidence for identifier in candidate.evidence_ids):
            _reject("unknown-evidence")

    if set(packet.model.candidate_ids) != set(candidates):
        _reject("candidate-set-mismatch")
    if packet.model.selected_capability_id not in candidates:
        _reject("unknown-capability")
    for section_claims in packet.model.sections.values():
        if any(identifier not in claims for identifier in section_claims):
            _reject("unknown-claim")

    for question in packet.questions:
        references = list(question.evidence_ids)
        for alternative in question.alternatives:
            references.extend(alternative.evidence_ids)
        if any(identifier not in evidence for identifier in references):
            _reject("unknown-evidence")
        outward_text = (question.prompt,) + tuple(
            item.label for item in question.alternatives)
        try:
            contains_private = privacy.contains_private_fragment(
                outward_text, (item.excerpt for item in packet.evidence))
        except privacy.PrivacyBudgetExceeded:
            _reject("json-resource-limit")
        if (privacy.contains_forbidden_marker("\n".join(outward_text))
                or contains_private):
            _reject("question-private-content")
    for uncertainty in packet.uncertainties:
        if any(identifier not in evidence for identifier in uncertainty.evidence_ids):
            _reject("unknown-evidence")


def validate_packet(raw: Any) -> KnowledgePacket:
    """Validate and materialize one closed knowledge packet."""

    value = _object(raw, {
        "schema_version", "evidence", "claims", "decisions", "candidates",
        "model", "questions", "uncertainties",
    })
    if value["schema_version"] != SCHEMA_VERSION:
        _reject("unsupported-schema")

    seen: Set[str] = set()
    evidence = tuple(_validate_evidence(item) for item in _array(value["evidence"], 1))
    for item in evidence:
        _unique(item.evidence_id, seen)
    seen.clear()
    claims = tuple(_validate_claim(item) for item in _array(value["claims"], 1))
    for item in claims:
        _unique(item.claim_id, seen)
    seen.clear()
    decisions = tuple(_validate_decision(item) for item in _array(value["decisions"]))
    for item in decisions:
        _unique(item.decision_id, seen)
    seen.clear()
    candidates = tuple(_validate_candidate(item) for item in _array(value["candidates"], 1))
    for item in candidates:
        _unique(item.capability_id, seen)
    seen.clear()
    questions = tuple(
        _validate_question(item) for item in _array(value["questions"], 0, 12)
    )
    for item in questions:
        _unique(item.question_id, seen)
    seen.clear()
    uncertainties = tuple(
        _validate_uncertainty(item) for item in _array(value["uncertainties"])
    )
    for item in uncertainties:
        _unique(item.uncertainty_id, seen)

    scores = tuple(sorted(
        (_score(item) for item in candidates),
        key=lambda item: (
            -item.total, -item.decision_impact, -item.testability,
            item.capability_id,
        ),
    ))
    packet = KnowledgePacket(
        schema_version=SCHEMA_VERSION,
        evidence=evidence,
        claims=claims,
        decisions=decisions,
        candidates=candidates,
        model=_validate_model(value["model"]),
        questions=questions,
        uncertainties=uncertainties,
        scores=scores,
    )
    _require_references(packet)
    return packet


def claim_outcome(packet: KnowledgePacket, claim_id: str) -> str:
    """Return the current user decision for a claim, or its proposed default."""

    if type(packet) is not KnowledgePacket:
        _reject("invalid-type")
    if claim_id not in {item.claim_id for item in packet.claims}:
        _reject("unknown-claim")
    for decision in packet.decisions:
        if decision.claim_id == claim_id:
            return decision.outcome
    return "proposed"


def next_critical_question(packet: KnowledgePacket) -> Optional[CriticalQuestion]:
    """Select at most one material question using the fixed priority order."""

    if type(packet) is not KnowledgePacket:
        _reject("invalid-type")
    if not packet.questions:
        return None
    return min(
        packet.questions,
        key=lambda item: (QUESTION_PRIORITY[item.reason], item.question_id),
    )
