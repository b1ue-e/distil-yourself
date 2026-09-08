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

from dataclasses import dataclass, field, fields
import hashlib
import json
import re
import sys
from typing import Optional, Tuple

from . import adapters, redaction, sources


LARK_RAW_CONTENT_ADAPTER = adapters.AdapterIdentity(*adapters.LARK_RAW_CONTENT_ADAPTER)
LARK_NATIVE_SCHEMA_DIGEST = (
    "sha256:9ea00db953bf91884d198de7a10282a0c519cefc0065b5f113bc8cce528d273f"
)
CODEX_ROLLOUT_ADAPTER = adapters.AdapterIdentity(*adapters.CODEX_ROLLOUT_ADAPTER)
_CODEX_SCHEMA_DESCRIPTOR = (
    b"codex-rollout-jsonl-v1\x00outer:timestamp,type,payload,ordinal\x00"
    b"product:0.153.0\x00projection:response-item,compacted\x00"
    b"ordering:contiguous-ordinal-from-zero"
)
CODEX_NATIVE_SCHEMA_DIGEST = "sha256:" + hashlib.sha256(_CODEX_SCHEMA_DESCRIPTOR).hexdigest()
MAX_CODEX_RECORDS = 100_000
_ROOT_FIELDS = frozenset(("ok", "identity", "data"))
_DATA_FIELDS = frozenset(("content",))
_BLOCK_ID = "lark-raw-content"
_NATIVE_BLOCK_ID = "raw-content"

_CODEX_LINE_FIELDS = frozenset(("timestamp", "type", "payload", "ordinal"))
_SESSION_REQUIRED = frozenset((
    "session_id", "id", "timestamp", "cwd", "originator", "cli_version", "source",
    "thread_source", "model_provider", "base_instructions", "context_window", "history_mode",
))
_SESSION_OPTIONAL = frozenset((
    "agent_nickname", "agent_path", "multi_agent_version", "parent_thread_id",
    "forked_from_id", "subagent_history_start_ordinal",
))
_MESSAGE_FIELDS = frozenset((
    "type", "id", "role", "content", "phase", "internal_chat_message_metadata_passthrough",
))
_REASONING_FIELDS = frozenset((
    "type", "id", "summary", "encrypted_content", "internal_chat_message_metadata_passthrough",
))
_FUNCTION_CALL_FIELDS = frozenset((
    "type", "id", "name", "namespace", "arguments", "call_id",
    "internal_chat_message_metadata_passthrough",
))
_CUSTOM_CALL_FIELDS = frozenset((
    "type", "id", "name", "input", "call_id", "status",
    "internal_chat_message_metadata_passthrough",
))
_TOOL_OUTPUT_FIELDS = frozenset((
    "type", "id", "call_id", "output", "internal_chat_message_metadata_passthrough",
))
_METADATA_FIELDS = frozenset((
    "turn_id", "create_time", "content_item_kinds", "cell_id", "executed_tool_calls",
    "tool_calls_complete",
))
_CONTEXT_ITEM_KINDS = frozenset((
    "environments.environment_context", "plugins.recommendations",
))
_DEVELOPER_CONTEXT_ITEM_KINDS = frozenset((
    "apps.instructions", "collaboration_mode.instructions", "host_skills.instructions",
    "model_switch.instructions", "multi_agent.mode_instructions",
    "multi_agent.role_instructions", "multi_agent.usage_hint",
    "permissions.approved_command_prefix_saved", "permissions.instructions",
    "plugins.usage_instructions",
))
_TURN_CONTEXT_FIELDS = frozenset((
    "active_permission_profile", "approval_policy", "approvals_reviewer", "collaboration_mode",
    "comp_hash", "current_date", "cwd", "effort", "file_system_sandbox_policy", "model",
    "multi_agent_version", "permission_profile", "personality", "realtime_active",
    "root_turn_id", "sandbox_policy", "summary", "timezone", "turn_id", "workspace_roots",
))
_TOKEN_USAGE_FIELDS = frozenset((
    "response_id", "root_turn_id", "session_id", "thread_id", "turn_id", "usage",
    "thread_token_usage", "turn_token_usage",
))
_COMPACTED_FIELDS = frozenset((
    "message", "replacement_history", "guardian_history", "latest_token_usage_record",
    "compaction_response_id", "window_number", "first_window_id", "previous_window_id",
    "window_id",
))


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
            adapters._snapshot(self.expected_owner_id, "/")
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


