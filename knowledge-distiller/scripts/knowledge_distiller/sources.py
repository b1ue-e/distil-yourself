"""Immutable document and shared source contracts; no native adapters or I/O.

Documents retain array order, require parents before children, and number each
parent's children contiguously from zero. A native block locator is the document
ID, pinned revision, and native block ID together. Segment/artifact semantics and
permitted fidelity losses reuse the event graph contract. Raw JSON must first
pass the existing strict decoder; dictionaries cannot prove duplicate-key absence.
Documents additionally allow at most 10,000 blocks, 250,000 traversed JSON items
(including object keys), 64 nesting levels, and 64 MiB canonical UTF-8 JSON.
The preflight checks these ceilings before typed block normalization.
"""

from dataclasses import dataclass, fields
import hashlib
from itertools import chain
import json
from typing import Any, Optional, Tuple, Union

from . import adapters
from .authorization import AuthorizationError, _exact


DOCUMENT_SCHEMA = "knowledge-distiller.document/v1"
SNAPSHOT_SCHEMA = "knowledge-distiller.source-snapshot/v1"
MAX_DOCUMENT_BYTES = adapters.MAX_GRAPH_BYTES
MAX_DOCUMENT_BLOCKS = 10_000
MAX_DOCUMENT_ITEMS = adapters.MAX_JSON_VALUE_TOKENS
DOCUMENT_FIELDS = frozenset({"schema_version", "adapter", "source_snapshot_id", "owner",
                             "native_document_id", "revision", "blocks", "fidelity_losses"})
BLOCK_FIELDS = frozenset({"id", "native_block_id", "parent_block_id", "order", "author",
                         "content_segments", "artifact_locators", "claim_eligible"})
SNAPSHOT_FIELDS = frozenset({"schema_version", "source_kind", "source_snapshot_id", "adapter", "owner",
                            "raw_digest", "canonical_digest", "source_byte_count", "source_item_count",
                            "fidelity_losses", "payload_kind", "payload_reference"})


