"""Exact, read-only source acquisition through explicitly trusted boundaries.

No production launcher, credential discovery, or native adapter is provided.
The injected Lark launcher must execute only the Docx raw-content GET, enforce
the requested timeout/stream byte limit
(including stderr, raising BufferError before retaining excess bytes),
pin the endpoint/TLS identity, forbid redirects and principal fallback, and
produce LarkResponse evidence (including JSON transport type and metadata
revision-before/after) from authenticated transport state, never document body
fields. Response bytes remain opaque; syntax and native content validation belong
to the isolated parser.
Credential variable names are declared by that launcher integration; this module
does not claim any particular environment variable is supported by lark-cli.

SessionIdentity and the pinned prefix digest are external trust anchors, never
assertions derived from session text. Parsers receive BrokerSnapshot bytes and
evidence only; credentials, locators, and ambient environment are excluded.
"""

from dataclasses import asdict, dataclass, field, fields
import hashlib
import re
import subprocess
from typing import Callable, Optional, Tuple

from . import adapters, authorization, source_io
from .adapters import MAX_GRAPH_BYTES
from .sources import OwnerBinding


class BrokerError(ValueError):
    """Bounded code-only diagnostic."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LarkRequest:
    selector: str = field(repr=False)
    revision: str


@dataclass(frozen=True)
class CredentialBinding:
    principal_kind: str
    active_principal: str
    tenant_account: str
    environment: Tuple[Tuple[str, str], ...] = field(repr=False)


@dataclass(frozen=True)
class LarkResponse:
    """Trusted launcher receipt, NOT a schema for lark-cli stdout."""

    returncode: int
    raw: bytes = field(repr=False)
    media_type: str
    principal_kind: str
    active_principal: str
    tenant_account: str
    selector_digest: str
    purpose: str
    revision_before: str
    revision_after: str
    content_owner: str
    product_version: str
    native_schema_digest: str
    redirected: bool
    fallback_principal: bool


@dataclass(frozen=True)
class SessionRequest:
    path: str = field(repr=False)
    project_id: str
    prefix_length: int
    prefix_digest: str


@dataclass(frozen=True)
class SessionIdentity:
    active_principal: str
    tenant_account: str
    project_id: str
    content_owner: str
    product_version: str
    native_schema_digest: str
    expected_owner_uid: Optional[int] = field(default=None, repr=False)


@dataclass(frozen=True)
class NativeEvidence:
    """Observed product/schema evidence; never a supported adapter tuple."""

    product: str
    product_version: str
    native_schema_digest: str
    project_id: Optional[str]


@dataclass(frozen=True)
class BrokerSnapshot:
    raw: bytes = field(repr=False)
    owner: OwnerBinding
    selector_digest: str
    raw_digest: str
    source_snapshot_id: str
    source_byte_count: int
    evidence: NativeEvidence


def _closed(value, cls, code: str) -> None:
    if type(value) is not cls or set(vars(value)) != {item.name for item in fields(cls)}:
        raise BrokerError(code)


def _authorize(grant, attestation, context) -> None:
    _closed(context, authorization.AuthorizationContext, "invalid-authorization-context")
    _closed(grant, authorization.ContentGrant, "invalid-authorization-record")
    _closed(attestation, authorization.AuthorityAttestation, "invalid-authorization-record")
    try:
        # Typed values are not proof of validation: revalidate their current
        # fields and exact context through the pure Task 1 authorization APIs.
        authorization.validate_content_grant(asdict(grant), context=context)
        authorization.validate_authority_attestation(asdict(attestation), context=context)
    except authorization.AuthorizationError as error:
        raise BrokerError(error.code) from None
    except (TypeError, ValueError, RecursionError):
        raise BrokerError("invalid-authorization-record") from None


def _identifier(value, code: str) -> None:
    try:
        adapters._identifier(value, "/")
    except adapters.GraphValidationError:
        raise BrokerError(code) from None


def _digest_value(value, code: str) -> None:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise BrokerError(code)


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def session_selector_commitment(path) -> str:
    if type(path) is not str:
        raise BrokerError("invalid-selector")
    try:
        raw = path.encode("utf-8")
    except UnicodeError:
        raise BrokerError("invalid-selector") from None
    if not raw or len(raw) > 4096 or b"\x00" in raw:
        raise BrokerError("invalid-selector")
    return _digest(b"local-session-selector\x00" + raw)


def _evidence(product: str, version, schema_digest, project_id=None) -> NativeEvidence:
    _identifier(version, "broker-response-invalid")
    _digest_value(schema_digest, "broker-response-invalid")
    if project_id is not None:
        _identifier(project_id, "broker-response-invalid")
    return NativeEvidence(product, version, schema_digest, project_id)


def _snapshot(raw: bytes, context, evidence: NativeEvidence) -> BrokerSnapshot:
    raw_digest = _digest(raw)
    # _authorize independently validates the attestation's owner against this
    # external context. The authenticated reader is not necessarily that owner.
    return BrokerSnapshot(raw, OwnerBinding("user", context.content_owner, "verified-principal"),
                          _digest(context.selector.encode("utf-8")), raw_digest, raw_digest,
                          len(raw), evidence)


def _lark_request(request, context) -> str:
    _closed(request, LarkRequest, "invalid-broker-request")
    # Only normalized docx URLs or one opaque alphanumeric document token.
    # No wiki indirection, query, fragment, alternate port, userinfo, or aliases.
    selector = request.selector
    token = r"[A-Za-z0-9]+"
    url = r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.(?:larkoffice\.com|larksuite\.com|feishu\.cn)/docx/" + token
    if (type(selector) is not str or len(selector) > 4096
            or re.fullmatch(r"(?:" + token + "|" + url + ")", selector) is None):
        raise BrokerError("invalid-selector")
    # CLI help documents -1 as latest. Nonnegative canonical decimal revisions
    # cannot become options, aliases, or a request for an implicit current view.
    if (type(request.revision) is not str or len(request.revision) > 20
            or re.fullmatch(r"(?:0|[1-9][0-9]*)", request.revision) is None):
        raise BrokerError("invalid-revision")
    if (context.session_range is not None or selector != context.selector
            or request.revision != context.revision):
        raise BrokerError("authorization-context-mismatch")
    return selector.rsplit("/", 1)[-1]


def _credentials(resolver, required_variables, context) -> dict:
    if type(required_variables) is not tuple or not 1 <= len(required_variables) <= 8:
        raise BrokerError("invalid-credentials")
    for name in required_variables:
        if (type(name) is not str or len(name) > 128
                or re.fullmatch(r"LARK_[A-Z0-9_]+(?:TOKEN|SECRET)", name) is None):
            raise BrokerError("invalid-credentials")
    if len(set(required_variables)) != len(required_variables):
        raise BrokerError("invalid-credentials")
    try:
        binding = resolver(active_principal=context.active_principal,
                           tenant_account=context.tenant_account,
                           required_variables=required_variables)
    except Exception:
        raise BrokerError("invalid-credentials") from None
    _closed(binding, CredentialBinding, "invalid-credentials")
    for name in ("principal_kind", "active_principal", "tenant_account"):
        _identifier(getattr(binding, name), "invalid-credentials")
    if (binding.principal_kind != "user" or binding.active_principal != context.active_principal
            or binding.tenant_account != context.tenant_account
            or type(binding.environment) is not tuple
            or len(binding.environment) != len(required_variables)):
        raise BrokerError("invalid-credentials")
    env = {}
    for pair in binding.environment:
        if type(pair) is not tuple or len(pair) != 2:
            raise BrokerError("invalid-credentials")
        name, value = pair
        if type(name) is not str or name not in required_variables or name in env:
            raise BrokerError("invalid-credentials")
        if type(value) is not str or not 1 <= len(value) <= 8192 or "\x00" in value:
            raise BrokerError("invalid-credentials")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise BrokerError("invalid-credentials") from None
        env[name] = value
    return env


def fetch_lark(request: LarkRequest, *, grant: authorization.ContentGrant,
               attestation: authorization.AuthorityAttestation,
               context: authorization.AuthorizationContext, runner: Callable,
               credential_resolver: Callable,
               required_auth_variables: Tuple[str, ...]) -> BrokerSnapshot:
    """Construct exactly one approved command; injected launcher must bound I/O.

    No default runner exists. A plain subprocess CompletedProcess cannot provide
    verified identity, redirect, or revision evidence and is always rejected.
    """
    _authorize(grant, attestation, context)
    document_token = _lark_request(request, context)
    env = _credentials(credential_resolver, required_auth_variables, context)
    argv = (
        "lark-cli", "api", "GET",
        "/open-apis/docx/v1/documents/" + document_token + "/raw_content",
        "--as", "user")
    try:
        response = runner(argv, env=env, shell=False, timeout=30, max_bytes=MAX_GRAPH_BYTES,
                          allow_redirects=False, allow_fallback_principal=False)
    except (subprocess.TimeoutExpired, TimeoutError):
        raise BrokerError("broker-timeout") from None
    except subprocess.CalledProcessError:
        raise BrokerError("broker-command-failed") from None
    except BufferError:
        raise BrokerError("broker-response-too-large") from None
    except OSError:
        raise BrokerError("broker-unavailable") from None
    except Exception:
        raise BrokerError("broker-response-invalid") from None
    _closed(response, LarkResponse, "broker-response-invalid")
    if (type(response.returncode) is not int or type(response.raw) is not bytes
            or type(response.media_type) is not str or response.media_type != "application/json"):
        raise BrokerError("broker-response-invalid")
    if len(response.raw) > MAX_GRAPH_BYTES:
        raise BrokerError("broker-response-too-large")
    if response.returncode != 0:
        raise BrokerError("broker-command-failed")
    for name in ("principal_kind", "active_principal", "tenant_account", "selector_digest",
                 "purpose", "revision_before", "revision_after", "content_owner"):
        _identifier(getattr(response, name), "broker-response-invalid")
    if (response.redirected is not False or response.fallback_principal is not False
            or response.principal_kind != "user"
            or response.active_principal != context.active_principal
            or response.tenant_account != context.tenant_account
            or response.selector_digest != _digest(context.selector.encode("utf-8"))
            or response.purpose != context.purpose
            or response.revision_before != context.revision or response.revision_after != context.revision
            or response.content_owner != context.content_owner):
        raise BrokerError("broker-evidence-mismatch")
    evidence = _evidence("lark", response.product_version, response.native_schema_digest)
    return _snapshot(response.raw, context, evidence)


def read_local_session(request: SessionRequest, *, grant: authorization.ContentGrant,
                       attestation: authorization.AuthorityAttestation,
                       context: authorization.AuthorizationContext,
                       identity: SessionIdentity) -> BrokerSnapshot:
    """Read an exact closed byte prefix; nonzero-start ranges are unsupported.

    The authorized inclusive end becomes an exclusive prefix length with +1.
    Identity and prefix digest must come from the trusted caller, never the file.
    Later appends are excluded; an edited/reordered/truncated prefix is rejected.
    """
    _authorize(grant, attestation, context)
    _closed(request, SessionRequest, "invalid-broker-request")
    _closed(identity, SessionIdentity, "invalid-broker-request")
    for name in ("active_principal", "tenant_account", "content_owner", "project_id"):
        _identifier(getattr(identity, name), "invalid-broker-request")
    _identifier(request.project_id, "invalid-broker-request")
    if (identity.expected_owner_uid is not None
            and (type(identity.expected_owner_uid) is not int
                 or not 0 <= identity.expected_owner_uid <= 9223372036854775807)):
        raise BrokerError("invalid-broker-request")
    _digest_value(request.prefix_digest, "invalid-source-bound")
    if (context.revision is not None or context.session_range is None
            or context.session_range.start != 0 or type(request.prefix_length) is not int
            or request.prefix_length != context.session_range.end + 1
            or not 0 < request.prefix_length <= MAX_GRAPH_BYTES):
        raise BrokerError("invalid-source-bound")
    if type(request.path) is not str:
        raise BrokerError("invalid-broker-request")
    selector_matches = (
        request.path == context.selector
        or session_selector_commitment(request.path) == context.selector)
    if (not selector_matches
            or identity.active_principal != context.active_principal
            or identity.tenant_account != context.tenant_account
            or identity.content_owner != context.content_owner or identity.project_id != request.project_id):
        raise BrokerError("authorization-context-mismatch")
    evidence = _evidence("codex", identity.product_version, identity.native_schema_digest, identity.project_id)
    try:
        raw = source_io.read_source(request.path, max_bytes=MAX_GRAPH_BYTES,
                                    prefix_length=request.prefix_length,
                                    expected_digest=request.prefix_digest,
                                    expected_owner_uid=identity.expected_owner_uid)
    except source_io.SourceIOError as error:
        raise BrokerError(error.code) from None
    return _snapshot(raw, context, evidence)
