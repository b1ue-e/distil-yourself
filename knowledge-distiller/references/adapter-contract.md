# Canonical adapter contract

This is the normative normalization target for future read-only adapters. It
defines data and invariants only; it does not make a native adapter supported.
The compatibility gate remains authoritative for native-version readiness.

## Representation rules

The graph is one JSON object. JSON booleans are not integers. Numbers are bounded
integers; floats, NaN, Infinity, duplicate keys, byte strings, and lone Unicode
surrogates are invalid. Unknown fields are rejected for the root and every nested
object. Every table row is a required key; `Nullable: yes` means the key remains
present and may contain JSON `null`.

Identifier strings are 1..256 UTF-8 bytes. Version strings are 1..64 UTF-8 bytes
and cannot be `latest`. Free-text bounds are stated below and count UTF-8 bytes.
Each serialized event is at most 1048576 bytes, including its nested values. The
complete canonical graph is at most 67108864 bytes.

Raw JSON must pass through `decode_event_graph_json` before
`validate_event_graph`. The strict decoder rejects duplicate keys at every
nesting level before a dictionary can erase that evidence, as well as invalid
UTF-8, floats, non-finite numbers, oversized integers, malformed JSON, and
excessive nesting. An already-materialized dictionary has no duplicate-key
provenance and the validator does not claim otherwise.

Before UTF-8 decoding or materialization, an iterative raw-byte lexical scan
enforces at most 750000 total lexical tokens, 500000 structural tokens, 250000
value tokens, 200000 string tokens, and 64 nesting levels, and returns all four
token counts. Property names count as string and value tokens. The scanner
follows JSON string escaping so punctuation inside a string does not consume
structural budget. After decoding and before `json.loads`, the parser requires
`getsizeof(raw) + 2 * getsizeof(decoded_text) + MAX_GRAPH_BYTES + 384 *
total_tokens <= 512 MiB`. The two decoded-text terms reserve the live decoded
buffer and a conservative aggregate copy of materialized scalar payloads;
`MAX_GRAPH_BYTES` reserves the canonical output buffer; and 384 bytes per token
reserve container, scalar-object, and slot overhead. A resource-budget
violation rejects the input before `json.loads` materializes any value. This
dynamic check permits non-ASCII text when the total remains below the ceiling.

### Top-level object

| Top-level field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `schema_version` | yes | string | no | exactly `knowledge-distiller.event-graph/v1` |
| `adapter` | yes | object | no | Adapter object below |
| `source_snapshot_id` | yes | string | no | snapshot digest defined below |
| `owner` | yes | object | no | Owner object below |
| `events` | yes | array[object] | no | event envelopes, no duplicate `id` |
| `edges` | yes | array[object] | no | Edge objects below, no duplicate `id` |
| `fidelity_losses` | yes | array[object] | no | Fidelity-loss objects below |

### Adapter object

| Adapter field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `name` | yes | string | no | enum: `synthetic`, `lark`, `codex`, `claude-code`, `trae` |
| `adapter_version` | yes | string | no | version string |
| `product_version` | yes | string | no | version string |
| `native_schema_version` | yes | string | no | version string |

The accepted tuple registry is closed:

| Adapter name | adapter_version | product_version | native_schema_version |
| --- | --- | --- | --- |
| `synthetic` | `1.0.0` | `synthetic-1` | `synthetic-1` |
| `lark` | none | none | none |
| `codex` | none | none | none |
| `claude-code` | none | none | none |
| `trae` | none | none | none |

Only that synthetic tuple is accepted in this milestone; every native tuple
allowlist is empty. Any other adapter/version tuple is rejected.

### Owner object

| Owner field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `kind` | yes | string | no | exactly `user` |
| `id` | yes | string | no | identifier string |
| `verification` | yes | string | no | exactly `verified-principal` |

The owner is established outside source content. Text, path names, and account
availability never establish or change it. Validation requires an immutable
`ValidationContext` containing the externally established expected owner ID and
source snapshot ID. Matching owner, actor, and snapshot fields inside the graph
cannot replace these trust anchors.

