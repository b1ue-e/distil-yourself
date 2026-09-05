"""Fail-closed validation for canonical Knowledge Distiller event graphs."""

from dataclasses import dataclass
import hashlib
import json
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


SCHEMA_VERSION = "knowledge-distiller.event-graph/v1"
MAX_EVENT_BYTES = 1024 * 1024
MAX_GRAPH_BYTES = 64 * 1024 * 1024
MAX_JSON_TOKENS = 750_000
MAX_JSON_STRUCTURAL_TOKENS = 500_000
MAX_JSON_VALUE_TOKENS = 250_000
MAX_JSON_STRING_TOKENS = 200_000
MAX_JSON_NESTING_DEPTH = 64
MAX_JSON_PARSER_BYTES = 512 * 1024 * 1024
JSON_TOKEN_OVERHEAD_BYTES = 384
MAX_CANONICAL_DEPTH = MAX_JSON_NESTING_DEPTH
SNAPSHOT_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")

ROOT_FIELDS = frozenset(
    {"schema_version", "adapter", "source_snapshot_id", "owner", "events", "edges", "fidelity_losses"}
)
ADAPTER_FIELDS = frozenset({"name", "adapter_version", "product_version", "native_schema_version"})
OWNER_FIELDS = frozenset({"kind", "id", "verification"})
EVENT_FIELDS = frozenset(
    {
        "id",
        "native_event_id",
        "native_offset",
        "source_snapshot_id",
        "root_session_id",
        "session_id",
        "thread_id",
        "root_stream_id",
        "stream_id",
        "branch_id",
        "parent_event_id",
        "span_id",
        "event_type",
        "actor",
        "project_workspace",
        "native_timestamp",
        "stream_position",
        "correlation_id",
        "chunk_index",
        "content_segments",
        "artifact_locators",
        "observable_outcome",
        "semantic_markers",
        "claim_eligible",
    }
)
ACTOR_FIELDS = frozenset({"kind", "id", "resolution"})
WORKSPACE_FIELDS = frozenset({"kind", "id"})
SEGMENT_FIELDS = frozenset({"type", "text", "media_type", "artifact_locator_id"})
ARTIFACT_FIELDS = frozenset({"id", "kind", "locator", "revision"})
OUTCOME_FIELDS = frozenset({"kind", "status", "summary"})
EDGE_FIELDS = frozenset({"id", "edge_type", "from", "to"})
REFERENCE_FIELDS = frozenset({"status", "event_id", "source_snapshot_id", "reason"})
LOSS_FIELDS = frozenset({"code", "event_id", "native_fact", "reason"})

ADAPTER_NAMES = frozenset({"synthetic", "lark", "codex", "claude-code", "trae"})
SUPPORTED_ADAPTER = ("synthetic", "1.0.0", "synthetic-1", "synthetic-1")
EVENT_TYPES = frozenset(
    {
        "message",
        "tool-call",
        "tool-output-chunk",
        "tool-result",
        "compacted-summary",
        "agent-start",
        "agent-completion",
        "agent-cancellation",
        "agent-failure",
        "message-sent",
        "message-delivered",
        "message-consumed",
        "joined",
        "returned",
        "cancellation-observed",
    }
)
ACTOR_KINDS = frozenset({"user", "assistant", "agent", "tool", "system", "external"})
ACTOR_RESOLUTIONS = frozenset({"verified-owner", "native", "deterministic", "unresolved"})
WORKSPACE_KINDS = frozenset({"project", "workspace"})
SEGMENT_TYPES = frozenset(
    {"text", "code", "artifact-reference", "tool-call-summary", "tool-result-summary", "compacted-summary"}
)
ARTIFACT_KINDS = frozenset({"document", "session", "file", "url", "other"})
OUTCOME_KINDS = frozenset({"tool", "agent", "message", "artifact", "other"})
OUTCOME_STATUSES = frozenset({"succeeded", "failed", "cancelled", "unknown"})
MARKERS = frozenset({"edit", "retry", "supersession", "fork", "spawn", "completion", "cancellation", "failure", "compaction"})
EDGE_TYPES = frozenset(
    {
        "precedes",
        "tool-output-of",
        "tool-result-of",
        "edits",
        "retries",
        "supersedes",
        "forks",
        "spawned-by",
        "sent",
        "delivered",
        "consumed",
        "joined",
        "returned",
        "cancellation-observed",
    }
)
REFERENCE_STATUSES = frozenset({"local", "external", "missing"})
REFERENCE_REASONS = frozenset({"native-omission", "outside-snapshot", "redacted", "unsupported"})
LOSS_FACTS = frozenset({"formatting", "token-count", "wall-clock-timestamp", "model-reasoning"})
LOSS_REASONS = frozenset({"unrepresentable", "native-unavailable", "native-unexposed"})
LOSS_FACT_BY_CODE = {
    "non-semantic-formatting": "formatting",
    "unavailable-token-counts": "token-count",
    "unavailable-wall-clock-timestamp": "wall-clock-timestamp",
    "hidden-unexposed-model-reasoning": "model-reasoning",
}
TOOL_EVENTS = frozenset({"tool-call", "tool-output-chunk", "tool-result"})
MESSAGE_LIFECYCLE_EVENTS = frozenset({"message-sent", "message-delivered", "message-consumed"})
TERMINAL_EVENTS = frozenset({"agent-completion", "agent-cancellation", "agent-failure"})
OBSERVATION_EDGES = frozenset({"joined", "returned", "cancellation-observed"})

