"""Closed owner-only orchestration for one local Lark Docx ingestion."""

from dataclasses import dataclass, fields, replace
import hashlib
import os
from pathlib import Path

from . import (
    adapters, authorization, brokers, ingestion, lark_cli_transport,
    lark_profile, persistence, runtime_support,
)
from .journal import canonical_json
from .lark_cli_transport import LarkCliTransport
from .lark_selector import ParsedDocumentSelector, parse_document_selector


REQUEST_SCHEMA = "knowledge-distiller.local-lark-ingestion-request/v1"
MAX_LOCAL_REQUEST_BYTES = ingestion.MAX_REQUEST_BYTES
READ_WINDOW_SECONDS = runtime_support.READ_WINDOW_SECONDS
MAX_DERIVED_SECONDS = runtime_support.MAX_DERIVED_SECONDS
PURPOSE = "distill-knowledge"
LARK_OWNER_VERIFIER = "lark-owner-verifier-v1"
_LIVE_STATUS = "live-enabled"
_VERIFIED_ENDPOINT = "verified"
_ACCEPTED_CONSISTENCY = frozenset(("authoritative", "observational"))
REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "transaction_id",
        "expected_generation_id",
        "document_selector",
        "derived_processing_until",
    }
)


class LarkRuntimeError(ValueError):
    """A code-only local Lark request failure."""

    ALLOWED = frozenset(
        {
            "invalid-lark-request",
            "invalid-derived-deadline",
            "local-identity-unavailable",
            "unsafe-redaction-key",
            "lark-live-disabled",
            "lark-cli-incompatible",
            "lark-identity-unverified",
            "lark-missing-scope",
            "lark-owner-mismatch",
            "lark-document-unavailable",
            "lark-document-unstable",
            "lark-transport-unavailable",
            "lark-runtime-failed",
        }
    )

    def __init__(self, code):
        self.code = (
            code
            if type(code) is str and code in self.ALLOWED
            else "invalid-lark-request"
        )
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class LocalLarkRequest:
    schema_version: str
    transaction_id: str
    expected_generation_id: str
    document_selector: str
    derived_processing_until: int


@dataclass(frozen=True, repr=False)
class MaterializedLarkRequest:
    request: ingestion.IngestionRequest
    expected_open_id: str
    principal: str
    account: str


def _closed(value, cls):
    try:
        valid = type(value) is cls and set(vars(value)) == {
            item.name for item in fields(cls)
        }
    except Exception:
        valid = False
    if not valid:
        raise LarkRuntimeError("invalid-lark-request")
    return value


def _digest(domain, raw):
    return "sha256:" + hashlib.sha256(
        domain.encode("ascii") + b"\0" + raw
    ).hexdigest()


def _decision_digest(record):
    return "sha256:" + hashlib.sha256(canonical_json(record)).hexdigest()


def _safe_persistence_id(value):
    if type(value) is not str:
        raise LarkRuntimeError("invalid-lark-request")
    try:
        return persistence._require_id(value)
    except persistence.TaskPersistenceError:
        raise LarkRuntimeError("invalid-lark-request") from None


def _absolute_task_root(task_root):
    try:
        supplied = os.fspath(task_root)
        if type(supplied) is not str or "\0" in supplied:
            raise ValueError
        absolute = os.path.abspath(supplied)
        if type(absolute) is not str or "\0" in absolute:
            raise ValueError
        encoded = absolute.encode("utf-8")
        if b"\0" in encoded:
            raise ValueError
        return absolute, encoded
    except Exception:
        raise LarkRuntimeError("local-identity-unavailable") from None


