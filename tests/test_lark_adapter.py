"""Synthetic tests for the revision-pinned Lark raw-content adapter."""

import dataclasses
import hashlib
import importlib
import json
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "adapters" / "lark" / "1.0.86" / "docx-v1-raw-content-v1"
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))
from knowledge_distiller import redaction, sources


def digest(value):
    raw = value if type(value) is bytes else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


try:
    native = importlib.import_module("knowledge_distiller.native_adapters")
except ModuleNotFoundError:
    native = None


class LarkRawContentAdapterTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(native, "native Lark adapter module is missing")
        self.key = b"k" * 32
        self.owner = digest("owner-open-id")
        self.document = digest("document-token")

    def trust(self, snapshot):
        context = redaction.TrustContext(
            context_id=digest("trust-context"), owner_id=self.owner,
            ingestion_run_id=digest("ingestion-run"), content_grant_digest=digest("grant"),
            authority_attestation_digest=digest("attestation"), participant_names=(),
            participant_ids=(), allowlist=())
        binding = redaction.SpanBinding(
            context_id=context.context_id, source_snapshot_id=snapshot,
            native_locator_digest=native.lark_native_locator_digest(self.document, "3365"), actor_kind="external",
            actor_id=None, actor_resolution="unresolved")
        return context, binding

    def normalize_raw(self, raw, **changes):
        snapshot = digest(raw)
        trust, binding = self.trust(snapshot)
        content = native.decode_lark_raw_content(raw)
        result = redaction.Redactor(trust, self.key).redact(
            (redaction.SourceSpan(binding), redaction.SourceChunk(content)))
        fields = dict(
            expected_owner_id=self.owner, expected_source_snapshot_id=snapshot,
            expected_revision="3365", native_document_id=self.document,
            product_version="1.0.86")
        fields.update(changes.pop("context_changes", {}))
        return native.normalize_lark_raw_content(
            result, context=native.LarkNormalizationContext(**fields),
            redaction_context=changes.pop("redaction_context", trust),
            redaction_key=changes.pop("redaction_key", self.key),
            expected_binding=changes.pop("expected_binding", binding), **changes)

    def normalize(self, content, **changes):
        raw = json.dumps({"ok": True, "identity": "user", "data": {"content": content}},
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.normalize_raw(raw, **changes)

    def reject(self, action, code=None):
        with self.assertRaises(native.NativeAdapterError) as caught:
            action()
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.args, (caught.exception.code,))
        self.assertEqual(vars(caught.exception), {"code": caught.exception.code})
        for value in (str(caught.exception), repr(caught.exception), repr(vars(caught.exception))):
            self.assertNotIn("synthetic-secret", value)

    def test_normalizes_only_validated_redacted_content(self):
        document = self.normalize("Rule: password=synthetic-secret contact alice@example.org")
        self.assertEqual(document.adapter, native.LARK_RAW_CONTENT_ADAPTER)
        self.assertEqual(document.revision, "3365")
        self.assertEqual(document.owner.id, self.owner)
        self.assertEqual(document.native_document_id, self.document)
        self.assertEqual(len(document.blocks), 1)
        block = document.blocks[0]
        self.assertEqual(block.author.kind, "external")
        self.assertEqual(block.author.resolution, "unresolved")
        self.assertIsNone(block.author.id)
        self.assertFalse(block.claim_eligible)
        self.assertIn("[redacted:credential:hmac-sha256:", block.content_segments[0].text)
        self.assertIn("[redacted:email:hmac-sha256:", block.content_segments[0].text)
        self.assertNotIn("synthetic-secret", repr(document))
        self.assertEqual([(loss.code, loss.native_fact, loss.reason) for loss in document.fidelity_losses],
                         [("non-semantic-formatting", "formatting", "native-unavailable")])
        self.assertEqual(document, self.normalize("Rule: password=synthetic-secret contact alice@example.org"))

    def test_json_escapes_are_decoded_before_redaction(self):
        raw = (b'{"ok":true,"identity":"user","data":{"content":'
               b'"alice\\u0040example.org password\\u003dsynthetic-secret"}}')
        text = self.normalize_raw(raw).blocks[0].content_segments[0].text
        self.assertIn("[redacted:email:hmac-sha256:", text)
        self.assertIn("[redacted:credential:hmac-sha256:", text)
        self.assertNotIn("alice@example.org", text)
        self.assertNotIn("synthetic-secret", text)

    def test_observed_shape_fixture_is_fully_synthetic_and_pinned(self):
        raw = (FIXTURES / "redacted-current.json").read_bytes()
        document = self.normalize_raw(raw)
        expected = json.loads((FIXTURES / "expected-current.json").read_text(encoding="utf-8"))
        actual = json.loads(json.dumps(dataclasses.asdict(document), sort_keys=True))
        self.assertEqual(actual, expected)
        metadata = json.loads((FIXTURES / "fixture-metadata.json").read_text(encoding="utf-8"))
        shape = json.dumps(metadata["response_schema_shape"], sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
        self.assertEqual(metadata["response_schema_digest"], digest(shape))
        self.assertEqual(metadata["observed_cli_version"], "1.0.86")
        self.assertEqual(metadata["observed_revision"], "3365")
        self.assertEqual(metadata["response_schema_shape"], {
            "ok": "literal:true", "identity": "literal:user",
            "data": {"content": "string"},
        })
        self.assertEqual(metadata["response_schema_digest"],
                         "sha256:9ea00db953bf91884d198de7a10282a0c519cefc0065b5f113bc8cce528d273f")
        self.assertFalse(metadata["comment_content_requested"])
        self.assertFalse(metadata["content_retained_from_observation"])
        self.assertNotIn("bytedance", raw.decode("utf-8").lower())

    def test_closed_raw_response_schema_and_error_envelope(self):
        invalid = (
            b'{"ok":true,"identity":"user","data":{"content":"ok"},"extra":"synthetic-secret"}',
            b'{"ok":true,"identity":"user","data":{"content":"ok","extra":"synthetic-secret"}}',
            b'{"ok":true,"identity":"user","data":{}}',
            b'{"ok":true,"identity":"user","data":{"content":1}}',
            b'{"ok":false,"identity":"user","data":{"content":"synthetic-secret"}}',
            b'{"ok":true,"identity":"bot","data":{"content":"synthetic-secret"}}',
            b'{"ok":true,"ok":true,"identity":"user","data":{"content":"synthetic-secret"}}',
            b'not-json-synthetic-secret',
        )
        for raw in invalid:
            with self.subTest(raw=raw[:20]):
                self.reject(lambda raw=raw: self.normalize_raw(raw))
        self.reject(lambda: self.normalize_raw(b'\xffsynthetic-secret'), "invalid-utf8")

    def test_external_context_and_redaction_binding_are_mandatory(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"hello"}}'
        snapshot = digest(raw)
        trust, binding = self.trust(snapshot)
        for context_changes in (
                {"expected_owner_id": digest("other")},
                {"expected_source_snapshot_id": digest("other")},
                {"expected_revision": "3366"},
                {"native_document_id": ""},
                {"native_document_id": digest("other-document")},
                {"product_version": "1.0.87"}):
            with self.subTest(changes=context_changes):
                self.reject(lambda context_changes=context_changes: self.normalize_raw(
                    raw, context_changes=context_changes))
        self.reject(lambda: self.normalize_raw(raw, redaction_key=b"z" * 32))
        self.reject(lambda: self.normalize_raw(
            raw, expected_binding=dataclasses.replace(binding, source_snapshot_id=digest("other"))))
        self.reject(lambda: self.normalize_raw(raw, redaction_context=dataclasses.replace(
            trust, owner_id=digest("other"))))
        self.reject(lambda: self.normalize_raw(raw, redaction_context={"owner_id": "synthetic-secret"}))
        mutated = dataclasses.replace(binding)
        object.__setattr__(mutated, "extra", "synthetic-secret")
        self.reject(lambda: self.normalize_raw(raw, expected_binding=mutated))

    def test_rejects_unredacted_or_mutated_results(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"hello"}}'
        snapshot = digest(raw)
        trust, binding = self.trust(snapshot)
        result = redaction.Redactor(trust, self.key).redact(
            (redaction.SourceSpan(binding),
             redaction.SourceChunk(native.decode_lark_raw_content(raw))))
        object.__setattr__(result[0], "text", result[0].text.replace("hello", "synthetic-secret"))
        context = native.LarkNormalizationContext(
            self.owner, snapshot, "3365", self.document, "1.0.86")
        self.reject(lambda: native.normalize_lark_raw_content(
            result, context=context, redaction_context=trust, redaction_key=self.key,
            expected_binding=binding))
        self.reject(lambda: native.normalize_lark_raw_content(
            raw, context=context, redaction_context=trust, redaction_key=self.key,
            expected_binding=binding))

    def test_rejects_non_external_binding_and_multiple_spans(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"hello"}}'
        snapshot = digest(raw)
        trust, binding = self.trust(snapshot)
        context = native.LarkNormalizationContext(
            self.owner, snapshot, "3365", self.document, "1.0.86")
        for changes in (
                {"actor_kind": "user", "actor_id": self.owner,
                 "actor_resolution": "verified-owner"},
                {"actor_kind": "assistant", "actor_id": digest("assistant"),
                 "actor_resolution": "native"}):
            candidate = dataclasses.replace(binding, **changes)
            result = redaction.Redactor(trust, self.key).redact((
                redaction.SourceSpan(candidate), redaction.SourceChunk(b"hello")))
            self.reject(lambda result=result, candidate=candidate:
                        native.normalize_lark_raw_content(
                            result, context=context, redaction_context=trust,
                            redaction_key=self.key, expected_binding=candidate),
                        "invalid-context")
        result = redaction.Redactor(trust, self.key).redact((
            redaction.SourceSpan(binding), redaction.SourceChunk(b"one"),
            redaction.SourceSpan(binding), redaction.SourceChunk(b"two")))
        self.reject(lambda: native.normalize_lark_raw_content(
            result, context=context, redaction_context=trust,
            redaction_key=self.key, expected_binding=binding), "invalid-context")

    def test_empty_content_is_valid_and_resource_limits_fail_closed(self):
        self.assertEqual(self.normalize("").blocks[0].content_segments[0].text, "")
        with mock.patch.object(redaction, "MAX_INPUT_BYTES", 64):
            self.reject(lambda: self.normalize("x" * 100), "input-limit")

    def test_revision_bounds_and_context_constructor_are_code_only(self):
        self.assertEqual(len("9" * 20), 20)
        native.LarkNormalizationContext(
            self.owner, digest("snapshot"), "9" * 20, self.document, "1.0.86")
        self.assertTrue(native.lark_native_locator_digest(self.document, "9" * 20).startswith("sha256:"))
        for action in (
                lambda: native.LarkNormalizationContext(
                    self.owner, digest("snapshot"), "9" * 21, self.document, "1.0.86"),
                lambda: native.lark_native_locator_digest(self.document, "9" * 21),
                lambda: native.LarkNormalizationContext(private_secret="synthetic-secret"),
                lambda: native.LarkNormalizationContext(self.owner)):
            self.reject(action)

    def test_normalized_document_enters_snapshot_manifest_without_conversion(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe text"}}'
        document = self.normalize_raw(raw)
        payload = sources.canonical_document_payload(document)
        manifest = {
            "schema_version": sources.SNAPSHOT_SCHEMA,
            "source_kind": "document",
            "source_snapshot_id": digest(raw),
            "adapter": payload["adapter"],
            "owner": payload["owner"],
            "raw_digest": digest(raw),
            "canonical_digest": document.canonical_digest,
            "source_byte_count": len(raw),
            "source_item_count": len(document.blocks),
            "fidelity_losses": payload["fidelity_losses"],
            "payload_kind": "canonical-document",
            "payload_reference": document.canonical_digest,
        }
        record = sources.validate_source_snapshot(
            manifest, payload=document, raw_bytes=raw,
            context=sources.DocumentValidationContext(
                self.owner, digest(raw), "3365", ()))
        self.assertEqual(record.payload, document)
        tampered = dataclasses.replace(document, canonical_digest=digest("other"))
        with self.assertRaises(sources.SourceValidationError) as caught:
            sources.validate_source_snapshot(
                manifest, payload=tampered, raw_bytes=raw,
                context=sources.DocumentValidationContext(
                    self.owner, digest(raw), "3365", ()))
        self.assertEqual(caught.exception.code, "snapshot-binding-mismatch")
        mutated_block = dataclasses.replace(document.blocks[0])
        object.__setattr__(mutated_block, "private_secret", "synthetic-secret")
        mutated_document = dataclasses.replace(document, blocks=(mutated_block,))
        with self.assertRaises(sources.SourceValidationError) as caught:
            sources.canonical_document_payload(mutated_document)
        self.assertEqual(caught.exception.code, "invalid-type")

    def test_normalizer_is_pure_and_does_not_discover_or_traverse(self):
        with mock.patch("builtins.open", side_effect=AssertionError("I/O")), \
             mock.patch.object(os, "getenv", side_effect=AssertionError("environment")), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
            document = self.normalize("safe text")
        self.assertEqual(document.blocks[0].content_segments[0].text, "safe text")
        self.assertEqual(document.blocks[0].artifact_locators, ())


if __name__ == "__main__":
    unittest.main()
