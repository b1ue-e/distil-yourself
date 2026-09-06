"""Pure, bounded pre-ingestion redaction; no I/O or ambient trust discovery.

The event stream contains SourceSpan headers followed by SourceChunk bytes. V1
buffers one bounded span, so UTF-8 and detections can cross any chunk boundary;
it does not stream output. A new header ends the preceding span (detectors do
not cross independently attributed spans). Malformed UTF-8 fails closed.

Overlapping intervals are merged, covering their entire union. The longest
match supplies the type; ties use the fixed detector priority then position.
Exact literal allowlisting happens before merging, except private keys and
declared IDs can never be allowlisted. Match lengths and all run totals are
bounded. Regexes are fixed; multiline/credential scanning uses bounded searches.

Caller establishes trust and supplies digest-only provenance and verified actor
resolution. This module cannot verify the external attestation itself. Clearable
buffers are wiped on success, failure, and cancellation; Python immutable copies,
caller-owned inputs and traceback frames prevent guaranteed memory erasure.

Frozen records prevent ordinary assignment, not object.__setattr__ tampering.
Consumers must call validate_redaction_result with their external context and
run key before using/serializing a retained result. It defensively copies strict
records and authenticates all output text (including placeholders), typed edges,
claim eligibility and ordering. HMAC identifiers explicitly use hmac-sha256;
external SHA-256 digests use sha256. HMAC domains are versioned and type-separated.
"""

from dataclasses import dataclass, fields
import hashlib
import hmac
import json
import re
from typing import Optional, Tuple

MAX_INPUT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_SPANS = 1024
MAX_CHUNKS = 1024 * 1024
MAX_DETECTIONS = 8192
MAX_SECRET_CHARS = 4096
MAX_PRIVATE_KEY_CHARS = 16384
MAX_LITERALS = 128
MAX_LITERAL_BYTES = 512
MAX_LITERAL_TOTAL_BYTES = 16384
MAX_ARMOR_LINE_CHARS = 128

_CODES = frozenset(("invalid-record", "invalid-context", "invalid-key", "invalid-event",
                    "invalid-utf8", "invalid-private-key", "secret-limit", "input-limit",
                    "output-limit", "span-limit", "chunk-limit", "detection-limit",
                    "already-finalized", "stream-failed", "cancelled"))


class RedactionError(ValueError):
    """Stable bounded code only, including when instantiated by a caller."""

    def __init__(self, code):
        self.code = code if type(code) is str and code in _CODES else "invalid-record"
        super().__init__(self.code)


def _fail(code="invalid-record"):
    raise RedactionError(code)


class _Closed(type):
    def __call__(cls, *args, **kwargs):
        # Generated dataclass constructors otherwise echo unknown keyword names.
        code = None
        try:
            return super().__call__(*args, **kwargs)
        except RedactionError as error:
            code = error.code
        except Exception:
            code = "invalid-record"
        raise RedactionError(code)


def _digest(value):
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        _fail()


def _mac_id(value):
    if type(value) is not str or re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", value) is None:
        _fail()


def _key(key):
    if type(key) is not bytes or not 32 <= len(key) <= 64:
        _fail("invalid-key")


def _mac(key, domain, value):
    message = b"knowledge-distiller:redaction:v1\x00" + domain.encode("ascii") + b"\x00" + value.encode("utf-8")
    return "hmac-sha256:" + hmac.new(key, message, hashlib.sha256).hexdigest()


def _literals(values):
    if type(values) is not tuple or len(values) > MAX_LITERALS:
        _fail()
    total = 0
    for value in values:
        if type(value) is not str or not value or len(value) > MAX_LITERAL_BYTES:
            _fail()
        try:
            length = len(value.encode("utf-8"))
        except UnicodeError:
            _fail()
        if length > MAX_LITERAL_BYTES:
            _fail()
        total += length
        if total > MAX_LITERAL_TOTAL_BYTES:
            _fail()
    if len(set(values)) != len(values):
        _fail()
    return tuple(sorted(values))