## Event schema

| Event field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `id` | yes | string | no | identifier; unique across snapshot |
| `native_event_id` | yes | string | yes | identifier when non-null |
| `native_offset` | yes | string | no | identifier-sized stable byte/record offset |
| `source_snapshot_id` | yes | string | no | equals root `source_snapshot_id` |
| `root_session_id` | yes | string | yes | identifier when non-null |
| `session_id` | yes | string | yes | identifier when non-null |
| `thread_id` | yes | string | yes | identifier when non-null |
| `root_stream_id` | yes | string | no | identifier |
| `stream_id` | yes | string | yes | identifier when non-null |
| `branch_id` | yes | string | yes | identifier when non-null |
| `parent_event_id` | yes | string | yes | when non-null, resolves in this snapshot |
| `span_id` | yes | string | yes | identifier when non-null |
| `event_type` | yes | string | no | closed enum below |
| `actor` | yes | object | no | Actor object below |
| `project_workspace` | yes | object | yes | Workspace object below |
| `native_timestamp` | yes | string | yes | 1..128 UTF-8 bytes when non-null |
| `stream_position` | yes | integer | no | 0..9223372036854775807 |
| `correlation_id` | yes | string | yes | identifier when non-null |
| `chunk_index` | yes | integer | yes | 0..2147483647 when non-null; conditional rule below |
| `content_segments` | yes | array[object] | no | Content-segment objects below |
| `artifact_locators` | yes | array[object] | no | Artifact-locator objects below, unique `id` |
| `observable_outcome` | yes | object | yes | Observable-outcome object below |
| `semantic_markers` | yes | array[string] | no | closed enum below; no duplicates |
| `claim_eligible` | yes | boolean | no | owner-attribution rule below |

A non-null `native_event_id` is unique across the entire snapshot. The native
stream key is `stream_id` when non-null and otherwise `root_stream_id`;
`stream_position` is unique and strictly increasing within that key.
`stream_position` defines order within each native stream. The `events` array
order has no chronological meaning. `parent_event_id` is the native immediate or
logical event reference, not a lifecycle matching key. On a child terminal,
`parent_event_id` need not equal the child start ID.

`event_type` is exactly one of `message`, `tool-call`, `tool-output-chunk`,
`tool-result`, `compacted-summary`, `agent-start`, `agent-completion`,
`agent-cancellation`, `agent-failure`, `message-sent`, `message-delivered`,
`message-consumed`, `joined`, `returned`, and `cancellation-observed`.
`correlation_id` is non-null for tool and message lifecycle events. `chunk_index`
is in 0..2147483647 only for `tool-output-chunk`, where indices for a call are
contiguous from zero; it is null for every other event type.

### Actor object

| Actor field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `kind` | yes | string | no | enum: `user`, `assistant`, `agent`, `tool`, `system`, `external` |
| `id` | yes | string | yes | identifier when non-null |
| `resolution` | yes | string | no | enum: `verified-owner`, `native`, `deterministic`, `unresolved` |

`claim_eligible` is true if and only if `actor.kind` is `user`,
`actor.resolution` is `verified-owner`, and `actor.id == owner.id`. Therefore a
true value also requires a non-null actor ID string. `verified-owner` is invalid
for any other actor or ID.

### Workspace object

| Workspace field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `kind` | yes | string | no | enum: `project`, `workspace` |
| `id` | yes | string | no | identifier string |

### Content-segment object

| Segment field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `type` | yes | string | no | enum: `text`, `code`, `artifact-reference`, `tool-call-summary`, `tool-result-summary`, `compacted-summary` |
| `text` | yes | string | yes | 0..1048576 UTF-8 bytes when non-null |
| `media_type` | yes | string | yes | 1..128 ASCII bytes when non-null |
| `artifact_locator_id` | yes | string | yes | identifier when non-null |

For `artifact-reference`, `artifact_locator_id` is non-null, resolves exactly one
locator in the event, and `text` is null. For every other type,
`artifact_locator_id` is null and `text` is non-null. `media_type` is non-null
only for `code`. A `compacted-summary` contains visible summary text only and does
not reconstruct hidden events.