EDGE_EVENT_TYPES = {
    "tool-output-of": (frozenset({"tool-call"}), frozenset({"tool-output-chunk"})),
    "tool-result-of": (frozenset({"tool-call"}), frozenset({"tool-result"})),
    "edits": (frozenset({"message"}), frozenset({"message"})),
    "forks": (EVENT_TYPES, frozenset({"agent-start"})),
    "spawned-by": (EVENT_TYPES, frozenset({"agent-start"})),
    "sent": (frozenset({"message"}), frozenset({"message-sent"})),
    "delivered": (frozenset({"message-sent"}), frozenset({"message-delivered"})),
    "consumed": (frozenset({"message-delivered"}), frozenset({"message-consumed"})),
    "joined": (TERMINAL_EVENTS, frozenset({"joined"})),
    "returned": (frozenset({"agent-completion"}), frozenset({"returned"})),
    "cancellation-observed": (frozenset({"agent-cancellation"}), frozenset({"cancellation-observed"})),
}


class GraphValidationError(ValueError):
    """A bounded diagnostic that never includes source values."""

    def __init__(self, code: str, pointer: Optional[str] = None) -> None:
        self.code = code
        self.pointer = pointer[:512] if pointer is not None else None
        super().__init__(code if self.pointer is None else f"{code}: {self.pointer}")


@dataclass(frozen=True)
class AdapterIdentity:
    name: str
    adapter_version: str
    product_version: str
    native_schema_version: str


@dataclass(frozen=True)
class ValidationContext:
    """Broker-established identity and immutable source revision."""

    expected_owner_id: str
    expected_source_snapshot_id: str


@dataclass(frozen=True)
class EventGraphManifest:
    schema_version: str
    adapter: AdapterIdentity
    source_snapshot_id: str
    event_count: int
    edge_count: int
    canonical_digest: str


@dataclass
class _EdgeIndex:
    incoming: Dict[Tuple[str, str], List[Dict[str, Any]]]
    outgoing: Dict[Tuple[str, str], List[Dict[str, Any]]]
    adjacency: Dict[str, Set[str]]


def _reject(code: str, pointer: Optional[str] = None) -> None:
    raise GraphValidationError(code, pointer)


def _canonical_bytes(value: Any) -> bytes:
    active_containers: Set[int] = set()
    stack: List[Tuple[Iterable[Any], int, Optional[int]]] = [
        (iter((value,)), 0, None)
    ]
    while stack:
        values, depth, container_id = stack[-1]
        try:
            item = next(values)
        except StopIteration:
            stack.pop()
            if container_id is not None:
                active_containers.remove(container_id)
            continue
        if type(item) not in (dict, list):
            continue
        if depth >= MAX_CANONICAL_DEPTH or id(item) in active_containers:
            _reject("invalid-type", "/")
        item_id = id(item)
        active_containers.add(item_id)
        children = item.values() if type(item) is dict else item
        stack.append((iter(children), depth + 1, item_id))

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _reject("invalid-type", "/")
    raise AssertionError("unreachable")


def _decoded_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _reject("duplicate-json-key", "/")
        value[key] = item
    return value


def _invalid_json_number(_: str) -> None:
    _reject("invalid-number", "/")


def _decoded_integer(value: str) -> int:
    if len(value.lstrip("-")) > 19:
        _reject("invalid-number", "/")
    return int(value)


