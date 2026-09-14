"""Atomic, dependency-injected ingestion for one explicitly authorized source."""

from dataclasses import asdict, dataclass, fields
import hashlib
import hmac
from pathlib import Path
import re
from typing import Any, Callable

from . import (
    adapters, authorization, brokers, lark_selector, native_adapters,
    redaction, sources,
)
from .journal import canonical_json
from .persistence import TaskCoordinator, TaskPersistenceError
from .state import Event, Phase, TransitionFacts

MAX_REQUEST_BYTES = 1024 * 1024


class IngestionError(ValueError):
    """Bounded code-only diagnostic."""

    def __init__(self, code: str) -> None:
        allowed = {
            "ingestion-invalid", "ingestion-failed", "invalid-request",
            "ingestion-runtime-unavailable", "ingestion-runtime-invalid",
            "invalid-authorization-context", "invalid-authorization-record",
            "authorization-revoked", "authorization-expired",
            "authorization-context-mismatch", "authorization-not-yet-valid",
            "derived-processing-expired", "unauthenticated-issuer",
            "invalid-grant-issuer", "decision-digest-mismatch",
            "unsupported-source-kind", "unsupported-adapter-version",
            "invalid-broker-request", "broker-response-invalid",
            "broker-evidence-mismatch", "snapshot-binding-mismatch",
            "generation-lineage-mismatch", "acquisition-unavailable",
            "acquisition-failed", "task-busy", "source-already-ingested",
            "persistence-rejected",
        }
        self.code = code if type(code) is str and code in allowed else "ingestion-failed"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class IngestionRequest:
    source_kind: str
    transaction_id: str
    expected_generation_id: str
    authorization_context: authorization.AuthorizationContext
    content_grant: dict
    authority_attestation: dict
    native_request: Any
    native_locator_id: str


@dataclass(frozen=True)
class IngestionResult:
    status: str
    source_kind: str
    source_snapshot_id: str
    canonical_digest: str
    source_byte_count: int
    source_item_count: int
    generation_id: str
    manifest_digest: str


@dataclass(frozen=True, repr=False)
class AcquisitionDispatch:
    """Trusted, source-specific acquisition entry points."""

    lark: Callable
    codex: Callable


@dataclass(frozen=True, repr=False)
class IngestionRuntime:
    request_decoder: Callable
    acquire: AcquisitionDispatch
    redaction_key: bytes


def _fail(code="ingestion-invalid"):
    raise IngestionError(code)


def _closed(value, cls, code="ingestion-invalid"):
    if type(value) is not cls or set(vars(value)) != {item.name for item in fields(cls)}:
        _fail(code)
    return value


def _clone_range(value, code):
    if value is None:
        return None
    value = _closed(value, authorization.SessionRange, code)
    return authorization.SessionRange(value.start, value.end)


def _clone_authorization_context(value):
    value = _closed(
        value, authorization.AuthorizationContext,
        "invalid-authorization-context")
    if type(value.authenticated_issuers) is not tuple:
        _fail("invalid-authorization-context")
    values = dict(vars(value))
    values["session_range"] = _clone_range(
        value.session_range, "invalid-authorization-context")
    values["authenticated_issuers"] = tuple(value.authenticated_issuers)
    return authorization.AuthorizationContext(**values)


def _clone_authorization_record(value, cls):
    value = _closed(value, cls, "invalid-authorization-record")
    values = dict(vars(value))
    values["session_range"] = _clone_range(
        value.session_range, "invalid-authorization-record")
    return cls(**values)


def _clone_record_input(value):
    if type(value) is not dict:
        _fail("invalid-authorization-record")
    cloned = dict(value)
    if type(cloned.get("session_range")) is dict:
        cloned["session_range"] = dict(cloned["session_range"])
    return cloned


def _clone_native_request(source_kind, value):
    if source_kind == "lark":
        value = _closed(value, brokers.LarkRequest, "invalid-broker-request")
        return brokers.LarkRequest(value.selector, value.revision)
    value = _closed(value, brokers.SessionRequest, "invalid-broker-request")
    return brokers.SessionRequest(
        value.path, value.project_id, value.prefix_length, value.prefix_digest)