### Artifact-locator object

| Artifact field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `id` | yes | string | no | identifier; unique within event |
| `kind` | yes | string | no | enum: `document`, `session`, `file`, `url`, `other` |
| `locator` | yes | string | no | 1..4096 UTF-8 bytes; inert, never authority |
| `revision` | yes | string | yes | identifier when non-null |

### Observable-outcome object

| Outcome field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `kind` | yes | string | no | enum: `tool`, `agent`, `message`, `artifact`, `other` |
| `status` | yes | string | no | enum: `succeeded`, `failed`, `cancelled`, `unknown` |
| `summary` | yes | string | yes | 0..4096 UTF-8 bytes when non-null |

`semantic_markers` contains only `edit`, `retry`, `supersession`, `fork`,
`spawn`, `completion`, `cancellation`, `failure`, and `compaction`. A marker is
descriptive; every required causal relation also has an edge.

## Edge and reference schema

### Edge object

| Edge field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `id` | yes | string | no | identifier; unique across snapshot |
| `edge_type` | yes | string | no | closed enum below |
| `from` | yes | object | no | Endpoint-reference object |
| `to` | yes | object | no | Endpoint-reference object |

Every local edge is directed from the causal or ordering predecessor to its
dependent. Edge names do not reverse this direction: in particular,
`tool-output-of` and `spawned-by` still point predecessor -> dependent. `*` below
means any allowed `event_type`.

Every local edge whose endpoints have the same native stream key requires the
source `stream_position` to be strictly less than the target
`stream_position`. Validation also adds an implicit ordering edge between each
consecutive pair of events in a native stream. These edges participate in cycle
and reachability validation only; they do not change the manifest edge count or
canonical digest.

| Edge type | Direction | Allowed from event_type | Allowed to event_type | Cardinality |
| --- | --- | --- | --- | --- |
| `precedes` | predecessor -> dependent | `*` | `*` | 0..N incoming and outgoing |
| `tool-output-of` | predecessor -> dependent | `tool-call` | `tool-output-chunk` | each chunk exactly 1 incoming; call 0..N outgoing |
| `tool-result-of` | predecessor -> dependent | `tool-call` | `tool-result` | each call exactly 1 outgoing; result exactly 1 incoming |
| `edits` | predecessor -> dependent | `message` | `message` | each edit-marked target exactly 1 incoming |
| `retries` | predecessor -> dependent | `*` | `*` | same event type; each retry-marked target exactly 1 incoming |
| `supersedes` | predecessor -> dependent | `*` | `*` | each supersession-marked target exactly 1 incoming |
| `forks` | predecessor -> dependent | `*` | `agent-start` | each fork-marked start exactly 1 incoming |
| `spawned-by` | predecessor -> dependent | `*` | `agent-start` | each non-root child start exactly 1 incoming |
| `sent` | predecessor -> dependent | `message` | `message-sent` | each sent event exactly 1 incoming |
| `delivered` | predecessor -> dependent | `message-sent` | `message-delivered` | each delivered event exactly 1 incoming; sent event 1..N outgoing when delivery is observable |
| `consumed` | predecessor -> dependent | `message-delivered` | `message-consumed` | each consumed event exactly 1 incoming; delivered event 0..1 outgoing |
| `joined` | predecessor -> dependent | `agent-completion`, `agent-cancellation`, `agent-failure` | `joined` | target exactly 1 incoming; terminal has at most 1 lifecycle-observation edge |
| `returned` | predecessor -> dependent | `agent-completion` | `returned` | target exactly 1 incoming; completion has at most 1 lifecycle-observation edge |
| `cancellation-observed` | predecessor -> dependent | `agent-cancellation` | `cancellation-observed` | target exactly 1 incoming; cancellation has at most 1 lifecycle-observation edge |

### Endpoint-reference object

| Reference field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `status` | yes | string | no | enum: `local`, `external`, `missing` |
| `event_id` | yes | string | yes | identifier when non-null |
| `source_snapshot_id` | yes | string | yes | snapshot digest when non-null |
| `reason` | yes | string | yes | enum when non-null: `native-omission`, `outside-snapshot`, `redacted`, `unsupported` |