@dataclass(frozen=True, repr=False)
class TrustContext(metaclass=_Closed):
    context_id: str
    owner_id: str
    ingestion_run_id: str
    content_grant_digest: str
    authority_attestation_digest: str
    participant_names: Tuple[str, ...] = ()
    participant_ids: Tuple[str, ...] = ()
    allowlist: Tuple[str, ...] = ()

    def __post_init__(self):
        for value in (self.context_id, self.owner_id, self.ingestion_run_id,
                      self.content_grant_digest, self.authority_attestation_digest):
            _digest(value)
        for name in ("participant_names", "participant_ids", "allowlist"):
            object.__setattr__(self, name, _literals(getattr(self, name)))
        for literal in self.allowlist:
            if (any(identifier in literal for identifier in self.participant_ids)
                    or "-----BEGIN" in literal or "-----END" in literal):
                _fail()


@dataclass(frozen=True, repr=False)
class SpanBinding(metaclass=_Closed):
    context_id: str
    source_snapshot_id: str
    native_locator_digest: str
    actor_id: Optional[str] = None
    actor_resolution: str = "unknown"

    def __post_init__(self):
        for value in (self.context_id, self.source_snapshot_id, self.native_locator_digest):
            _digest(value)
        if self.actor_id is not None:
            _digest(self.actor_id)
        if (type(self.actor_resolution) is not str
                or self.actor_resolution not in ("verified", "ambiguous", "unknown")
                or (self.actor_resolution == "verified" and self.actor_id is None)):
            _fail()


def _record(value, cls):
    if type(value) is not cls or set(vars(value)) != {field.name for field in fields(cls)}:
        _fail()
    # Reconstruct to reject records mutated through object.__setattr__/__dict__.
    return cls(**vars(value))


@dataclass(frozen=True, repr=False)
class SourceSpan(metaclass=_Closed):
    binding: SpanBinding

    def __post_init__(self):
        _record(self.binding, SpanBinding)


@dataclass(frozen=True, repr=False)
class SourceChunk(metaclass=_Closed):
    data: bytes

    def __post_init__(self):
        if type(self.data) is not bytes:
            _fail()
        if len(self.data) > MAX_INPUT_BYTES:
            _fail("input-limit")


_RELATIONS = ("native-locator", "source-snapshot", "ingestion-run", "content-grant", "authority-attestation")
_NODE_KINDS = ("redacted-span",) + _RELATIONS


@dataclass(frozen=True, repr=False)
class ProvenanceNode(metaclass=_Closed):
    kind: str
    digest: str

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in _NODE_KINDS:
            _fail()
        (_mac_id if self.kind == "redacted-span" else _digest)(self.digest)


@dataclass(frozen=True, repr=False)
class DerivationEdge(metaclass=_Closed):
    relation: str
    from_node: ProvenanceNode
    to_node: ProvenanceNode

    def __post_init__(self):
        if type(self.relation) is not str or self.relation not in _RELATIONS:
            _fail()
        source = _record(self.from_node, ProvenanceNode)
        target = _record(self.to_node, ProvenanceNode)
        index = _RELATIONS.index(self.relation)
        if source.kind != _NODE_KINDS[index] or target.kind != _NODE_KINDS[index + 1]:
            _fail()
        object.__setattr__(self, "from_node", source)
        object.__setattr__(self, "to_node", target)


