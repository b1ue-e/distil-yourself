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
        if type(value) is not str or not value:
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
    actor_kind: str
    actor_id: Optional[str] = None
    actor_resolution: str = "unresolved"

    def __post_init__(self):
        for value in (self.context_id, self.source_snapshot_id, self.native_locator_digest):
            _digest(value)
        if self.actor_id is not None:
            _digest(self.actor_id)
        if (type(self.actor_kind) is not str
                or self.actor_kind not in ("user", "assistant", "agent", "tool", "system", "external")
                or type(self.actor_resolution) is not str
                or self.actor_resolution not in ("verified-owner", "native", "deterministic", "unresolved")
                or (self.actor_resolution == "verified-owner"
                    and (self.actor_kind != "user" or self.actor_id is None))):
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


def _span_payload(context, index, text, eligible, nodes, binding):
    context_values = tuple(getattr(context, field.name) for field in fields(TrustContext))
    binding_values = tuple(getattr(binding, field.name) for field in fields(SpanBinding))
    return json.dumps((context_values, binding_values, index, text, eligible,
                       tuple((node.kind, node.digest) for node in nodes)),
                      ensure_ascii=True, separators=(",", ":"))


def validate_redaction_result(result, *, context: TrustContext, key: bytes,
                              expected_bindings: Tuple[SpanBinding, ...]) -> Tuple[RedactedSpan, ...]:
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
        if type(expected_bindings) is not tuple or len(expected_bindings) != len(result):
            _fail("invalid-context")
        validated = []
        output_bytes = 0
        for index, item in enumerate(result):
            span = _record(item, RedactedSpan)
            binding = _record(expected_bindings[index], SpanBinding)
            if binding.context_id != context.context_id:
                _fail("invalid-context")
            if (binding.actor_resolution == "verified-owner"
                    and (binding.actor_kind != "user" or binding.actor_id != context.owner_id)):
                _fail("invalid-context")
            output_bytes += len(span.text.encode("utf-8"))
            if output_bytes > MAX_OUTPUT_BYTES:
                _fail("output-limit")
            nodes = tuple(edge.to_node for edge in span.derivations)
            expected_nodes = tuple(ProvenanceNode(kind, digest) for kind, digest in zip(
                _RELATIONS, (binding.native_locator_digest, binding.source_snapshot_id, context.ingestion_run_id,
                             context.content_grant_digest, context.authority_attestation_digest)))
            if nodes != expected_nodes:
                _fail("invalid-context")
            eligible = (binding.actor_kind == "user" and binding.actor_resolution == "verified-owner"
                        and binding.actor_id == context.owner_id)
            if span.claim_eligible != eligible:
                _fail("invalid-context")
            payload = _span_payload(context, index, span.text, span.claim_eligible, expected_nodes, binding)
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
_PRIVATE_KEY_COMPACTS = frozenset(label.replace(" ", "") for label in _PRIVATE_KEY_LABELS)
_MAX_PRIVATE_KEY_COMPACT_CHARS = max(len(label) for label in _PRIVATE_KEY_COMPACTS)
_ARMOR_DELIMITER = re.compile(r"-----(BEGIN|END) ([A-Z0-9 ]{1,64})-----")
_ASSIGN = re.compile(r"(?i)(?<![^\W_])(?:password|passwd|pwd|secret|secret[_-]access[_-]key|client_secret|token|api[_-]?key|api[_-]?token|session(?:[_-]?token|[_-]?id)?|access[_-]?token|refresh[_-]?token)[\"']?[ \t]*[:=][ \t]*")
_BEARER = re.compile(r"(?i)(?<!\w)bearer[ \t]+")
_COOKIE = re.compile(r"(?im)^[ \t]*(?:set-cookie|cookie)[ \t]*:[ \t]*")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,128}@[\w-]{1,128}(?:\.[\w-]{1,63}){1,8}(?![\w.-])")
_PHONE = re.compile(r"(?<!\w)\+?[0-9][0-9 ()-]{5,30}[0-9](?!\w)")


def _bare_armor_suspicious(text, start, end):
    """Recognize only the closed, unfenced BEGIN/END label grammar."""
    def word(index, value):
        return (index + len(value) <= end and all(text[index + offset] in (char, char.lower())
                                                   for offset, char in enumerate(value)))

    # Bare candidates consume only horizontal indentation before exact BEGIN/END
    # and a closed label sequence. This leaves ordinary imperative prose outside
    # the grammar while preserving indented private-key candidates.
    bare_start = start
    while bare_start < end and text[bare_start] in " \t":
        bare_start += 1
    if word(bare_start, "BEGIN"):
        marker_end = bare_start + 5
    elif word(bare_start, "END"):
        marker_end = bare_start + 3
    else:
        return False
    if marker_end == end or text[marker_end].isascii() and text[marker_end].isalnum():
        return False
    compact = []
    for index in range(marker_end, end):
        char = text[index]
        if char.isascii() and char.isalnum():
            if len(compact) == _MAX_PRIVATE_KEY_COMPACT_CHARS:
                return False
            compact.append(char.upper())
        elif not (char.isspace() or char in "-_/"):
            return False
    return "".join(compact) in _PRIVATE_KEY_COMPACTS