For `local`, `event_id` resolves exactly once in this snapshot and the other two
fields are null. For `external`, `event_id` and `source_snapshot_id` are non-null
and `reason` is null. For `missing`, only `reason` is non-null. External and
missing endpoints do not create reachability and cannot satisfy a required
decision-relevant relation.

### Fidelity-loss object

| Fidelity-loss field | Required | Type | Nullable | Constraints |
| --- | --- | --- | --- | --- |
| `code` | yes | string | no | allowlist below |
| `event_id` | yes | string | yes | resolves in snapshot when non-null |
| `native_fact` | yes | string | no | enum: `formatting`, `token-count`, `wall-clock-timestamp`, `model-reasoning` |
| `reason` | yes | string | no | enum: `unrepresentable`, `native-unavailable`, `native-unexposed` |

The complete `code` allowlist and required native fact are:

- `non-semantic-formatting` with `formatting`;
- `unavailable-token-counts` with `token-count`;
- `unavailable-wall-clock-timestamp` with `wall-clock-timestamp`, only while
  monotonic ordering remains intact;
- `hidden-unexposed-model-reasoning` with `model-reasoning`, only when the native
  source never exposed it.

No other loss is permitted. Ambiguous ownership on a claim-bearing event, omitted
user content, missing decision-relevant spawn/message/join/branch/supersession
edges, unmatched or multiply terminated decision-relevant tool calls, ID
collisions, and graph cycles quarantine the entire incident or root case.

## Canonical serialization and snapshot binding

After validation, canonical bytes are exactly UTF-8 encoding of
`json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))`.
Floats are already prohibited, so runtime float formatting is irrelevant. The
canonical graph digest is `sha256:` plus lowercase SHA-256 hex of those bytes.

A source snapshot ID is `sha256:` followed by the 64 lowercase hexadecimal digits
of `SHA-256(native_snapshot_bytes)`. Every root/event/reference snapshot ID uses
that exact form. Parsing a moving source, combining revisions, or a digest
mismatch rejects the graph.

## Normative graph invariants

- The local graph is acyclic. “Earlier” means local-edge reachability, never
  timestamp comparison across streams. Every identifier reference resolves or is
  represented by a typed endpoint reference.
- Every tool output chunk and terminal result has exactly one correlated tool call
  that is a reachable predecessor. Chunks are contiguous and each call has exactly
  one terminal result.
- Edits, retries, forks, and supersession use explicit edges.
- Every child-agent root has exactly one `spawned-by` edge from a reachable
  preceding parent event and a resolved child actor.
- Cross-agent communication uses ordered sent, delivered, and, only when
  observable, consumed events and matching edges; it is not collapsed text.
- Every child start has exactly one matching terminal: completion, cancellation,
  or failure. The exact lifecycle identity is
  `(root_stream_id, actor.id, correlation_id)`; the actor ID and correlation ID
  are non-null, and both actors are resolved. Every terminal matches exactly one
  child start, is reachable from it in the augmented causal graph, and precedes
  the matching parent joined, returned, or cancellation-observed event. V1 has
  no incomplete-snapshot state; orphans and omitted decision-relevant relations
  quarantine the root case.

## Failure contract

Reject the entire graph before persistence or state advancement on any unsupported
version, schema/type/enum/bound error, unknown field, snapshot mismatch, identity
ambiguity, unresolved required relation, prohibited loss, or invariant failure.
There is no coercion, fallback principal, best-effort record skip, partial graph,
or inferred causality/order.

Diagnostics contain only a stable error code and bounded JSON pointer or canonical
ID. They never echo content, native records, credentials, locators, absolute paths,
or nearby values. Native documents, sessions, indexes, and cloud resources are
never modified.

Conformance requires 100% agreement with expected graphs for version-pinned
synthetic fixtures and any separately authorized redacted fixtures. Passing this
contract does not authorize a source read or make a native adapter supported.
