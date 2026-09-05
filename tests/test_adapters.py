import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "adapters"
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

import knowledge_distiller.adapters as adapters  # noqa: E402
from knowledge_distiller.adapters import (  # noqa: E402
    AdapterIdentity,
    EventGraphManifest,
    GraphValidationError,
    validate_event_graph,
)


class CanonicalGraphTest(unittest.TestCase):
    def load_fixture(self, name: str):
        with (FIXTURES / name).open(encoding="utf-8") as fixture:
            return json.load(fixture)

    def context_for(self, graph):
        return adapters.ValidationContext(
            expected_owner_id="owner-1",
            expected_source_snapshot_id=graph["source_snapshot_id"],
        )

    def validate(self, graph, context=None):
        return validate_event_graph(
            graph,
            context=context if context is not None else self.context_for(graph),
        )

    def assert_rejected(self, graph, code: str, context=None):
        with self.assertRaises(GraphValidationError) as raised:
            self.validate(graph, context=context)
        self.assertEqual(getattr(raised.exception, "code", None), code)
        return raised.exception

    def test_external_context_anchors_owner_and_snapshot(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        context = adapters.ValidationContext(
            expected_owner_id=graph["owner"]["id"],
            expected_source_snapshot_id=graph["source_snapshot_id"],
        )

        owner_substitution = copy.deepcopy(graph)
        owner_substitution["owner"]["id"] = "substituted-owner"
        owner_substitution["events"][0]["actor"]["id"] = "substituted-owner"
        with self.assertRaises(GraphValidationError) as owner_error:
            validate_event_graph(owner_substitution, context=context)
        self.assertEqual(owner_error.exception.code, "owner-context-mismatch")

        snapshot_substitution = copy.deepcopy(graph)
        substituted_snapshot = "sha256:" + "d" * 64
        snapshot_substitution["source_snapshot_id"] = substituted_snapshot
        snapshot_substitution["events"][0]["source_snapshot_id"] = substituted_snapshot
        with self.assertRaises(GraphValidationError) as snapshot_error:
            validate_event_graph(snapshot_substitution, context=context)
        self.assertEqual(
            snapshot_error.exception.code,
            "source-snapshot-context-mismatch",
        )

        with self.assertRaises(AttributeError):
            context.expected_owner_id = "other"

    def test_strict_json_decoder_rejects_duplicate_keys_and_invalid_scalars(self) -> None:
        duplicate = (
            b'{"schema_version":"knowledge-distiller.event-graph/v1",'
            b'"schema_version":"knowledge-distiller.event-graph/v1"}'
        )
        nested_duplicate = b'{"outer":{"field":1,"field":2}}'
        cases = (
            (duplicate, "duplicate-json-key"),
            (nested_duplicate, "duplicate-json-key"),
            (b"\xff", "invalid-utf8"),
            (b'{"value":1.5}', "invalid-number"),
            (b'{"value":NaN}', "invalid-number"),
            (b"[" * 1100 + b"0" + b"]" * 1100, "json-too-deep"),
        )

        for raw, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(GraphValidationError) as raised:
                    adapters.decode_event_graph_json(raw)
                self.assertEqual(raised.exception.code, code)

    def test_json_preflight_rejects_value_amplification_before_materialization(self) -> None:
        value_limit = getattr(adapters, "MAX_JSON_VALUE_TOKENS", 250_000)
        raw = b"[" + (b"0," * value_limit) + b"0]"
        self.assertLess(len(raw), adapters.MAX_GRAPH_BYTES)

        with self.assertRaises(GraphValidationError) as raised:
            adapters.decode_event_graph_json(raw)

        self.assertEqual(raised.exception.code, "json-resource-limit")

    def test_json_memory_budget_rejects_wide_string_before_materialization(self) -> None:
        raw = b'"' + (b"a" * 120) + "😀".encode("utf-8") + b'"'
        with mock.patch.object(adapters, "MAX_GRAPH_BYTES", len(raw)), mock.patch.object(
            adapters, "MAX_JSON_PARSER_BYTES", 1000
        ):
            self.assertEqual(adapters.decode_event_graph_json('"中文"'.encode()), "中文")
            with mock.patch.object(
                adapters.json, "loads", wraps=adapters.json.loads
            ) as loads, self.assertRaises(GraphValidationError) as raised:
                adapters.decode_event_graph_json(raw)

        self.assertEqual(raised.exception.code, "json-resource-limit")
        loads.assert_not_called()

    def test_json_preflight_ignores_escaped_string_punctuation(self) -> None:
        self.assertTrue(hasattr(adapters, "_preflight_event_graph_json"))
        raw = b"[\"{[,:]}\",\"escaped quote: \\\"\"]"
        self.assertEqual(adapters._preflight_event_graph_json(raw), 5)
        limits = (
            mock.patch.object(adapters, "MAX_JSON_TOKENS", 5),
            mock.patch.object(adapters, "MAX_JSON_STRUCTURAL_TOKENS", 3),
            mock.patch.object(adapters, "MAX_JSON_VALUE_TOKENS", 2),
            mock.patch.object(adapters, "MAX_JSON_STRING_TOKENS", 2),
        )
        with limits[0], limits[1], limits[2], limits[3]:
            self.assertEqual(
                adapters.decode_event_graph_json(raw),
                ["{[,:]}", 'escaped quote: "'],
            )

    def test_fixture_passes_through_strict_decoder_and_validator(self) -> None:
        raw = (FIXTURES / "minimal-valid.json").read_bytes()
        graph = adapters.decode_event_graph_json(raw)

        self.validate(graph)

    def test_canonical_depth_and_cycle_fail_with_stable_codes(self) -> None:
        nested = None
        for _ in range(adapters.MAX_CANONICAL_DEPTH + 1):
            nested = [nested]
        cyclic = []
        cyclic.append(cyclic)

        for value in (nested, cyclic):
            with self.subTest(kind="depth" if value is nested else "cycle"):
                with self.assertRaises(GraphValidationError) as raised:
                    adapters._canonical_bytes(value)
                self.assertEqual(raised.exception.code, "invalid-type")
                self.assertEqual(raised.exception.pointer, "/")

    def append_event(self, graph, event_id: str, event_type: str = "message"):
        event = copy.deepcopy(graph["events"][0])
        event.update(
            {
                "id": event_id,
                "native_event_id": "native-" + event_id,
                "native_offset": "offset-" + event_id,
                "event_type": event_type,
                "stream_position": max(item["stream_position"] for item in graph["events"])
                + 1,
                "parent_event_id": None,
                "correlation_id": None,
                "chunk_index": None,
                "semantic_markers": [],
                "claim_eligible": False,
            }
        )
        event["actor"] = {
            "kind": "assistant",
            "id": "assistant-1",
            "resolution": "native",
        }
        graph["events"].append(event)
        return event

    def local_ref(self, event_id: str):
        return {
            "status": "local",
            "event_id": event_id,
            "source_snapshot_id": None,
            "reason": None,
        }

    def append_edge(self, graph, edge_id: str, edge_type: str, source: str, target: str):
        graph["edges"].append(
            {
                "id": edge_id,
                "edge_type": edge_type,
                "from": self.local_ref(source),
                "to": self.local_ref(target),
            }
        )

    def test_minimal_graph_has_stable_manifest(self) -> None:
        manifest = self.validate(self.load_fixture("minimal-valid.json"))

        self.assertEqual(
            manifest,
            EventGraphManifest(
                schema_version="knowledge-distiller.event-graph/v1",
                adapter=AdapterIdentity(
                    name="synthetic",
                    adapter_version="1.0.0",
                    product_version="synthetic-1",
                    native_schema_version="synthetic-1",
                ),
                source_snapshot_id=(
                    "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                ),
                event_count=1,
                edge_count=0,
                canonical_digest=(
                    "sha256:0e2faa702a5ae1b64c87f2f5fb74634a20a12ced04bc4949e4c8de8a10fae1da"
                ),
            ),
        )
        with self.assertRaises(AttributeError):
            manifest.event_count = 2

    def test_valid_tool_and_nested_agent_fixtures(self) -> None:
        tool = self.validate(self.load_fixture("tool-flow-valid.json"))
        nested_graph = self.load_fixture("nested-agent-valid.json")
        nested_graph["events"].reverse()
        nested = self.validate(nested_graph)

        self.assertEqual((tool.event_count, tool.edge_count), (4, 3))
        self.assertEqual((nested.event_count, nested.edge_count), (4, 3))

    def test_rejects_unknown_or_missing_fields_at_every_schema_level(self) -> None:
        cases = []

        graph = self.load_fixture("minimal-valid.json")
        cases.append((graph, graph))
        cases.append((copy.deepcopy(graph), None))
        cases[-1][0]["adapter"]["extra"] = None
        cases.append((copy.deepcopy(graph), None))
        cases[-1][0]["owner"]["extra"] = None
        cases.append((copy.deepcopy(graph), None))
        cases[-1][0]["events"][0]["extra"] = None
        cases.append((copy.deepcopy(graph), None))
        cases[-1][0]["events"][0]["actor"]["extra"] = None
        cases.append((copy.deepcopy(graph), None))
        cases[-1][0]["events"][0]["project_workspace"]["extra"] = None
        cases.append((copy.deepcopy(graph), None))
        cases[-1][0]["events"][0]["content_segments"][0]["extra"] = None

        artifact_graph = copy.deepcopy(graph)
        artifact_graph["events"][0]["artifact_locators"] = [
            {"id": "artifact-1", "kind": "file", "locator": "fixture.txt", "revision": None, "extra": None}
        ]
        cases.append((artifact_graph, None))

        outcome_graph = copy.deepcopy(graph)
        outcome_graph["events"][0]["observable_outcome"] = {
            "kind": "other", "status": "unknown", "summary": None, "extra": None
        }
        cases.append((outcome_graph, None))

        edge_graph = copy.deepcopy(graph)
        edge_graph["edges"] = [
            {
                "id": "edge-1",
                "edge_type": "precedes",
                "from": {"status": "missing", "event_id": None, "source_snapshot_id": None, "reason": "native-omission", "extra": None},
                "to": self.local_ref("event-1"),
                "extra": None,
            }
        ]
        cases.extend([(copy.deepcopy(edge_graph), None), (edge_graph, None)])
        del cases[-2][0]["edges"][0]["extra"]

        loss_graph = copy.deepcopy(graph)
        loss_graph["fidelity_losses"] = [
            {"code": "non-semantic-formatting", "event_id": None, "native_fact": "formatting", "reason": "unrepresentable", "extra": None}
        ]
        cases.append((loss_graph, None))

        for index, (candidate, _) in enumerate(cases):
            if index == 0:
                candidate["extra"] = None
            with self.subTest(index=index):
                self.assert_rejected(candidate, "unknown-field")

        missing = self.load_fixture("minimal-valid.json")
        del missing["events"][0]["native_offset"]
        self.assert_rejected(missing, "missing-field")

    def test_unknown_fields_do_not_enter_diagnostics_or_get_traversed(self) -> None:
        malicious_key = "/private/synthetic-secret/../../credential"
        candidates = []

        graph = self.load_fixture("minimal-valid.json")
        graph[malicious_key] = 1.5
        candidates.append(graph)

        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["actor"][malicious_key] = 1.5
        candidates.append(graph)

        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                error = self.assert_rejected(candidate, "unknown-field")
                self.assertNotIn(malicious_key, str(error))
                self.assertNotIn(malicious_key, error.pointer or "")

    def test_rejects_invalid_runtime_types_without_bool_integer_coercion(self) -> None:
        for value in (True, 1.0, "0"):
            graph = self.load_fixture("minimal-valid.json")
            graph["events"][0]["stream_position"] = value
            with self.subTest(value=value):
                self.assert_rejected(graph, "invalid-type")

        graph = self.load_fixture("minimal-valid.json")
        graph["events"] = "not-an-array"
        self.assert_rejected(graph, "invalid-type")

    def test_deep_invalid_values_fail_with_a_stable_code(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        nested = None
        for _ in range(1100):
            nested = [nested]
        graph["events"] = nested

        self.assert_rejected(graph, "invalid-type")

    def test_rejects_identifier_text_and_version_bounds(self) -> None:
        for value in ("", "x" * 257):
            graph = self.load_fixture("minimal-valid.json")
            graph["events"][0]["id"] = value
            with self.subTest(kind="identifier", size=len(value)):
                self.assert_rejected(graph, "invalid-identifier")

        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["native_timestamp"] = "x" * 129
        self.assert_rejected(graph, "invalid-text")

        for version in ("latest", "x" * 65):
            graph = self.load_fixture("minimal-valid.json")
            graph["adapter"]["adapter_version"] = version
            with self.subTest(version=version):
                self.assert_rejected(graph, "invalid-version")

    def test_rejects_unsupported_schema_and_adapter_tuples(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        graph["schema_version"] = "knowledge-distiller.event-graph/v2"
        self.assert_rejected(graph, "unsupported-schema-version")

        for adapter in (
            {"name": "synthetic", "adapter_version": "1.0.1", "product_version": "synthetic-1", "native_schema_version": "synthetic-1"},
            {"name": "lark", "adapter_version": "1", "product_version": "1", "native_schema_version": "1"},
        ):
            graph = self.load_fixture("minimal-valid.json")
            graph["adapter"] = adapter
            with self.subTest(adapter=adapter["name"]):
                self.assert_rejected(graph, "unsupported-adapter-version")

    def test_rejects_duplicate_identity_and_stream_keys(self) -> None:
        for field, code in (
            ("id", "duplicate-event-id"),
            ("native_event_id", "duplicate-native-event-id"),
        ):
            graph = self.load_fixture("minimal-valid.json")
            duplicate = copy.deepcopy(graph["events"][0])
            duplicate["stream_position"] = 1
            if field != "id":
                duplicate["id"] = "event-2"
            graph["events"].append(duplicate)
            with self.subTest(field=field):
                self.assert_rejected(graph, code)

        graph = self.load_fixture("minimal-valid.json")
        duplicate = copy.deepcopy(graph["events"][0])
        duplicate.update({"id": "event-2", "native_event_id": None})
        graph["events"].append(duplicate)
        self.assert_rejected(graph, "duplicate-stream-position")

    def test_rejects_oversized_events_and_snapshot_mismatches(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["content_segments"][0]["text"] = "x" * 1048576
        self.assert_rejected(graph, "event-too-large")

        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["source_snapshot_id"] = "sha256:" + "d" * 64
        self.assert_rejected(graph, "snapshot-mismatch")

        graph = self.load_fixture("minimal-valid.json")
        graph["source_snapshot_id"] = "sha256:ABC"
        self.assert_rejected(graph, "invalid-snapshot-id")

    def test_diagnostics_never_echo_source_content(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        secret = "SYNTHETIC-SOURCE-CONTENT-MUST-NOT-LEAK"
        graph["events"][0]["content_segments"][0]["text"] = secret
        graph["events"][0]["event_type"] = secret

        error = self.assert_rejected(graph, "invalid-enum")

        self.assertNotIn(secret, str(error))

    def test_accepts_typed_external_and_missing_references(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        graph["edges"] = [
            {
                "id": "external-edge",
                "edge_type": "precedes",
                "from": {
                    "status": "external",
                    "event_id": "outside-event",
                    "source_snapshot_id": "sha256:" + "d" * 64,
                    "reason": None,
                },
                "to": self.local_ref("event-1"),
            },
            {
                "id": "missing-edge",
                "edge_type": "precedes",
                "from": {"status": "missing", "event_id": None, "source_snapshot_id": None, "reason": "redacted"},
                "to": self.local_ref("event-1"),
            },
        ]

        manifest = self.validate(graph)

        self.assertEqual(manifest.edge_count, 2)

    def test_rejects_malformed_or_unresolved_references(self) -> None:
        invalid_refs = (
            {"status": "local", "event_id": "unknown", "source_snapshot_id": None, "reason": None},
            {"status": "external", "event_id": None, "source_snapshot_id": "sha256:" + "d" * 64, "reason": None},
            {"status": "missing", "event_id": None, "source_snapshot_id": None, "reason": None},
        )
        for index, reference in enumerate(invalid_refs):
            graph = self.load_fixture("minimal-valid.json")
            graph["edges"] = [{"id": "edge-1", "edge_type": "precedes", "from": reference, "to": self.local_ref("event-1")}]
            with self.subTest(index=index):
                self.assert_rejected(
                    graph,
                    "unresolved-reference" if index == 0 else "invalid-reference",
                )

    def test_rejects_graph_cycles(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        self.append_event(graph, "event-2")
        self.append_edge(graph, "edge-1", "precedes", "event-1", "event-2")
        self.append_edge(graph, "edge-2", "precedes", "event-2", "event-1")

        self.assert_rejected(graph, "graph-cycle")

    def test_deep_acyclic_graph_does_not_depend_on_python_recursion(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        template = graph["events"][0]
        graph["events"] = []
        graph["edges"] = []
        previous = None
        for index in range(1050):
            event = copy.deepcopy(template)
            event.update(
                {
                    "id": f"event-{index}",
                    "native_event_id": f"native-{index}",
                    "native_offset": f"offset-{index}",
                    "stream_position": index,
                    "parent_event_id": previous,
                }
            )
            graph["events"].append(event)
            if previous is not None:
                self.append_edge(graph, f"edge-{index}", "precedes", previous, event["id"])
            previous = event["id"]

        manifest = self.validate(graph)

        self.assertEqual((manifest.event_count, manifest.edge_count), (1050, 1049))

    def test_graph_size_gate_precedes_invariant_walks(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        with mock.patch.object(adapters, "MAX_GRAPH_BYTES", 100), mock.patch.object(
            adapters,
            "_validate_acyclic",
            wraps=adapters._validate_acyclic,
        ) as acyclic:
            self.assert_rejected(graph, "graph-too-large")

        acyclic.assert_not_called()

    def test_tool_relations_do_not_rescan_the_complete_edge_array(self) -> None:
        fixture = self.load_fixture("tool-flow-valid.json")
        graph = copy.deepcopy(fixture)
        graph["events"] = []
        graph["edges"] = []
        call_template = fixture["events"][0]
        result_template = fixture["events"][-1]
        for index in range(80):
            correlation_id = f"tool-flow-{index}"
            call = copy.deepcopy(call_template)
            call.update(
                {
                    "id": f"call-{index}",
                    "native_event_id": f"native-call-{index}",
                    "native_offset": f"call-offset-{index}",
                    "stream_position": index * 2,
                    "correlation_id": correlation_id,
                }
            )
            result = copy.deepcopy(result_template)
            result.update(
                {
                    "id": f"result-{index}",
                    "native_event_id": f"native-result-{index}",
                    "native_offset": f"result-offset-{index}",
                    "stream_position": index * 2 + 1,
                    "parent_event_id": call["id"],
                    "correlation_id": correlation_id,
                }
            )
            graph["events"].extend((call, result))
            self.append_edge(
                graph,
                f"result-edge-{index}",
                "tool-result-of",
                call["id"],
                result["id"],
            )

        with mock.patch.object(
            adapters,
            "_build_edge_index",
            wraps=adapters._build_edge_index,
        ) as build_index:
            self.validate(graph)

        build_index.assert_called_once()
        self.assertEqual(len(build_index.call_args.args[1]), len(graph["edges"]))

    def test_enforces_tool_cardinality_correlation_and_chunk_indices(self) -> None:
        mutations = []

        graph = self.load_fixture("tool-flow-valid.json")
        graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "edge-result"]
        mutations.append((graph, "invalid-edge-cardinality"))

        graph = self.load_fixture("tool-flow-valid.json")
        result = copy.deepcopy(graph["events"][-1])
        result.update({"id": "tool-result-2", "native_event_id": "native-result-2", "native_offset": "offset-4", "stream_position": 4})
        graph["events"].append(result)
        self.append_edge(graph, "edge-result-2", "tool-result-of", "tool-call-1", "tool-result-2")
        mutations.append((graph, "invalid-edge-cardinality"))

        graph = self.load_fixture("tool-flow-valid.json")
        graph["events"][1]["correlation_id"] = "wrong-correlation"
        mutations.append((graph, "unresolved-tool-flow"))

        graph = self.load_fixture("tool-flow-valid.json")
        graph["events"][2]["chunk_index"] = 2
        mutations.append((graph, "noncontiguous-chunks"))

        graph = self.load_fixture("tool-flow-valid.json")
        graph["events"][0]["correlation_id"] = None
        mutations.append((graph, "missing-correlation-id"))

        for index, (candidate, code) in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_rejected(candidate, code)

    def test_external_edges_cannot_satisfy_required_tool_relations(self) -> None:
        graph = self.load_fixture("tool-flow-valid.json")
        graph["edges"][0]["from"] = {
            "status": "external",
            "event_id": "outside-call",
            "source_snapshot_id": "sha256:" + "d" * 64,
            "reason": None,
        }

        self.assert_rejected(graph, "invalid-edge-cardinality")

    def test_local_edges_follow_native_order_within_the_same_stream(self) -> None:
        graph = self.load_fixture("tool-flow-valid.json")
        graph["events"][0]["stream_position"] = 1
        graph["events"][1]["stream_position"] = 0

        self.assert_rejected(graph, "invalid-stream-order")

    def test_causal_path_cannot_leave_and_reenter_against_native_order(self) -> None:
        graph = self.load_fixture("nested-agent-valid.json")
        parent_event = next(
            event for event in graph["events"] if event["id"] == "parent-event"
        )
        parent_event["stream_position"] = 2

        self.assert_rejected(graph, "graph-cycle")

    def test_compaction_contains_only_visible_summary_and_marker(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        event = graph["events"][0]
        event["event_type"] = "compacted-summary"
        event["semantic_markers"] = ["compaction"]
        event["content_segments"] = [
            {"type": "compacted-summary", "text": "Visible synthetic summary.", "media_type": None, "artifact_locator_id": None}
        ]
        self.validate(graph)

        for mutation in ("missing-marker", "wrong-segment", "hidden-artifact"):
            candidate = copy.deepcopy(graph)
            if mutation == "missing-marker":
                candidate["events"][0]["semantic_markers"] = []
            elif mutation == "wrong-segment":
                candidate["events"][0]["content_segments"][0]["type"] = "text"
            else:
                candidate["events"][0]["artifact_locators"] = [
                    {"id": "hidden", "kind": "other", "locator": "synthetic-hidden", "revision": None}
                ]
            with self.subTest(mutation=mutation):
                self.assert_rejected(candidate, "invalid-compaction")

    def test_marked_edits_retries_forks_and_supersessions_need_explicit_edges(self) -> None:
        edge_cases = (
            ("edit", "edits", "message"),
            ("retry", "retries", "message"),
            ("supersession", "supersedes", "message"),
            ("fork", "forks", "agent-start"),
        )
        for marker, edge_type, target_type in edge_cases:
            graph = self.load_fixture("minimal-valid.json")
            target = self.append_event(graph, "target", target_type)
            target["semantic_markers"] = [marker]
            if target_type == "agent-start":
                target["actor"] = {"kind": "agent", "id": "agent-1", "resolution": "deterministic"}
            with self.subTest(marker=marker, state="missing"):
                self.assert_rejected(graph, "missing-marker-edge")

            self.append_edge(graph, "relation", edge_type, "event-1", "target")
            self.validate(graph)

    def test_retry_edges_require_matching_event_types(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        target = self.append_event(graph, "target", "agent-start")
        target["actor"] = {"kind": "agent", "id": "agent-1", "resolution": "deterministic"}
        target["semantic_markers"] = ["retry"]
        self.append_edge(graph, "retry", "retries", "event-1", "target")

        self.assert_rejected(graph, "invalid-edge-semantics")

    def test_nested_agents_require_resolved_preceding_spawn_and_parent_observation(self) -> None:
        mutations = []

        graph = self.load_fixture("nested-agent-valid.json")
        graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "edge-spawn"]
        mutations.append((graph, "invalid-child-spawn"))

        graph = self.load_fixture("nested-agent-valid.json")
        graph["events"][1]["actor"]["resolution"] = "unresolved"
        mutations.append((graph, "invalid-child-agent"))

        graph = self.load_fixture("nested-agent-valid.json")
        graph["events"][1]["parent_event_id"] = "parent-joined"
        mutations.append((graph, "invalid-child-spawn"))

        graph = self.load_fixture("nested-agent-valid.json")
        graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "edge-join"]
        mutations.append((graph, "invalid-edge-cardinality"))

        graph = self.load_fixture("nested-agent-valid.json")
        graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "edge-child-run"]
        graph["events"][2]["stream_id"] = "disconnected-child-stream"
        mutations.append((graph, "invalid-child-lifecycle"))

        for index, (candidate, code) in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_rejected(candidate, code)

    def test_child_lifecycle_accepts_an_explicit_multihop_child_stream(self) -> None:
        graph = self.load_fixture("nested-agent-valid.json")
        child_event = self.append_event(graph, "child-event")
        child_event.update(
            {
                "native_event_id": "native-child-event",
                "native_offset": "child-middle",
                "root_stream_id": "child-stream",
                "stream_id": "child-stream",
                "stream_position": 1,
                "parent_event_id": "child-start",
                "actor": {
                    "kind": "agent",
                    "id": "child-agent",
                    "resolution": "deterministic",
                },
                "claim_eligible": False,
            }
        )
        terminal = next(
            event for event in graph["events"] if event["id"] == "child-completion"
        )
        terminal["stream_position"] = 2
        terminal["parent_event_id"] = "child-event"
        graph["edges"] = [
            edge for edge in graph["edges"] if edge["id"] != "edge-child-run"
        ]
        self.append_edge(
            graph,
            "edge-child-first",
            "precedes",
            "child-start",
            "child-event",
        )
        self.append_edge(
            graph,
            "edge-child-second",
            "precedes",
            "child-event",
            "child-completion",
        )

        self.validate(graph)

    def test_child_start_cannot_be_left_without_a_terminal(self) -> None:
        graph = self.load_fixture("nested-agent-valid.json")
        graph["events"] = [
            event
            for event in graph["events"]
            if event["id"] not in {"child-completion", "parent-joined"}
        ]
        graph["edges"] = [
            edge
            for edge in graph["edges"]
            if edge["id"] not in {"edge-child-run", "edge-join"}
        ]

        self.assert_rejected(graph, "invalid-child-lifecycle")

    def test_terminal_cannot_exist_without_a_matching_child_start(self) -> None:
        graph = self.load_fixture("nested-agent-valid.json")
        terminal = next(
            event for event in graph["events"] if event["id"] == "child-completion"
        )
        terminal.update(
            {
                "root_stream_id": "orphan-stream",
                "stream_id": "orphan-stream",
                "parent_event_id": "parent-event",
                "correlation_id": "orphan-run",
            }
        )
        terminal["actor"]["id"] = "orphan-agent"
        graph["events"] = [
            event
            for event in graph["events"]
            if event["id"] not in {"child-start", "parent-joined"}
        ]
        graph["edges"] = []

        self.assert_rejected(graph, "invalid-child-lifecycle")

    def test_child_identity_tuple_requires_resolved_non_null_values(self) -> None:
        unresolved_terminal = self.load_fixture("nested-agent-valid.json")
        unresolved_terminal["events"][2]["actor"]["resolution"] = "unresolved"

        missing_correlation = self.load_fixture("nested-agent-valid.json")
        missing_correlation["events"][1]["correlation_id"] = None
        missing_correlation["events"][2]["correlation_id"] = None

        for graph in (unresolved_terminal, missing_correlation):
            with self.subTest():
                self.assert_rejected(graph, "invalid-child-lifecycle")

    def test_child_semantics_require_both_spawn_edge_and_explicit_parent(self) -> None:
        graph = self.load_fixture("nested-agent-valid.json")
        graph["events"][1]["parent_event_id"] = None
        self.assert_rejected(graph, "invalid-child-spawn")

        graph = self.load_fixture("nested-agent-valid.json")
        graph["events"][1]["parent_event_id"] = None
        graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "edge-spawn"]
        self.assert_rejected(graph, "invalid-child-spawn")

    def test_duplicate_child_root_stream_is_order_independent_ambiguity(self) -> None:
        graph = self.load_fixture("nested-agent-valid.json")
        duplicate = copy.deepcopy(graph["events"][1])
        duplicate.update(
            {
                "id": "child-start-2",
                "native_event_id": "native-child-start-2",
                "native_offset": "child-2",
                "stream_position": 2,
                "correlation_id": "child-run-2",
            }
        )
        duplicate["actor"]["id"] = "child-agent-2"
        graph["events"].append(duplicate)
        self.append_edge(
            graph,
            "edge-spawn-2",
            "spawned-by",
            "parent-event",
            "child-start-2",
        )

        candidates = [copy.deepcopy(graph)]
        permuted = copy.deepcopy(graph)
        first = next(index for index, event in enumerate(permuted["events"]) if event["id"] == "child-start")
        second = next(index for index, event in enumerate(permuted["events"]) if event["id"] == "child-start-2")
        permuted["events"][first], permuted["events"][second] = (
            permuted["events"][second],
            permuted["events"][first],
        )
        candidates.append(permuted)

        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                self.assert_rejected(candidate, "ambiguous-child-root")

    def test_child_terminal_root_drift_cannot_hide_an_orphan(self) -> None:
        graph = self.load_fixture("nested-agent-valid.json")
        terminal = next(
            event for event in graph["events"] if event["id"] == "child-completion"
        )
        terminal["root_stream_id"] = "drifted-child-stream"
        graph["events"] = [
            event for event in graph["events"] if event["id"] != "parent-joined"
        ]
        graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "edge-join"]

        self.assert_rejected(graph, "invalid-child-lifecycle")

    def test_enforces_join_return_and_cancellation_observation_semantics(self) -> None:
        returned = self.load_fixture("nested-agent-valid.json")
        returned["events"][-1]["event_type"] = "returned"
        returned["edges"][-1]["edge_type"] = "returned"
        self.validate(returned)

        cancelled = self.load_fixture("nested-agent-valid.json")
        cancelled["events"][2]["event_type"] = "agent-cancellation"
        cancelled["events"][2]["semantic_markers"] = ["cancellation"]
        cancelled["events"][2]["observable_outcome"]["status"] = "cancelled"
        cancelled["events"][-1]["event_type"] = "cancellation-observed"
        cancelled["edges"][-1]["edge_type"] = "cancellation-observed"
        self.validate(cancelled)

        duplicated = self.load_fixture("nested-agent-valid.json")
        returned_event = self.append_event(duplicated, "parent-returned", "returned")
        returned_event["correlation_id"] = "child-run"
        self.append_edge(duplicated, "edge-returned", "returned", "child-completion", "parent-returned")
        self.assert_rejected(duplicated, "invalid-edge-cardinality")

    def test_enforces_message_lifecycle_cardinality_and_correlation(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["correlation_id"] = "message-flow"
        sent = self.append_event(graph, "sent", "message-sent")
        delivered = self.append_event(graph, "delivered", "message-delivered")
        consumed = self.append_event(graph, "consumed", "message-consumed")
        for event in (sent, delivered, consumed):
            event["correlation_id"] = "message-flow"
        self.append_edge(graph, "edge-sent", "sent", "event-1", "sent")
        self.append_edge(graph, "edge-delivered", "delivered", "sent", "delivered")
        self.append_edge(graph, "edge-consumed", "consumed", "delivered", "consumed")
        self.validate(graph)

        duplicate = copy.deepcopy(graph)
        second = self.append_event(duplicate, "consumed-2", "message-consumed")
        second["correlation_id"] = "message-flow"
        self.append_edge(duplicate, "edge-consumed-2", "consumed", "delivered", "consumed-2")
        self.assert_rejected(duplicate, "invalid-edge-cardinality")

        mismatch = copy.deepcopy(graph)
        mismatch["events"][2]["correlation_id"] = "wrong"
        self.assert_rejected(mismatch, "invalid-edge-semantics")

    def test_claim_eligibility_is_exactly_the_verified_owner_rule(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["claim_eligible"] = False
        self.assert_rejected(graph, "invalid-claim-eligibility")

        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["actor"]["id"] = "someone-else"
        self.assert_rejected(graph, "invalid-owner-resolution")

        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["actor"] = {"kind": "assistant", "id": "owner-1", "resolution": "verified-owner"}
        graph["events"][0]["claim_eligible"] = False
        self.assert_rejected(graph, "invalid-owner-resolution")

    def test_closed_enums_reject_unknown_events_edges_content_markers_and_losses(self) -> None:
        cases = []
        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["event_type"] = "future-event"
        cases.append(graph)
        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["content_segments"][0]["type"] = "future-content"
        cases.append(graph)
        graph = self.load_fixture("minimal-valid.json")
        graph["events"][0]["semantic_markers"] = ["future-marker"]
        cases.append(graph)
        graph = self.load_fixture("minimal-valid.json")
        graph["edges"] = [{"id": "edge-1", "edge_type": "future-edge", "from": self.local_ref("event-1"), "to": self.local_ref("event-1")}]
        cases.append(graph)
        graph = self.load_fixture("minimal-valid.json")
        graph["fidelity_losses"] = [{"code": "future-loss", "event_id": None, "native_fact": "formatting", "reason": "unrepresentable"}]
        cases.append(graph)
        for index, candidate in enumerate(cases):
            with self.subTest(index=index):
                self.assert_rejected(candidate, "invalid-enum" if index < 4 else "invalid-fidelity-loss")

    def test_permits_only_the_documented_fidelity_losses(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        graph["fidelity_losses"] = [
            {"code": "non-semantic-formatting", "event_id": None, "native_fact": "formatting", "reason": "unrepresentable"},
            {"code": "unavailable-token-counts", "event_id": "event-1", "native_fact": "token-count", "reason": "native-unavailable"},
            {"code": "unavailable-wall-clock-timestamp", "event_id": "event-1", "native_fact": "wall-clock-timestamp", "reason": "native-unavailable"},
            {"code": "hidden-unexposed-model-reasoning", "event_id": None, "native_fact": "model-reasoning", "reason": "native-unexposed"},
        ]
        self.validate(graph)

        graph["fidelity_losses"][-1]["reason"] = "unrepresentable"
        self.assert_rejected(graph, "invalid-fidelity-loss")

    def test_validates_artifacts_content_media_outcomes_and_marker_uniqueness(self) -> None:
        graph = self.load_fixture("minimal-valid.json")
        event = graph["events"][0]
        event["artifact_locators"] = [
            {"id": "artifact-1", "kind": "file", "locator": "synthetic.txt", "revision": None}
        ]
        event["content_segments"] = [
            {"type": "artifact-reference", "text": None, "media_type": None, "artifact_locator_id": "artifact-1"},
            {"type": "code", "text": "synthetic()", "media_type": "text/x-python", "artifact_locator_id": None},
        ]
        event["observable_outcome"] = {"kind": "artifact", "status": "succeeded", "summary": "Synthetic artifact."}
        self.validate(graph)

        unresolved = copy.deepcopy(graph)
        unresolved["events"][0]["content_segments"][0]["artifact_locator_id"] = "absent"
        self.assert_rejected(unresolved, "invalid-artifact-reference")

        invalid_media = copy.deepcopy(graph)
        invalid_media["events"][0]["content_segments"][0]["media_type"] = "text/plain"
        self.assert_rejected(invalid_media, "invalid-content-segment")

        duplicate_marker = self.load_fixture("minimal-valid.json")
        duplicate_marker["events"][0]["semantic_markers"] = ["edit", "edit"]
        self.assert_rejected(duplicate_marker, "duplicate-marker")


if __name__ == "__main__":
    unittest.main()
