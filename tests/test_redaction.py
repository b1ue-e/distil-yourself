"""Synthetic tests for the deterministic, offline pre-ingestion boundary."""
import dataclasses
import hashlib
import importlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge-distiller" / "scripts"))
try:
    r = importlib.import_module("knowledge_distiller.redaction")
except ModuleNotFoundError:
    r = None


def digest(label):
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


class RedactionTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(r, "pre-ingestion redaction module is missing")

    def context(self, **changes):
        fields = dict(context_id=digest("trust"), owner_id=digest("owner"),
                      ingestion_run_id=digest("run"), content_grant_digest=digest("grant"),
                      authority_attestation_digest=digest("attestation"),
                      participant_names=("张三", "Alice Example"), participant_ids=("account-123",),
                      allowlist=())
        return r.TrustContext(**dict(fields, **changes))

    def binding(self, **changes):
        fields = dict(context_id=digest("trust"), source_snapshot_id=digest("snapshot"),
                      native_locator_digest=digest("locator"), actor_id=digest("owner"),
                      actor_resolution="verified")
        return r.SpanBinding(**dict(fields, **changes))

    def run_text(self, text, *, key=b"k" * 32, context=None, binding=None, one_byte=False):
        raw = text.encode() if type(text) is str else text
        chunks = [raw[i:i+1] for i in range(len(raw))] if one_byte else [raw]
        events = [r.SourceSpan(binding or self.binding())]
        events.extend(r.SourceChunk(chunk) for chunk in chunks)
        return r.Redactor(context or self.context(), key).redact(iter(events))

    def reject(self, action, code=None):
        with self.assertRaises(r.RedactionError) as caught:
            action()
        error = caught.exception
        if code:
            self.assertEqual(error.code, code)
        self.assertEqual(error.args, (error.code,))
        self.assertRegex(error.code, r"^[a-z0-9-]{1,40}$")
        self.assertEqual(vars(error), {"code": error.code})
        return error

    def test_every_detector_and_unicode_one_byte_chunking(self):
        secrets = ("-----BEGIN RSA PRIVATE KEY-----\nQUJDREVGRw==\n-----END RSA PRIVATE KEY-----",
                   "s3cr3tBearer", "api-value-XYZ", "session-value-XYZ", "access-value-XYZ",
                   "cookie-secret", "password-secret", "hidden-secret", "token-secret",
                   "alice@example.org", "+1 (212) 555-0199", "张三", "Alice Example", "account-123")
        text = (secrets[0] + "\nAuthorization: Bearer " + secrets[1] + "\napi_key=" + secrets[2]
                + "\nsession_token=" + secrets[3] + "\naccess_token=" + secrets[4]
                + "\nCookie: sid=" + secrets[5] + "; preference=private\npassword='" + secrets[6]
                + "' secret=" + secrets[7] + " token=" + secrets[8] + "\n" + " | ".join(secrets[9:]))
        result = self.run_text(text, one_byte=True)
        serialized = repr(dataclasses.asdict(result[0]))
        for secret in secrets:
            self.assertNotIn(secret, serialized)
        self.assertNotIn("preference=private", serialized)
        for kind in ("private-key", "credential", "cookie", "email", "phone", "participant-name", "participant-id"):
            self.assertIn("[redacted:" + kind + ":", result[0].text)
        self.assertEqual(result, self.run_text(text))

    def test_plain_prose_and_exact_allowlist(self):
        text = "Discuss token rotation and secret management. Version 12345. 你好🙂"
        self.assertEqual(self.run_text(text)[0].text, text)
        allowed = self.context(allowlist=("example@example.org", "Alice Example"))
        result = self.run_text("example@example.org other@example.org Alice Example", context=allowed)
        self.assertTrue(result[0].text.startswith("example@example.org [redacted:email:"))
        self.assertTrue(result[0].text.endswith("Alice Example"))
        self.assertEqual(allowed.allowlist, ("Alice Example", "example@example.org"))

    def test_allowlist_forbidden_before_input_consumption(self):
        for value in ("account-123", "-----BEGIN PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----", "", "\ud800"):
            self.reject(lambda: self.context(allowlist=(value,)))
        self.reject(lambda: self.context(allowlist=["x"]))
        self.reject(lambda: self.context(allowlist=("x", "x")))
        self.reject(lambda: self.context(allowlist=("x" * (r.MAX_LITERAL_BYTES + 1),)))
        self.reject(lambda: self.context(allowlist=tuple(str(i) for i in range(r.MAX_LITERALS + 1))))

    def test_overlap_largest_match_and_keyed_stability(self):
        text = "password='alice@example.org' alice@example.org alice@example.org"
        out = self.run_text(text)[0].text
        self.assertEqual(out.count("[redacted:credential:"), 1)
        self.assertEqual(out.count("[redacted:email:"), 2)
        self.assertEqual(out.split()[-1], out.split()[-2])
        self.assertEqual(out, self.run_text(text)[0].text)
        self.assertNotEqual(out, self.run_text(text, key=b"z" * 32)[0].text)
        self.assertNotIn(hashlib.sha256(b"alice@example.org").hexdigest(), out)

    def test_shared_secret_id_across_detector_types_and_partial_overlaps(self):
        out = self.run_text("password=alice@example.org alice@example.org")[0].text
        placeholders = __import__("re").findall(r"\[redacted:[a-z-]+:([a-f0-9]{64})\]", out)
        self.assertEqual(placeholders[0], placeholders[1])
        context = self.context(participant_names=("abc def", "def ghi"), participant_ids=())
        out = self.run_text("abc def ghi", context=context)[0].text
        self.assertEqual(out.count("[redacted:"), 1)
        for fragment in ("abc", "def", "ghi"):
            self.assertNotIn(fragment, out.split(":")[0])

    def test_prefixed_credential_assignments_and_orphan_private_key(self):
        for field in ("db_password", "auth_token", "AWS_SECRET_ACCESS_KEY", "client-secret", "X-API-Key"):
            with self.subTest(field=field):
                self.assertNotIn("private-value", self.run_text(field + "=private-value")[0].text)
        self.reject(lambda: self.run_text("-----END PRIVATE KEY-----"), "invalid-private-key")

    def test_derivation_chain_mutations_fail_closed(self):
        span = self.run_text("hello")[0]
        edge = dataclasses.replace(span.derivations[1], source_id=digest("wrong"))
        self.reject(lambda: dataclasses.replace(span, derivations=(span.derivations[0], edge) + span.derivations[2:]))
        self.reject(lambda: dataclasses.replace(span, claim_eligible=1))
        self.reject(lambda: dataclasses.replace(span, derivations=list(span.derivations)))
        self.reject(lambda: dataclasses.replace(span, derivations=span.derivations[::-1]))

    def test_exact_literal_and_aggregate_resource_caps(self):
        self.context(allowlist=("x" * r.MAX_LITERAL_BYTES,))
        self.context(allowlist=tuple(str(i) for i in range(r.MAX_LITERALS)))
        with mock.patch.object(r, "MAX_LITERAL_TOTAL_BYTES", 6):
            self.context(participant_names=(), participant_ids=(), allowlist=("abc", "def"))
            self.reject(lambda: self.context(participant_names=(), participant_ids=(), allowlist=("abc", "defg")))
        events = [r.SourceSpan(self.binding()), r.SourceChunk(b"ab")] * 2
        with mock.patch.object(r, "MAX_INPUT_BYTES", 3):
            self.reject(lambda: r.Redactor(self.context(), b"k" * 32).redact(events), "input-limit")
        with mock.patch.object(r, "MAX_OUTPUT_BYTES", 3):
            self.reject(lambda: r.Redactor(self.context(), b"k" * 32).redact(events), "output-limit")
        text = self.run_text("a@b.org")[0].text
        with mock.patch.object(r, "MAX_OUTPUT_BYTES", len(text)):
            self.run_text("a@b.org")
        with mock.patch.object(r, "MAX_OUTPUT_BYTES", len(text) - 1):
            self.reject(lambda: self.run_text("a@b.org"), "output-limit")
        start, end = "-----BEGIN PRIVATE KEY-----\n", "\n-----END PRIVATE KEY-----"
        self.run_text(start + "x" * (r.MAX_PRIVATE_KEY_CHARS - len(start) - len(end)) + end)
        self.reject(lambda: self.run_text(start + "x" * (r.MAX_PRIVATE_KEY_CHARS - len(start) - len(end) + 1) + end), "secret-limit")

    def test_provenance_chain_and_owner_eligibility(self):
        span = self.run_text("Hello")[0]
        self.assertTrue(span.claim_eligible)
        edges = span.derivations
        self.assertEqual([(e.relation, e.source_id, e.target_id) for e in edges], [
            ("native-locator", span.span_id, digest("locator")),
            ("source-snapshot", digest("locator"), digest("snapshot")),
            ("ingestion-run", digest("snapshot"), digest("run")),
            ("content-grant", digest("run"), digest("grant")),
            ("authority-attestation", digest("grant"), digest("attestation"))])
        self.assertNotIn(digest("owner"), repr(span))
        for changes in ({"actor_id": digest("someone")}, {"actor_resolution": "ambiguous"},
                        {"actor_resolution": "unknown", "actor_id": None}):
            self.assertFalse(self.run_text("I am the owner", binding=self.binding(**changes))[0].claim_eligible)
        self.reject(lambda: self.run_text("hidden", binding=self.binding(context_id=digest("other"))))
        self.assertNotEqual(span.span_id, self.run_text("Hello", context=self.context(content_grant_digest=digest("other")))[0].span_id)

    def test_runtime_types_closed_records_and_mutation(self):
        class String(str):
            pass
        class Blob(bytes):
            pass
        for value in (True, 1, [], String(digest("owner")), "SHa256:" + "a" * 64):
            self.reject(lambda: self.context(owner_id=value))
        self.reject(lambda: r.TrustContext(unknown="private-selector"))
        self.reject(lambda: r.SourceChunk(unknown="private-selector"))
        for data in ("secret", bytearray(b"secret"), Blob(b"secret"), True):
            self.reject(lambda: r.SourceChunk(data))
        self.reject(lambda: r.Redactor(self.context(), b"short"))
        self.reject(lambda: self.context(participant_names=["Alice"]))
        context = self.context()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            context.owner_id = digest("other")
        object.__setattr__(context, "allowlist", ("account-123",))
        touched = []
        def events():
            touched.append(True)
            yield r.SourceSpan(self.binding())
        self.reject(lambda: r.Redactor(context, b"k" * 32).redact(events()))
        self.assertFalse(touched)
        binding = self.binding()
        object.__setattr__(binding, "native_locator_digest", "raw-private-path")
        self.reject(lambda: self.run_text("secret", binding=binding))
        valid = self.run_text("hello")[0]
        self.reject(lambda: dataclasses.replace(valid, span_id="raw-private-value"))
        self.reject(lambda: dataclasses.replace(valid.derivations[0], relation="arbitrary"))

    def test_invalid_utf8_and_error_leakage_matrix(self):
        for raw in (b"secret\xff", b"secret\xe2", b"\xc0\xaf", b"\xed\xa0\x80"):
            error = self.reject(lambda: self.run_text(raw, one_byte=True), "invalid-utf8")
            for diagnostic in (str(error), repr(error), repr(error.args), repr(vars(error))):
                self.assertNotIn("secret", diagnostic)
        for value in ("sensitive/selector", "password=mysecret", "owner@example.org", "\ud800"):
            error = self.reject(lambda: self.binding(native_locator_digest=value))
            self.assertNotIn(value, repr(error))

    def test_exact_input_output_chunk_span_and_detection_limits(self):
        with mock.patch.object(r, "MAX_INPUT_BYTES", 6), mock.patch.object(r, "MAX_OUTPUT_BYTES", 6):
            self.assertEqual(self.run_text("你好")[0].text, "你好")
            self.reject(lambda: self.run_text("你好x"), "input-limit")
        with mock.patch.object(r, "MAX_OUTPUT_BYTES", 3):
            self.assertEqual(self.run_text("abc")[0].text, "abc")
            self.reject(lambda: self.run_text("abcd"), "output-limit")
        with mock.patch.object(r, "MAX_CHUNKS", 3):
            self.run_text("abc", one_byte=True)
            self.reject(lambda: self.run_text("abcd", one_byte=True), "chunk-limit")
        with mock.patch.object(r, "MAX_SPANS", 2):
            events = [r.SourceSpan(self.binding()), r.SourceChunk(b"a")] * 2
            self.assertEqual(len(r.Redactor(self.context(), b"k" * 32).redact(events)), 2)
            self.reject(lambda: r.Redactor(self.context(), b"k" * 32).redact(events + events[:1]), "span-limit")
        with mock.patch.object(r, "MAX_DETECTIONS", 2):
            self.run_text("a@b.org c@d.org")
            self.reject(lambda: self.run_text("a@b.org c@d.org e@f.org"), "detection-limit")

    def test_bounded_secrets_and_private_keys_fail_closed(self):
        self.run_text("password=" + "x" * r.MAX_SECRET_CHARS)
        self.reject(lambda: self.run_text("password=" + "x" * (r.MAX_SECRET_CHARS + 1)), "secret-limit")
        self.reject(lambda: self.run_text("-----BEGIN PRIVATE KEY-----\nsecret"), "invalid-private-key")
        self.reject(lambda: self.run_text("-----BEGIN PRIVATE KEY-----\n" + "x" * r.MAX_PRIVATE_KEY_CHARS + "\n-----END PRIVATE KEY-----"), "secret-limit")

    def test_cancellation_and_generator_error_cleanup_and_no_reuse(self):
        for exception in (KeyboardInterrupt("private-secret"), RuntimeError("private-secret"), SystemExit("private-secret")):
            redactor = r.Redactor(self.context(), b"k" * 32)
            def events():
                yield r.SourceSpan(self.binding())
                yield r.SourceChunk(b"password=private-secret")
                raise exception
            error = self.reject(lambda: redactor.redact(events()))
            self.assertNotIn("private-secret", repr(error))
            self.assertEqual(redactor._buffer, bytearray())
            self.assertEqual(redactor._key, bytearray())
            self.assertIsNone(redactor._context)
            self.reject(lambda: redactor.redact(()), "already-finalized")
        redactor = r.Redactor(self.context(), b"k" * 32)
        redactor.redact(())
        self.reject(lambda: redactor.redact(()), "already-finalized")

    def test_unknown_events_and_chunks_without_span(self):
        for events in ([{}], [r.SourceChunk(b"secret")], [True]):
            self.reject(lambda: r.Redactor(self.context(), b"k" * 32).redact(events))

    def test_offline_no_side_effects(self):
        with mock.patch("builtins.open", side_effect=AssertionError("I/O")), \
             mock.patch.object(os, "open", side_effect=AssertionError("I/O")), \
             mock.patch.object(os, "getenv", side_effect=AssertionError("environment")), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
            self.assertIn("[redacted:", self.run_text("password=synthetic")[0].text)


if __name__ == "__main__":
    unittest.main()
