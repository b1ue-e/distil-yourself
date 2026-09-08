"""Compile confirmed knowledge into a closed, non-installed skill draft."""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Dict, Iterable, Tuple
import unicodedata

from . import artifacts, knowledge
from .journal import canonical_json
from .persistence import TaskCoordinator, TaskPersistenceError
from .state import Event, Phase, TransitionFacts


SECTION_HEADINGS = {
    "triggers": "## Triggers",
    "non_triggers": "## Non-triggers",
    "goals": "## Goals",
    "non_goals": "## Non-goals",
    "inputs": "## Inputs",
    "outputs": "## Outputs",
    "invariants": "## Invariants",
    "cues": "## Cues",
    "decision_rules": "## Decision rules",
    "workflow": "## Adaptive workflow",
    "exceptions": "## Exceptions",
    "failures": "## Failures and recovery",
    "examples": "## Examples",
    "dependencies": "## Dependencies",
}
SOURCE_PATHS = {
    "document": (
        "sources/lark-snapshot.json", "evidence/lark-native.json",
        "provenance/lark-spans.json", "grants/lark-content-grant.json",
        "grants/lark-authority-attestation.json",
    ),
    "session": (
        "sources/codex-snapshot.json", "evidence/codex-native.json",
        "provenance/codex-spans.json", "grants/codex-content-grant.json",
        "grants/codex-authority-attestation.json",
    ),
}
PROVENANCE_RELATIONS = (
    "native-locator", "source-snapshot", "ingestion-run", "content-grant",
    "authority-attestation",
)
PROVENANCE_KINDS = ("redacted-span",) + PROVENANCE_RELATIONS
ADJUDICATED_PACKET_PATH = "model/adjudicated-knowledge-packet.json"
SELECTED_CAPABILITY_PATH = "model/selected-capability.json"
DECISIONS_PATH = "decisions/claim-decisions.json"
SENSITIVE_PRIVATE_FIELDS = frozenset({
    "active_principal", "actor_id", "content_owner", "decision_digest", "digest",
    "id", "issuer", "locator", "native_locator_digest", "participant_ids",
    "participant_names",
    "project_id", "record_id", "selector", "session_id", "source_snapshot_id",
    "span_id", "task_id", "tenant_account", "text",
})
FORBIDDEN_GUIDANCE_PATTERNS = (
    re.compile(r"(?:sha256|hmac-sha256):[0-9a-f]{64}\b", re.IGNORECASE),
    re.compile(r"\[redacted:[^\]\n]{1,256}\]", re.IGNORECASE),
    re.compile(
        r"(?<![\w.+-])[\w.+-]{1,128}@[\w-]{1,128}"
        r"(?:\.[\w-]{1,63}){1,8}(?![\w.-])"
    ),
)
COMPILER_ERROR_CODES = frozenset({
    "adjudication-invalid", "adjudication-mismatch", "asset-not-allowlisted",
    "bundle-too-large", "compilation-invalid", "critical-question-pending",
    "description-too-large", "directory-mutated", "directory-substituted",
    "draft-not-directory", "draft-substituted", "executable-file",
    "executable-instruction", "expected-directory", "extended-attribute",
    "file-substituted", "file-too-large", "generation-lineage-mismatch",
    "hardlink", "image-magic-mismatch", "invalid-path-encoding",
    "invalid-structured-reference", "invalid-type",
    "invalid-utf8", "missing-skill", "non-normalized-path",
    "nonpublishable-claim", "package-manifest", "path-collision",
    "persistence-rejected", "private-content-in-draft", "script-directory",
    "source-grant-expired", "source-grant-invalid",
    "source-provenance-invalid", "source-provenance-mismatch",
    "source-provenance-missing", "sparse-file", "special-file", "symlink",
    "task-busy", "too-many-entries", "unknown-root-entry",
    "unreadable-directory", "unreadable-file", "unreadable-metadata",
    "unreadable-path", "unresolved-claim", "unsupported-asset",
    "unsupported-reference", "xattr-check-unavailable",
}) | knowledge.KNOWLEDGE_ERROR_CODES