class _NativeDecimal(str):
    """Exact finite native JSON decimal retained only for shape validation."""


def _native_decimal(value):
    if (type(value) is not str or len(value) > 64
            or re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value) is None):
        raise adapters.GraphValidationError("invalid-number", "/")
    return _NativeDecimal(value)


def _decode_native_json(raw):
    if type(raw) is not bytes:
        raise adapters.GraphValidationError("invalid-type", "/")
    if len(raw) > adapters.MAX_GRAPH_BYTES:
        raise adapters.GraphValidationError("graph-too-large", "/")
    total_tokens = adapters._preflight_event_graph_json(raw)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise adapters.GraphValidationError("invalid-utf8", "/") from None
    projected_bytes = (
        sys.getsizeof(raw) + 2 * sys.getsizeof(text) + adapters.MAX_GRAPH_BYTES
        + total_tokens * adapters.JSON_TOKEN_OVERHEAD_BYTES)
    if projected_bytes > adapters.MAX_JSON_PARSER_BYTES:
        raise adapters.GraphValidationError("json-resource-limit", "/")
    try:
        value = json.loads(
            text, object_pairs_hook=adapters._decoded_object,
            parse_float=_native_decimal, parse_int=adapters._decoded_integer,
            parse_constant=adapters._invalid_json_number)
    except adapters.GraphValidationError:
        raise
    except RecursionError:
        raise adapters.GraphValidationError("json-too-deep", "/") from None
    except json.JSONDecodeError:
        raise adapters.GraphValidationError("invalid-json", "/") from None
    adapters._reject_invalid_unicode_scalars(value)
    return value


def _digest(value):
    raw = value if type(value) is bytes else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _native_id(domain, value):
    adapters._identifier(value, "/")
    return _digest(("knowledge-distiller:codex:" + domain + ":" + value).encode("utf-8"))


def _closed_object(value, required, optional=frozenset()):
    if type(value) is not dict:
        _fail("invalid-type")
    keys = set(value)
    if keys - required - optional:
        _fail("unknown-field")
    if required - keys:
        _fail("missing-field")
    return value


def _text(value, maximum=redaction.MAX_INPUT_BYTES):
    try:
        return adapters._string(value, "/", 0, maximum)
    except adapters.GraphValidationError as error:
        _fail(error.code)


def _native_identifier(value):
    try:
        return adapters._identifier(value, "/")
    except adapters.GraphValidationError as error:
        _fail(error.code)


@dataclass(frozen=True, repr=False)
class CodexNormalizationContext(metaclass=_Closed):
    expected_owner_id: str
    expected_source_snapshot_id: str
    project_id: str
    product_version: str
    native_schema_digest: str

    def __post_init__(self):
        try:
            adapters._identifier(self.expected_owner_id, "/")
            adapters._snapshot(self.expected_source_snapshot_id, "/")
            adapters._identifier(self.project_id, "/")
            if (self.product_version != CODEX_ROLLOUT_ADAPTER.product_version
                    or self.native_schema_digest != CODEX_NATIVE_SCHEMA_DIGEST):
                _fail("unsupported-adapter-version")
        except adapters.GraphValidationError as error:
            _fail(error.code)


@dataclass(frozen=True, repr=False)
class _CodexEvent(metaclass=_Closed):
    ordinal: int
    slot: int
    timestamp: str
    native_id: str
    turn_id: Optional[str]
    event_type: str
    actor_kind: str
    actor_id: Optional[str]
    actor_resolution: str
    segment_type: str
    text: str = field(repr=False)
    correlation_id: Optional[str] = None
    semantic_markers: Tuple[str, ...] = ()

    def __post_init__(self):
        if (type(self.ordinal) is not int or type(self.slot) is not int
                or self.ordinal < 0 or not 0 <= self.slot <= 3):
            _fail("invalid-native-order")
        _text(self.timestamp, 128)
        _native_identifier(self.native_id)
        if self.turn_id is not None:
            _native_identifier(self.turn_id)
        if self.event_type not in ("message", "tool-call", "tool-result", "compacted-summary"):
            _fail("unsupported-native-record")
        if self.actor_kind not in ("user", "assistant", "agent", "tool", "system"):
            _fail("invalid-context")
        if self.actor_id is not None:
            adapters._snapshot(self.actor_id, "/")
        if self.actor_resolution not in ("verified-owner", "native"):
            _fail("invalid-context")
        if self.segment_type not in (
                "text", "tool-call-summary", "tool-result-summary", "compacted-summary"):
            _fail("unsupported-content")
        _text(self.text)
        if self.correlation_id is not None:
            adapters._snapshot(self.correlation_id, "/")
        if type(self.semantic_markers) is not tuple:
            _fail()
        if self.semantic_markers not in ((), ("compaction",)):
            _fail("unsupported-causality")