def _preflight_event_graph_json(raw: bytes) -> int:
    """Bound JSON materialization with one iterative lexical scan."""
    total_tokens = 0
    structural_tokens = 0
    value_tokens = 0
    string_tokens = 0
    depth = 0
    index = 0
    length = len(raw)
    structural = b"{}[],:"
    whitespace = b" \t\r\n"

    while index < length:
        byte = raw[index]
        if byte in whitespace:
            index += 1
            continue
        if byte == 0x22:
            total_tokens += 1
            value_tokens += 1
            string_tokens += 1
            index += 1
            while index < length:
                byte = raw[index]
                if byte == 0x5C:
                    index += 2
                elif byte == 0x22:
                    index += 1
                    break
                else:
                    index += 1
        elif byte in structural:
            total_tokens += 1
            structural_tokens += 1
            if byte in b"{[":
                depth += 1
                if depth > MAX_JSON_NESTING_DEPTH:
                    _reject("json-too-deep", "/")
            elif byte in b"}]" and depth:
                depth -= 1
            index += 1
        else:
            total_tokens += 1
            value_tokens += 1
            index += 1
            while (
                index < length
                and raw[index] not in whitespace
                and raw[index] not in structural
                and raw[index] != 0x22
            ):
                index += 1

        if (
            total_tokens > MAX_JSON_TOKENS
            or structural_tokens > MAX_JSON_STRUCTURAL_TOKENS
            or value_tokens > MAX_JSON_VALUE_TOKENS
            or string_tokens > MAX_JSON_STRING_TOKENS
        ):
            _reject("json-resource-limit", "/")
    return total_tokens


def _reject_invalid_unicode_scalars(value: Any) -> None:
    stack = [iter((value,))]
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if type(item) is str:
            if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
                _reject("invalid-unicode-scalar", "/")
        elif type(item) is dict:
            stack.append(iter(item))
            stack.append(iter(item.values()))
        elif type(item) is list:
            stack.append(iter(item))


def decode_event_graph_json(raw: bytes) -> Any:
    """Decode one raw graph through the duplicate-key-safe JSON boundary."""

    if type(raw) is not bytes:
        _reject("invalid-type", "/")
    if len(raw) > MAX_GRAPH_BYTES:
        _reject("graph-too-large", "/")
    total_tokens = _preflight_event_graph_json(raw)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _reject("invalid-utf8", "/")
    projected_bytes = (
        sys.getsizeof(raw)
        + (2 * sys.getsizeof(text))
        + MAX_GRAPH_BYTES
        + (total_tokens * JSON_TOKEN_OVERHEAD_BYTES)
    )
    if projected_bytes > MAX_JSON_PARSER_BYTES:
        _reject("json-resource-limit", "/")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_decoded_object,
            parse_float=_invalid_json_number,
            parse_int=_decoded_integer,
            parse_constant=_invalid_json_number,
        )
    except GraphValidationError:
        raise
    except RecursionError:
        _reject("json-too-deep", "/")
    except json.JSONDecodeError:
        _reject("invalid-json", "/")
    _reject_invalid_unicode_scalars(value)
    return value


def _object(value: Any, fields: Set[str], pointer: str) -> Dict[str, Any]:
    if type(value) is not dict:
        _reject("invalid-type", pointer)
    keys = set(value)
    if keys - fields:
        _reject("unknown-field", pointer)
    if fields - keys:
        _reject("missing-field", pointer)
    return value


def _array(value: Any, pointer: str) -> List[Any]:
    if type(value) is not list:
        _reject("invalid-type", pointer)
    return value


def _string(value: Any, pointer: str, minimum: int, maximum: int, code: str = "invalid-text") -> str:
    if type(value) is not str:
        _reject("invalid-type", pointer)
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        _reject(code, pointer)
    if size < minimum or size > maximum:
        _reject(code, pointer)
    return value


def _identifier(value: Any, pointer: str, nullable: bool = False) -> Optional[str]:
    if value is None and nullable:
        return None
    return _string(value, pointer, 1, 256, "invalid-identifier")


def _version(value: Any, pointer: str) -> str:
    version = _string(value, pointer, 1, 64, "invalid-version")
    if version == "latest":
        _reject("invalid-version", pointer)
    return version


def _snapshot(value: Any, pointer: str) -> str:
    if type(value) is not str:
        _reject("invalid-type", pointer)
    if SNAPSHOT_ID.fullmatch(value) is None:
        _reject("invalid-snapshot-id", pointer)
    return value


def validate_validation_context(context: ValidationContext) -> Tuple[str, str]:
    """Validate caller-established trust anchors without source access."""

    if type(context) is not ValidationContext:
        _reject("invalid-validation-context", "/context")
    return (
        _identifier(context.expected_owner_id, "/context/expected_owner_id"),
        _snapshot(
            context.expected_source_snapshot_id,
            "/context/expected_source_snapshot_id",
        ),
    )


def _enum(value: Any, allowed: Iterable[str], pointer: str) -> str:
    if type(value) is not str:
        _reject("invalid-type", pointer)
    if value not in allowed:
        _reject("invalid-enum", pointer)
    return value


def _integer(value: Any, minimum: int, maximum: int, pointer: str) -> int:
    if type(value) is not int:
        _reject("invalid-type", pointer)
    if value < minimum or value > maximum:
        _reject("invalid-type", pointer)
    return value


def _boolean(value: Any, pointer: str) -> bool:
    if type(value) is not bool:
        _reject("invalid-type", pointer)
    return value


