"""Version-pinned native normalizers that consume authenticated redaction output.

The Lark v1 path intentionally targets the official Docx raw-content GET
response rather than ``docs +fetch``. The shortcut may attach visible comment
content, while the selected source grant excludes comments. A caller must bind
the exact current revision before and after acquisition. The bounded transport
decoder validates the closed CLI envelope, then only decoded ``data.content``
enters the redactor; the semantic normalizer consumes authenticated redaction
output.

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


class _Closed(type):
    def __call__(cls, *args, **kwargs):
        try:
            return super().__call__(*args, **kwargs)
        except NativeAdapterError:
            raise
        except Exception:
            _fail()


def _revision(value):
    if (type(value) is not str or len(value) > 20
            or re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None):
        _fail("invalid-revision")
    return value


@dataclass(frozen=True, repr=False)
class LarkNormalizationContext(metaclass=_Closed):
    expected_owner_id: str
    expected_source_snapshot_id: str
    expected_revision: str
    native_document_id: str
    product_version: str

    def __post_init__(self):
        try:
            adapters._identifier(self.expected_owner_id, "/")
            adapters._snapshot(self.expected_source_snapshot_id, "/")
            adapters._snapshot(self.native_document_id, "/")
            _revision(self.expected_revision)
            if self.product_version != LARK_RAW_CONTENT_ADAPTER.product_version:
                _fail("unsupported-adapter-version")
        except adapters.GraphValidationError as error:
            _fail(error.code)


def decode_lark_raw_content(raw):
    """Validate the exact CLI transport envelope and return decoded content bytes."""
    try:
        payload = adapters.decode_event_graph_json(raw)
        payload = adapters._object(payload, _ROOT_FIELDS, "/")
        if payload["ok"] is not True:
            _fail("native-response-error")
        if type(payload["identity"]) is not str or payload["identity"] != "user":
            _fail("native-identity-mismatch")
        data = adapters._object(payload["data"], _DATA_FIELDS, "/")
        content = adapters._string(
            data["content"], "/", 0, redaction.MAX_INPUT_BYTES,
            "input-limit")
        return content.encode("utf-8")
    except NativeAdapterError:
        raise
    except adapters.GraphValidationError as error:
        raise NativeAdapterError(error.code) from None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail()


def lark_native_locator_digest(native_document_id, revision):
    """Bind the privacy-preserving document identifier to one revision."""
    try:
        adapters._snapshot(native_document_id, "/")
        _revision(revision)
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
        if (type(context) is not LarkNormalizationContext
                or set(vars(context)) != {
                    item.name for item in fields(LarkNormalizationContext)}):
            _fail()
        context = LarkNormalizationContext(**vars(context))
        if (type(redaction_context) is not redaction.TrustContext
                or set(vars(redaction_context)) != {item.name for item in fields(redaction.TrustContext)}):
            _fail("invalid-context")
        redaction_context = redaction.TrustContext(**vars(redaction_context))
        if (type(expected_binding) is not redaction.SpanBinding
                or set(vars(expected_binding)) != {
                    item.name for item in fields(redaction.SpanBinding)}):
            _fail()
        binding = redaction.SpanBinding(**vars(expected_binding))
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
        content = adapters._string(
            spans[0].text, "/", 0, adapters.MAX_EVENT_BYTES)
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