@dataclass(frozen=True, repr=False)
class CodexPreparedSession(metaclass=_Closed):
    source_snapshot_id: str
    project_id: str
    root_session_id: str
    session_id: str
    thread_id: str
    events: Tuple[_CodexEvent, ...] = field(repr=False)
    redaction_events: Tuple[object, ...] = field(repr=False)
    expected_bindings: Tuple[redaction.SpanBinding, ...] = field(repr=False)
    reasoning_omitted: bool

    def __post_init__(self):
        adapters._identifier(self.project_id, "/")
        for value in (self.source_snapshot_id, self.root_session_id,
                      self.session_id, self.thread_id):
            adapters._snapshot(value, "/")
        if (type(self.events) is not tuple or type(self.redaction_events) is not tuple
                or type(self.expected_bindings) is not tuple or type(self.reasoning_omitted) is not bool):
            _fail()
        if (not self.events or len(self.events) > redaction.MAX_SPANS
                or len(self.expected_bindings) != len(self.events)
                or len(self.redaction_events) != 2 * len(self.events)):
            _fail("input-limit")


def _metadata(value):
    metadata = _closed_object(value, frozenset(), _METADATA_FIELDS)
    if "turn_id" in metadata:
        _native_identifier(metadata["turn_id"])
    if "create_time" in metadata and type(metadata["create_time"]) not in (int, _NativeDecimal):
        _fail("invalid-type")
    if "content_item_kinds" in metadata:
        if (type(metadata["content_item_kinds"]) is not list
                or len(metadata["content_item_kinds"]) > redaction.MAX_CHUNKS
                or any(type(item) is not str for item in metadata["content_item_kinds"])):
            _fail("invalid-type")
    if "cell_id" in metadata:
        _native_identifier(metadata["cell_id"])
    if "executed_tool_calls" in metadata:
        if type(metadata["executed_tool_calls"]) is not list:
            _fail("invalid-type")
        if metadata["executed_tool_calls"]:
            _fail("unsupported-causality")
    if "tool_calls_complete" in metadata:
        if type(metadata["tool_calls_complete"]) is not bool:
            _fail("invalid-type")
        if not metadata["tool_calls_complete"]:
            _fail("unsupported-causality")
    return metadata


def _render_native_json(value):
    if type(value) is _NativeDecimal:
        return str(value)
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is list:
        return "[" + ",".join(_render_native_json(item) for item in value) + "]"
    if type(value) is dict:
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, allow_nan=False) + ":" + _render_native_json(value[key])
            for key in sorted(value)) + "}"
    _fail("invalid-type")


def _embedded_text(value, *, require_json=False):
    value = _text(value)
    stripped = value.strip()
    if not stripped:
        if require_json:
            _fail("invalid-json")
        return value
    try:
        decoded = _decode_native_json(stripped.encode("utf-8"))
    except adapters.GraphValidationError as error:
        if error.code == "invalid-json" and not require_json:
            return value
        _fail(error.code)
    return _text(_render_native_json(decoded))


def _content_text(content, role, metadata):
    if type(content) is not list or len(content) > redaction.MAX_CHUNKS:
        _fail("invalid-type")
    kinds = metadata.get("content_item_kinds")
    if type(kinds) is not list or len(kinds) != len(content):
        _fail("unsupported-content")
    selected = []
    for item, kind in zip(content, kinds):
        item = _closed_object(item, frozenset(("type", "text")))
        item_type = item["type"]
        if role == "user":
            if kind == "user.text":
                if item_type != "input_text":
                    _fail("unsupported-content")
                selected.append(_text(item["text"]))
            elif kind not in _CONTEXT_ITEM_KINDS:
                _fail("unsupported-content")
        elif role == "assistant":
            if kind != "unknown" or item_type != "output_text":
                _fail("unsupported-content")
            selected.append(_text(item["text"]))
        elif role == "developer":
            if kind not in _DEVELOPER_CONTEXT_ITEM_KINDS or item_type != "input_text":
                _fail("unsupported-content")
        else:
            _fail("unsupported-content")
    if len(selected) > 1:
        _fail("unsupported-content")
    return selected[0] if selected else ""