def _validate_actor(value: Any, pointer: str, owner_id: str) -> Dict[str, Any]:
    actor = _object(value, ACTOR_FIELDS, pointer)
    kind = _enum(actor["kind"], ACTOR_KINDS, pointer + "/kind")
    actor_id = _identifier(actor["id"], pointer + "/id", nullable=True)
    resolution = _enum(actor["resolution"], ACTOR_RESOLUTIONS, pointer + "/resolution")
    if resolution == "verified-owner" and not (kind == "user" and actor_id == owner_id):
        _reject("invalid-owner-resolution", pointer)
    return actor


def _validate_workspace(value: Any, pointer: str) -> None:
    if value is None:
        return
    workspace = _object(value, WORKSPACE_FIELDS, pointer)
    _enum(workspace["kind"], WORKSPACE_KINDS, pointer + "/kind")
    _identifier(workspace["id"], pointer + "/id")


def _validate_artifacts(value: Any, pointer: str) -> Set[str]:
    artifacts = _array(value, pointer)
    identifiers: Set[str] = set()
    for index, raw in enumerate(artifacts):
        item_pointer = f"{pointer}/{index}"
        artifact = _object(raw, ARTIFACT_FIELDS, item_pointer)
        artifact_id = _identifier(artifact["id"], item_pointer + "/id")
        if artifact_id in identifiers:
            _reject("duplicate-artifact-id", item_pointer)
        identifiers.add(artifact_id)
        _enum(artifact["kind"], ARTIFACT_KINDS, item_pointer + "/kind")
        _string(artifact["locator"], item_pointer + "/locator", 1, 4096)
        _identifier(artifact["revision"], item_pointer + "/revision", nullable=True)
    return identifiers


def _validate_segments(value: Any, artifact_ids: Set[str], pointer: str) -> None:
    segments = _array(value, pointer)
    for index, raw in enumerate(segments):
        item_pointer = f"{pointer}/{index}"
        segment = _object(raw, SEGMENT_FIELDS, item_pointer)
        segment_type = _enum(segment["type"], SEGMENT_TYPES, item_pointer + "/type")
        text = segment["text"]
        media_type = segment["media_type"]
        locator_id = segment["artifact_locator_id"]
        if segment_type == "artifact-reference":
            if text is not None or media_type is not None:
                _reject("invalid-content-segment", item_pointer)
            resolved = _identifier(locator_id, item_pointer + "/artifact_locator_id")
            if resolved not in artifact_ids:
                _reject("invalid-artifact-reference", item_pointer)
        else:
            if locator_id is not None:
                _reject("invalid-content-segment", item_pointer)
            _string(text, item_pointer + "/text", 0, 1048576)
            if media_type is not None:
                if segment_type != "code":
                    _reject("invalid-content-segment", item_pointer)
                media = _string(media_type, item_pointer + "/media_type", 1, 128)
                try:
                    media.encode("ascii")
                except UnicodeEncodeError:
                    _reject("invalid-text", item_pointer + "/media_type")


def _validate_outcome(value: Any, pointer: str) -> None:
    if value is None:
        return
    outcome = _object(value, OUTCOME_FIELDS, pointer)
    _enum(outcome["kind"], OUTCOME_KINDS, pointer + "/kind")
    _enum(outcome["status"], OUTCOME_STATUSES, pointer + "/status")
    if outcome["summary"] is not None:
        _string(outcome["summary"], pointer + "/summary", 0, 4096)