def _validate_runtime_inputs(request, parsed_selector, identity, before, now):
    _closed(request, LocalLarkRequest)
    _closed(parsed_selector, ParsedDocumentSelector)
    _closed(identity, lark_cli_transport.VerifiedUser)
    _closed(before, lark_cli_transport.LarkObservation)
    try:
        if (
            type(request.schema_version) is not str
            or request.schema_version != REQUEST_SCHEMA
        ):
            raise ValueError
        _safe_persistence_id(request.transaction_id)
        _safe_persistence_id(request.expected_generation_id)
        canonical_selector = parse_document_selector(request.document_selector)
        verified_identity = lark_cli_transport.VerifiedUser(
            identity.open_id, identity.scopes,
        )
        verified_before = lark_cli_transport.LarkObservation(
            before.token, before.revision, before.owner_open_id,
        )
        if (
            verified_identity != identity
            or verified_before != before
            or canonical_selector != parsed_selector
            or before.token != parsed_selector.token
        ):
            raise ValueError
    except Exception:
        raise LarkRuntimeError("invalid-lark-request") from None
    if type(now) is not int or not 0 <= now <= 2**63 - 1:
        raise LarkRuntimeError("local-identity-unavailable")
    if now + READ_WINDOW_SECONDS > 2**63 - 1:
        raise LarkRuntimeError("local-identity-unavailable")
    if (
        type(request.derived_processing_until) is not int
        or request.derived_processing_until <= now
        or request.derived_processing_until > now + MAX_DERIVED_SECONDS
    ):
        raise LarkRuntimeError("invalid-derived-deadline")
    if identity.open_id != before.owner_open_id:
        raise LarkRuntimeError("lark-owner-mismatch")


def materialize_local_lark_request(
    task_root,
    request,
    parsed_selector,
    identity,
    before,
    now,
):
    """Derive authorization only from verified owner and revision evidence."""

    _validate_runtime_inputs(request, parsed_selector, identity, before, now)
    try:
        task_path = _absolute_task_root(task_root)[1]
        open_id = identity.open_id.encode("utf-8")
        generation = request.expected_generation_id.encode("utf-8")
    except Exception:
        raise LarkRuntimeError("local-identity-unavailable") from None

    principal = _digest("lark-user-principal/v1", open_id)
    account = _digest("lark-account-scope/v1", open_id)
    task_id = _digest(
        "local-lark-task/v1", task_path + b"\0" + generation,
    )
    context = authorization.AuthorizationContext(
        task_id=task_id,
        active_principal=principal,
        tenant_account=account,
        selector=parsed_selector.commitment,
        purpose=PURPOSE,
        revision=before.revision,
        session_range=None,
        now=now,
        task_active=True,
        authenticated_issuers=(principal, LARK_OWNER_VERIFIER),
        content_owner=principal,
    )
    record_binding = (
        request.transaction_id.encode("utf-8")
        + b"\0"
        + task_id.encode("ascii")
        + b"\0"
        + parsed_selector.commitment.encode("ascii")
        + b"\0"
        + before.revision.encode("ascii")
    )
    common_scope = {
        "task_id": task_id,
        "active_principal": principal,
        "tenant_account": account,
        "selector": parsed_selector.commitment,
        "purpose": PURPOSE,
        "revision": before.revision,
        "session_range": None,
        "issued_at": now,
        "expires_at": now + READ_WINDOW_SECONDS,
        "derived_processing_until": request.derived_processing_until,
        "revoked": False,
    }
    grant = dict(
        common_scope,
        record_type="content-grant",
        record_id=_digest("local-lark-content-grant", record_binding),
        issuer=principal,
        operation="read-content",
    )
    grant["decision_digest"] = _decision_digest(grant)
    attestation = dict(
        common_scope,
        record_type="authority-attestation",
        record_id=_digest("local-lark-authority-attestation", record_binding),
        issuer=LARK_OWNER_VERIFIER,
        operation="process-third-party",
        content_owner=principal,
        authority_basis="verified-ownership",
    )
    attestation["decision_digest"] = _decision_digest(attestation)
    native_request = brokers.LarkRequest(
        parsed_selector.token, before.revision,
    )
    return MaterializedLarkRequest(
        ingestion.IngestionRequest(
            "lark",
            request.transaction_id,
            request.expected_generation_id,
            context,
            grant,
            attestation,
            native_request,
            parsed_selector.commitment,
        ),
        identity.open_id,
        principal,
        account,
    )