def _output_text(value):
    if type(value) is str:
        return _embedded_text(value)
    if type(value) is not list or len(value) > redaction.MAX_CHUNKS:
        _fail("unsupported-content")
    if len(value) > 1:
        _fail("unsupported-content")
    parts = []
    for item in value:
        if type(item) is not dict or item.get("type") != "input_text":
            _fail("unsupported-content")
        item = _closed_object(item, frozenset(("type", "text")))
        parts.append(_text(item["text"]))
    return "\n".join(parts)


def _event_binding(event, context, redaction_context, root_ids, reasoning_omitted):
    structural = json.dumps((
        event.ordinal, event.slot, event.timestamp, event.native_id, event.turn_id,
        event.event_type, event.actor_kind, event.actor_id, event.actor_resolution,
        event.segment_type, _digest(event.text), event.correlation_id,
        event.semantic_markers, context.project_id,
        context.expected_source_snapshot_id, root_ids, reasoning_omitted,
    ), ensure_ascii=True, separators=(",", ":"))
    return redaction.SpanBinding(
        context_id=redaction_context.context_id,
        source_snapshot_id=context.expected_source_snapshot_id,
        native_locator_digest=_digest(structural),
        actor_kind=event.actor_kind,
        actor_id=event.actor_id,
        actor_resolution=event.actor_resolution)


def _ignored_payload(payload, fields):
    _closed_object(payload, fields)


def _parse_session_meta(payload, context):
    payload = _closed_object(payload, _SESSION_REQUIRED, _SESSION_OPTIONAL)
    if any(name in payload for name in (
            "agent_nickname", "agent_path", "parent_thread_id", "forked_from_id",
            "subagent_history_start_ordinal")):
        _fail("unsupported-causality")
    for name in ("session_id", "id", "timestamp", "cwd", "originator", "cli_version",
                 "thread_source", "model_provider", "history_mode"):
        if type(payload[name]) is not str:
            _fail("invalid-type")
    _native_identifier(payload["session_id"])
    _native_identifier(payload["id"])
    _text(payload["timestamp"], 128)
    _text(payload["cwd"], 4096)
    if payload["cli_version"] != context.product_version:
        _fail("unsupported-adapter-version")
    if payload["source"] != "cli" or payload["thread_source"] not in ("cli", "user"):
        _fail("unsupported-causality")
    if type(payload["base_instructions"]) is not dict or type(payload["context_window"]) is not dict:
        _fail("invalid-type")
    for optional in ("multi_agent_version",):
        if optional in payload and type(payload[optional]) is not str:
            _fail("invalid-type")
    return (_native_id("root-session", payload["session_id"]),
            _native_id("session", payload["id"]), _native_id("thread", payload["id"]))


