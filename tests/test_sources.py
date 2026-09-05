import copy
import importlib
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from tests.test_authorization import digest

ROOT = Path(__file__).resolve().parents[1]
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
        "source_snapshot_id": "sha256:" + "a" * 64,
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
            expected_owner_id="owner-1", expected_source_snapshot_id="sha256:" + "a" * 64,
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

    def test_no_native_adapter_versions_are_added(self):
        value = document_data()
        value["adapter"]["name"] = "lark"
        self.reject(value, "unsupported-adapter-version")

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
        with (ROOT / "tests/fixtures/adapters/minimal-valid.json").open(encoding="utf-8") as fixture:
            payload = json.load(fixture)
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


if __name__ == "__main__":
    unittest.main()