def _armor_matches(text, add):
    """Scan a bounded span once for exact armor and malformed fenced candidates.

    The candidate DFA never builds a normalized copy: it advances ASCII BEGIN,
    END, PRIVATE and KEY states directly, allowing only delimiter separators
    between letters. Exact delimiters remain the sole accepted grammar.
    """
    active = None
    exact_delimiters = pair_count = partial_count = 0
    fence_seen = False
    line_start = 0
    line_leading_hyphens = 0
    line_prefix = True
    line_marker_fragment = line_private = line_key = line_unicode = False
    begin_index = end_index = private_index = key_index = 0
    private_ready = marker_pending = False

    def advance(index, value, char):
        if char in " \t\r\n-_/":
            return index
        if char == value[index] or char == value[index].lower():
            return index + 1
        return 1 if char == value[0] or char == value[0].lower() else 0

    def finish_line(line_end):
        nonlocal active, exact_delimiters, partial_count, fence_seen
        nonlocal line_leading_hyphens, line_marker_fragment, line_private, line_key, line_unicode
        nonlocal marker_pending
        trimmed_start, trimmed_end = line_start, line_end
        while trimmed_start < trimmed_end and text[trimmed_start] in " \t\r":
            trimmed_start += 1
        while trimmed_end > trimmed_start and text[trimmed_end - 1] in " \t\r":
            trimmed_end -= 1
        trailing = trimmed_end
        while trailing > trimmed_start and text[trailing - 1] == "-":
            trailing -= 1
        line_fence = line_leading_hyphens >= 3 or trimmed_end - trailing >= 3
        if line_fence:
            fence_seen = True
            if (line_private or line_key) and line_end - line_start > MAX_ARMOR_LINE_CHARS:
                _fail("invalid-private-key")
        if not line_unicode and line_marker_fragment:
            partial_count += 1
        if active is not None and line_end - active[1] > MAX_PRIVATE_KEY_CHARS:
            _fail("secret-limit")
        delimiter = _ARMOR_DELIMITER.fullmatch(text, trimmed_start, trimmed_end)
        if delimiter is not None and delimiter.group(2) in _PRIVATE_KEY_LABELS:
            exact_delimiters += 1
            action, label = delimiter.groups()
            if action == "BEGIN":
                if active is not None:
                    _fail("invalid-private-key")
                active = (label, line_start)
            else:
                if active is None or active[0] != label:
                    _fail("invalid-private-key")
                add(active[1], line_end, "private-key", 0, MAX_PRIVATE_KEY_CHARS)
                active = None
        elif not line_fence and _bare_armor_suspicious(text, line_start, line_end):
            _fail("invalid-private-key")
        if line_unicode:
            marker_pending = False

    for position, char in enumerate(text):
        if char == "\n":
            finish_line(position)
            line_start = position + 1
            line_leading_hyphens = 0
            line_prefix = True
            line_marker_fragment = line_private = line_key = line_unicode = False
            continue
        if line_prefix:
            if char in " \t\r":
                pass
            elif char == "-":
                line_leading_hyphens += 1
            else:
                line_prefix = False
        if not char.isascii() and char.isalnum():
            line_unicode = True
            begin_index = end_index = private_index = key_index = 0
            # A Unicode letter cannot complete an ASCII token, but it must not
            # erase a completed PRIVATE before a later ASCII KEY.
            marker_pending = False
            continue
        begin_index = advance(begin_index, "BEGIN", char)
        end_index = advance(end_index, "END", char)
        private_index = advance(private_index, "PRIVATE", char)
        key_index = advance(key_index, "KEY", char)
        if begin_index == 5 or end_index == 3:
            marker_pending = True
            begin_index = end_index = 0
        if private_index == 7:
            line_private = True
            private_ready = True
            if marker_pending:
                line_marker_fragment = True
                marker_pending = False
            private_index = 0
        if key_index == 3:
            line_key = True
            if private_ready:
                pair_count += 1
                private_ready = False
            if marker_pending:
                line_marker_fragment = True
                marker_pending = False
            key_index = 0
    finish_line(len(text))
    if active is not None:
        _fail("invalid-private-key")
    # A boundary fence makes an ASCII PRIVATE ... KEY run a candidate even when
    # marker spelling or line breaks are malformed. Exact delimiter lines are
    # the only candidate events that may be consumed.
    if fence_seen and (pair_count != exact_delimiters or partial_count != exact_delimiters):
        _fail("invalid-private-key")


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

        _armor_matches(text, add)
        for pattern, kind, priority in ((_COOKIE, "cookie", 1), (_ASSIGN, "credential", 2), (_BEARER, "credential", 2)):
            for match in pattern.finditer(text):
                start = match.end()
                end = start
                quote = text[start:start + 1] if text[start:start + 1] in ("'", '"') else None
                if quote and kind != "cookie":
                    value_start = start + 1
                    end = value_start
                    while end < len(text):
                        if end - value_start > MAX_SECRET_CHARS:
                            _fail("secret-limit")
                        if text[end] == quote:
                            start = value_start
                            break
                        if text[end] in "\r\n":
                            _fail("invalid-event")
                        if text[end] == "\\":
                            escaped = end + 1
                            if escaped == len(text) or text[escaped] not in (quote, "\\"):
                                _fail("invalid-event")
                            end += 2
                        else:
                            end += 1
                    else:
                        _fail("secret-limit" if len(text) - value_start > MAX_SECRET_CHARS else "invalid-event")
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
        eligible = (binding.actor_kind == "user" and binding.actor_resolution == "verified-owner"
                    and binding.actor_id == context.owner_id)
        digests = (binding.native_locator_digest, binding.source_snapshot_id, context.ingestion_run_id,
                   context.content_grant_digest, context.authority_attestation_digest)
        nodes = tuple(ProvenanceNode(kind, digest) for kind, digest in zip(_RELATIONS, digests))
        payload = _span_payload(context, index, redacted, eligible, nodes, binding)
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
                    if (new_binding.actor_resolution == "verified-owner"
                            and (new_binding.actor_kind != "user"
                                 or new_binding.actor_id != self._context.owner_id)):
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
