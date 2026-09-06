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


@dataclass(frozen=True)
class DerivationEdge(metaclass=_Closed):
    relation: str
    source_id: str
    target_id: str

    def __post_init__(self):
        if type(self.relation) is not str or self.relation not in _RELATIONS:
            _fail()
        _digest(self.source_id)
        _digest(self.target_id)


@dataclass(frozen=True)
class RedactedSpan(metaclass=_Closed):
    span_id: str
    text: str
    claim_eligible: bool
    derivations: Tuple[DerivationEdge, ...]

    def __post_init__(self):
        _digest(self.span_id)
        if type(self.text) is not str or type(self.claim_eligible) is not bool:
            _fail()
        if len(self.text) > MAX_OUTPUT_BYTES or len(self.text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            _fail("output-limit")
        if type(self.derivations) is not tuple or len(self.derivations) != 5:
            _fail()
        previous = self.span_id
        for relation, edge in zip(_RELATIONS, self.derivations):
            _record(edge, DerivationEdge)
            if edge.relation != relation or edge.source_id != previous:
                _fail()
            previous = edge.target_id


_PEM_START = re.compile(r"-----BEGIN ((?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY)-----")
_PEM_END = re.compile(r"-----END [A-Z ]{0,64}PRIVATE KEY-----")
_ASSIGN = re.compile(r"(?i)(?<![^\W_])(?:password|passwd|pwd|secret|secret[_-]access[_-]key|client_secret|token|api[_-]?key|api[_-]?token|session(?:[_-]?token|[_-]?id)?|access[_-]?token|refresh[_-]?token)[\"']?[ \t]{0,32}[:=][ \t]{0,32}")
_BEARER = re.compile(r"(?i)(?<!\w)bearer[ \t]{1,32}")
_COOKIE = re.compile(r"(?im)^[ \t]{0,32}(?:set-cookie|cookie)[ \t]{0,32}:[ \t]{0,32}")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,128}@[\w-]{1,128}(?:\.[\w-]{1,63}){1,8}(?![\w.-])")
_PHONE = re.compile(r"(?<!\w)\+?[0-9][0-9 ()-]{5,30}[0-9](?!\w)")


class Redactor:
    """Single-use run; externally owned input objects are never modified."""

    def __init__(self, context: TrustContext, key: bytes):
        self._context = _record(context, TrustContext)
        if type(key) is not bytes or not 32 <= len(key) <= 64:
            _fail("invalid-key")
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

    def _mac(self, domain, value):
        return hmac.new(self._key, domain.encode() + b"\x00" + value.encode("utf-8"), hashlib.sha256).hexdigest()

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

        key_endings = set()
        for match in _PEM_START.finditer(text):
            ending = "-----END " + match.group(1) + "-----"
            end = text.find(ending, match.end(), match.start() + MAX_PRIVATE_KEY_CHARS + 1)
            if end < 0:
                _fail("secret-limit" if len(text) - match.start() > MAX_PRIVATE_KEY_CHARS else "invalid-private-key")
            add(match.start(), end + len(ending), "private-key", 0, MAX_PRIVATE_KEY_CHARS)
            key_endings.add(end)
        for match in _PEM_END.finditer(text):
            if match.start() not in key_endings:
                _fail("invalid-private-key")
        # Unsupported/malformed private-key delimiters must not pass through.
        for line in text.splitlines():
            if "PRIVATE KEY-----" in line and "-----BEGIN" in line and _PEM_START.search(line) is None:
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
            append("[redacted:" + kind + ":" + self._mac("placeholder", text[start:end]) + "]")
            cursor = end
        append(text[cursor:])
        redacted = "".join(pieces)
        context = self._context
        eligible = binding.actor_resolution == "verified" and binding.actor_id == context.owner_id
        nodes = (binding.native_locator_digest, binding.source_snapshot_id, context.ingestion_run_id,
                 context.content_grant_digest, context.authority_attestation_digest)
        payload = json.dumps((context.context_id, index, redacted, eligible, nodes), ensure_ascii=True, separators=(",", ":"))
        span_id = "sha256:" + self._mac("redacted-span", payload)
        edges = tuple(DerivationEdge(relation, source, target) for relation, source, target
                      in zip(_RELATIONS, (span_id,) + nodes[:-1], nodes))
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
