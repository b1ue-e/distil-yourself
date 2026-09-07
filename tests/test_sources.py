import copy
import hashlib
import importlib
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock

from tests.test_authorization import assert_private_error, digest, forbid_side_effects

ROOT = Path(__file__).resolve().parents[1]
RAW_BYTES = b"synthetic raw"
RAW_DIGEST = "sha256:" + hashlib.sha256(RAW_BYTES).hexdigest()
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))
from knowledge_distiller import adapters
try:
    sources = importlib.import_module("knowledge_distiller.sources")
except ModuleNotFoundError:
    sources = None


def document_data():
    return {
        "schema_version": "knowledge-distiller.document/v1",
        "adapter": {"name": "synthetic", "adapter_version": "1.0.0",
                    "product_version": "synthetic-1", "native_schema_version": "synthetic-1"},
        "source_snapshot_id": RAW_DIGEST,
        "owner": {"kind": "user", "id": "owner-1", "verification": "verified-principal"},
        "native_document_id": "doc-1", "revision": "rev-1",
        "blocks": [{"id": "block-1", "native_block_id": "native-1",
                    "parent_block_id": None, "order": 0,
                    "author": {"kind": "user", "id": "owner-1", "resolution": "verified-owner"},
                    "content_segments": [{"type": "text", "text": "Synthetic owner statement.",
                                          "media_type": None, "artifact_locator_id": None}],
                    "artifact_locators": [], "claim_eligible": True}],
        "fidelity_losses": [],
    }


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sources, "sources module must be implemented")
        self.context = sources.DocumentValidationContext(
            expected_owner_id="owner-1", expected_source_snapshot_id=RAW_DIGEST,
            expected_revision="rev-1", verified_authors=(("native-1", "owner-1"),),
        )

    def validate(self, value, context=None):
        return sources.validate_canonical_document(value, context=context or self.context)

    def reject(self, value, code, context=None):
        with self.assertRaises(sources.SourceValidationError) as caught:
            self.validate(value, context)
        self.assertEqual(str(caught.exception), code)

    def test_document_preserves_order_locators_segments_and_immutable_records(self):
        value = document_data()
        child = copy.deepcopy(value["blocks"][0])
        child.update(id="child", native_block_id="native-child", parent_block_id="block-1")
        child["author"] = {"kind": "external", "id": None, "resolution": "unresolved"}
        child["claim_eligible"] = False
        value["blocks"].append(child)
        doc = self.validate(value)
        self.assertIsInstance(doc, sources.CanonicalDocument)
        self.assertEqual(doc.revision, "rev-1")
        self.assertEqual(doc.blocks[1].parent_block_id, "block-1")
        self.assertEqual(doc.blocks[0].native_block_id, "native-1")
        self.assertEqual(doc.blocks[0].content_segments[0].type, "text")
        self.assertEqual(doc.canonical_digest, digest(value))
        self.assertEqual(doc, self.validate(dict(reversed(list(value.items())))))
        with self.assertRaises(FrozenInstanceError):
            doc.source_snapshot_id = "other"
        with self.assertRaises(FrozenInstanceError):
            doc.blocks[0].author.id = "other"
        value["blocks"][0]["content_segments"][0]["text"] = "changed"
        self.assertEqual(doc.blocks[0].content_segments[0].text, "Synthetic owner statement.")

    def test_unknown_and_missing_document_fields_at_every_level(self):
        paths = ((), ("adapter",), ("owner",), ("blocks", 0),
                 ("blocks", 0, "author"), ("blocks", 0, "content_segments", 0))
        for path in paths:
            value = document_data()
            target = value
            for part in path:
                target = target[part]
            target["private-native-secret"] = "secret"
            self.reject(value, "unknown-field")
        for field in document_data():
            value = document_data()
            del value[field]
            self.reject(value, "missing-field")

    def test_owner_snapshot_revision_are_external_trust_anchors(self):
        value = document_data()
        value["owner"]["id"] = value["blocks"][0]["author"]["id"] = "other"
        self.reject(value, "owner-context-mismatch")
        for field, changed, code in (("source_snapshot_id", "sha256:" + "b" * 64, "source-snapshot-context-mismatch"),
                                     ("source_snapshot_id", "mutable", "invalid-snapshot-id"),
                                     ("revision", "rev-2", "revision-context-mismatch"),
                                     ("revision", "latest", "invalid-revision")):
            self.reject(dict(document_data(), **{field: changed}), code)

    def test_claim_eligibility_requires_external_deterministic_owner_resolution(self):
        self.reject(document_data(), "unverified-document-author", replace(self.context, verified_authors=()))
        for fields in ({"resolution": "unresolved"}, {"id": "collaborator"}, {"kind": "assistant"}):
            value = document_data()
            value["blocks"][0]["author"].update(fields)
            with self.assertRaises(sources.SourceValidationError):
                self.validate(value)

    def test_parent_order_and_native_locator_integrity(self):
        for fields, code in (({"parent_block_id": "missing"}, "invalid-block-parent"),
                             ({"parent_block_id": "block-1"}, "invalid-block-parent"),
                             ({"order": 1}, "invalid-block-order"),
                             ({"native_block_id": ""}, "invalid-identifier")):
            value = document_data()
            value["blocks"][0].update(fields)
            self.reject(value, code)
        for field, code in (("id", "duplicate-block-id"), ("native_block_id", "duplicate-native-block-id"),
                             ("order", "invalid-block-order")):
            value = document_data()
            second = copy.deepcopy(value["blocks"][0])
            second.update(id="block-2", native_block_id="native-2", order=1,
                          author={"kind": "external", "id": None, "resolution": "unresolved"}, claim_eligible=False)
            second[field] = value["blocks"][0][field]
            value["blocks"].append(second)
            self.reject(value, code)

    def test_segments_and_fidelity_losses_fail_closed(self):
        value = document_data()
        value["blocks"][0]["content_segments"][0]["type"] = "executable"
        self.reject(value, "invalid-enum")
        value = document_data()
        value["fidelity_losses"] = [{"code": "non-semantic-formatting", "block_id": "block-1",
                                     "native_fact": "formatting", "reason": "unrepresentable"}]
        self.assertEqual(self.validate(value).fidelity_losses[0].code, "non-semantic-formatting")
        value["fidelity_losses"][0]["code"] = "omitted-owner-content"
        self.reject(value, "invalid-fidelity-loss")

    def test_document_adapter_registry_accepts_only_the_pinned_lark_tuple(self):
        value = document_data()
        value["adapter"] = {
            "name": "lark", "adapter_version": "1.0.0",
            "product_version": "1.0.86",
            "native_schema_version": "docx-v1-raw-content-v1",
        }
        value["blocks"][0]["author"] = {
            "kind": "external", "id": None, "resolution": "unresolved",
        }
        value["blocks"][0]["claim_eligible"] = False
        value["fidelity_losses"] = [{
            "code": "non-semantic-formatting", "block_id": "block-1",
            "native_fact": "formatting", "reason": "native-unavailable",
        }]
        document = self.validate(value)
        snapshot = self.validate_snapshot(self.snapshot_data(value), value)
        self.assertEqual(document.adapter, adapters.AdapterIdentity(
            "lark", "1.0.0", "1.0.86", "docx-v1-raw-content-v1"))
        self.assertEqual(snapshot.adapter, document.adapter)

        invalid_adapters = (
            {**value["adapter"], "adapter_version": "1.0.1"},
            {**value["adapter"], "product_version": "1.0.87"},
            {**value["adapter"], "native_schema_version": "docx-v1-raw-content-v2"},
            {**value["adapter"], "name": "codex"},
            {**value["adapter"], "name": "claude-code"},
            {**value["adapter"], "name": "trae"},
        )
        for identity in invalid_adapters:
            with self.subTest(identity=identity):
                candidate = copy.deepcopy(value)
                candidate["adapter"] = identity
                self.reject(candidate, "unsupported-adapter-version")
                with self.assertRaises(sources.SourceValidationError) as caught:
                    self.validate_snapshot(self.snapshot_data(candidate), candidate)
                self.assertEqual(caught.exception.code, "unsupported-adapter-version")

    def session_data(self):
        with (ROOT / "tests/fixtures/adapters/minimal-valid.json").open(encoding="utf-8") as fixture:
            graph = json.load(fixture)
        graph["source_snapshot_id"] = RAW_DIGEST
        for event in graph["events"]:
            event["source_snapshot_id"] = RAW_DIGEST
        return graph

    def snapshot_data(self, payload, kind="document"):
        import hashlib
        return {
            "schema_version": "knowledge-distiller.source-snapshot/v1",
            "source_kind": kind, "source_snapshot_id": payload["source_snapshot_id"],
            "adapter": copy.deepcopy(payload["adapter"]), "owner": copy.deepcopy(payload["owner"]),
            "raw_digest": "sha256:" + hashlib.sha256(b"synthetic raw").hexdigest(),
            "canonical_digest": digest(payload), "source_byte_count": 13,
            "source_item_count": len(payload["blocks"] if kind == "document" else payload["events"]),
            "fidelity_losses": copy.deepcopy(payload["fidelity_losses"]),
            "payload_kind": "canonical-document" if kind == "document" else "event-graph",
            "payload_reference": digest(payload),
        }

    def validate_snapshot(self, manifest, payload, context=None):
        return sources.validate_source_snapshot(manifest, payload=payload,
                                                raw_bytes=b"synthetic raw", context=context or self.context)

    def test_shared_snapshot_manifest_binds_document_bytes_counts_and_payload(self):
        payload = document_data()
        record = self.validate_snapshot(self.snapshot_data(payload), payload)
        self.assertIsInstance(record, sources.SourceSnapshotManifest)
        self.assertIsInstance(record.payload, sources.CanonicalDocument)
        self.assertEqual(record.source_item_count, 1)
        self.assertEqual(record.payload_reference, record.payload.canonical_digest)
        with self.assertRaises(FrozenInstanceError):
            record.raw_digest = "other"

    def test_session_snapshot_reuses_full_event_graph_validator_and_manifest(self):
        payload = self.session_data()
        context = adapters.ValidationContext("owner-1", payload["source_snapshot_id"])
        record = self.validate_snapshot(self.snapshot_data(payload, "session"), payload, context)
        self.assertIs(type(record.payload), adapters.EventGraphManifest)
        self.assertEqual(record.payload, adapters.validate_event_graph(payload, context=context))
        payload["events"][0]["claim_eligible"] = False
        with self.assertRaises(sources.SourceValidationError):
            self.validate_snapshot(self.snapshot_data(payload, "session"), payload, context)

    def test_snapshot_rejects_unknown_fields_and_all_binding_substitutions(self):
        payload = document_data()
        manifest = self.snapshot_data(payload)
        cases = {"private-selector": "secret", "source_kind": "directory",
                 "payload_kind": "event-graph", "payload_reference": "sha256:" + "b" * 64,
                 "canonical_digest": "sha256:" + "b" * 64,
                 "raw_digest": "sha256:" + "b" * 64,
                 "source_snapshot_id": "sha256:" + "b" * 64,
                 "source_byte_count": 12, "source_item_count": 2,
                 "fidelity_losses": [{"code": "omitted-content"}]}
        for field, value in cases.items():
            with self.subTest(field=field), self.assertRaises(sources.SourceValidationError) as caught:
                self.validate_snapshot(dict(manifest, **{field: value}), payload)
            self.assertLess(len(str(caught.exception)), 80)
            self.assertNotIn("secret", str(caught.exception))
        for field in manifest:
            invalid = dict(manifest)
            del invalid[field]
            with self.assertRaises(sources.SourceValidationError):
                self.validate_snapshot(invalid, payload)

    def test_source_validation_has_no_io_for_documents_and_sessions(self):
        document = document_data()
        document_manifest = self.snapshot_data(document)
        # Load only the checked-in synthetic fixture before arming the guard.
        graph = self.session_data()
        graph_manifest = self.snapshot_data(graph, "session")
        graph_context = adapters.ValidationContext("owner-1", graph["source_snapshot_id"])
        with forbid_side_effects():
            self.validate(document)
            self.reject(dict(document, revision="latest"), "invalid-revision")
            self.validate_snapshot(document_manifest, document)
            self.validate_snapshot(graph_manifest, graph, graph_context)
            for manifest, payload, context in ((document_manifest, document, self.context),
                                               (graph_manifest, graph, graph_context)):
                with self.assertRaises(sources.SourceValidationError) as caught:
                    self.validate_snapshot(dict(manifest, source_byte_count=0), payload, context)
                self.assertEqual(caught.exception.code, "snapshot-binding-mismatch")

    def test_unknown_fields_in_artifact_loss_and_snapshot_bindings_are_private(self):
        secret = "SENSITIVE-UNKNOWN-FIELD-DO-NOT-EMIT"
        locator = "document:PRIVATE-LOCATOR-DO-NOT-EMIT"
        payload = document_data()
        payload["blocks"][0]["artifact_locators"] = [
            {"id": "artifact-1", "kind": "document", "locator": locator, "revision": "rev-1"},
        ]
        payload["fidelity_losses"] = [
            {"code": "non-semantic-formatting", "block_id": "block-1",
             "native_fact": "formatting", "reason": "unrepresentable"},
        ]
        self.validate(payload)
        for path in (("blocks", 0, "artifact_locators", 0), ("fidelity_losses", 0)):
            invalid = copy.deepcopy(payload)
            nested = invalid
            for part in path:
                nested = nested[part]
            nested[secret] = locator
            with self.assertRaises(sources.SourceValidationError) as caught:
                self.validate(invalid)
            assert_private_error(self, caught.exception, "unknown-field", (secret, locator))
        for path in (("adapter",), ("owner",), ("fidelity_losses", 0)):
            manifest = self.snapshot_data(payload)
            nested = manifest
            for part in path:
                nested = nested[part]
            nested[secret] = locator
            with self.assertRaises(sources.SourceValidationError) as caught:
                self.validate_snapshot(manifest, payload)
            assert_private_error(self, caught.exception, "unknown-field", (secret, locator))

    def test_nested_source_diagnostics_never_disclose_source_values(self):
        secret = "SENSITIVE-SOURCE-TEXT-DO-NOT-EMIT"
        locator = "document:PRIVATE-LOCATOR-DO-NOT-EMIT"
        base = document_data()
        base["blocks"][0]["content_segments"][0]["text"] = secret
        base["blocks"][0]["artifact_locators"] = [
            {"id": "artifact-1", "kind": "document", "locator": locator, "revision": "rev-1"},
        ]
        base["fidelity_losses"] = [
            {"code": "non-semantic-formatting", "block_id": "block-1",
             "native_fact": "formatting", "reason": "unrepresentable"},
        ]
        cases = (
            (("adapter", "product_version"), secret, "unsupported-adapter-version"),
            (("owner", "id"), secret, "owner-context-mismatch"),
            (("blocks", 0, "author", "kind"), secret, "invalid-enum"),
            (("blocks", 0, "parent_block_id"), secret, "invalid-block-parent"),
            (("blocks", 0, "native_block_id"), secret * 20, "invalid-identifier"),
            (("blocks", 0, "content_segments", 0, "type"), secret, "invalid-enum"),
            (("blocks", 0, "content_segments", 0, "text"), {secret: locator}, "invalid-type"),
            (("blocks", 0, "artifact_locators", 0, "kind"), secret, "invalid-enum"),
            (("fidelity_losses", 0, "code"), secret, "invalid-fidelity-loss"),
            (("fidelity_losses", 0, "block_id"), secret, "unresolved-reference"),
        )
        for path, replacement, code in cases:
            with self.subTest(path=path, code=code):
                invalid = copy.deepcopy(base)
                target = invalid
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = replacement
                with self.assertRaises(sources.SourceValidationError) as caught:
                    self.validate(invalid)
                assert_private_error(self, caught.exception, code, (secret, locator))
        # The session wrapper must strip even the graph validator's pointer.
        graph = self.session_data()
        graph["events"][0]["content_segments"][0]["type"] = secret
        with self.assertRaises(sources.SourceValidationError) as caught:
            self.validate_snapshot(self.snapshot_data(graph, "session"), graph,
                                   adapters.ValidationContext("owner-1", graph["source_snapshot_id"]))
        assert_private_error(self, caught.exception, "invalid-enum", (secret, locator))

    def test_snapshot_identity_is_the_digest_of_native_bytes(self):
        for kind, original in (("document", document_data()), ("session", self.session_data())):
            for mismatch in ("snapshot", "raw-digest", "native-bytes"):
                with self.subTest(kind=kind, mismatch=mismatch):
                    payload = copy.deepcopy(original)
                    if mismatch == "snapshot":
                        payload["source_snapshot_id"] = "sha256:" + "a" * 64
                        for event in payload.get("events", []):
                            event["source_snapshot_id"] = payload["source_snapshot_id"]
                    manifest = self.snapshot_data(payload, kind)
                    context = (replace(self.context, expected_source_snapshot_id=payload["source_snapshot_id"])
                               if kind == "document" else adapters.ValidationContext("owner-1", payload["source_snapshot_id"]))
                    if mismatch == "raw-digest":
                        manifest["raw_digest"] = "sha256:" + "b" * 64
                    raw = b"modified raw!" if mismatch == "native-bytes" else RAW_BYTES
                    with self.assertRaises(sources.SourceValidationError) as caught:
                        sources.validate_source_snapshot(manifest, payload=payload, raw_bytes=raw, context=context)
                    self.assertEqual(caught.exception.code, "snapshot-binding-mismatch")

    def test_document_resource_limits_precede_normalization_and_serialization(self):
        cases = []
        blocks = document_data()
        blocks["blocks"].append(object())
        cases.append((blocks, "MAX_DOCUMENT_BLOCKS", 1, "document-resource-limit"))
        items = document_data()
        items["blocks"][0]["content_segments"] = [object()] * 1000
        cases.append((items, "MAX_DOCUMENT_ITEMS", 100, "document-resource-limit"))
        text = document_data()
        text["blocks"][0]["content_segments"][0]["text"] = "界" * 1000
        text["blocks"].append(object())
        cases.append((text, "MAX_DOCUMENT_BYTES", 2048, "document-too-large"))
        for value, limit, maximum, code in cases:
            with self.subTest(limit=limit), mock.patch.object(sources, limit, maximum), \
                    mock.patch.object(adapters, "_canonical_bytes", side_effect=AssertionError("serialization reached")), \
                    mock.patch.object(sources, "DocumentBlock", side_effect=AssertionError("normalization reached")):
                self.reject(value, code)

    def test_typed_document_limits_precede_wire_materialization(self):
        document = self.validate(document_data())
        for limit in ("MAX_DOCUMENT_BLOCKS", "MAX_DOCUMENT_ITEMS"):
            with self.subTest(limit=limit), mock.patch.object(sources, limit, 0), \
                    mock.patch.object(
                        sources, "_typed_data",
                        side_effect=AssertionError("wire materialization reached")):
                with self.assertRaises(sources.SourceValidationError) as caught:
                    sources.canonical_document_payload(document)
                self.assertEqual(caught.exception.code, "document-resource-limit")
        with mock.patch.object(sources, "MAX_DOCUMENT_ITEMS", 63):
            payload = sources.canonical_document_payload(document)
            self.validate(payload)
        with mock.patch.object(sources, "MAX_DOCUMENT_ITEMS", 62):
            with self.assertRaises(sources.SourceValidationError) as typed_error:
                sources.canonical_document_payload(document)
            with self.assertRaises(sources.SourceValidationError) as wire_error:
                self.validate(document_data())
        self.assertEqual(typed_error.exception.code, "document-resource-limit")
        self.assertEqual(wire_error.exception.code, "document-resource-limit")

    def test_document_byte_limit_is_exact_for_unicode_and_json_escaping(self):
        value = document_data()
        value["blocks"][0]["content_segments"][0]["text"] = "界é😀\n\t\"\\\u0000"
        size = len(adapters._canonical_bytes(value))
        with mock.patch.object(sources, "MAX_DOCUMENT_BYTES", size):
            self.validate(value)
        with mock.patch.object(sources, "MAX_DOCUMENT_BYTES", size - 1):
            self.reject(value, "document-too-large")
        with mock.patch.object(sources, "MAX_DOCUMENT_BLOCKS", 1):
            self.validate(value)
        value["blocks"][0]["order"] = True
        self.reject(value, "invalid-type")


if __name__ == "__main__":
    unittest.main()