class CompilerError(ValueError):
    """A bounded compiler diagnostic that never includes private values."""

    def __init__(self, code: str) -> None:
        self.code = (
            code if type(code) is str and code in COMPILER_ERROR_CODES
            else "compilation-failed"
        )
        super().__init__(self.code)


@dataclass(frozen=True)
class CompilationResult:
    status: str
    phase: str
    generation_id: str
    manifest_digest: str
    manifest: Tuple[artifacts.ArtifactRecord, ...]
    draft_files: Dict[str, bytes]


def _reject(code: str) -> None:
    raise CompilerError(code)


def _decoded_private_json(raw: bytes):
    try:
        return knowledge.decode_packet(raw)
    except knowledge.KnowledgeError:
        _reject("source-provenance-invalid")


def _artifact_map(items) -> Dict[str, bytes]:
    return {entry["path"]: content for entry, content in (items or [])}


def _active_grant(raw: bytes, record_type: str, now: int) -> str:
    value = _decoded_private_json(raw)
    if (type(value) is not dict or value.get("record_type") != record_type
            or type(value.get("revoked")) is not bool or value["revoked"]
            or type(value.get("expires_at")) is not int
            or type(value.get("derived_processing_until")) is not int
            or type(value.get("decision_digest")) is not str
            or knowledge.SNAPSHOT_DIGEST.fullmatch(value["decision_digest"]) is None):
        _reject("source-grant-invalid")
    if now >= value["expires_at"] or now >= value["derived_processing_until"]:
        _reject("source-grant-expired")
    claimed = value["decision_digest"]
    unsigned = dict(value)
    del unsigned["decision_digest"]
    expected = "sha256:" + hashlib.sha256(canonical_json(unsigned)).hexdigest()
    if claimed != expected:
        _reject("source-grant-invalid")
    return claimed


def _source_snapshot(raw: bytes, source_kind: str, snapshot_id: str) -> None:
    value = _decoded_private_json(raw)
    if type(value) is not dict or set(value) != {"manifest", "payload"}:
        _reject("source-provenance-invalid")
    manifest = value["manifest"]
    payload = value["payload"]
    if (type(manifest) is not dict or type(payload) is not dict
            or manifest.get("source_kind") != source_kind
            or manifest.get("source_snapshot_id") != snapshot_id
            or payload.get("source_snapshot_id") != snapshot_id):
        _reject("source-provenance-mismatch")


def _node(value, expected_kind: str):
    if (type(value) is not dict or set(value) != {"kind", "digest"}
            or value.get("kind") != expected_kind
            or type(value.get("digest")) is not str):
        _reject("source-provenance-invalid")
    digest = value["digest"]
    pattern = (
        knowledge.SPAN_DIGEST if expected_kind == "redacted-span"
        else knowledge.SNAPSHOT_DIGEST
    )
    if pattern.fullmatch(digest) is None:
        _reject("source-provenance-invalid")
    return digest


def _provenance_spans(
    raw: bytes,
    snapshot_id: str,
    grant_id: str,
    authority_id: str,
) -> Dict[str, Tuple[bool, str]]:
    value = _decoded_private_json(raw)
    if (type(value) is not dict
            or set(value) != {"schema_version", "source_snapshot_id", "spans"}
            or value.get("schema_version") != "knowledge-distiller.provenance/v1"
            or value.get("source_snapshot_id") != snapshot_id
            or type(value.get("spans")) is not list
            or not value["spans"] or len(value["spans"]) > 4096):
        _reject("source-provenance-invalid")
    spans = {}
    for span in value["spans"]:
        if (type(span) is not dict
                or set(span) != {"span_id", "text", "claim_eligible", "derivations"}
                or type(span.get("text")) is not str
                or type(span.get("claim_eligible")) is not bool
                or type(span.get("derivations")) is not list
                or len(span["derivations"]) != len(PROVENANCE_RELATIONS)):
            _reject("source-provenance-invalid")
        span_id = span.get("span_id")
        if (type(span_id) is not str or knowledge.SPAN_DIGEST.fullmatch(span_id) is None
                or span_id in spans):
            _reject("source-provenance-invalid")
        previous = span_id
        final_digests = []
        for index, edge in enumerate(span["derivations"]):
            if (type(edge) is not dict
                    or set(edge) != {"relation", "from_node", "to_node"}
                    or edge.get("relation") != PROVENANCE_RELATIONS[index]):
                _reject("source-provenance-invalid")
            source = _node(edge["from_node"], PROVENANCE_KINDS[index])
            target = _node(edge["to_node"], PROVENANCE_KINDS[index + 1])
            if source != previous:
                _reject("source-provenance-invalid")
            previous = target
            final_digests.append(target)
        if (final_digests[1] != snapshot_id
                or final_digests[3] != grant_id
                or final_digests[4] != authority_id):
            _reject("source-provenance-mismatch")
        spans[span_id] = (span["claim_eligible"], span["text"])
    return spans