def _validate_event(raw: Any, index: int, snapshot_id: str, owner_id: str) -> Dict[str, Any]:
    pointer = f"/events/{index}"
    event = _object(raw, EVENT_FIELDS, pointer)
    _identifier(event["id"], pointer + "/id")
    _identifier(event["native_event_id"], pointer + "/native_event_id", nullable=True)
    _identifier(event["native_offset"], pointer + "/native_offset")
    if _snapshot(event["source_snapshot_id"], pointer + "/source_snapshot_id") != snapshot_id:
        _reject("snapshot-mismatch", pointer)
    for field in ("root_session_id", "session_id", "thread_id"):
        _identifier(event[field], f"{pointer}/{field}", nullable=True)
    _identifier(event["root_stream_id"], pointer + "/root_stream_id")
    for field in ("stream_id", "branch_id", "parent_event_id", "span_id"):
        _identifier(event[field], f"{pointer}/{field}", nullable=True)
    event_type = _enum(event["event_type"], EVENT_TYPES, pointer + "/event_type")
    actor = _validate_actor(event["actor"], pointer + "/actor", owner_id)
    _validate_workspace(event["project_workspace"], pointer + "/project_workspace")
    if event["native_timestamp"] is not None:
        _string(event["native_timestamp"], pointer + "/native_timestamp", 1, 128)
    _integer(event["stream_position"], 0, 9223372036854775807, pointer + "/stream_position")
    correlation_id = _identifier(event["correlation_id"], pointer + "/correlation_id", nullable=True)
    if event_type in TOOL_EVENTS | MESSAGE_LIFECYCLE_EVENTS and correlation_id is None:
        _reject("missing-correlation-id", pointer)
    if event_type == "tool-output-chunk":
        _integer(event["chunk_index"], 0, 2147483647, pointer + "/chunk_index")
    elif event["chunk_index"] is not None:
        _reject("invalid-chunk-index", pointer)
    artifact_ids = _validate_artifacts(event["artifact_locators"], pointer + "/artifact_locators")
    _validate_segments(event["content_segments"], artifact_ids, pointer + "/content_segments")
    _validate_outcome(event["observable_outcome"], pointer + "/observable_outcome")
    markers = _array(event["semantic_markers"], pointer + "/semantic_markers")
    seen_markers: Set[str] = set()
    for marker_index, marker in enumerate(markers):
        validated = _enum(marker, MARKERS, f"{pointer}/semantic_markers/{marker_index}")
        if validated in seen_markers:
            _reject("duplicate-marker", pointer + "/semantic_markers")
        seen_markers.add(validated)
    claim_eligible = _boolean(event["claim_eligible"], pointer + "/claim_eligible")
    expected_claim = actor["kind"] == "user" and actor["resolution"] == "verified-owner" and actor["id"] == owner_id
    if claim_eligible != expected_claim:
        _reject("invalid-claim-eligibility", pointer)
    if event_type == "compacted-summary":
        segments = event["content_segments"]
        if (
            "compaction" not in seen_markers
            or not segments
            or any(segment["type"] != "compacted-summary" for segment in segments)
            or event["artifact_locators"]
        ):
            _reject("invalid-compaction", pointer)
    elif "compaction" in seen_markers or any(segment["type"] == "compacted-summary" for segment in event["content_segments"]):
        _reject("invalid-compaction", pointer)
    if len(_canonical_bytes(event)) > MAX_EVENT_BYTES:
        _reject("event-too-large", pointer)
    return event


def _validate_reference(value: Any, pointer: str, event_ids: Set[str]) -> Tuple[str, Optional[str]]:
    reference = _object(value, REFERENCE_FIELDS, pointer)
    status = _enum(reference["status"], REFERENCE_STATUSES, pointer + "/status")
    event_id = reference["event_id"]
    snapshot_id = reference["source_snapshot_id"]
    reason = reference["reason"]
    if status == "local":
        resolved = _identifier(event_id, pointer + "/event_id")
        if snapshot_id is not None or reason is not None:
            _reject("invalid-reference", pointer)
        if resolved not in event_ids:
            _reject("unresolved-reference", pointer)
        return status, resolved
    if status == "external":
        if event_id is None or snapshot_id is None or reason is not None:
            _reject("invalid-reference", pointer)
        _identifier(event_id, pointer + "/event_id")
        _snapshot(snapshot_id, pointer + "/source_snapshot_id")
        return status, event_id
    if event_id is not None or snapshot_id is not None or reason is None:
        _reject("invalid-reference", pointer)
    _enum(reason, REFERENCE_REASONS, pointer + "/reason")
    return status, None


def _validate_edge_semantics(
    edge_type: str,
    source: Tuple[str, Optional[str]],
    target: Tuple[str, Optional[str]],
    events: Dict[str, Dict[str, Any]],
    pointer: str,
) -> None:
    source_status, source_id = source
    target_status, target_id = target
    if edge_type in EDGE_EVENT_TYPES:
        from_types, to_types = EDGE_EVENT_TYPES[edge_type]
        if source_status == "local" and events[source_id]["event_type"] not in from_types:
            _reject("invalid-edge-semantics", pointer)
        if target_status == "local" and events[target_id]["event_type"] not in to_types:
            _reject("invalid-edge-semantics", pointer)
    if edge_type == "retries" and source_status == target_status == "local":
        if events[source_id]["event_type"] != events[target_id]["event_type"]:
            _reject("invalid-edge-semantics", pointer)
    if edge_type in {"tool-output-of", "tool-result-of", "sent", "delivered", "consumed"}:
        if source_status == target_status == "local":
            source_correlation = events[source_id]["correlation_id"]
            target_correlation = events[target_id]["correlation_id"]
            if source_correlation is None or source_correlation != target_correlation:
                code = "unresolved-tool-flow" if edge_type.startswith("tool-") else "invalid-edge-semantics"
                _reject(code, pointer)