def _effective_uid():
    try:
        return runtime_support.effective_uid()
    except runtime_support.RuntimeSupportError as error:
        raise LarkRuntimeError(error.code) from None


def _read_redaction_key(path, uid):
    try:
        return runtime_support.read_redaction_key(path, uid)
    except runtime_support.RuntimeSupportError as error:
        raise LarkRuntimeError(error.code) from None


def _runtime_time():
    try:
        return runtime_support.runtime_time()
    except runtime_support.RuntimeSupportError as error:
        raise LarkRuntimeError(error.code) from None


def _require_live_ingestion(allow_live_read):
    """Require explicit consent and a reviewed live-read profile."""

    if allow_live_read is not True:
        raise LarkRuntimeError("lark-live-disabled")
    try:
        profile = lark_profile.load_pinned_profile()
        _closed(profile, lark_profile.PinnedLarkProfile)
        if (
            type(profile.status) is not str
            or profile.status != _LIVE_STATUS
            or type(profile.endpoint_integrity_status) is not str
            or profile.endpoint_integrity_status != _VERIFIED_ENDPOINT
            or type(profile.consistency_mode) is not str
            or profile.consistency_mode not in _ACCEPTED_CONSISTENCY
        ):
            raise ValueError
        return profile
    except Exception:
        raise LarkRuntimeError("lark-live-disabled") from None