def _validate_persisted_provenance(
    packet: knowledge.KnowledgePacket,
    stored: Dict[str, bytes],
    now: int,
) -> None:
    indexes = {}
    for source_kind in {item.source_kind for item in packet.evidence}:
        (source_path, evidence_path, provenance_path,
         grant_path, authority_path) = SOURCE_PATHS[source_kind]
        required = (source_path, evidence_path, provenance_path, grant_path, authority_path)
        if any(path not in stored for path in required):
            _reject("source-provenance-missing")
        grant_id = _active_grant(stored[grant_path], "content-grant", now)
        authority_id = _active_grant(
            stored[authority_path], "authority-attestation", now)
        native_evidence = _decoded_private_json(stored[evidence_path])
        if type(native_evidence) is not dict:
            _reject("source-provenance-invalid")
        source_records = [
            item for item in packet.evidence if item.source_kind == source_kind
        ]
        snapshot_ids = {item.source_snapshot_id for item in source_records}
        if (len(snapshot_ids) != 1
                or native_evidence.get("source_snapshot_id") not in snapshot_ids):
            _reject("source-provenance-mismatch")
        snapshot_id = next(iter(snapshot_ids))
        _source_snapshot(stored[source_path], source_kind, snapshot_id)
        indexes[source_kind] = _provenance_spans(
            stored[provenance_path], snapshot_id,
            grant_id, authority_id,
        )
    for evidence in packet.evidence:
        spans = indexes[evidence.source_kind]
        linked = [spans.get(span_id) for span_id in evidence.redacted_span_ids]
        if (any(item is None or not item[0] for item in linked)
                or evidence.excerpt != "\n".join(item[1] for item in linked)):
            _reject("source-provenance-mismatch")


def _adjudication_bytes(packet: knowledge.KnowledgePacket, raw_packet: bytes):
    return {
        ADJUDICATED_PACKET_PATH: raw_packet,
        SELECTED_CAPABILITY_PATH: canonical_json({
            "schema_version": "knowledge-distiller.capability-selection/v1",
            "selected_capability_id": packet.model.selected_capability_id,
        }),
        DECISIONS_PATH: canonical_json([asdict(item) for item in packet.decisions]),
    }


def validate_compilation_input(raw: bytes) -> knowledge.KnowledgePacket:
    """Decode and validate one private packet for compilation."""

    try:
        return knowledge.validate_packet(knowledge.decode_packet(raw))
    except knowledge.KnowledgeError as error:
        raise CompilerError(error.code) from None


def _selected_candidate(
    packet: knowledge.KnowledgePacket,
) -> knowledge.CapabilityCandidate:
    return next(
        item for item in packet.candidates
        if item.capability_id == packet.model.selected_capability_id
    )


def _confirmed_claims(packet: knowledge.KnowledgePacket) -> Dict[str, knowledge.Claim]:
    claims = {item.claim_id: item for item in packet.claims}
    selected = {
        claim_id for claim_ids in packet.model.sections.values()
        for claim_id in claim_ids
    }
    for claim_id in selected:
        claim = claims[claim_id]
        if knowledge.claim_outcome(packet, claim_id) != "confirmed":
            _reject("unresolved-claim")
        if claim.sensitivity != "publishable-guidance":
            _reject("nonpublishable-claim")
    return claims


def _frontmatter_name(identifier: str) -> str:
    return "distilled-capability-" + hashlib.sha256(
        identifier.encode("utf-8")
    ).hexdigest()[:16]


