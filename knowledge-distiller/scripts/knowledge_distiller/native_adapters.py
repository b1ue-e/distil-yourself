"""Version-pinned native normalizers that consume authenticated redaction output.

The Lark v1 path intentionally targets the official Docx raw-content GET
response rather than ``docs +fetch``. The shortcut may attach visible comment
content, while the selected source grant excludes comments. A caller must bind
the exact current revision before and after acquisition and redact the opaque
response bytes before invoking this pure normalizer.

Raw content exposes neither native block structure nor per-block authorship.
Accordingly the canonical document contains one synthetic raw-content block,
records formatting loss, and leaves its author unresolved/claim-ineligible.
Availability or document ownership is never treated as proof of authorship.
"""

from dataclasses import dataclass, fields
import hashlib
import re

from . import adapters, redaction, sources


LARK_RAW_CONTENT_ADAPTER = adapters.AdapterIdentity(*adapters.LARK_RAW_CONTENT_ADAPTER)
_ROOT_FIELDS = frozenset(("ok", "identity", "data"))
_DATA_FIELDS = frozenset(("content",))
_BLOCK_ID = "lark-raw-content"
_NATIVE_BLOCK_ID = "raw-content"


class NativeAdapterError(ValueError):
    """Bounded code-only diagnostic."""

    def __init__(self, code):
        self.code = code if type(code) is str and re.fullmatch(r"[a-z0-9-]{1,64}", code) else "native-adapter-invalid"
        super().__init__(self.code)


def _fail(code="native-adapter-invalid"):
    raise NativeAdapterError(code)


@dataclass(frozen=True, repr=False)
class LarkNormalizationContext:
    expected_owner_id: str
    expected_source_snapshot_id: str
    expected_revision: str
    native_document_id: str
    product_version: str

    def __post_init__(self):
        try:
            adapters._identifier(self.expected_owner_id, "/")
            adapters._snapshot(self.expected_source_snapshot_id, "/")
            adapters._identifier(self.native_document_id, "/")
            if (type(self.expected_revision) is not str
                    or re.fullmatch(r"(?:0|[1-9][0-9]*)", self.expected_revision) is None):
                _fail("invalid-revision")
            if self.product_version != LARK_RAW_CONTENT_ADAPTER.product_version:
                _fail("unsupported-adapter-version")
        except adapters.GraphValidationError as error:
            _fail(error.code)


def _record(value, cls):
    if type(value) is not cls or set(vars(value)) != {item.name for item in fields(cls)}:
        _fail()
    try:
        return cls(**vars(value))
    except NativeAdapterError:
        raise
    except Exception:
        _fail()


def lark_native_locator_digest(native_document_id, revision):
    """Bind the privacy-preserving document identifier to one revision."""
    try:
        adapters._snapshot(native_document_id, "/")
        if type(revision) is not str or re.fullmatch(r"(?:0|[1-9][0-9]*)", revision) is None:
            _fail("invalid-revision")
    except adapters.GraphValidationError as error:
        _fail(error.code)
    raw = b"knowledge-distiller:lark-raw-content:v1\x00" + native_document_id.encode("ascii")
    raw += b"\x00" + revision.encode("ascii")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def normalize_lark_raw_content(result, *, context: LarkNormalizationContext,
                               redaction_context: redaction.TrustContext,
                               redaction_key: bytes,
                               expected_binding: redaction.SpanBinding) -> sources.CanonicalDocument:
    """Normalize one exact, redacted Docx raw-content response.

    The source snapshot and locator remain external trust anchors. This function
    performs no I/O, credential discovery, traversal, retries, or fallback.
    """
    try:
        context = _record(context, LarkNormalizationContext)
        if (type(redaction_context) is not redaction.TrustContext
                or set(vars(redaction_context)) != {item.name for item in fields(redaction.TrustContext)}):
            _fail("invalid-context")
        redaction_context = redaction.TrustContext(**vars(redaction_context))
        binding = redaction.SpanBinding(**vars(expected_binding)) if type(expected_binding) is redaction.SpanBinding else _fail()
        if (binding.source_snapshot_id != context.expected_source_snapshot_id
                or binding.native_locator_digest != lark_native_locator_digest(
                    context.native_document_id, context.expected_revision)
                or binding.actor_kind != "external" or binding.actor_id is not None
                or binding.actor_resolution != "unresolved"
                or redaction_context.owner_id != context.expected_owner_id):
            _fail("invalid-context")
        spans = redaction.validate_redaction_result(
            result, context=redaction_context, key=redaction_key,
            expected_bindings=(binding,))
        if len(spans) != 1:
            _fail("invalid-context")
        payload = adapters.decode_event_graph_json(spans[0].text.encode("utf-8"))
        payload = adapters._object(payload, _ROOT_FIELDS, "/")
        if payload["ok"] is not True:
            _fail("native-response-error")
        if type(payload["identity"]) is not str or payload["identity"] != "user":
            _fail("native-identity-mismatch")
        data = adapters._object(payload["data"], _DATA_FIELDS, "/")
        content = adapters._string(data["content"], "/", 0, adapters.MAX_EVENT_BYTES)
        canonical = {
            "schema_version": sources.DOCUMENT_SCHEMA,
            "adapter": {
                "name": LARK_RAW_CONTENT_ADAPTER.name,
                "adapter_version": LARK_RAW_CONTENT_ADAPTER.adapter_version,
                "product_version": LARK_RAW_CONTENT_ADAPTER.product_version,
                "native_schema_version": LARK_RAW_CONTENT_ADAPTER.native_schema_version,
            },
            "source_snapshot_id": context.expected_source_snapshot_id,
            "owner": {"kind": "user", "id": context.expected_owner_id,
                      "verification": "verified-principal"},
            "native_document_id": context.native_document_id,
            "revision": context.expected_revision,
            "blocks": [{
                "id": _BLOCK_ID, "native_block_id": _NATIVE_BLOCK_ID,
                "parent_block_id": None, "order": 0,
                "author": {"kind": "external", "id": None, "resolution": "unresolved"},
                "content_segments": [{"type": "text", "text": content,
                                      "media_type": None, "artifact_locator_id": None}],
                "artifact_locators": [], "claim_eligible": False,
            }],
            "fidelity_losses": [{
                "code": "non-semantic-formatting", "block_id": _BLOCK_ID,
                "native_fact": "formatting", "reason": "native-unavailable",
            }],
        }
        return sources.validate_canonical_document(
            canonical, context=sources.DocumentValidationContext(
                expected_owner_id=context.expected_owner_id,
                expected_source_snapshot_id=context.expected_source_snapshot_id,
                expected_revision=context.expected_revision, verified_authors=()))
    except NativeAdapterError:
        raise
    except (redaction.RedactionError, adapters.GraphValidationError,
            sources.SourceValidationError) as error:
        raise NativeAdapterError(error.code) from None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail()