def _parse_response(payload, ordinal, timestamp, context, events, ids, calls, outputs):
    if type(payload) is not dict or type(payload.get("type")) is not str:
        _fail("invalid-type")
    kind = payload["type"]
    if kind == "message":
        payload = _closed_object(payload, _MESSAGE_FIELDS - frozenset(("phase",)),
                                 frozenset(("phase",)))
        if "phase" in payload and payload["phase"] not in (None, "commentary", "final_answer"):
            _fail("unsupported-content")
        item_id = _native_identifier(payload["id"])
        if item_id in ids:
            _fail("duplicate-native-event-id")
        ids.add(item_id)
        role = payload["role"]
        if type(role) is not str:
            _fail("invalid-type")
        metadata = _metadata(payload["internal_chat_message_metadata_passthrough"])
        text = _content_text(payload["content"], role, metadata)
        if not text:
            return False
        turn_id = metadata.get("turn_id")
        if turn_id is None:
            _fail("unsupported-causality")
        if role == "user":
            actor_kind, actor_id, resolution = "user", context.expected_owner_id, "verified-owner"
        else:
            actor_kind, actor_id, resolution = "assistant", _native_id("actor", "assistant"), "native"
        events.append(_CodexEvent(
            ordinal, 0, timestamp, item_id, turn_id, "message", actor_kind, actor_id,
            resolution, "text", text))
        return False
    if kind == "reasoning":
        payload = _closed_object(payload, _REASONING_FIELDS)
        item_id = _native_identifier(payload["id"])
        if item_id in ids:
            _fail("duplicate-native-event-id")
        ids.add(item_id)
        _metadata(payload["internal_chat_message_metadata_passthrough"])
        if type(payload["summary"]) is not list or type(payload["encrypted_content"]) is not str:
            _fail("invalid-type")
        if payload["summary"]:
            _fail("unsupported-content")
        return bool(payload["encrypted_content"])
    if kind in ("function_call", "custom_tool_call"):
        required = (_FUNCTION_CALL_FIELDS - frozenset(("namespace",)) if kind == "function_call"
                    else _CUSTOM_CALL_FIELDS)
        optional = frozenset(("namespace",)) if kind == "function_call" else frozenset()
        payload = _closed_object(payload, required, optional)
        if kind == "function_call" and "namespace" in payload \
                and payload["namespace"] is not None and type(payload["namespace"]) is not str:
            _fail("invalid-type")
        if kind == "custom_tool_call" and payload["status"] != "completed":
            _fail("unsupported-causality")
        item_id = _native_identifier(payload["id"])
        call_id = _native_identifier(payload["call_id"])
        if item_id in ids or call_id in calls:
            _fail("duplicate-native-event-id")
        ids.add(item_id)
        metadata = _metadata(payload["internal_chat_message_metadata_passthrough"])
        name = _native_identifier(payload["name"])
        input_field = "arguments" if kind == "function_call" else "input"
        text = name + "\n" + _embedded_text(
            payload[input_field], require_json=kind == "function_call")
        correlation = _native_id("call", call_id)
        event = _CodexEvent(
            ordinal, 0, timestamp, item_id, metadata.get("turn_id"), "tool-call", "tool",
            _native_id("tool", name), "native", "tool-call-summary", text,
            correlation_id=correlation)
        calls[call_id] = (kind, event)
        events.append(event)
        return False
    if kind in ("function_call_output", "custom_tool_call_output"):
        payload = _closed_object(payload, _TOOL_OUTPUT_FIELDS)
        item_id = _native_identifier(payload["id"])
        call_id = _native_identifier(payload["call_id"])
        if item_id in ids or call_id in outputs:
            _fail("duplicate-native-event-id")
        ids.add(item_id)
        metadata = _metadata(payload["internal_chat_message_metadata_passthrough"])
        event = _CodexEvent(
            ordinal, 0, timestamp, item_id, metadata.get("turn_id"), "tool-result", "tool",
            None, "native", "tool-result-summary", _output_text(payload["output"]),
            correlation_id=_native_id("call", call_id))
        outputs[call_id] = (kind, event)
        events.append(event)
        return False
    if kind == "agent_message":
        _fail("unsupported-causality")
    _fail("unsupported-native-record")


def _parse_event_message(payload):
    if type(payload) is not dict or type(payload.get("type")) is not str:
        _fail("invalid-type")
    kind = payload["type"]
    fields_by_kind = {
        "task_started": (frozenset(("type", "turn_id", "model_context_window",
                                    "collaboration_mode_kind", "started_at")), frozenset()),
        "task_complete": (frozenset(("type", "turn_id", "last_agent_message", "started_at",
                                     "completed_at", "duration_ms", "time_to_first_token_ms")),
                          frozenset(("error",))),
        "token_count": (frozenset(("type", "info", "rate_limits")), frozenset()),
        "thread_settings_applied": (frozenset(("type", "thread_id", "thread_settings")), frozenset()),
    }
    if kind in ("turn_aborted", "thread_rolled_back", "item_completed"):
        _fail("unsupported-causality")
    schema = fields_by_kind.get(kind)
    if schema is None:
        _fail("unsupported-native-record")
    _closed_object(payload, *schema)
    if kind in ("task_started", "task_complete"):
        turn_id = _native_identifier(payload["turn_id"])
        if kind == "task_started":
            if (type(payload["model_context_window"]) is not int
                    or payload["model_context_window"] <= 0
                    or type(payload["collaboration_mode_kind"]) is not str
                    or not payload["collaboration_mode_kind"]
                    or len(payload["collaboration_mode_kind"].encode("utf-8")) > 128
                    or type(payload["started_at"]) is not int
                    or payload["started_at"] < 0):
                _fail("invalid-type")
            return kind, turn_id, payload["started_at"]
        last = payload["last_agent_message"]
        if last is not None and type(last) is not str:
            _fail("invalid-type")
        if last is not None:
            _text(last)
        for name in ("started_at", "completed_at", "duration_ms", "time_to_first_token_ms"):
            if type(payload[name]) is not int or payload[name] < 0:
                _fail("invalid-type")
        if (payload["completed_at"] < payload["started_at"]
                or payload["time_to_first_token_ms"] > payload["duration_ms"]):
            _fail("unsupported-causality")
        if payload.get("error") is not None:
            _fail("unsupported-causality")
        return kind, turn_id, (
            last, payload["started_at"], payload["completed_at"])
    return kind, None, None


