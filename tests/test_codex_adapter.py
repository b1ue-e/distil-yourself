"""Conformance tests for the version-pinned Codex rollout adapter."""

import copy
import dataclasses
import hashlib
import importlib
import json
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "adapters" / "codex" / "0.153.0" / "rollout-jsonl-v1"
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))
from knowledge_distiller import adapters, redaction, sources


def digest(value):
    raw = value if type(value) is bytes else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


try:
    native = importlib.import_module("knowledge_distiller.native_adapters")
except ModuleNotFoundError:
    native = None


def jsonl(*records):
    return b"".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        for record in records
    )


def meta(version="0.153.0", **changes):
    payload = {
        "session_id": "root-session", "id": "thread-1",
        "timestamp": "2026-09-07T01:00:00Z", "cwd": "/synthetic/project",
        "originator": "codex_cli_rs", "cli_version": version, "source": "cli",
        "thread_source": "cli", "model_provider": "openai",
        "base_instructions": {}, "context_window": {}, "history_mode": "full",
    }
    payload.update(changes)
    return {"timestamp": "2026-09-07T01:00:00Z", "type": "session_meta",
            "payload": payload, "ordinal": 0}


def response(ordinal, payload):
    return {"timestamp": f"2026-09-07T01:00:{ordinal:02d}Z", "type": "response_item",
            "payload": payload, "ordinal": ordinal}


def message(ordinal, role="user", text="safe", item_id=None, kind=None):
    return response(ordinal, {
        "type": "message", "id": item_id or f"message-{ordinal}", "role": role,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        "internal_chat_message_metadata_passthrough": {
            "turn_id": "turn-1", "create_time": 1.25,
            "content_item_kinds": [kind or ("user.text" if role == "user" else "unknown")],
        },
    })


class CodexAdapterTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(native, "native adapter module is missing")
        self.key = b"k" * 32
        self.owner = digest("owner-open-id")
        self.project = digest("project")
        self.raw = (FIXTURES / "redacted-current.jsonl").read_bytes()
        self.snapshot = digest(self.raw)
        self.context = native.CodexNormalizationContext(
            expected_owner_id=self.owner,
            expected_source_snapshot_id=self.snapshot,
            project_id=self.project,
            product_version="0.153.0",
            native_schema_digest=native.CODEX_NATIVE_SCHEMA_DIGEST,
        )
        self.trust = redaction.TrustContext(
            context_id=digest("trust-context"), owner_id=self.owner,
            ingestion_run_id=digest("ingestion-run"), content_grant_digest=digest("grant"),
            authority_attestation_digest=digest("attestation"), participant_names=(),
            participant_ids=(), allowlist=())

    def normalize(self, raw=None, context=None, trust=None, key=None):
        raw = self.raw if raw is None else raw
        context = context or dataclasses.replace(
            self.context, expected_source_snapshot_id=digest(raw))
        trust = trust or self.trust
        prepared = native.prepare_codex_session(raw, context=context, redaction_context=trust)
        result = redaction.Redactor(trust, key or self.key).redact(prepared.redaction_events)
        graph = native.normalize_codex_session(
            prepared, result, context=context, redaction_context=trust,
            redaction_key=key or self.key)
        return prepared, graph

    def reject(self, raw=None, code=None, action=None):
        with self.assertRaises(native.NativeAdapterError) as caught:
            (action or (lambda: self.normalize(raw)))()
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.args, (caught.exception.code,))
        self.assertEqual(vars(caught.exception), {"code": caught.exception.code})
        for diagnostic in (str(caught.exception), repr(caught.exception.args), repr(vars(caught.exception))):
            self.assertLess(len(diagnostic), 160)
            self.assertNotIn("synthetic-secret", diagnostic)

    def test_observed_fixture_normalizes_messages_tool_flow_compaction_and_owner(self):
        prepared, graph = self.normalize()
        self.assertNotIn("bounded synthetic", repr(prepared).lower())
        self.assertNotIn("/synthetic/project", repr(prepared))
        self.assertEqual(graph["adapter"], {
            "name": "codex", "adapter_version": "1.0.0",
            "product_version": "0.153.0", "native_schema_version": "rollout-jsonl-v1",
        })
        self.assertEqual([event["event_type"] for event in graph["events"]],
                         ["message", "tool-call", "tool-result", "message", "compacted-summary"])
        owner_event, tool_call, tool_result, assistant_event, compacted = graph["events"]
        self.assertEqual(owner_event["actor"], {
            "kind": "user", "id": self.owner, "resolution": "verified-owner"})
        self.assertTrue(owner_event["claim_eligible"])
        self.assertFalse(any(event["claim_eligible"] for event in graph["events"][1:]))
        self.assertEqual(tool_call["correlation_id"], tool_result["correlation_id"])
        self.assertEqual(graph["edges"], [{
            "id": graph["edges"][0]["id"], "edge_type": "tool-result-of",
            "from": {"status": "local", "event_id": tool_call["id"],
                     "source_snapshot_id": None, "reason": None},
            "to": {"status": "local", "event_id": tool_result["id"],
                   "source_snapshot_id": None, "reason": None},
        }])
        self.assertEqual(compacted["semantic_markers"], ["compaction"])
        self.assertEqual(compacted["content_segments"][0]["type"], "compacted-summary")
        self.assertEqual(graph["fidelity_losses"], [{
            "code": "hidden-unexposed-model-reasoning", "event_id": None,
            "native_fact": "model-reasoning", "reason": "native-unexposed",
        }])
        manifest = adapters.validate_event_graph(
            graph, context=adapters.ValidationContext(self.owner, self.snapshot))
        self.assertEqual((manifest.event_count, manifest.edge_count), (5, 1))
        expected = json.loads((FIXTURES / "expected-current.json").read_text(encoding="utf-8"))
        actual = {
            "adapter": list(dataclasses.astuple(manifest.adapter)),
            "event_count": manifest.event_count, "edge_count": manifest.edge_count,
            "event_types": [event["event_type"] for event in graph["events"]],
            "edge_types": [edge["edge_type"] for edge in graph["edges"]],
            "segment_types": [event["content_segments"][0]["type"] for event in graph["events"]],
            "claim_eligible": [event["claim_eligible"] for event in graph["events"]],
            "loss_codes": [loss["code"] for loss in graph["fidelity_losses"]],
        }
        self.assertEqual(actual, expected)
        snapshot_record = sources.validate_source_snapshot({
            "schema_version": sources.SNAPSHOT_SCHEMA, "source_kind": "session",
            "source_snapshot_id": self.snapshot, "adapter": graph["adapter"],
            "owner": graph["owner"], "raw_digest": self.snapshot,
            "canonical_digest": manifest.canonical_digest,
            "source_byte_count": len(self.raw), "source_item_count": len(graph["events"]),
            "fidelity_losses": graph["fidelity_losses"], "payload_kind": "event-graph",
            "payload_reference": manifest.canonical_digest,
        }, payload=graph, raw_bytes=self.raw,
            context=adapters.ValidationContext(self.owner, self.snapshot))
        self.assertEqual(snapshot_record.payload, manifest)
        self.assertTrue(all(event["source_snapshot_id"] == self.snapshot for event in graph["events"]))
        self.assertNotIn("00000000-0000", json.dumps(graph))

    def test_decodes_json_escapes_before_redaction_and_ignores_context_injection(self):
        raw = jsonl(
            meta(),
            message(1, text="password=synthetic-secret contact alice@example.org"),
            message(2, text="<environment_context>synthetic-secret</environment_context>",
                    kind="environments.environment_context"),
        )
        _, graph = self.normalize(raw)
        self.assertEqual(len(graph["events"]), 1)
        text = graph["events"][0]["content_segments"][0]["text"]
        self.assertIn("[redacted:credential:hmac-sha256:", text)
        self.assertIn("[redacted:email:hmac-sha256:", text)
        self.assertNotIn("synthetic-secret", json.dumps(graph))

        split = message(1, text="password")
        split["payload"]["content"].append({"type": "input_text", "text": "=synthetic-secret"})
        split["payload"]["internal_chat_message_metadata_passthrough"]["content_item_kinds"].append(
            "user.text")
        self.reject(jsonl(meta(), split), "unsupported-content")

    def test_embedded_tool_json_decodes_scalars_and_preserves_decimal_lexemes(self):
        call = {
            "type": "function_call", "id": "call-record", "name": "inspect",
            "arguments": "\"password\\u003dsynthetic-secret\"", "call_id": "call-1",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"},
        }
        output = {
            "type": "function_call_output", "id": "output-record", "call_id": "call-1",
            "output": "{\"large\":1.234567890123456789,\"tiny\":1e-400}",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"},
        }
        _, graph = self.normalize(jsonl(meta(), response(1, call), response(2, output)))
        call_text = graph["events"][0]["content_segments"][0]["text"]
        output_text = graph["events"][1]["content_segments"][0]["text"]
        self.assertIn("[redacted:credential:hmac-sha256:", call_text)
        self.assertNotIn("synthetic-secret", call_text)
        self.assertIn("[redacted:phone:hmac-sha256:", output_text)
        self.assertIn("1e-400", output_text)

    def test_tool_calls_require_one_ordered_result_and_unique_native_ids(self):
        call = {"type": "function_call", "id": "call-record", "name": "inspect",
                "arguments": "{}", "call_id": "call-1",
                "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}}
        output = {"type": "function_call_output", "id": "output-record", "call_id": "call-1",
                  "output": "safe", "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}}
        self.normalize(jsonl(meta(), response(1, call), response(2, output)))
        cases = (
            (jsonl(meta(), response(1, call)), "unresolved-tool-flow"),
            (jsonl(meta(), response(1, output), response(2, call)), "invalid-stream-order"),
            (jsonl(meta(), response(1, call), response(2, call), response(3, output)), "duplicate-native-event-id"),
            (jsonl(meta(), response(1, dict(call, id="")), response(2, output)), "invalid-identifier"),
            (jsonl(meta(), response(1, call), response(2, {
                **output, "type": "custom_tool_call_output"})), "unresolved-tool-flow"),
            (jsonl(meta(), response(1, {
                "type": "custom_tool_call", "id": "call-record", "name": "inspect",
                "input": "{}", "call_id": "call-1", "status": "completed",
                "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"},
            }), response(2, output)), "unresolved-tool-flow"),
            (jsonl(meta(), response(1, call), response(2, {
                **output, "output": [
                    {"type": "input_text", "text": "password"},
                    {"type": "input_text", "text": "=synthetic-secret"},
                ]})), "unsupported-content"),
            (jsonl(meta(), response(1, {**call, "arguments": "not-json"}),
                   response(2, output)), "invalid-json"),
            (jsonl(meta(), response(1, {**call, "arguments": "{\"a\":1,\"a\":2}"}),
                   response(2, output)), "duplicate-json-key"),
        )
        for raw, code in cases:
            with self.subTest(code=code):
                self.reject(raw, code)

    def test_partial_reordered_unknown_mixed_and_forked_sessions_fail_closed(self):
        records = [meta(), message(1)]
        unknown_root = copy.deepcopy(records)
        unknown_root[1]["type"] = "private-new-record"
        unknown_payload = copy.deepcopy(records)
        unknown_payload[1]["payload"]["private_new_field"] = "synthetic-secret"
        mixed = [meta("0.149.0"), message(1)]
        forked = [meta(forked_from_id="parent-thread"), message(1)]
        parented = [meta(parent_thread_id="parent-thread"), message(1)]
        subagent_source = [meta(source={"sub_agent": {"thread_spawn": {
            "parent_thread_id": "parent-thread", "depth": 1}}}), message(1)]
        subagent_thread = [meta(thread_source="subagent"), message(1)]
        duplicate_meta = [meta(), dict(meta(), ordinal=1)]
        duplicate_key = (b'{"timestamp":"x","type":"session_meta","payload":'
                         b'{"session_id":"root","id":"one","id":"two","timestamp":"x",'
                         b'"cwd":"/x","originator":"codex_cli_rs","cli_version":"0.153.0",'
                         b'"source":"cli","thread_source":"cli","model_provider":"openai",'
                         b'"base_instructions":{},"context_window":{},"history_mode":"full"},"ordinal":0}\n')
        cases = (
            (jsonl(*records)[:-1], "partial-json-line"),
            (jsonl(records[0], dict(records[1], ordinal=2)), "invalid-native-order"),
            (jsonl(*unknown_root), "unsupported-native-record"),
            (jsonl(*unknown_payload), "unknown-field"),
            (jsonl(*mixed), "unsupported-adapter-version"),
            (jsonl(*forked), "unsupported-causality"),
            (jsonl(*parented), "unsupported-causality"),
            (jsonl(*subagent_source), "unsupported-causality"),
            (jsonl(*subagent_thread), "unsupported-causality"),
            (jsonl(*duplicate_meta), "mixed-session"),
            (duplicate_key, "duplicate-json-key"),
        )
        for raw, code in cases:
            with self.subTest(code=code):
                self.reject(raw, code)

    def test_cross_agent_message_is_quarantined_without_recipient_receipt(self):
        agent_message = {"timestamp": "2026-09-07T01:00:01Z", "type": "response_item",
                         "payload": {
                             "type": "agent_message", "id": "agent-message", "author": "/root/sender",
                             "recipient": "/root/receiver",
                             "content": [{"type": "encrypted_content",
                                          "encrypted_content": "synthetic-ciphertext"}],
                             "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}},
                         "ordinal": 1}
        metadata = {"timestamp": "2026-09-07T01:00:02Z",
                    "type": "inter_agent_communication_metadata",
                    "payload": {"trigger_turn": True}, "ordinal": 2}
        started = {"timestamp": "2026-09-07T01:00:03Z", "type": "event_msg",
                   "payload": {"type": "task_started", "turn_id": "turn-1",
                               "model_context_window": 272000,
                               "collaboration_mode_kind": "default",
                               "started_at": 1788742803}, "ordinal": 3}
        completed = {"timestamp": "2026-09-07T01:00:04Z", "type": "event_msg",
                     "payload": {"type": "task_complete", "turn_id": "turn-1",
                                 "last_agent_message": None, "started_at": 1788742803,
                                 "completed_at": 1788742804, "duration_ms": 1000,
                                 "time_to_first_token_ms": 100}, "ordinal": 4}
        self.reject(
            jsonl(meta(), agent_message, metadata, started, completed),
            "unsupported-causality",
        )
        self.reject(
            jsonl(meta(), dict(metadata, ordinal=1), dict(agent_message, ordinal=2)),
            "unsupported-causality",
        )
        self.reject(
            jsonl(meta(), agent_message, {
                **metadata, "payload": {"trigger_turn": False}}),
            "unsupported-causality",
        )
        self.reject(jsonl(meta(), agent_message, metadata), "unsupported-causality")
        self.reject(jsonl(meta(), agent_message, metadata, started), "unsupported-causality")
        self.reject(
            jsonl(meta(), dict(started, ordinal=1), dict(agent_message, ordinal=2),
                  dict(metadata, ordinal=3)),
            "unsupported-causality",
        )

    def test_task_lifecycle_requires_pinned_scalar_types_and_order(self):
        started = {"timestamp": "2026-09-07T01:00:01Z", "type": "event_msg",
                   "payload": {"type": "task_started", "turn_id": "turn-1",
                               "model_context_window": 272000,
                               "collaboration_mode_kind": "default",
                               "started_at": 1788742801}, "ordinal": 1}
        completed = {"timestamp": "2026-09-07T01:00:03Z", "type": "event_msg",
                     "payload": {"type": "task_complete", "turn_id": "turn-1",
                                 "last_agent_message": None, "started_at": 1788742801,
                                 "completed_at": 1788742803, "duration_ms": 2000,
                                 "time_to_first_token_ms": 100}, "ordinal": 3}
        self.normalize(jsonl(meta(), started, message(2), completed))
        cases = []
        for field, value in (
                ("model_context_window", {}), ("collaboration_mode_kind", {}),
                ("started_at", -1)):
            record = copy.deepcopy(started)
            record["payload"][field] = value
            cases.append((jsonl(meta(), record, message(2), completed), "invalid-type"))
        for field, value in (
                ("started_at", {}), ("completed_at", -1), ("duration_ms", -1),
                ("time_to_first_token_ms", -1)):
            record = copy.deepcopy(completed)
            record["payload"][field] = value
            cases.append((jsonl(meta(), started, message(2), record), "invalid-type"))
        reversed_time = copy.deepcopy(completed)
        reversed_time["payload"]["completed_at"] = 1788742800
        cases.append((jsonl(meta(), started, message(2), reversed_time),
                      "unsupported-causality"))
        mismatched_start = copy.deepcopy(completed)
        mismatched_start["payload"]["started_at"] += 1
        cases.append((jsonl(meta(), started, message(2), mismatched_start),
                      "unsupported-causality"))
        early_complete = dict(completed, ordinal=1)
        late_start = dict(started, ordinal=3)
        cases.append((jsonl(meta(), early_complete, message(2), late_start),
                      "unsupported-causality"))
        for raw, code in cases:
            with self.subTest(code=code):
                self.reject(raw, code)

    def test_rollback_unpaired_communication_and_non_text_output_are_quarantined(self):
        variants = (
            {"type": "inter_agent_communication_metadata", "payload": {"trigger_turn": True}},
            {"type": "event_msg", "payload": {"type": "thread_rolled_back", "num_turns": 1}},
            {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": "thread-1",
             "turn_id": "turn-1", "started_at_ms": 1, "completed_at_ms": 2,
             "item": {"type": "SubAgentActivity", "id": "child-1", "agent_path": "/root/child",
                      "agent_thread_id": "child-thread", "kind": "started"}}},
            {"type": "event_msg", "payload": {"type": "item_completed",
             "thread_id": "thread-1", "turn_id": "turn-1", "started_at_ms": 1,
             "completed_at_ms": 2,
             "item": {"type": "UserMessage", "content": "owner decision"}}},
            {"type": "response_item", "payload": {
                "type": "function_call_output", "id": "output-record", "call_id": "call-1",
                "output": [{"type": "input_image", "image_url": "data:image/png;base64,PRIVATE", "detail": "high"}],
                "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}}},
        )
        for index, variant in enumerate(variants):
            record = dict(variant, timestamp="2026-09-07T01:00:01Z", ordinal=1)
            code = "unsupported-content" if index == 4 else "unsupported-causality"
            with self.subTest(index=index):
                self.reject(jsonl(meta(), record), code)

        completion = {"timestamp": "2026-09-07T01:00:02Z", "type": "event_msg",
                      "payload": {"type": "task_complete", "turn_id": "turn-1",
                                  "last_agent_message": "missing assistant",
                                  "started_at": 1, "completed_at": 2, "duration_ms": 1,
                                  "time_to_first_token_ms": 1}, "ordinal": 2}
        self.reject(jsonl(meta(), message(1), completion), "unsupported-causality")

    def test_exposed_reasoning_summary_is_not_silently_discarded(self):
        reasoning = {
            "type": "reasoning", "id": "reasoning-1",
            "summary": [{"type": "summary_text", "text": "visible rationale"}],
            "encrypted_content": "synthetic-ciphertext",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"},
        }
        self.reject(
            jsonl(meta(), message(1), response(2, reasoning)),
            "unsupported-content",
        )

        reasoning["summary"] = []
        reasoning["encrypted_content"] = ""
        _, graph = self.normalize(jsonl(meta(), message(1), response(2, reasoning)))
        self.assertEqual(graph["fidelity_losses"], [])

    def test_known_metadata_fields_keep_the_observed_scalar_types(self):
        invalid = message(1)
        invalid["payload"]["internal_chat_message_metadata_passthrough"]["create_time"] = {}
        self.reject(jsonl(meta(), invalid), "invalid-type")
        developer = message(1, role="developer")
        self.reject(jsonl(meta(), developer), "unsupported-content")
        injected = message(1, role="developer", kind="host_skills.instructions")
        injected["payload"]["content"][0]["type"] = "input_text"
        _, graph = self.normalize(jsonl(meta(), injected, message(2)))
        self.assertEqual(len(graph["events"]), 1)
        self.assertEqual(graph["events"][0]["actor"]["kind"], "user")

    def test_external_context_redaction_integrity_and_code_only_diagnostics(self):
        prepared = native.prepare_codex_session(
            self.raw, context=self.context, redaction_context=self.trust)
        result = redaction.Redactor(self.trust, self.key).redact(prepared.redaction_events)
        mutated = dataclasses.replace(prepared)
        object.__setattr__(mutated, "project_id", digest("other"))
        self.reject(action=lambda: native.normalize_codex_session(
            mutated, result, context=self.context, redaction_context=self.trust,
            redaction_key=self.key), code="invalid-context")
        for field in ("root_session_id", "session_id", "thread_id"):
            mutated = dataclasses.replace(prepared)
            object.__setattr__(mutated, field, digest("other-" + field))
            with self.subTest(field=field):
                self.reject(action=lambda mutated=mutated: native.normalize_codex_session(
                    mutated, result, context=self.context, redaction_context=self.trust,
                    redaction_key=self.key), code="invalid-context")
        mutated = dataclasses.replace(prepared)
        object.__setattr__(mutated, "reasoning_omitted", not prepared.reasoning_omitted)
        self.reject(action=lambda: native.normalize_codex_session(
            mutated, result, context=self.context, redaction_context=self.trust,
            redaction_key=self.key), code="invalid-context")
        mutated = dataclasses.replace(prepared)
        object.__setattr__(mutated, "events", list(prepared.events))
        self.reject(action=lambda: native.normalize_codex_session(
            mutated, result, context=self.context, redaction_context=self.trust,
            redaction_key=self.key), code="native-adapter-invalid")
        self.reject(action=lambda: native.normalize_codex_session(
            prepared, result, context=dataclasses.replace(
                self.context, expected_owner_id=digest("other")),
            redaction_context=self.trust, redaction_key=self.key), code="invalid-context")
        tampered = list(result)
        object.__setattr__(tampered[0], "text", "synthetic-secret")
        self.reject(action=lambda: native.normalize_codex_session(
            prepared, tuple(tampered), context=self.context,
            redaction_context=self.trust, redaction_key=self.key))

    def test_project_accepts_broker_identifier_contract(self):
        context = dataclasses.replace(self.context, project_id="project-1")
        _, graph = self.normalize(context=context)
        self.assertEqual(graph["owner"]["id"], self.owner)
        self.assertEqual(graph["events"][0]["project_workspace"]["id"], "project-1")

    def test_preparation_and_normalization_are_pure_and_bounded(self):
        with mock.patch("builtins.open", side_effect=AssertionError("I/O")), \
                mock.patch("os.listdir", side_effect=AssertionError("enumeration")), \
                mock.patch("os.scandir", side_effect=AssertionError("enumeration")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("network")):
            self.normalize()
        with mock.patch.object(redaction, "MAX_INPUT_BYTES", 8):
            self.reject(jsonl(meta(), message(1, text="x" * 20)), "input-limit")
        with mock.patch.object(redaction, "MAX_SPANS", 1):
            self.reject(jsonl(meta(), message(1), message(2)), "input-limit")
        with mock.patch.object(adapters, "MAX_JSON_PARSER_BYTES", 1):
            self.reject(self.raw, "json-resource-limit")
        invalid_unicode = b"".join(
            json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
            for record in (meta(), message(1, text="\ud800")))
        self.reject(invalid_unicode, "invalid-unicode-scalar")

    def test_observation_metadata_is_aggregate_only_and_fixture_is_synthetic(self):
        metadata = json.loads((FIXTURES / "fixture-metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["observed_cli_version"], "0.153.0")
        self.assertEqual(metadata["native_schema_version"], "rollout-jsonl-v1")
        self.assertEqual(metadata["native_schema_digest"], native.CODEX_NATIVE_SCHEMA_DIGEST)
        self.assertEqual(metadata["authorized_roots"], ["sessions", "archived_sessions"])
        self.assertEqual(metadata["observed_file_count"], 83)
        self.assertEqual(metadata["observed_record_count"], 47658)
        self.assertEqual(metadata["complete_current_files_supported_after_hardening"], 0)
        self.assertEqual(metadata["supported_closed_prefix_file_count"], 1)
        self.assertEqual(metadata["supported_closed_prefix_max_record_count"], 9)
        self.assertEqual(metadata["supported_closed_prefix_max_event_count"], 1)
        self.assertTrue(metadata["compatibility_probe_is_point_in_time"])
        self.assertTrue(metadata["all_0_153_0_ordinals_contiguous_from_zero"])
        self.assertFalse(metadata["content_retained_from_observation"])
        self.assertFalse(metadata["paths_or_session_ids_retained_from_observation"])
        self.assertTrue(metadata["fixture_is_fully_synthetic"])
        self.assertNotIn("bytedance", self.raw.decode("utf-8").lower())


if __name__ == "__main__":
    unittest.main()
