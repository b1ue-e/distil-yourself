"""Pure, exact-scope consent validation; credential verification belongs to the broker."""

from dataclasses import dataclass
import hashlib
import unicodedata
from typing import Any, Optional, Tuple

from . import adapters


COMMON_FIELDS = frozenset({
    "record_type", "record_id", "task_id", "issuer", "active_principal",
    "tenant_account", "selector", "operation", "purpose", "revision",
    "session_range", "issued_at", "expires_at", "derived_processing_until",
    "decision_digest", "revoked",
})
AUTHORITY_FIELDS = COMMON_FIELDS | {"content_owner", "authority_basis"}
MAX_GRANT_SECONDS = 24 * 60 * 60


class AuthorizationError(ValueError):
    """Code-only diagnostic; no source selectors or text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SessionRange:
    """Pinned inclusive native record offsets, never an open-ended tail."""

    start: int
    end: int


@dataclass(frozen=True)
class AuthorizationContext:
    """Externally verified request and issuer identities, not source assertions."""

    task_id: str
    active_principal: str
    tenant_account: str
    selector: str
    purpose: str
    revision: Optional[str]
    session_range: Optional[SessionRange]
    now: int
    task_active: bool
    authenticated_issuers: Tuple[str, ...]
    content_owner: Optional[str] = None


@dataclass(frozen=True)
class ContentGrant:
    record_type: str
    record_id: str
    task_id: str
    issuer: str
    active_principal: str
    tenant_account: str
    selector: str
    operation: str
    purpose: str
    revision: Optional[str]
    session_range: Optional[SessionRange]
    issued_at: int
    expires_at: int
    derived_processing_until: int
    decision_digest: str
    revoked: bool


@dataclass(frozen=True)
class AuthorityAttestation:
    record_type: str
    record_id: str
    task_id: str
    issuer: str
    active_principal: str
    tenant_account: str
    selector: str
    operation: str
    purpose: str
    revision: Optional[str]
    session_range: Optional[SessionRange]
    issued_at: int
    expires_at: int
    derived_processing_until: int
    decision_digest: str
    revoked: bool
    content_owner: str
    authority_basis: str


def _reject(code: str) -> None:
    raise AuthorizationError(code)


def _exact(value: Any, code: str, maximum: int = 4096) -> str:
    value = adapters._string(value, "/", 1, maximum, code)
    if (value != value.strip() or value.lower() == "latest"
            or any(char in value for char in "*?[]")
            or any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value)):
        _reject(code)
    return value


def _time(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 9223372036854775807:
        _reject("invalid-time-bound")
    return value


def _bound(revision: Any, session_range: Any) -> Optional[SessionRange]:
    if (revision is None) == (session_range is None):
        _reject("invalid-source-bound")
    if revision is not None:
        _exact(revision, "invalid-revision", 256)
        return None
    bounds = adapters._object(session_range, {"start", "end"}, "/")
    start, end = _time(bounds["start"]), _time(bounds["end"])
    if start > end:
        _reject("invalid-source-bound")
    return SessionRange(start, end)


def _validate(value: Any, context: AuthorizationContext, authority: bool):
    record = adapters._object(value, AUTHORITY_FIELDS if authority else COMMON_FIELDS, "/")
    if record["record_type"] != ("authority-attestation" if authority else "content-grant"):
        _reject("invalid-record-type")
    if type(context) is not AuthorizationContext:
        _reject("invalid-authorization-context")
    for field in ("record_id", "task_id", "issuer", "active_principal", "tenant_account", "purpose"):
        adapters._identifier(record[field], "/")
    _exact(record["selector"], "invalid-selector")
    bounds = _bound(record["revision"], record["session_range"])
    if record["operation"] != ("process-third-party" if authority else "read-content"):
        _reject("invalid-operation")
    for field in ("task_id", "active_principal", "tenant_account", "purpose"):
        adapters._identifier(getattr(context, field), "/")
    _exact(context.selector, "invalid-selector")
    if context.session_range is not None and type(context.session_range) is not SessionRange:
        _reject("invalid-authorization-context")
    context_range = (None if context.session_range is None else
                     {"start": context.session_range.start, "end": context.session_range.end})
    _bound(context.revision, context_range)
    for field in ("task_id", "active_principal", "tenant_account", "selector", "purpose", "revision"):
        if record[field] != getattr(context, field):
            _reject("authorization-context-mismatch")
    if bounds != context.session_range:
        _reject("authorization-context-mismatch")
    if type(context.task_active) is not bool or type(record["revoked"]) is not bool:
        _reject("invalid-type")
    if not context.task_active:
        _reject("task-inactive")
    if record["revoked"]:
        _reject("authorization-revoked")
    now = _time(context.now)
    issued, expiry, derived = (_time(record[field]) for field in
                               ("issued_at", "expires_at", "derived_processing_until"))
    if expiry <= issued or expiry - issued > MAX_GRANT_SECONDS or derived <= issued:
        _reject("invalid-time-bound")
    if issued > now:
        _reject("authorization-not-yet-valid")
    if now >= expiry:
        _reject("authorization-expired")
    if now >= derived:
        _reject("derived-processing-expired")
    if type(context.authenticated_issuers) is not tuple:
        _reject("invalid-authorization-context")
    for issuer in context.authenticated_issuers:
        adapters._identifier(issuer, "/")
    if record["issuer"] not in context.authenticated_issuers:
        _reject("unauthenticated-issuer")
    if authority:
        adapters._identifier(record["content_owner"], "/")
        adapters._identifier(context.content_owner, "/")
        if record["content_owner"] != context.content_owner:
            _reject("authorization-context-mismatch")
        if record["issuer"] == context.active_principal and record["content_owner"] != context.active_principal:
            _reject("self-attestation")
        if record["authority_basis"] not in ("verified-ownership", "policy", "participant-consent"):
            _reject("invalid-authority-basis")
    elif record["issuer"] != context.active_principal:
        _reject("invalid-grant-issuer")
    adapters._snapshot(record["decision_digest"], "/")
    decision = {key: item for key, item in record.items() if key != "decision_digest"}
    computed = "sha256:" + hashlib.sha256(adapters._canonical_bytes(decision)).hexdigest()
    if computed != record["decision_digest"]:
        _reject("decision-digest-mismatch")
    fields = dict(record, session_range=bounds)
    return AuthorityAttestation(**fields) if authority else ContentGrant(**fields)


def validate_content_grant(value: Any, *, context: AuthorizationContext) -> ContentGrant:
    """Validate current consent; this neither checks credentials nor reads content."""
    try:
        return _validate(value, context, False)
    except adapters.GraphValidationError as error:
        raise AuthorizationError(error.code) from None


def validate_authority_attestation(value: Any, *, context: AuthorizationContext) -> AuthorityAttestation:
    """Require independent, broker-authenticated authority for third-party content."""
    try:
        return _validate(value, context, True)
    except adapters.GraphValidationError as error:
        raise AuthorizationError(error.code) from None