def _parse_compacted(payload, ordinal, timestamp, events, ids):
    payload = _closed_object(payload, _COMPACTED_FIELDS)
    for name in ("replacement_history", "guardian_history"):
        if type(payload[name]) is not list:
            _fail("invalid-type")
    native_id = _native_identifier(payload["compaction_response_id"])
    if native_id in ids:
        _fail("duplicate-native-event-id")
    ids.add(native_id)
    events.append(_CodexEvent(
        ordinal, 0, timestamp, native_id, None, "compacted-summary", "system",
        _native_id("actor", "system"), "native", "compacted-summary",
        _text(payload["message"]), semantic_markers=("compaction",)))


def _prepared(context, redaction_context, root_ids, events, reasoning_omitted):
    bindings = tuple(_event_binding(
        event, context, redaction_context, root_ids, reasoning_omitted) for event in events)
    redaction_events = []
    for event, binding in zip(events, bindings):
        redaction_events.extend((redaction.SourceSpan(binding),
                                 redaction.SourceChunk(event.text.encode("utf-8"))))
    return CodexPreparedSession(
        context.expected_source_snapshot_id, context.project_id, *root_ids, tuple(events),
        tuple(redaction_events), bindings, reasoning_omitted)


def prepare_codex_session(raw, *, context: CodexNormalizationContext,
                          redaction_context: redaction.TrustContext) -> CodexPreparedSession:
    """Strictly decode one closed Codex rollout prefix into redaction spans."""
    try:
        if type(context) is not CodexNormalizationContext or set(vars(context)) != {
                item.name for item in fields(CodexNormalizationContext)}:
            _fail("invalid-context")
        context = CodexNormalizationContext(**vars(context))
        if type(redaction_context) is not redaction.TrustContext or set(vars(redaction_context)) != {
                item.name for item in fields(redaction.TrustContext)}:
            _fail("invalid-context")
        redaction_context = redaction.TrustContext(**vars(redaction_context))
        if redaction_context.owner_id != context.expected_owner_id:
            _fail("invalid-context")
        if type(raw) is not bytes or len(raw) > adapters.MAX_GRAPH_BYTES:
            _fail("graph-too-large")
        if not raw or not raw.endswith(b"\n"):
            _fail("partial-json-line")
        if _digest(raw) != context.expected_source_snapshot_id:
            _fail("source-snapshot-context-mismatch")
        record_count = raw.count(b"\n")
        if record_count > MAX_CODEX_RECORDS:
            _fail("input-limit")
        lines = raw[:-1].split(b"\n")
        if len(lines) != record_count or any(not line for line in lines):
            _fail("invalid-json")
        events = []
        ids = set()
        calls = {}
        outputs = {}
        starts = {}
        completions = []
        root_ids = None
        reasoning_omitted = False
        text_total = 0
        for index, line in enumerate(lines):
            event_start = len(events)
            record = adapters._object(_decode_native_json(line), _CODEX_LINE_FIELDS, "/")
            if type(record["ordinal"]) is not int or record["ordinal"] != index:
                _fail("invalid-native-order")
            timestamp = _text(record["timestamp"], 128)
            if type(record["type"]) is not str or type(record["payload"]) is not dict:
                _fail("invalid-type")
            kind = record["type"]
            if kind == "session_meta":
                if index != 0 or root_ids is not None:
                    _fail("mixed-session")
                root_ids = _parse_session_meta(record["payload"], context)
            elif root_ids is None:
                _fail("mixed-session")
            elif kind == "response_item":
                reasoning_omitted |= _parse_response(
                    record["payload"], index, timestamp, context, events, ids, calls, outputs)
            elif kind == "event_msg":
                event_kind, turn_id, detail = _parse_event_message(record["payload"])
                if event_kind == "task_started":
                    if turn_id in starts:
                        _fail("unsupported-causality")
                    starts[turn_id] = (index, detail)
                elif event_kind == "task_complete":
                    completions.append((turn_id, *detail, index))
            elif kind == "compacted":
                _parse_compacted(record["payload"], index, timestamp, events, ids)
            elif kind == "turn_context":
                _ignored_payload(record["payload"], _TURN_CONTEXT_FIELDS)
            elif kind == "token_usage_record":
                _ignored_payload(record["payload"], _TOKEN_USAGE_FIELDS)
            elif kind == "world_state":
                _ignored_payload(record["payload"], frozenset(("full", "state")))
            elif kind == "inter_agent_communication_metadata":
                payload = _closed_object(record["payload"], frozenset(("trigger_turn",)))
                if type(payload["trigger_turn"]) is not bool:
                    _fail("invalid-type")
                _fail("unsupported-causality")
            else:
                _fail("unsupported-native-record")
            if len(events) > redaction.MAX_SPANS:
                _fail("input-limit")
            for event in events[event_start:]:
                text_total += len(event.text.encode("utf-8"))
                if text_total > redaction.MAX_INPUT_BYTES:
                    _fail("input-limit")
        if root_ids is None:
            _fail("mixed-session")
        if not events:
            _fail("unsupported-content")
        if set(calls) != set(outputs):
            _fail("unresolved-tool-flow")
        for call_id, (call_kind, call) in calls.items():
            output_kind, output = outputs[call_id]
            if ((call_kind == "function_call") != (output_kind == "function_call_output")):
                _fail("unresolved-tool-flow")
            if output.ordinal <= call.ordinal:
                _fail("invalid-stream-order")
        completion_ordinals = {}
        for turn_id, last, completion_started_at, completed_at, completion_ordinal in completions:
            if turn_id in completion_ordinals:
                _fail("unsupported-causality")
            completion_ordinals[turn_id] = completion_ordinal
            start = starts.get(turn_id)
            if (start is None or start[0] >= completion_ordinal
                    or start[1] != completion_started_at
                    or completed_at < completion_started_at):
                _fail("unsupported-causality")
            if last and not any(
                    event.event_type == "message" and event.actor_kind == "assistant"
                    and event.turn_id == turn_id and event.text == last for event in events):
                _fail("unsupported-causality")
        return _prepared(context, redaction_context, root_ids, events, reasoning_omitted)
    except NativeAdapterError:
        raise
    except adapters.GraphValidationError as error:
        raise NativeAdapterError(error.code) from None
    except redaction.RedactionError as error:
        raise NativeAdapterError(error.code) from None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail()