def _pinned_request(value):
    value = _closed(value, IngestionRequest)
    if type(value.source_kind) is not str or value.source_kind not in ("lark", "codex"):
        _fail("unsupported-source-kind")
    context = _clone_authorization_context(value.authorization_context)
    native = _clone_native_request(value.source_kind, value.native_request)
    return IngestionRequest(
        value.source_kind, value.transaction_id, value.expected_generation_id,
        context, _clone_record_input(value.content_grant),
        _clone_record_input(value.authority_attestation), native,
        value.native_locator_id)


def _validated_dispatch(value, code="acquisition-unavailable"):
    value = _closed(value, AcquisitionDispatch, code)
    if not callable(value.lark) or not callable(value.codex):
        _fail(code)
    return value


def _artifact_paths(source_kind):
    return {
        "lark": (
            "sources/lark-snapshot.json",
            "evidence/lark-native.json",
            "provenance/lark-spans.json",
            "grants/lark-content-grant.json",
            "grants/lark-authority-attestation.json",
        ),
        "codex": (
            "sources/codex-snapshot.json",
            "evidence/codex-native.json",
            "provenance/codex-spans.json",
            "grants/codex-content-grant.json",
            "grants/codex-authority-attestation.json",
        ),
    }[source_kind]


def _existing_source_kinds(artifacts):
    paths = {entry["path"] for entry, unused in artifacts}
    present = set()
    for source_kind in ("lark", "codex"):
        required = set(_artifact_paths(source_kind))
        found = required & paths
        if found and found != required:
            _fail("persistence-rejected")
        if found:
            present.add(source_kind)
    return present


def _digest(value):
    raw = value if type(value) is bytes else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _trust(request, grant, attestation):
    context = request.authorization_context
    return redaction.TrustContext(
        context_id=_digest("ingestion-context:" + context.task_id),
        owner_id=context.content_owner,
        ingestion_run_id=_digest("ingestion-run:" + request.transaction_id),
        content_grant_digest=grant.decision_digest,
        authority_attestation_digest=attestation.decision_digest,
        participant_names=(), participant_ids=(), allowlist=())


def _snapshot_manifest(snapshot, payload, kind, context):
    if kind == "document":
        payload_dict = sources.canonical_document_payload(payload)
        canonical_digest = payload.canonical_digest
        item_count = len(payload.blocks)
        validation_context = sources.DocumentValidationContext(
            context.content_owner, snapshot.source_snapshot_id, context.revision, ())
        payload_kind = "canonical-document"
    else:
        payload_dict = payload
        validated = adapters.validate_event_graph(
            payload, context=adapters.ValidationContext(
                context.content_owner, snapshot.source_snapshot_id))
        canonical_digest = validated.canonical_digest
        item_count = validated.event_count
        validation_context = adapters.ValidationContext(
            context.content_owner, snapshot.source_snapshot_id)
        payload_kind = "event-graph"
    manifest = {
        "schema_version": sources.SNAPSHOT_SCHEMA,
        "source_kind": kind,
        "source_snapshot_id": snapshot.source_snapshot_id,
        "adapter": payload_dict["adapter"],
        "owner": payload_dict["owner"],
        "raw_digest": snapshot.raw_digest,
        "canonical_digest": canonical_digest,
        "source_byte_count": snapshot.source_byte_count,
        "source_item_count": item_count,
        "fidelity_losses": payload_dict["fidelity_losses"],
        "payload_kind": payload_kind,
        "payload_reference": canonical_digest,
    }
    record = sources.validate_source_snapshot(
        manifest, payload=payload, raw_bytes=snapshot.raw, context=validation_context)
    return record, payload_dict, manifest