@dataclass(frozen=True, repr=False)
class RedactedSpan(metaclass=_Closed):
    span_id: str
    text: str
    claim_eligible: bool
    derivations: Tuple[DerivationEdge, ...]

    def __post_init__(self):
        _mac_id(self.span_id)
        if type(self.text) is not str or type(self.claim_eligible) is not bool:
            _fail()
        if len(self.text) > MAX_OUTPUT_BYTES or len(self.text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            _fail("output-limit")
        if type(self.derivations) is not tuple or len(self.derivations) != 5:
            _fail()
        previous = ProvenanceNode("redacted-span", self.span_id)
        edges = []
        for relation, edge in zip(_RELATIONS, self.derivations):
            edge = _record(edge, DerivationEdge)
            if edge.relation != relation or edge.from_node != previous:
                _fail()
            edges.append(edge)
            previous = edge.to_node
        object.__setattr__(self, "derivations", tuple(edges))


def _span_payload(context, index, text, eligible, nodes):
    context_values = tuple(getattr(context, field.name) for field in fields(TrustContext))
    return json.dumps((context_values, index, text, eligible, tuple((node.kind, node.digest) for node in nodes)),
                      ensure_ascii=True, separators=(",", ":"))


def validate_redaction_result(result, *, context: TrustContext, key: bytes) -> Tuple[RedactedSpan, ...]:
    """Return defensive copies authenticated against the external run context/key.

    No secret values are needed: the span MAC binds placeholder text and every
    output field. This validates integrity, not the truth of external attestations.
    The caller must retain its run key externally; Redactor clears its own copy.
    """
    code = None
    try:
        context = _record(context, TrustContext)
        _key(key)
        if type(result) is not tuple:
            _fail()
        if len(result) > MAX_SPANS:
            _fail("span-limit")
        validated = []
        output_bytes = 0
        for index, item in enumerate(result):
            span = _record(item, RedactedSpan)
            output_bytes += len(span.text.encode("utf-8"))
            if output_bytes > MAX_OUTPUT_BYTES:
                _fail("output-limit")
            nodes = tuple(edge.to_node for edge in span.derivations)
            if tuple(node.digest for node in nodes[2:]) != (context.ingestion_run_id,
                    context.content_grant_digest, context.authority_attestation_digest):
                _fail("invalid-context")
            payload = _span_payload(context, index, span.text, span.claim_eligible, nodes)
            if not hmac.compare_digest(span.span_id, _mac(key, "redacted-span", payload)):
                _fail()
            validated.append(span)
        return tuple(validated)
    except RedactionError as error:
        code = error.code
    except Exception:
        code = "invalid-record"
    raise RedactionError(code)


_PRIVATE_KEY_LABELS = frozenset(("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "DSA PRIVATE KEY",
                               "OPENSSH PRIVATE KEY", "ENCRYPTED PRIVATE KEY", "PGP PRIVATE KEY BLOCK"))
_ARMOR_DELIMITER = re.compile(r"-----(BEGIN|END) ([A-Z0-9 ]{1,64})-----")
_ARMOR_MARKER = re.compile(r"[\s_-]*(?ai:BEGIN|END)(?=[\s_-])")
_ARMOR_LABEL_PAIR = re.compile(r"(?<![A-Za-z0-9])(?ai:PRIVATE)[\s_-]+(?ai:KEY)(?![A-Za-z0-9])")
_ARMOR_LABEL_END = re.compile(r"(?<![A-Za-z0-9])(?ai:PRIVATE)[\s_-]+(?ai:KEY)(?:[\s_-]+(?ai:BLOCK))?[\s_-]*\Z")
_ASSIGN = re.compile(r"(?i)(?<![^\W_])(?:password|passwd|pwd|secret|secret[_-]access[_-]key|client_secret|token|api[_-]?key|api[_-]?token|session(?:[_-]?token|[_-]?id)?|access[_-]?token|refresh[_-]?token)[\"']?[ \t]{0,32}[:=][ \t]{0,32}")
_BEARER = re.compile(r"(?i)(?<!\w)bearer[ \t]{1,32}")
_COOKIE = re.compile(r"(?im)^[ \t]{0,32}(?:set-cookie|cookie)[ \t]{0,32}:[ \t]{0,32}")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,128}@[\w-]{1,128}(?:\.[\w-]{1,63}){1,8}(?![\w.-])")
_PHONE = re.compile(r"(?<!\w)\+?[0-9][0-9 ()-]{5,30}[0-9](?!\w)")