def _validated_prepared(prepared, context, redaction_context):
    if type(prepared) is not CodexPreparedSession or set(vars(prepared)) != {
            item.name for item in fields(CodexPreparedSession)}:
        _fail("invalid-context")
    prepared = CodexPreparedSession(**vars(prepared))
    events = []
    for event in prepared.events:
        if type(event) is not _CodexEvent or set(vars(event)) != {
                item.name for item in fields(_CodexEvent)}:
            _fail("invalid-context")
        events.append(_CodexEvent(**vars(event)))
    if (prepared.source_snapshot_id != context.expected_source_snapshot_id
            or prepared.project_id != context.project_id):
        _fail("invalid-context")
    expected = _prepared(
        context, redaction_context,
        (prepared.root_session_id, prepared.session_id, prepared.thread_id),
        events, prepared.reasoning_omitted)
    if (prepared.redaction_events != expected.redaction_events
            or prepared.expected_bindings != expected.expected_bindings):
        _fail("invalid-context")
    return expected


def _reference(event_id):
    return {"status": "local", "event_id": event_id,
            "source_snapshot_id": None, "reason": None}


def normalize_codex_session(prepared, result, *, context: CodexNormalizationContext,
                            redaction_context: redaction.TrustContext,
                            redaction_key: bytes):
    """Normalize authenticated redaction output to the canonical event graph."""
    try:
        if type(context) is not CodexNormalizationContext or set(vars(context)) != {
                item.name for item in fields(CodexNormalizationContext)}:
            _fail("invalid-context")
        context = CodexNormalizationContext(**vars(context))
        if type(redaction_context) is not redaction.TrustContext or set(vars(redaction_context)) != {
                item.name for item in fields(redaction.TrustContext)}:
            _fail("invalid-context")
        redaction_context = redaction.TrustContext(**vars(redaction_context))
        if redaction_context.owner_id != context.expected_owner_id:
            _fail("invalid-context")
        prepared = _validated_prepared(prepared, context, redaction_context)
        spans = redaction.validate_redaction_result(
            result, context=redaction_context, key=redaction_key,
            expected_bindings=prepared.expected_bindings)
        canonical_events = []
        by_correlation = {}
        losses = []
        for native_event, span in zip(prepared.events, spans):
            event_id = _native_id(
                "event", native_event.native_id + ":" + native_event.event_type
                + ":" + str(native_event.ordinal) + ":" + str(native_event.slot))
            correlation = native_event.correlation_id
            actor = {"kind": native_event.actor_kind, "id": native_event.actor_id,
                     "resolution": native_event.actor_resolution}
            canonical = {
                "id": event_id,
                "native_event_id": _native_id(
                    "native-event", native_event.native_id + ":" + native_event.event_type),
                "native_offset": f"ordinal:{native_event.ordinal}:{native_event.slot}",
                "source_snapshot_id": prepared.source_snapshot_id,
                "root_session_id": prepared.root_session_id,
                "session_id": prepared.session_id,
                "thread_id": prepared.thread_id,
                "root_stream_id": prepared.thread_id,
                "stream_id": prepared.thread_id,
                "branch_id": None,
                "parent_event_id": None,
                "span_id": span.span_id,
                "event_type": native_event.event_type,
                "actor": actor,
                "project_workspace": {"kind": "project", "id": prepared.project_id},
                "native_timestamp": native_event.timestamp,
                "stream_position": native_event.ordinal * 4 + native_event.slot,
                "correlation_id": correlation,
                "chunk_index": None,
                "content_segments": [{
                    "type": native_event.segment_type, "text": span.text,
                    "media_type": None, "artifact_locator_id": None,
                }],
                "artifact_locators": [],
                "observable_outcome": ({"kind": "tool", "status": "unknown", "summary": None}
                                       if native_event.event_type == "tool-result" else None),
                "semantic_markers": list(native_event.semantic_markers),
                "claim_eligible": span.claim_eligible,
            }
            canonical_events.append(canonical)
            if correlation is not None:
                pair = by_correlation.setdefault(correlation, {})
                if native_event.event_type in pair:
                    _fail("unsupported-causality")
                pair[native_event.event_type] = event_id
        edges = []
        for correlation, pair in sorted(by_correlation.items()):
            if set(pair) == {"tool-call", "tool-result"}:
                relations = (("tool-result-of", "tool-call", "tool-result"),)
            else:
                _fail("unresolved-tool-flow")
            for edge_type, source_type, target_type in relations:
                edges.append({
                    "id": _native_id("edge", correlation + ":" + edge_type),
                    "edge_type": edge_type,
                    "from": _reference(pair[source_type]), "to": _reference(pair[target_type]),
                })
        if prepared.reasoning_omitted:
            losses.append({
                "code": "hidden-unexposed-model-reasoning", "event_id": None,
                "native_fact": "model-reasoning", "reason": "native-unexposed",
            })
        graph = {
            "schema_version": adapters.SCHEMA_VERSION,
            "adapter": {
                "name": CODEX_ROLLOUT_ADAPTER.name,
                "adapter_version": CODEX_ROLLOUT_ADAPTER.adapter_version,
                "product_version": CODEX_ROLLOUT_ADAPTER.product_version,
                "native_schema_version": CODEX_ROLLOUT_ADAPTER.native_schema_version,
            },
            "source_snapshot_id": prepared.source_snapshot_id,
            "owner": {"kind": "user", "id": context.expected_owner_id,
                      "verification": "verified-principal"},
            "events": canonical_events, "edges": edges, "fidelity_losses": losses,
        }
        adapters.validate_event_graph(
            graph, context=adapters.ValidationContext(
                context.expected_owner_id, context.expected_source_snapshot_id))
        return graph
    except NativeAdapterError:
        raise
    except (redaction.RedactionError, adapters.GraphValidationError) as error:
        raise NativeAdapterError(error.code) from None
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail()