def _validated_snapshot(snapshot, request, selector_key):
    snapshot = _closed(snapshot, brokers.BrokerSnapshot, "broker-response-invalid")
    owner = _closed(snapshot.owner, sources.OwnerBinding, "broker-response-invalid")
    evidence = _closed(snapshot.evidence, brokers.NativeEvidence, "broker-response-invalid")
    context = request.authorization_context
    if (any(type(value) is not str for value in (
                snapshot.selector_digest, snapshot.raw_digest,
                snapshot.source_snapshot_id, owner.kind, owner.id,
                owner.verification, evidence.product, evidence.product_version,
                evidence.native_schema_digest))
            or (evidence.project_id is not None
                and type(evidence.project_id) is not str)
            or type(snapshot.raw) is not bytes
            or len(snapshot.raw) > adapters.MAX_GRAPH_BYTES
            or type(snapshot.source_byte_count) is not int
            or snapshot.source_byte_count != len(snapshot.raw)):
        _fail("broker-response-invalid")
    if (snapshot.raw_digest != _digest(snapshot.raw)
            or snapshot.source_snapshot_id != snapshot.raw_digest):
        _fail("snapshot-binding-mismatch")
    if (owner.kind != "user" or owner.id != context.content_owner
            or owner.verification != "verified-principal"):
        _fail("broker-evidence-mismatch")
    if request.source_kind == "lark":
        native_request = request.native_request
        try:
            parsed = lark_selector.parse_document_selector(
                native_request.selector)
        except (TypeError, ValueError, RecursionError):
            _fail("invalid-broker-request")
        revision = native_request.revision
        if (type(revision) is not str or len(revision) > 20
                or re.fullmatch(r"(?:0|[1-9][0-9]*)", revision) is None):
            _fail("invalid-broker-request")
        committed_selector = (type(context.selector) is str and re.fullmatch(
            r"sha256:[0-9a-f]{64}", context.selector) is not None)
        if committed_selector:
            selector_matches = hmac.compare_digest(
                parsed.commitment, context.selector)
            expected_selector_digest = context.selector
        else:
            selector_matches = native_request.selector == context.selector
            expected_selector_digest = _digest(context.selector)
        if (not selector_matches
                or snapshot.selector_digest != expected_selector_digest
                or revision != context.revision
                or context.session_range is not None):
            _fail("broker-evidence-mismatch")
    else:
        if snapshot.selector_digest != _digest(context.selector):
            _fail("broker-evidence-mismatch")
        native_request = request.native_request
        source_range = context.session_range
        if (type(native_request.path) is not str
                or type(native_request.prefix_length) is not int
                or not 0 < native_request.prefix_length <= adapters.MAX_GRAPH_BYTES
                or type(native_request.prefix_digest) is not str
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}", native_request.prefix_digest) is None):
            _fail("invalid-broker-request")
        committed_selector = re.fullmatch(
            r"hmac-sha256:[0-9a-f]{64}", context.selector) is not None
        if committed_selector:
            try:
                expected_selector = brokers.session_selector_commitment(
                    native_request.path, selector_key)
            except brokers.BrokerError:
                _fail("invalid-broker-request")
            selector_matches = hmac.compare_digest(
                expected_selector, context.selector)
        else:
            selector_matches = native_request.path == context.selector
        if (not selector_matches or context.revision is not None
                or source_range is None or source_range.start != 0
                or native_request.prefix_length != source_range.end + 1
                or native_request.prefix_digest != snapshot.raw_digest
                or snapshot.source_byte_count != native_request.prefix_length):
            _fail("broker-evidence-mismatch")
    return brokers.BrokerSnapshot(
        snapshot.raw, sources.OwnerBinding(owner.kind, owner.id, owner.verification),
        snapshot.selector_digest, snapshot.raw_digest, snapshot.source_snapshot_id,
        snapshot.source_byte_count, brokers.NativeEvidence(**vars(evidence)))