def _armor_suspicious(line):
    # Recognition only: whitespace/hyphen/underscore separators never repair an
    # invalid envelope. Case matching stays ASCII, excluding Unicode confusables.
    # Bare BEGIN/END lines must end in PRIVATE KEY [BLOCK]; continuation prose
    # such as "begin private key rotation safely" is not a delimiter. Dashed
    # private-key-looking lines remain suspicious even with broken marker words.
    # These lexical scans are linear over the already bounded source. They make
    # no normalized line copy; the delimiter length gate runs before strip/fullmatch.
    return ((_ARMOR_MARKER.match(line) is not None and _ARMOR_LABEL_END.search(line) is not None)
            or ("---" in line and _ARMOR_LABEL_PAIR.search(line) is not None))


class Redactor:
    """Single-use run; externally owned input objects are never modified."""

    def __init__(self, context: TrustContext, key: bytes):
        self._context = _record(context, TrustContext)
        _key(key)
        self._key = bytearray(key)
        self._buffer = bytearray()
        self._finalized = False
        self._detections = 0
        self._output_bytes = 0

    def _wipe(self):
        for buffer in (self._buffer, self._key):
            for index in range(len(buffer)):
                buffer[index] = 0
            buffer.clear()
        self._context = None
        self._detections = 0

    def _matches(self, text):
        matches = []
        context = self._context

        def add(start, end, kind, priority, limit=MAX_SECRET_CHARS):
            if end - start > limit:
                _fail("secret-limit")
            self._detections += 1
            if self._detections > MAX_DETECTIONS:
                _fail("detection-limit")
            if kind not in ("private-key", "participant-id") and text[start:end] in context.allowlist:
                return
            matches.append((start, end, kind, priority))

        # Single linear line walk. Only complete, allowlisted delimiter lines
        # are accepted; any private-key-looking malformed armor fails closed.
        # No nested blocks, cross-label endings or regex search over key bodies.
        position = 0
        active = None
        while position < len(text):
            end = text.find("\n", position)
            end = len(text) if end < 0 else end
            if active is not None and end - active[1] > MAX_PRIVATE_KEY_CHARS:
                _fail("secret-limit")
            line = text[position:end]
            if _armor_suspicious(line):
                if len(line) > MAX_ARMOR_LINE_CHARS:
                    _fail("invalid-private-key")
                delimiter = _ARMOR_DELIMITER.fullmatch(line.strip(" \t\r"))
                if delimiter is None or delimiter.group(2) not in _PRIVATE_KEY_LABELS:
                    _fail("invalid-private-key")
                action, label = delimiter.groups()
                if action == "BEGIN":
                    if active is not None:
                        _fail("invalid-private-key")
                    active = (label, position)
                else:
                    if active is None or active[0] != label:
                        _fail("invalid-private-key")
                    add(active[1], end, "private-key", 0, MAX_PRIVATE_KEY_CHARS)
                    active = None
            position = end + 1
        if active is not None:
            _fail("invalid-private-key")
        for pattern, kind, priority in ((_COOKIE, "cookie", 1), (_ASSIGN, "credential", 2), (_BEARER, "credential", 2)):
            for match in pattern.finditer(text):
                start = match.end()
                end = start
                quote = text[start:start + 1] if text[start:start + 1] in ("'", '"') else None
                if quote and kind != "cookie":
                    end = text.find(quote, start + 1, start + MAX_SECRET_CHARS + 3)
                    if end < 0:
                        _fail("secret-limit" if len(text) - start > MAX_SECRET_CHARS else "invalid-event")
                    start += 1
                else:
                    while end < len(text) and (text[end] not in "\r\n" if kind == "cookie" else not text[end].isspace() and text[end] not in ",;\"'}]"):
                        end += 1
                        if end - start > MAX_SECRET_CHARS:
                            _fail("secret-limit")
                if end > start:
                    add(start, end, kind, priority)
        for pattern, kind, priority in ((_EMAIL, "email", 3), (_PHONE, "phone", 4)):
            for match in pattern.finditer(text):
                if kind != "phone" or sum(c.isdigit() for c in match.group()) >= 7:
                    add(match.start(), match.end(), kind, priority)
        for literals, kind, priority in ((context.participant_ids, "participant-id", 5),
                                         (context.participant_names, "participant-name", 6)):
            for literal in literals:
                start = text.find(literal)
                while start >= 0:
                    add(start, start + len(literal), kind, priority)
                    start = text.find(literal, start + 1)
        # Sorting then merging is O(n log n), without quadratic overlap checks.
        merged = []
        for start, end, kind, priority in sorted(matches):
            rank = (-(end - start), priority, start, kind)
            if merged and start < merged[-1][1]:
                previous = merged[-1]
                previous[1] = max(previous[1], end)
                if rank < previous[3]:
                    previous[2], previous[3] = kind, rank
            else:
                merged.append([start, end, kind, rank])
        return merged

    def _finish_span(self, binding, index):
        try:
            text = self._buffer.decode("utf-8", errors="strict")
        except UnicodeError:
            _fail("invalid-utf8")
        pieces = []

        def append(piece):
            length = len(piece.encode("utf-8"))
            if self._output_bytes + length > MAX_OUTPUT_BYTES:
                _fail("output-limit")
            self._output_bytes += length
            pieces.append(piece)

        cursor = 0
        for start, end, kind, _ in self._matches(text):
            append(text[cursor:start])
            append("[redacted:" + kind + ":" + _mac(self._key, "placeholder:" + kind, text[start:end]) + "]")
            cursor = end
        append(text[cursor:])
        redacted = "".join(pieces)
        context = self._context
        eligible = binding.actor_resolution == "verified" and binding.actor_id == context.owner_id
        digests = (binding.native_locator_digest, binding.source_snapshot_id, context.ingestion_run_id,
                   context.content_grant_digest, context.authority_attestation_digest)
        nodes = tuple(ProvenanceNode(kind, digest) for kind, digest in zip(_RELATIONS, digests))
        payload = _span_payload(context, index, redacted, eligible, nodes)
        span_id = _mac(self._key, "redacted-span", payload)
        edges = tuple(DerivationEdge(relation, source, target) for relation, source, target
                      in zip(_RELATIONS, (ProvenanceNode("redacted-span", span_id),) + nodes[:-1], nodes))
        for position in range(len(self._buffer)):
            self._buffer[position] = 0
        self._buffer.clear()
        return RedactedSpan(span_id, redacted, eligible, edges)

    def redact(self, events) -> Tuple[RedactedSpan, ...]:
        if self._finalized:
            _fail("already-finalized")
        self._finalized = True
        output = []
        binding = None
        chunks = spans = input_bytes = 0
        error_code = None
        try:
            self._context = _record(self._context, TrustContext)
            for event in events:
                if type(event) is SourceSpan:
                    event = _record(event, SourceSpan)
                    new_binding = _record(event.binding, SpanBinding)
                    if new_binding.context_id != self._context.context_id:
                        _fail("invalid-context")
                    spans += 1
                    if spans > MAX_SPANS:
                        _fail("span-limit")
                    if binding is not None:
                        output.append(self._finish_span(binding, spans - 2))
                    binding = new_binding
                elif type(event) is SourceChunk:
                    event = _record(event, SourceChunk)
                    if binding is None:
                        _fail("invalid-event")
                    chunks += 1
                    if chunks > MAX_CHUNKS:
                        _fail("chunk-limit")
                    input_bytes += len(event.data)
                    if input_bytes > MAX_INPUT_BYTES:
                        _fail("input-limit")
                    self._buffer.extend(event.data)
                else:
                    _fail("invalid-event")
            if binding is not None:
                output.append(self._finish_span(binding, spans - 1))
        except RedactionError as error:
            error_code = error.code
        except Exception:
            error_code = "stream-failed"
        except BaseException:
            error_code = "cancelled"
        finally:
            self._wipe()
        if error_code:
            output.clear()
            raise RedactionError(error_code)
        return tuple(output)
