"""Strict decoding for standalone local Codex ingestion requests."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import time

from . import (
    adapters, authorization, brokers, ingestion, native_adapters, source_io,
)
from .journal import canonical_json


REQUEST_SCHEMA = "knowledge-distiller.local-codex-ingestion-request/v1"
MAX_LOCAL_REQUEST_BYTES = ingestion.MAX_REQUEST_BYTES
READ_WINDOW_SECONDS = 300
MAX_DERIVED_SECONDS = 90 * 24 * 60 * 60
PURPOSE = "distill-knowledge"
LOCAL_OWNER_VERIFIER = "local-owner-verifier-v1"
REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "transaction_id",
        "expected_generation_id",
        "session_path",
        "project_id",
        "prefix_length",
        "prefix_digest",
        "derived_processing_until",
    }
)


class LocalRuntimeError(ValueError):
    """Allowlisted code-only local runtime diagnostic."""

    ALLOWED = frozenset(
        {
            "invalid-local-request",
            "invalid-derived-deadline",
            "local-identity-unavailable",
            "unsafe-redaction-key",
            "local-runtime-failed",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self.ALLOWED else "invalid-local-request"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class LocalCodexRequest:
    schema_version: str
    transaction_id: str
    expected_generation_id: str
    session_path: str
    project_id: str
    prefix_length: int
    prefix_digest: str
    derived_processing_until: int


@dataclass(frozen=True, repr=False)
class MaterializedCodexRequest:
    request: ingestion.IngestionRequest
    identity: brokers.SessionIdentity


def _digest(domain: str, raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(
        domain.encode("ascii") + b"\0" + raw
    ).hexdigest()


def _decision_digest(record: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(record)).hexdigest()


def _validate_runtime_inputs(
    request: LocalCodexRequest, effective_uid: int, now: int,
) -> None:
    if type(request) is not LocalCodexRequest or set(vars(request)) != REQUEST_FIELDS:
        raise LocalRuntimeError("invalid-local-request")
    if type(effective_uid) is not int or not 0 <= effective_uid <= 2**63 - 1:
        raise LocalRuntimeError("local-identity-unavailable")
    if type(now) is not int or not 0 <= now <= 2**63 - 1:
        raise LocalRuntimeError("local-identity-unavailable")
    if (
        type(request.derived_processing_until) is not int
        or request.derived_processing_until <= now
        or request.derived_processing_until > now + MAX_DERIVED_SECONDS
    ):
        raise LocalRuntimeError("invalid-derived-deadline")


def materialize_local_codex_request(
    task_root, request: LocalCodexRequest, *, selector_key: bytes,
    effective_uid: int, now: int,
) -> MaterializedCodexRequest:
    """Derive a closed local authorization envelope from trusted runtime facts."""

    _validate_runtime_inputs(request, effective_uid, now)
    if type(selector_key) is not bytes or not 32 <= len(selector_key) <= 64:
        raise LocalRuntimeError("unsafe-redaction-key")
    try:
        task_path = os.path.abspath(os.fspath(task_root)).encode("utf-8")
    except Exception:
        raise LocalRuntimeError("local-identity-unavailable") from None

    uid = str(effective_uid).encode("ascii")
    generation = request.expected_generation_id.encode("utf-8")
    task_id = _digest("local-codex-task", task_path + b"\0" + generation)
    principal = _digest("local-codex-principal", uid)
    tenant = _digest(
        "local-codex-tenant", uid + b"\0" + task_id.encode("ascii")
    )
    selector = brokers.session_selector_commitment(
        request.session_path, selector_key
    )
    session_range = authorization.SessionRange(0, request.prefix_length - 1)
    context = authorization.AuthorizationContext(
        task_id=task_id,
        active_principal=principal,
        tenant_account=tenant,
        selector=selector,
        purpose=PURPOSE,
        revision=None,
        session_range=session_range,
        now=now,
        task_active=True,
        authenticated_issuers=(principal, LOCAL_OWNER_VERIFIER),
        content_owner=principal,
    )
    record_binding = (
        request.transaction_id.encode("utf-8")
        + b"\0"
        + task_id.encode("ascii")
        + b"\0"
        + request.prefix_digest.encode("ascii")
    )
    common_scope = {
        "task_id": task_id,
        "active_principal": principal,
        "tenant_account": tenant,
        "selector": selector,
        "purpose": PURPOSE,
        "revision": None,
        "session_range": {"start": 0, "end": request.prefix_length - 1},
        "issued_at": now,
        "expires_at": now + READ_WINDOW_SECONDS,
        "derived_processing_until": request.derived_processing_until,
        "revoked": False,
    }
    grant = dict(
        common_scope,
        record_type="content-grant",
        record_id=_digest("local-codex-content-grant", record_binding),
        issuer=principal,
        operation="read-content",
    )
    grant["decision_digest"] = _decision_digest(grant)
    attestation = dict(
        common_scope,
        record_type="authority-attestation",
        record_id=_digest("local-codex-authority-attestation", record_binding),
        issuer=LOCAL_OWNER_VERIFIER,
        operation="process-third-party",
        content_owner=principal,
        authority_basis="verified-ownership",
    )
    attestation["decision_digest"] = _decision_digest(attestation)
    native_request = brokers.SessionRequest(
        request.session_path,
        request.project_id,
        request.prefix_length,
        request.prefix_digest,
    )
    identity = brokers.SessionIdentity(
        principal,
        tenant,
        request.project_id,
        principal,
        native_adapters.CODEX_ROLLOUT_ADAPTER.product_version,
        native_adapters.CODEX_NATIVE_SCHEMA_DIGEST,
        effective_uid,
        selector_key,
    )
    return MaterializedCodexRequest(
        ingestion.IngestionRequest(
            "codex",
            request.transaction_id,
            request.expected_generation_id,
            context,
            grant,
            attestation,
            native_request,
            request.project_id,
        ),
        identity,
    )


def _read_redaction_key(path, uid: int) -> bytes:
    try:
        raw = source_io.read_source(
            path,
            max_bytes=64,
            expected_owner_uid=uid,
            owner_only=True,
        )
    except source_io.SourceIOError:
        raise LocalRuntimeError("unsafe-redaction-key") from None
    if type(raw) is not bytes or not 32 <= len(raw) <= 64:
        raise LocalRuntimeError("unsafe-redaction-key")
    return raw


def _runtime_identity(request: LocalCodexRequest):
    try:
        effective_uid = os.geteuid()
        timestamp = time.time()
        if type(timestamp) not in (int, float):
            raise ValueError
        now = int(timestamp)
    except Exception:
        raise LocalRuntimeError("local-identity-unavailable") from None
    _validate_runtime_inputs(request, effective_uid, now)
    return effective_uid, now


def _ingest_codex_session(task_root, raw_request, redaction_key_file):
    request = decode_local_codex_request(raw_request)
    effective_uid, now = _runtime_identity(request)
    key = _read_redaction_key(redaction_key_file, effective_uid)
    materialized = materialize_local_codex_request(
        task_root,
        request,
        selector_key=key,
        effective_uid=effective_uid,
        now=now,
    )

    def acquire_codex(native_request, grant, attestation, context):
        return brokers.read_local_session(
            native_request,
            grant=grant,
            attestation=attestation,
            context=context,
            identity=materialized.identity,
        )

    def reject_lark(*_args, **_kwargs):
        raise ingestion.IngestionError("unsupported-source-kind")

    return ingestion.ingest_source(
        Path(task_root),
        materialized.request,
        acquire=ingestion.AcquisitionDispatch(
            lark=reject_lark,
            codex=acquire_codex,
        ),
        redaction_key=key,
    )


def ingest_codex_session(task_root, raw_request: bytes, redaction_key_file: str):
    try:
        return _ingest_codex_session(task_root, raw_request, redaction_key_file)
    except (LocalRuntimeError, ingestion.IngestionError):
        raise
    except Exception:
        raise LocalRuntimeError("local-runtime-failed") from None


def decode_local_codex_request(raw: bytes) -> LocalCodexRequest:
    """Decode one closed, pinned local Codex request without performing I/O."""

    if type(raw) is not bytes or not raw or len(raw) > MAX_LOCAL_REQUEST_BYTES:
        raise LocalRuntimeError("invalid-local-request")
    try:
        value = adapters.decode_event_graph_json(raw)
        request = adapters._object(value, REQUEST_FIELDS, "/")
        if type(request["schema_version"]) is not str or request["schema_version"] != REQUEST_SCHEMA:
            raise ValueError
        transaction_id = adapters._identifier(request["transaction_id"], "/transaction_id")
        expected_generation_id = adapters._identifier(
            request["expected_generation_id"], "/expected_generation_id"
        )
        session_path = authorization._exact(request["session_path"], "invalid-local-request")
        project_id = adapters._identifier(request["project_id"], "/project_id")
        prefix_length = request["prefix_length"]
        if type(prefix_length) is not int or not 1 <= prefix_length <= adapters.MAX_GRAPH_BYTES:
            raise ValueError
        prefix_digest = adapters._snapshot(request["prefix_digest"], "/prefix_digest")
        derived_processing_until = request["derived_processing_until"]
        if (
            type(derived_processing_until) is not int
            or not 0 <= derived_processing_until <= 2**63 - 1
        ):
            raise ValueError
        return LocalCodexRequest(
            request["schema_version"],
            transaction_id,
            expected_generation_id,
            session_path,
            project_id,
            prefix_length,
            prefix_digest,
            derived_processing_until,
        )
    except Exception:
        raise LocalRuntimeError("invalid-local-request") from None