def _normalize(request, snapshot, trust, key):
    context = request.authorization_context
    if request.source_kind == "lark":
        _closed(request.native_request, brokers.LarkRequest, "invalid-broker-request")
        if (snapshot.evidence.product != "lark"
                or snapshot.evidence.product_version != native_adapters.LARK_RAW_CONTENT_ADAPTER.product_version
                or snapshot.evidence.native_schema_digest != native_adapters.LARK_NATIVE_SCHEMA_DIGEST
                or snapshot.evidence.project_id is not None):
            _fail("unsupported-adapter-version")
        locator = native_adapters.lark_native_locator_digest(
            request.native_locator_id, context.revision)
        binding = redaction.SpanBinding(
            trust.context_id, snapshot.source_snapshot_id, locator,
            "external", None, "unresolved")
        content = native_adapters.decode_lark_raw_content(snapshot.raw)
        result = redaction.Redactor(trust, key).redact((
            redaction.SourceSpan(binding), redaction.SourceChunk(content)))
        payload = native_adapters.normalize_lark_raw_content(
            result, context=native_adapters.LarkNormalizationContext(
                context.content_owner, snapshot.source_snapshot_id, context.revision,
                request.native_locator_id, snapshot.evidence.product_version),
            redaction_context=trust, redaction_key=key, expected_binding=binding)
        spans = redaction.validate_redaction_result(
            result, context=trust, key=key, expected_bindings=(binding,))
        return (*_snapshot_manifest(snapshot, payload, "document", context), spans)
    if request.source_kind == "codex":
        native_request = _closed(
            request.native_request, brokers.SessionRequest, "invalid-broker-request")
        if (snapshot.evidence.product != "codex"
                or snapshot.evidence.product_version != native_adapters.CODEX_ROLLOUT_ADAPTER.product_version
                or snapshot.evidence.native_schema_digest != native_adapters.CODEX_NATIVE_SCHEMA_DIGEST
                or snapshot.evidence.project_id != native_request.project_id
                or request.native_locator_id != native_request.project_id):
            _fail("unsupported-adapter-version")
        adapter_context = native_adapters.CodexNormalizationContext(
            context.content_owner, snapshot.source_snapshot_id, native_request.project_id,
            snapshot.evidence.product_version, snapshot.evidence.native_schema_digest)
        prepared = native_adapters.prepare_codex_session(
            snapshot.raw, context=adapter_context, redaction_context=trust)
        result = redaction.Redactor(trust, key).redact(prepared.redaction_events)
        payload = native_adapters.normalize_codex_session(
            prepared, result, context=adapter_context, redaction_context=trust,
            redaction_key=key)
        spans = redaction.validate_redaction_result(
            result, context=trust, key=key,
            expected_bindings=prepared.expected_bindings)
        return (*_snapshot_manifest(snapshot, payload, "session", context), spans)
    _fail("unsupported-source-kind")


def _preflight_source(request, acquire):
    acquire = _validated_dispatch(acquire)
    if request.source_kind == "lark":
        _closed(request.native_request, brokers.LarkRequest, "invalid-broker-request")
        return acquire.lark
    elif request.source_kind == "codex":
        _closed(request.native_request, brokers.SessionRequest, "invalid-broker-request")
        return acquire.codex
    else:
        _fail("unsupported-source-kind")


def _require_ingestion_slot(coordinator, expected_generation_id, source_kind):
    if (coordinator.snapshot is None
            or coordinator.snapshot.generation_id != expected_generation_id):
        _fail("generation-lineage-mismatch")
    if coordinator.snapshot.state.phase is not Phase.INGEST:
        _fail("ingestion-invalid")
    existing_artifacts = coordinator._current_artifacts() or []
    existing_sources = _existing_source_kinds(existing_artifacts)
    if source_kind in existing_sources:
        _fail("source-already-ingested")
    return existing_artifacts, existing_sources


def preflight_ingestion_slot(
    task_root: Path, expected_generation_id: str, source_kind: str,
) -> None:
    """Check a task's ingestion slot without acquiring any source."""
    try:
        if type(source_kind) is not str or source_kind not in ("lark", "codex"):
            _fail("unsupported-source-kind")
        adapters._identifier(expected_generation_id, "/")
        with TaskCoordinator(Path(task_root)) as coordinator:
            _require_ingestion_slot(coordinator, expected_generation_id, source_kind)
    except IngestionError:
        raise
    except TaskPersistenceError as error:
        raise IngestionError(error.code) from None
    except Exception:
        raise IngestionError("ingestion-failed") from None