def _frontmatter_description(trigger: str) -> str:
    description = "Use when " + trigger.strip().rstrip(".").casefold() + "."
    if len(description.encode("utf-8")) > 1024:
        _reject("description-too-large")
    return json.dumps(description, ensure_ascii=False)


def _bullet_lines(values: Iterable[str]) -> str:
    return "\n".join("- " + value for value in values)


def _private_tokens(packet: knowledge.KnowledgePacket) -> Tuple[str, ...]:
    # Other private fields are structurally absent from the renderer. Excerpts
    # are the sole free-form private field that can collide with confirmed prose.
    return tuple(item.excerpt for item in packet.evidence)


def _stored_private_tokens(stored: Dict[str, bytes]) -> Tuple[str, ...]:
    tokens = set()

    def visit(value, sensitive=False):
        if type(value) is str:
            if sensitive and value:
                tokens.add(value)
            return
        if type(value) is list:
            for item in value:
                visit(item, sensitive)
            return
        if type(value) is dict:
            for key, item in value.items():
                visit(
                    item,
                    sensitive or key in SENSITIVE_PRIVATE_FIELDS or key.endswith("_id"),
                )

    for path, raw in stored.items():
        if path.split("/", 1)[0] in {"grants", "sources", "evidence", "provenance"}:
            visit(_decoded_private_json(raw))
    return tuple(sorted(tokens))


def _reject_private_content(
    packet: knowledge.KnowledgePacket,
    bundle: Dict[str, bytes],
    stored_tokens: Tuple[str, ...] = (),
) -> None:
    draft = b"\n".join(bundle[path] for path in sorted(bundle))
    try:
        text = draft.decode("utf-8")
    except UnicodeDecodeError:
        _reject("private-content-in-draft")
    normalized_draft = unicodedata.normalize("NFC", text).casefold()
    for token in _private_tokens(packet) + stored_tokens:
        try:
            normalized_token = unicodedata.normalize("NFC", token).casefold()
            encoded = normalized_token.encode("utf-8")
        except UnicodeEncodeError:
            _reject("private-content-in-draft")
        # Very short values cannot be matched safely in prose. They remain
        # structurally excluded by the closed renderer instead.
        if len(encoded) >= 4 and normalized_token in normalized_draft:
            _reject("private-content-in-draft")
    if any(pattern.search(text) for pattern in FORBIDDEN_GUIDANCE_PATTERNS):
        _reject("private-content-in-draft")


def _render_bundle(packet: knowledge.KnowledgePacket) -> Dict[str, bytes]:
    """Render only the fixed two-file, non-executable draft shape."""

    if type(packet) is not knowledge.KnowledgePacket:
        _reject("invalid-type")
    if packet.questions:
        _reject("critical-question-pending")
    claims = _confirmed_claims(packet)
    candidate = _selected_candidate(packet)
    trigger = claims[packet.model.sections["triggers"][0]].statement
    skill = (
        "---\n"
        f"name: {_frontmatter_name(candidate.capability_id)}\n"
        f"description: {_frontmatter_description(trigger)}\n"
        "---\n\n"
        "# Distilled capability\n\n"
        "Read `references/capability.md` and apply only the confirmed guidance.\n\n"
        "The reference defines the confirmed goals and outputs.\n"
    )
    reference_parts = [
        "# Confirmed capability guidance\n",
        "This reference contains only user-confirmed, publishable guidance.\n",
    ]
    for section in knowledge.SECTIONS:
        statements = [
            claims[claim_id].statement
            for claim_id in packet.model.sections[section]
        ]
        reference_parts.append(
            SECTION_HEADINGS[section] + "\n\n" + _bullet_lines(statements) + "\n"
        )
    bundle = {
        "SKILL.md": skill.encode("utf-8"),
        "references/capability.md": "\n".join(reference_parts).encode("utf-8"),
    }
    return bundle