class SourceValidationError(ValueError):
    """Code-only diagnostics, including errors from the existing graph validator."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DocumentValidationContext:
    expected_owner_id: str
    expected_source_snapshot_id: str
    expected_revision: str
    # External native block ID -> verified author ID bindings.
    verified_authors: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class OwnerBinding:
    kind: str
    id: str
    verification: str


@dataclass(frozen=True)
class AuthorResolution:
    kind: str
    id: Optional[str]
    resolution: str


@dataclass(frozen=True)
class ContentSegment:
    type: str
    text: Optional[str]
    media_type: Optional[str]
    artifact_locator_id: Optional[str]


@dataclass(frozen=True)
class ArtifactLocator:
    id: str
    kind: str
    locator: str
    revision: Optional[str]


@dataclass(frozen=True)
class FidelityLoss:
    code: str
    item_id: Optional[str]
    native_fact: str
    reason: str


@dataclass(frozen=True)
class DocumentBlock:
    id: str
    native_block_id: str
    parent_block_id: Optional[str]
    order: int
    author: AuthorResolution
    content_segments: Tuple[ContentSegment, ...]
    artifact_locators: Tuple[ArtifactLocator, ...]
    claim_eligible: bool


@dataclass(frozen=True)
class CanonicalDocument:
    schema_version: str
    adapter: adapters.AdapterIdentity
    source_snapshot_id: str
    owner: OwnerBinding
    native_document_id: str
    revision: str
    blocks: Tuple[DocumentBlock, ...]
    fidelity_losses: Tuple[FidelityLoss, ...]
    canonical_digest: str


@dataclass(frozen=True)
class SourceSnapshotManifest:
    schema_version: str
    source_kind: str
    source_snapshot_id: str
    adapter: adapters.AdapterIdentity
    owner: OwnerBinding
    raw_digest: str
    canonical_digest: str
    source_byte_count: int
    source_item_count: int
    fidelity_losses: Tuple[FidelityLoss, ...]
    payload_kind: str
    payload_reference: str
    payload: Union[CanonicalDocument, adapters.EventGraphManifest]


def _reject(code: str) -> None:
    raise SourceValidationError(code)


def _typed_record(value: Any, cls: type) -> Any:
    if type(value) is not cls or set(vars(value)) != {
            item.name for item in fields(cls)}:
        _reject("invalid-type")
    return value


def _typed_data(value: Any, cls: type) -> dict:
    value = _typed_record(value, cls)
    return {field.name: getattr(value, field.name) for field in fields(cls)}


def _preflight_typed_document(document: CanonicalDocument) -> None:
    if type(document.blocks) is not tuple or type(document.fidelity_losses) is not tuple:
        _reject("invalid-type")
    if len(document.blocks) > MAX_DOCUMENT_BLOCKS:
        _reject("document-resource-limit")
    # Exact item count of the wire payload produced below. A dict contributes
    # its keys and values; list members contribute their contained records.
    total = 31 + (9 * len(document.fidelity_losses))
    if total > MAX_DOCUMENT_ITEMS:
        _reject("document-resource-limit")
    for item in document.blocks:
        block = _typed_record(item, DocumentBlock)
        if type(block.content_segments) is not tuple or type(block.artifact_locators) is not tuple:
            _reject("invalid-type")
        added = 23 + (9 * len(block.content_segments)) + (9 * len(block.artifact_locators))
        if added > MAX_DOCUMENT_ITEMS - total:
            _reject("document-resource-limit")
        total += added


def canonical_document_payload(value: CanonicalDocument) -> dict:
    """Return the strict input-schema form of a typed canonical document.

    ``canonical_digest`` is derived output and fidelity losses use ``item_id``
    internally, so a generic dataclass conversion is not the wire schema.
    Every nested record and tuple is checked before conversion.
    """
    document = _typed_record(value, CanonicalDocument)
    _preflight_typed_document(document)
    identity = _typed_data(document.adapter, adapters.AdapterIdentity)
    owner = _typed_data(document.owner, OwnerBinding)
    blocks = []
    for item in document.blocks:
        block = _typed_record(item, DocumentBlock)
        blocks.append({
            "id": block.id, "native_block_id": block.native_block_id,
            "parent_block_id": block.parent_block_id, "order": block.order,
            "author": _typed_data(block.author, AuthorResolution),
            "content_segments": [
                _typed_data(segment, ContentSegment)
                for segment in block.content_segments],
            "artifact_locators": [
                _typed_data(locator, ArtifactLocator)
                for locator in block.artifact_locators],
            "claim_eligible": block.claim_eligible,
        })
    losses = []
    for item in document.fidelity_losses:
        loss = _typed_record(item, FidelityLoss)
        losses.append({
            "code": loss.code, "block_id": loss.item_id,
            "native_fact": loss.native_fact, "reason": loss.reason,
        })
    return {
        "schema_version": document.schema_version,
        "adapter": identity,
        "source_snapshot_id": document.source_snapshot_id,
        "owner": owner,
        "native_document_id": document.native_document_id,
        "revision": document.revision, "blocks": blocks,
        "fidelity_losses": losses,
    }


def _identity(value: Any) -> adapters.AdapterIdentity:
    value = adapters._object(value, adapters.ADAPTER_FIELDS, "/")
    name = adapters._enum(value["name"], adapters.ADAPTER_NAMES, "/")
    versions = tuple(adapters._version(value[key], "/") for key in
                     ("adapter_version", "product_version", "native_schema_version"))
    if (name,) + versions not in adapters.SUPPORTED_DOCUMENT_ADAPTERS:
        _reject("unsupported-adapter-version")
    return adapters.AdapterIdentity(name, *versions)


def _owner(value: Any, expected_owner_id: str) -> OwnerBinding:
    value = adapters._object(value, adapters.OWNER_FIELDS, "/")
    if value["kind"] != "user" or value["verification"] != "verified-principal":
        _reject("invalid-owner")
    owner_id = adapters._identifier(value["id"], "/")
    if owner_id != expected_owner_id:
        _reject("owner-context-mismatch")
    return OwnerBinding(**value)


def _losses(value: Any, ids: set, document: bool) -> Tuple[FidelityLoss, ...]:
    item_key = "block_id" if document else "event_id"
    normalized = []
    for raw in adapters._array(value, "/"):
        loss = adapters._object(raw, {"code", item_key, "native_fact", "reason"}, "/")
        normalized.append({"code": loss["code"], "event_id": loss[item_key],
                           "native_fact": loss["native_fact"], "reason": loss["reason"]})
    adapters._validate_losses(normalized, ids)
    return tuple(FidelityLoss(loss["code"], loss["event_id"], loss["native_fact"], loss["reason"])
                 for loss in normalized)


def _preflight_document(doc: dict) -> None:
    """Bound work and exact canonical UTF-8 bytes before retaining typed blocks.

    Iterators keep the traversal stack depth-bounded; wide containers are checked
    before their children are visited. Strings are encoded in small chunks so an
    oversized value cannot allocate a second unbounded serialized copy.
    """
    blocks = adapters._array(doc["blocks"], "/")
    if len(blocks) > MAX_DOCUMENT_BLOCKS:
        _reject("document-resource-limit")
    total_bytes = 0
    total_items = 0
    active = set()
    stack = [(iter((doc,)), None)]
    while stack:
        iterator, container_id = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            if container_id is not None:
                active.remove(container_id)
            continue
        total_items += 1
        if total_items > MAX_DOCUMENT_ITEMS:
            _reject("document-resource-limit")
        if type(item) in (dict, list):
            count = len(item) * (2 if type(item) is dict else 1)
            if count > MAX_DOCUMENT_ITEMS - total_items:
                _reject("document-resource-limit")
            if id(item) in active or len(stack) > adapters.MAX_CANONICAL_DEPTH:
                _reject("invalid-type")
            total_bytes += 2 + max(0, len(item) - 1) + (len(item) if type(item) is dict else 0)
            active.add(id(item))
            children = chain.from_iterable(item.items()) if type(item) is dict else iter(item)
            stack.append((children, id(item)))
        elif type(item) is str:
            total_bytes += 2
            # UTF-8 JSON needs at least one byte per character, before escaping.
            if len(item) > MAX_DOCUMENT_BYTES - total_bytes:
                _reject("document-too-large")
            for start in range(0, len(item), 4096):
                try:
                    chunk = json.dumps(item[start:start + 4096], ensure_ascii=False).encode("utf-8")
                except UnicodeEncodeError:
                    _reject("invalid-type")
                total_bytes += len(chunk) - 2
                if total_bytes > MAX_DOCUMENT_BYTES:
                    _reject("document-too-large")
        elif item is None:
            total_bytes += 4
        elif type(item) is bool:
            total_bytes += 4 if item else 5
        elif type(item) is int and -9223372036854775808 <= item <= 9223372036854775807:
            total_bytes += len(str(item))
        else:
            _reject("invalid-type")
        if total_bytes > MAX_DOCUMENT_BYTES:
            _reject("document-too-large")


def _document(value: Any, context: DocumentValidationContext) -> CanonicalDocument:
    if type(context) is not DocumentValidationContext:
        _reject("invalid-validation-context")
    graph_context = adapters.ValidationContext(context.expected_owner_id, context.expected_source_snapshot_id)
    adapters.validate_validation_context(graph_context)
    _exact(context.expected_revision, "invalid-revision", 256)
    if type(context.verified_authors) is not tuple:
        _reject("invalid-validation-context")
    authors = {}
    for binding in context.verified_authors:
        if type(binding) is not tuple or len(binding) != 2:
            _reject("invalid-validation-context")
        native_id, author_id = (adapters._identifier(item, "/") for item in binding)
        if native_id in authors:
            _reject("invalid-validation-context")
        authors[native_id] = author_id
    doc = adapters._object(value, DOCUMENT_FIELDS, "/")
    _preflight_document(doc)
    if doc["schema_version"] != DOCUMENT_SCHEMA:
        _reject("unsupported-schema-version")
    identity = _identity(doc["adapter"])
    snapshot = adapters._snapshot(doc["source_snapshot_id"], "/")
    if snapshot != context.expected_source_snapshot_id:
        _reject("source-snapshot-context-mismatch")
    owner = _owner(doc["owner"], context.expected_owner_id)
    native_document_id = adapters._identifier(doc["native_document_id"], "/")
    revision = _exact(doc["revision"], "invalid-revision", 256)
    if revision != context.expected_revision:
        _reject("revision-context-mismatch")
    blocks = []
    ids, native_ids, next_order = set(), set(), {}
    for raw in adapters._array(doc["blocks"], "/"):
        block = adapters._object(raw, BLOCK_FIELDS, "/")
        block_id = adapters._identifier(block["id"], "/")
        native_id = adapters._identifier(block["native_block_id"], "/")
        if block_id in ids:
            _reject("duplicate-block-id")
        if native_id in native_ids:
            _reject("duplicate-native-block-id")
        parent = adapters._identifier(block["parent_block_id"], "/", nullable=True)
        if parent is not None and parent not in ids:
            _reject("invalid-block-parent")
        order = adapters._integer(block["order"], 0, 9223372036854775807, "/")
        if order != next_order.get(parent, 0):
            _reject("invalid-block-order")
        next_order[parent] = order + 1
        author = adapters._validate_actor(block["author"], "/", owner.id)
        eligible = adapters._boolean(block["claim_eligible"], "/")
        resolved_owner = (author["kind"] == "user" and author["id"] == owner.id
                          and author["resolution"] == "verified-owner")
        if eligible != resolved_owner:
            _reject("invalid-claim-eligibility")
        if resolved_owner and authors.get(native_id) != owner.id:
            _reject("unverified-document-author")
        artifact_ids = adapters._validate_artifacts(block["artifact_locators"], "/")
        adapters._validate_segments(block["content_segments"], artifact_ids, "/")
        if len(adapters._canonical_bytes(block)) > adapters.MAX_EVENT_BYTES:
            _reject("block-too-large")
        blocks.append(DocumentBlock(block_id, native_id, parent, order, AuthorResolution(**author),
                                    tuple(ContentSegment(**item) for item in block["content_segments"]),
                                    tuple(ArtifactLocator(**item) for item in block["artifact_locators"]), eligible))
        ids.add(block_id)
        native_ids.add(native_id)
    losses = _losses(doc["fidelity_losses"], ids, True)
    canonical = adapters._canonical_bytes(doc)
    if len(canonical) > MAX_DOCUMENT_BYTES:
        _reject("document-too-large")
    return CanonicalDocument(DOCUMENT_SCHEMA, identity, snapshot, owner, native_document_id,
                             revision, tuple(blocks), losses, "sha256:" + hashlib.sha256(canonical).hexdigest())


def validate_canonical_document(value: Any, *, context: DocumentValidationContext) -> CanonicalDocument:
    """Validate ordered blocks with external owner, revision, and author bindings."""
    try:
        return _document(value, context)
    except (adapters.GraphValidationError, AuthorizationError) as error:
        raise SourceValidationError(error.code) from None


def validate_source_snapshot(value: Any, *, payload: Any, raw_bytes: bytes,
                             context: Union[DocumentValidationContext, adapters.ValidationContext]) -> SourceSnapshotManifest:
    """Bind supplied raw bytes and fully validated canonical payload to one manifest.

    The snapshot ID and raw digest both equal SHA-256 of the supplied native bytes.
    The raw bytes are used only for digest/count verification and are not retained.
    Sessions always pass through the complete existing graph validator.
    """
    try:
        manifest = adapters._object(value, SNAPSHOT_FIELDS, "/")
        if manifest["schema_version"] != SNAPSHOT_SCHEMA:
            _reject("unsupported-schema-version")
        kind = adapters._enum(manifest["source_kind"], {"document", "session"}, "/")
        expected_kind = "canonical-document" if kind == "document" else "event-graph"
        if manifest["payload_kind"] != expected_kind:
            _reject("payload-kind-mismatch")
        if type(raw_bytes) is not bytes or len(raw_bytes) > adapters.MAX_GRAPH_BYTES:
            _reject("invalid-source-bytes")
        typed_digest = None
        normalized_payload = payload
        if kind == "document" and type(payload) is CanonicalDocument:
            typed_digest = payload.canonical_digest
            normalized_payload = canonical_document_payload(payload)
        validated = (_document(normalized_payload, context) if kind == "document" else
                     adapters.validate_event_graph(normalized_payload, context=context))
        identity = _identity(manifest["adapter"])
        owner = _owner(manifest["owner"], context.expected_owner_id)
        snapshot = adapters._snapshot(manifest["source_snapshot_id"], "/")
        for field in ("raw_digest", "canonical_digest", "payload_reference"):
            adapters._snapshot(manifest[field], "/")
        byte_count = adapters._integer(manifest["source_byte_count"], 0, adapters.MAX_GRAPH_BYTES, "/")
        item_count = adapters._integer(manifest["source_item_count"], 0, 9223372036854775807, "/")
        raw_digest = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        expected_count = len(validated.blocks) if kind == "document" else validated.event_count
        if (snapshot != raw_digest or snapshot != validated.source_snapshot_id or identity != validated.adapter
                or manifest["raw_digest"] != raw_digest or byte_count != len(raw_bytes)
                or item_count != expected_count or manifest["canonical_digest"] != validated.canonical_digest
                or manifest["payload_reference"] != validated.canonical_digest
                or (typed_digest is not None and typed_digest != validated.canonical_digest)):
            _reject("snapshot-binding-mismatch")
        ids = {item["id"] for item in normalized_payload[
            "blocks" if kind == "document" else "events"]}
        losses = _losses(manifest["fidelity_losses"], ids, kind == "document")
        if manifest["fidelity_losses"] != normalized_payload["fidelity_losses"]:
            _reject("snapshot-binding-mismatch")
        return SourceSnapshotManifest(SNAPSHOT_SCHEMA, kind, snapshot, identity, owner, raw_digest,
                                      validated.canonical_digest, byte_count, item_count, losses,
                                      expected_kind, validated.canonical_digest, validated)
    except (adapters.GraphValidationError, AuthorizationError) as error:
        raise SourceValidationError(error.code) from None