def _build_edge_index(
    events: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> _EdgeIndex:
    incoming: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    outgoing: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    adjacency = {event_id: set() for event_id in events}
    for edge in edges:
        edge_type = edge["edge_type"]
        source = edge["from"]
        target = edge["to"]
        if source["status"] == "local":
            outgoing.setdefault((edge_type, source["event_id"]), []).append(edge)
        if target["status"] == "local":
            incoming.setdefault((edge_type, target["event_id"]), []).append(edge)
        if source["status"] == target["status"] == "local":
            adjacency[source["event_id"]].add(target["event_id"])
    return _EdgeIndex(incoming=incoming, outgoing=outgoing, adjacency=adjacency)


def _add_native_stream_order(
    events: Dict[str, Dict[str, Any]],
    edge_index: _EdgeIndex,
) -> None:
    streams: Dict[str, List[Tuple[int, str]]] = {}
    for event_id, event in events.items():
        stream_key = event["stream_id"] or event["root_stream_id"]
        streams.setdefault(stream_key, []).append((event["stream_position"], event_id))
    for stream in streams.values():
        stream.sort()
        for previous, current in zip(stream, stream[1:]):
            edge_index.adjacency[previous[1]].add(current[1])


def _one_local_edge(
    edge_index: _EdgeIndex,
    edge_type: str,
    endpoint: str,
    event_id: str,
    code: str = "invalid-edge-cardinality",
) -> Dict[str, Any]:
    lookup = edge_index.incoming if endpoint == "to" else edge_index.outgoing
    matches = lookup.get((edge_type, event_id), [])
    if len(matches) != 1:
        _reject(code, "/edges")
    opposite = "from" if endpoint == "to" else "to"
    if matches[0][opposite]["status"] != "local":
        _reject(code, "/edges")
    return matches[0]


def _validate_acyclic(
    events: Dict[str, Dict[str, Any]],
    edge_index: _EdgeIndex,
) -> List[str]:
    incoming_count = {event_id: 0 for event_id in events}
    for dependents in edge_index.adjacency.values():
        for target_id in dependents:
            incoming_count[target_id] += 1
    ready = [event_id for event_id, count in incoming_count.items() if count == 0]
    topological_order: List[str] = []
    while ready:
        event_id = ready.pop()
        topological_order.append(event_id)
        for dependent in edge_index.adjacency[event_id]:
            incoming_count[dependent] -= 1
            if incoming_count[dependent] == 0:
                ready.append(dependent)
    if len(topological_order) != len(events):
        _reject("graph-cycle", "/edges")
    return topological_order


def _validate_stream_edge_order(
    events: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> None:
    for edge in edges:
        if edge["from"]["status"] != "local" or edge["to"]["status"] != "local":
            continue
        source = events[edge["from"]["event_id"]]
        target = events[edge["to"]["event_id"]]
        source_stream = source["stream_id"] or source["root_stream_id"]
        target_stream = target["stream_id"] or target["root_stream_id"]
        if source_stream == target_stream and source["stream_position"] >= target["stream_position"]:
            _reject("invalid-stream-order", "/edges")


def _validate_tool_flows(
    events: Dict[str, Dict[str, Any]],
    edge_index: _EdgeIndex,
) -> None:
    chunks_by_call: Dict[str, List[int]] = {}
    for event_id, event in events.items():
        event_type = event["event_type"]
        if event_type == "tool-output-chunk":
            edge = _one_local_edge(edge_index, "tool-output-of", "to", event_id)
            call_id = edge["from"]["event_id"]
            chunks_by_call.setdefault(call_id, []).append(event["chunk_index"])
        elif event_type == "tool-result":
            _one_local_edge(edge_index, "tool-result-of", "to", event_id)
    for event_id, event in events.items():
        if event["event_type"] == "tool-call":
            _one_local_edge(edge_index, "tool-result-of", "from", event_id)
            indices = sorted(chunks_by_call.get(event_id, []))
            if indices != list(range(len(indices))):
                _reject("noncontiguous-chunks", "/events")


def _validate_marker_edges(events: Dict[str, Dict[str, Any]], edge_index: _EdgeIndex) -> None:
    relations = {"edit": "edits", "retry": "retries", "supersession": "supersedes", "fork": "forks"}
    for event_id, event in events.items():
        for marker, edge_type in relations.items():
            if marker in event["semantic_markers"]:
                _one_local_edge(edge_index, edge_type, "to", event_id, "missing-marker-edge")


def _validate_message_flows(events: Dict[str, Dict[str, Any]], edge_index: _EdgeIndex) -> None:
    incoming = {"message-sent": "sent", "message-delivered": "delivered", "message-consumed": "consumed"}
    for event_id, event in events.items():
        edge_type = incoming.get(event["event_type"])
        if edge_type is not None:
            _one_local_edge(edge_index, edge_type, "to", event_id)
        if event["event_type"] == "message-delivered":
            outgoing = edge_index.outgoing.get(("consumed", event_id), [])
            if len(outgoing) > 1:
                _reject("invalid-edge-cardinality", "/edges")


def _validate_child_agents(
    events: Dict[str, Dict[str, Any]],
    edge_index: _EdgeIndex,
    topological_order: List[str],
) -> None:
    children: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
    children_by_start: Dict[str, Tuple[str, str, str]] = {}
    child_starts = []
    for event_id, event in events.items():
        if event["event_type"] != "agent-start":
            continue
        has_spawn_edge = bool(edge_index.incoming.get(("spawned-by", event_id)))
        if event["parent_event_id"] is None and "spawn" not in event["semantic_markers"] and not has_spawn_edge:
            continue
        child_starts.append((event_id, event))

    child_streams: Set[str] = set()
    for _, event in child_starts:
        root_stream_id = event["root_stream_id"]
        if root_stream_id in child_streams:
            _reject("ambiguous-child-root", "/events")
        child_streams.add(root_stream_id)

    for event_id, event in child_starts:
        actor = event["actor"]
        if actor["kind"] != "agent" or actor["id"] is None or actor["resolution"] == "unresolved":
            _reject("invalid-child-agent", "/events")
        if event["correlation_id"] is None:
            _reject("invalid-child-lifecycle", "/events")
        if event["parent_event_id"] is None:
            _reject("invalid-child-spawn", "/events")
        spawn = _one_local_edge(
            edge_index,
            "spawned-by",
            "to",
            event_id,
            "invalid-child-spawn",
        )
        parent_id = spawn["from"]["event_id"]
        if parent_id != event["parent_event_id"]:
            _reject("invalid-child-spawn", "/edges")
        child_key = (
            event["root_stream_id"],
            actor["id"],
            event["correlation_id"],
        )
        if child_key in children:
            _reject("ambiguous-child-root", "/events")
        children[child_key] = (event_id, events[parent_id]["root_stream_id"])
        children_by_start[event_id] = child_key

    child_reachable: Set[str] = set(children_by_start)
    for event_id in topological_order:
        if event_id not in child_reachable:
            continue
        root_stream_id = events[event_id]["root_stream_id"]
        for dependent_id in edge_index.adjacency[event_id]:
            if events[dependent_id]["root_stream_id"] == root_stream_id:
                child_reachable.add(dependent_id)

    observations = {"joined": "joined", "returned": "returned", "cancellation-observed": "cancellation-observed"}
    terminal_count = {start_id: 0 for start_id in children_by_start}
    for event_id, event in events.items():
        edge_type = observations.get(event["event_type"])
        if edge_type is not None:
            _one_local_edge(edge_index, edge_type, "to", event_id)

    for event_id, event in events.items():
        if event["event_type"] not in TERMINAL_EVENTS:
            continue
        outgoing = [
            edge
            for edge_type in OBSERVATION_EDGES
            for edge in edge_index.outgoing.get((edge_type, event_id), [])
        ]
        if len(outgoing) > 1:
            _reject("invalid-edge-cardinality", "/edges")
        actor = event["actor"]
        correlation_id = event["correlation_id"]
        if (
            actor["kind"] != "agent"
            or actor["id"] is None
            or actor["resolution"] == "unresolved"
            or correlation_id is None
        ):
            _reject("invalid-child-lifecycle", "/events")
        child = children.get((event["root_stream_id"], actor["id"], correlation_id))
        if child is None:
            _reject("invalid-child-lifecycle", "/events")
        start_id, parent_stream_id = child
        if (
            event_id not in child_reachable
            or len(outgoing) != 1
            or outgoing[0]["to"]["status"] != "local"
            or events[outgoing[0]["to"]["event_id"]]["root_stream_id"] != parent_stream_id
        ):
            _reject("invalid-child-lifecycle", "/events")
        terminal_count[start_id] += 1
        if terminal_count[start_id] > 1:
            _reject("invalid-child-lifecycle", "/events")

    if any(count != 1 for count in terminal_count.values()):
        _reject("invalid-child-lifecycle", "/events")


def _validate_losses(value: Any, event_ids: Set[str]) -> None:
    losses = _array(value, "/fidelity_losses")
    for index, raw in enumerate(losses):
        pointer = f"/fidelity_losses/{index}"
        loss = _object(raw, LOSS_FIELDS, pointer)
        code = loss["code"]
        if type(code) is not str or code not in LOSS_FACT_BY_CODE:
            _reject("invalid-fidelity-loss", pointer)
        event_id = _identifier(loss["event_id"], pointer + "/event_id", nullable=True)
        if event_id is not None and event_id not in event_ids:
            _reject("unresolved-reference", pointer + "/event_id")
        fact = _enum(loss["native_fact"], LOSS_FACTS, pointer + "/native_fact")
        reason = _enum(loss["reason"], LOSS_REASONS, pointer + "/reason")
        if fact != LOSS_FACT_BY_CODE[code]:
            _reject("invalid-fidelity-loss", pointer)
        if code == "hidden-unexposed-model-reasoning" and reason != "native-unexposed":
            _reject("invalid-fidelity-loss", pointer)


def validate_event_graph(value: Any, *, context: ValidationContext) -> EventGraphManifest:
    """Validate a canonical graph and return its immutable content manifest."""

    expected_owner_id, expected_snapshot_id = validate_validation_context(context)
    graph = _object(value, ROOT_FIELDS, "/")
    schema_version = _string(graph["schema_version"], "/schema_version", 1, 64, "invalid-version")
    if schema_version != SCHEMA_VERSION:
        _reject("unsupported-schema-version", "/schema_version")

    adapter = _object(graph["adapter"], ADAPTER_FIELDS, "/adapter")
    adapter_name = _enum(adapter["name"], ADAPTER_NAMES, "/adapter/name")
    identity = AdapterIdentity(
        name=adapter_name,
        adapter_version=_version(adapter["adapter_version"], "/adapter/adapter_version"),
        product_version=_version(adapter["product_version"], "/adapter/product_version"),
        native_schema_version=_version(adapter["native_schema_version"], "/adapter/native_schema_version"),
    )
    if (identity.name, identity.adapter_version, identity.product_version, identity.native_schema_version) != SUPPORTED_ADAPTER:
        _reject("unsupported-adapter-version", "/adapter")

    snapshot_id = _snapshot(graph["source_snapshot_id"], "/source_snapshot_id")
    if snapshot_id != expected_snapshot_id:
        _reject("source-snapshot-context-mismatch", "/source_snapshot_id")
    owner = _object(graph["owner"], OWNER_FIELDS, "/owner")
    if owner["kind"] != "user" or owner["verification"] != "verified-principal":
        _reject("invalid-owner", "/owner")
    owner_id = _identifier(owner["id"], "/owner/id")
    if owner_id != expected_owner_id:
        _reject("owner-context-mismatch", "/owner/id")

    raw_events = _array(graph["events"], "/events")
    events: Dict[str, Dict[str, Any]] = {}
    native_ids: Set[str] = set()
    stream_positions: Set[Tuple[str, int]] = set()
    for index, raw in enumerate(raw_events):
        event = _validate_event(raw, index, snapshot_id, owner_id)
        event_id = event["id"]
        if event_id in events:
            _reject("duplicate-event-id", f"/events/{index}/id")
        events[event_id] = event
        native_id = event["native_event_id"]
        if native_id is not None:
            if native_id in native_ids:
                _reject("duplicate-native-event-id", f"/events/{index}/native_event_id")
            native_ids.add(native_id)
        stream_key = event["stream_id"] if event["stream_id"] is not None else event["root_stream_id"]
        position_key = (stream_key, event["stream_position"])
        if position_key in stream_positions:
            _reject("duplicate-stream-position", f"/events/{index}/stream_position")
        stream_positions.add(position_key)
    for index, event in enumerate(raw_events):
        parent_id = event["parent_event_id"]
        if parent_id is not None and parent_id not in events:
            _reject("unresolved-reference", f"/events/{index}/parent_event_id")

    raw_edges = _array(graph["edges"], "/edges")
    edges: List[Dict[str, Any]] = []
    edge_ids: Set[str] = set()
    event_ids = set(events)
    for index, raw in enumerate(raw_edges):
        pointer = f"/edges/{index}"
        edge = _object(raw, EDGE_FIELDS, pointer)
        edge_id = _identifier(edge["id"], pointer + "/id")
        if edge_id in edge_ids:
            _reject("duplicate-edge-id", pointer + "/id")
        edge_ids.add(edge_id)
        edge_type = _enum(edge["edge_type"], EDGE_TYPES, pointer + "/edge_type")
        source = _validate_reference(edge["from"], pointer + "/from", event_ids)
        target = _validate_reference(edge["to"], pointer + "/to", event_ids)
        _validate_edge_semantics(edge_type, source, target, events, pointer)
        edges.append(edge)

    _validate_losses(graph["fidelity_losses"], event_ids)
    canonical = _canonical_bytes(graph)
    if len(canonical) > MAX_GRAPH_BYTES:
        _reject("graph-too-large", "/")

    edge_index = _build_edge_index(events, edges)
    _validate_acyclic(events, edge_index)
    _validate_stream_edge_order(events, edges)
    _add_native_stream_order(events, edge_index)
    topological_order = _validate_acyclic(events, edge_index)
    _validate_tool_flows(events, edge_index)
    _validate_marker_edges(events, edge_index)
    _validate_message_flows(events, edge_index)
    _validate_child_agents(events, edge_index, topological_order)

    return EventGraphManifest(
        schema_version=schema_version,
        adapter=identity,
        source_snapshot_id=snapshot_id,
        event_count=len(events),
        edge_count=len(edges),
        canonical_digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
    )