def _validated_manifest(bundle: Dict[str, bytes]) -> Tuple[artifacts.ArtifactRecord, ...]:
    with tempfile.TemporaryDirectory(prefix="knowledge-distiller-draft-") as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        references = root / "references"
        references.mkdir(mode=0o700)
        for relative_path, content in bundle.items():
            path = root / relative_path
            path.write_bytes(content)
            path.chmod(0o600)
        try:
            manifest = artifacts.validate_draft(root)
        except artifacts.DraftValidationError as error:
            raise CompilerError(error.code) from None
        for record in manifest:
            if (root / record.path).read_bytes() != bundle[record.path]:
                _reject("draft-substituted")
        return manifest


def _preflight_bundle(
    packet: knowledge.KnowledgePacket,
    existing: Dict[str, bytes],
) -> Tuple[Dict[str, bytes], Tuple[artifacts.ArtifactRecord, ...]]:
    bundle = _render_bundle(packet)
    _reject_private_content(packet, bundle, _stored_private_tokens(existing))
    return bundle, _validated_manifest(bundle)


def adjudicate_knowledge_packet(
    task_root: Path,
    raw_packet: bytes,
    transaction_id: str,
    expected_generation_id: str,
):
    """Persist an exact, separate current-user adjudication before compilation."""

    packet = validate_compilation_input(raw_packet)
    if packet.questions:
        _reject("critical-question-pending")
    checked_now = int(time.time())
    try:
        with TaskCoordinator(Path(task_root)) as coordinator:
            snapshot = coordinator.snapshot
            if snapshot is None or snapshot.generation_id != expected_generation_id:
                _reject("generation-lineage-mismatch")
            if snapshot.state.phase is not Phase.CLAIM_REVIEW:
                _reject("adjudication-invalid")
            existing = _artifact_map(coordinator._current_artifacts())
            _validate_persisted_provenance(packet, existing, checked_now)
            _preflight_bundle(packet, existing)
            existing.update(_adjudication_bytes(packet, raw_packet))
            with coordinator.artifact_transaction(
                    transaction_id, expected_generation_id) as transaction:
                for path in sorted(existing):
                    transaction.add(path, existing[path])
                return transaction.commit(
                    Event.CLAIMS_ADJUDICATED,
                    TransitionFacts(claims_resolved=True),
                )
    except TaskPersistenceError as error:
        code = "task-busy" if error.code == "task-busy" else "persistence-rejected"
        raise CompilerError(code) from None


def compile_capability(
    task_root: Path,
    raw_packet: bytes,
    transaction_id: str,
    expected_generation_id: str,
) -> CompilationResult:
    """Validate, compile, and atomically persist a private draft snapshot."""

    packet = validate_compilation_input(raw_packet)
    checked_now = int(time.time())
    try:
        with TaskCoordinator(Path(task_root)) as coordinator:
            snapshot = coordinator.snapshot
            if snapshot is None or snapshot.generation_id != expected_generation_id:
                _reject("generation-lineage-mismatch")
            if snapshot.state.phase is not Phase.COMPILE:
                _reject("compilation-invalid")
            existing = _artifact_map(coordinator._current_artifacts())
            expected_adjudication = _adjudication_bytes(packet, raw_packet)
            if any(existing.get(path) != content
                   for path, content in expected_adjudication.items()):
                _reject("adjudication-mismatch")
            _validate_persisted_provenance(packet, existing, checked_now)
            bundle, manifest = _preflight_bundle(packet, existing)
            manifest_bytes = canonical_json([asdict(record) for record in manifest])
            existing = {
                path: content for path, content in existing.items()
                if not path.startswith("draft-skill/")
            }
            existing["draft-skill/manifest.json"] = manifest_bytes
            for relative_path, content in bundle.items():
                existing["draft-skill/" + relative_path] = content
            with coordinator.artifact_transaction(
                    transaction_id, expected_generation_id) as transaction:
                for path in sorted(existing):
                    transaction.add(path, existing[path])
                committed = transaction.commit(
                    Event.DRAFT_COMPILED,
                    TransitionFacts(draft_valid=True),
                )
    except TaskPersistenceError as error:
        code = "task-busy" if error.code == "task-busy" else "persistence-rejected"
        raise CompilerError(code) from None
    return CompilationResult(
        status="compiled",
        phase=committed.state.phase.value,
        generation_id=committed.generation_id,
        manifest_digest=committed.manifest_digest,
        manifest=manifest,
        draft_files=dict(bundle),
    )