def _verified_identity(transport):
    try:
        identity = _closed(
            transport.verify_user(), lark_cli_transport.VerifiedUser,
        )
        return lark_cli_transport.VerifiedUser(
            identity.open_id, identity.scopes,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise LarkRuntimeError("lark-identity-unverified") from None


def _observation(transport, parsed_selector):
    try:
        observation = _closed(
            transport.observe(parsed_selector), lark_cli_transport.LarkObservation,
        )
        return lark_cli_transport.LarkObservation(
            observation.token,
            observation.revision,
            observation.owner_open_id,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise LarkRuntimeError("lark-document-unavailable") from None


def _credential_resolver(materialized, **values):
    expected = {
        "active_principal": materialized.principal,
        "tenant_account": materialized.account,
        "required_variables": (),
    }
    if (
        set(values) != set(expected)
        or type(values["active_principal"]) is not str
        or type(values["tenant_account"]) is not str
        or type(values["required_variables"]) is not tuple
        or values != expected
    ):
        raise LarkRuntimeError("lark-transport-unavailable")
    return brokers.CredentialBinding(
        "user", materialized.principal, materialized.account, (),
    )


def _trusted_runner(transport, profile, parsed_selector, before, materialized):
    expected_argv = lark_cli_transport._broker_raw_argv(parsed_selector)
    expected_options = {
        "env": {},
        "shell": False,
        "timeout": profile.timeout_seconds,
        "max_bytes": adapters.MAX_GRAPH_BYTES,
        "allow_redirects": False,
        "allow_fallback_principal": False,
    }

    def run(argv, **options):
        if (
            type(argv) is not tuple
            or any(type(argument) is not str for argument in argv)
            or argv != expected_argv
            or set(options) != set(expected_options)
            or type(options["env"]) is not dict
            or options["env"] != {}
            or options["shell"] is not False
            or type(options["timeout"]) is not int
            or options["timeout"] != profile.timeout_seconds
            or type(options["max_bytes"]) is not int
            or options["max_bytes"] != adapters.MAX_GRAPH_BYTES
            or options["allow_redirects"] is not False
            or options["allow_fallback_principal"] is not False
        ):
            raise LarkRuntimeError("lark-transport-unavailable")
        raw = transport.raw_content(parsed_selector)
        after = _observation(transport, parsed_selector)
        if after != before:
            raise LarkRuntimeError("lark-document-unstable")
        return brokers.LarkResponse(
            returncode=0,
            raw=raw,
            media_type="application/json",
            principal_kind="user",
            active_principal=materialized.principal,
            tenant_account=materialized.account,
            selector_digest=parsed_selector.commitment,
            purpose=PURPOSE,
            revision_before=before.revision,
            revision_after=after.revision,
            content_owner=materialized.principal,
            product_version=profile.product_version,
            native_schema_digest=profile.native_schema_digest,
            redirected=False,
            fallback_principal=False,
        )

    return run


def _ingest_lark_document(
    task_root, raw_request, redaction_key_file, *, allow_live_read,
):
    request = decode_local_lark_request(raw_request)
    parsed_selector = parse_document_selector(request.document_selector)
    absolute_task_root, _ = _absolute_task_root(task_root)
    ingestion.preflight_ingestion_slot(
        Path(absolute_task_root), request.expected_generation_id, "lark",
    )
    uid = _effective_uid()
    key = _read_redaction_key(redaction_key_file, uid)
    now = _runtime_time()
    profile = _require_live_ingestion(allow_live_read)
    try:
        transport = LarkCliTransport(profile)
        version = transport.version()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        code = getattr(error, "code", None)
        if code == "lark-cli-incompatible":
            raise LarkRuntimeError("lark-cli-incompatible") from None
        raise LarkRuntimeError("lark-transport-unavailable") from None
    if type(version) is not str or version != profile.product_version:
        raise LarkRuntimeError("lark-cli-incompatible")
    identity = _verified_identity(transport)
    if not lark_cli_transport.has_required_scopes(identity.scopes):
        raise LarkRuntimeError("lark-missing-scope")
    before = _observation(transport, parsed_selector)
    materialized = materialize_local_lark_request(
        absolute_task_root, request, parsed_selector, identity, before, now,
    )

    def acquire_lark(native_request, grant, attestation, context):
        fresh_identity = _verified_identity(transport)
        if fresh_identity.open_id != materialized.expected_open_id:
            raise LarkRuntimeError("lark-identity-unverified")
        fresh_context = replace(context, now=_runtime_time())
        runner = _trusted_runner(
            transport, profile, parsed_selector, before, materialized,
        )
        return brokers.fetch_lark(
            native_request,
            grant=grant,
            attestation=attestation,
            context=fresh_context,
            runner=runner,
            credential_resolver=lambda **values: _credential_resolver(
                materialized, **values,
            ),
            required_auth_variables=(),
        )

    def reject_codex(*_args, **_kwargs):
        raise ingestion.IngestionError("unsupported-source-kind")

    return ingestion.ingest_source(
        Path(absolute_task_root),
        materialized.request,
        acquire=ingestion.AcquisitionDispatch(
            lark=acquire_lark,
            codex=reject_codex,
        ),
        redaction_key=key,
    )


def ingest_lark_document(
    task_root, raw_request, redaction_key_file, *, allow_live_read=False,
):
    try:
        return _ingest_lark_document(
            task_root,
            raw_request,
            redaction_key_file,
            allow_live_read=allow_live_read,
        )
    except (LarkRuntimeError, ingestion.IngestionError):
        raise
    except Exception:
        raise LarkRuntimeError("lark-runtime-failed") from None


def decode_local_lark_request(raw):
    """Decode a standalone local Lark request without I/O or deadline evaluation."""

    if type(raw) is not bytes or not raw or len(raw) > MAX_LOCAL_REQUEST_BYTES:
        raise LarkRuntimeError("invalid-lark-request")
    try:
        value = adapters.decode_event_graph_json(raw)
        request = adapters._object(value, REQUEST_FIELDS, "/")
        if (
            type(request["schema_version"]) is not str
            or request["schema_version"] != REQUEST_SCHEMA
        ):
            raise ValueError
        transaction_id = _safe_persistence_id(request["transaction_id"])
        expected_generation_id = _safe_persistence_id(
            request["expected_generation_id"]
        )
        selector = parse_document_selector(request["document_selector"])
        derived_processing_until = request["derived_processing_until"]
        if (
            type(derived_processing_until) is not int
            or not 0 <= derived_processing_until <= 2**63 - 1
        ):
            raise ValueError
        return LocalLarkRequest(
            request["schema_version"],
            transaction_id,
            expected_generation_id,
            selector.token,
            derived_processing_until,
        )
    except Exception:
        raise LarkRuntimeError("invalid-lark-request") from None