def ingest_source(task_root: Path, request: IngestionRequest, *, acquire: AcquisitionDispatch,
                  redaction_key: bytes) -> IngestionResult:
    """Validate, acquire, normalize, and atomically persist one source snapshot."""
    try:
        request = _pinned_request(request)
        adapters._identifier(request.transaction_id, "/")
        adapters._identifier(request.expected_generation_id, "/")
        grant = authorization.validate_content_grant(
            request.content_grant, context=request.authorization_context)
        attestation = authorization.validate_authority_attestation(
            request.authority_attestation, context=request.authorization_context)
        acquire_source = _preflight_source(request, acquire)
        if type(redaction_key) is not bytes or not 32 <= len(redaction_key) <= 64:
            _fail("ingestion-runtime-invalid")
        with TaskCoordinator(Path(task_root)) as coordinator:
            existing_artifacts, existing_sources = _require_ingestion_slot(
                coordinator, request.expected_generation_id, request.source_kind)
            try:
                snapshot = acquire_source(
                    _clone_native_request(
                        request.source_kind, request.native_request),
                    _clone_authorization_record(
                        grant, authorization.ContentGrant),
                    _clone_authorization_record(
                        attestation, authorization.AuthorityAttestation),
                    _clone_authorization_context(
                        request.authorization_context))
            except Exception:
                _fail("acquisition-failed")
            snapshot = _validated_snapshot(snapshot, request, redaction_key)
            trust = _trust(request, grant, attestation)
            record, payload, manifest, spans = _normalize(
                request, snapshot, trust, redaction_key)
            bundle = canonical_json({"manifest": manifest, "payload": payload})
            provenance = canonical_json({
                "schema_version": "knowledge-distiller.provenance/v1",
                "source_snapshot_id": record.source_snapshot_id,
                "spans": [asdict(span) for span in spans],
            })
            evidence = canonical_json({
                "schema_version": "knowledge-distiller.native-evidence/v1",
                "source_snapshot_id": record.source_snapshot_id,
                "selector_digest": snapshot.selector_digest,
                "source_byte_count": snapshot.source_byte_count,
                "native": asdict(snapshot.evidence),
            })
            with coordinator.artifact_transaction(
                    request.transaction_id, request.expected_generation_id) as transaction:
                for entry, content in existing_artifacts:
                    transaction.add(entry["path"], content)
                (source_path, evidence_path, provenance_path,
                 content_grant_path, attestation_path) = _artifact_paths(
                    request.source_kind)
                transaction.add(content_grant_path, canonical_json(asdict(grant)))
                transaction.add(attestation_path, canonical_json(asdict(attestation)))
                transaction.add(source_path, bundle)
                transaction.add(evidence_path, evidence)
                transaction.add(provenance_path, provenance)
                sources_complete = existing_sources | {request.source_kind} == {"lark", "codex"}
                committed = transaction.commit(
                    (Event.SOURCES_SNAPSHOTTED if sources_complete
                     else Event.SOURCE_SNAPSHOTTED),
                    (TransitionFacts(sources_ready=True) if sources_complete
                     else TransitionFacts(source_ready=True)))
        return IngestionResult(
            "snapshotted", record.source_kind, record.source_snapshot_id,
            record.canonical_digest, record.source_byte_count, record.source_item_count,
            committed.generation_id, committed.manifest_digest)
    except IngestionError:
        raise
    except authorization.AuthorizationError as error:
        raise IngestionError(error.code) from None
    except (brokers.BrokerError, native_adapters.NativeAdapterError,
            redaction.RedactionError, sources.SourceValidationError,
            adapters.GraphValidationError, TaskPersistenceError) as error:
        raise IngestionError(error.code) from None
    except Exception:
        raise IngestionError("acquisition-failed") from None


def ingest_request_bytes(task_root: Path, raw: bytes, *, runtime) -> IngestionResult:
    """Decode through a trusted host and execute the shared validated transaction."""
    if type(raw) is not bytes or not raw or len(raw) > MAX_REQUEST_BYTES:
        _fail("invalid-request")
    runtime = _closed(runtime, IngestionRuntime, "ingestion-runtime-unavailable")
    if (not callable(runtime.request_decoder)
            or type(runtime.redaction_key) is not bytes):
        _fail("ingestion-runtime-unavailable")
    _validated_dispatch(runtime.acquire, "ingestion-runtime-unavailable")
    try:
        request = runtime.request_decoder(raw)
    except IngestionError:
        raise
    except Exception:
        raise IngestionError("invalid-request") from None
    return ingest_source(
        Path(task_root), request, acquire=runtime.acquire,
        redaction_key=runtime.redaction_key)
