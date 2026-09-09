import hashlib
import json
from pathlib import Path
import sys
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import adapters, ingestion, local_runtime


def digest(raw=b"pinned Codex session prefix"):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def request_data():
    return {
        "schema_version": local_runtime.REQUEST_SCHEMA,
        "transaction_id": "transaction-1",
        "expected_generation_id": "generation-1",
        "session_path": "/Users/example/.codex/sessions/2026/session-私密.jsonl",
        "project_id": "project-1",
        "prefix_length": 128,
        "prefix_digest": digest(),
        "derived_processing_until": 2_000_000_000,
    }


def encoded(value=None):
    return json.dumps(
        request_data() if value is None else value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class LocalRuntimeDecoderTest(unittest.TestCase):
    def assert_invalid(self, raw, secrets=()):
        with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
            local_runtime.decode_local_codex_request(raw)
        error = caught.exception
        self.assertEqual(error.code, "invalid-local-request")
        self.assertEqual(str(error), "invalid-local-request")
        self.assertEqual(error.args, ("invalid-local-request",))
        self.assertEqual(vars(error), {"code": "invalid-local-request"})
        for diagnostic in (str(error), repr(error.args), repr(vars(error))):
            self.assertLess(len(diagnostic), 160)
            for secret in secrets:
                self.assertNotIn(secret, diagnostic)

    def test_decodes_exact_request_into_immutable_private_record(self):
        request = local_runtime.decode_local_codex_request(encoded())

        self.assertEqual(
            request,
            local_runtime.LocalCodexRequest(
                schema_version=local_runtime.REQUEST_SCHEMA,
                transaction_id="transaction-1",
                expected_generation_id="generation-1",
                session_path="/Users/example/.codex/sessions/2026/session-私密.jsonl",
                project_id="project-1",
                prefix_length=128,
                prefix_digest=digest(),
                derived_processing_until=2_000_000_000,
            ),
        )
        self.assertNotIn("session-私密.jsonl", repr(request))
        with self.assertRaises(FrozenInstanceError):
            request.session_path = "/other/private/session.jsonl"

    def test_exports_fixed_decoder_contract(self):
        self.assertEqual(
            local_runtime.REQUEST_SCHEMA,
            "knowledge-distiller.local-codex-ingestion-request/v1",
        )
        self.assertEqual(local_runtime.MAX_LOCAL_REQUEST_BYTES, ingestion.MAX_REQUEST_BYTES)
        self.assertEqual(local_runtime.READ_WINDOW_SECONDS, 300)
        self.assertEqual(local_runtime.MAX_DERIVED_SECONDS, 90 * 24 * 60 * 60)
        self.assertEqual(local_runtime.PURPOSE, "distill-knowledge")
        self.assertEqual(local_runtime.LOCAL_OWNER_VERIFIER, "local-owner-verifier-v1")
        self.assertEqual(
            local_runtime.REQUEST_FIELDS,
            frozenset(
                {
                    "schema_version",
                    "transaction_id",
                    "expected_generation_id",
                    "session_path",
                    "project_id",
                    "prefix_length",
                    "prefix_digest",
                    "derived_processing_until",
                }
            ),
        )

    def test_error_codes_are_allowlisted_and_code_only(self):
        for code in (
            "invalid-local-request",
            "invalid-derived-deadline",
            "local-identity-unavailable",
            "unsafe-redaction-key",
            "local-runtime-failed",
        ):
            with self.subTest(code=code):
                error = local_runtime.LocalRuntimeError(code)
                self.assertEqual(error.code, code)
                self.assertEqual(error.args, (code,))
                self.assertEqual(str(error), code)

        private = "private-path-and-source-text"
        error = local_runtime.LocalRuntimeError(private)
        self.assertEqual(error.code, "invalid-local-request")
        self.assertEqual(error.args, ("invalid-local-request",))
        self.assertNotIn(private, repr(vars(error)))

    def test_rejects_invalid_field_values_without_private_diagnostics(self):
        invalid_values = (
            ("schema_version", "knowledge-distiller.local-codex-ingestion-request/v2"),
            ("transaction_id", ""),
            ("expected_generation_id", ""),
            ("project_id", ""),
            ("prefix_length", False),
            ("prefix_length", 0),
            ("prefix_length", adapters.MAX_GRAPH_BYTES + 1),
            ("prefix_digest", digest().upper()),
            ("prefix_digest", "sha256:" + ("a" * 63)),
            ("session_path", "/private/sessions/*.jsonl"),
            ("session_path", " /private/sessions/session.jsonl"),
            ("session_path", "/private/sessions/session.jsonl "),
            ("session_path", "latest"),
            ("session_path", "LATEST"),
            ("session_path", "/private/session\u202e.jsonl"),
            ("derived_processing_until", False),
            ("derived_processing_until", -1),
            ("derived_processing_until", 2**63),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=ascii(value)):
                data = request_data()
                data[field] = value
                self.assert_invalid(
                    encoded(data),
                    (data["session_path"], "private", "session-私密.jsonl"),
                )

    def test_rejects_every_unknown_caller_authority_and_adapter_field(self):
        unknown_fields = (
            "active_principal",
            "product_version",
            "caller_uid",
            "owner",
            "issuer",
            "grant",
            "content_grant",
            "attestation",
            "authority_attestation",
            "adapter",
            "native_schema_version",
            "start",
        )
        for field in unknown_fields:
            with self.subTest(field=field):
                data = request_data()
                data[field] = "PRIVATE-CALLER-SUPPLIED-VALUE"
                self.assert_invalid(
                    encoded(data),
                    (data["session_path"], "PRIVATE-CALLER-SUPPLIED-VALUE"),
                )

    def test_rejects_missing_duplicate_invalid_utf8_and_invalid_unicode(self):
        data = request_data()
        del data["project_id"]
        duplicate = encoded()[:-1] + b',"project_id":"PRIVATE-DUPLICATE"}'
        cases = (
            (encoded(data), ("session-私密.jsonl",)),
            (duplicate, ("PRIVATE-DUPLICATE", "session-私密.jsonl")),
            (b"\xffPRIVATE-SOURCE", ("PRIVATE-SOURCE",)),
            (b'{"schema_version":"\\ud800"}', ()),
            (b'{"\\udc00":0}', ()),
        )
        for raw, secrets in cases:
            with self.subTest(raw=raw[:40]):
                self.assert_invalid(raw, secrets)

    def test_rejects_empty_oversized_and_non_exact_bytes(self):
        self.assert_invalid(b"")
        self.assert_invalid(b" " * (local_runtime.MAX_LOCAL_REQUEST_BYTES + 1))

        class BytesSubclass(bytes):
            __slots__ = ()

        for raw in (encoded().decode("utf-8"), bytearray(encoded()), memoryview(encoded()), BytesSubclass(encoded())):
            with self.subTest(kind=type(raw).__name__):
                self.assert_invalid(raw, ("session-私密.jsonl",))

    def test_rejects_string_subclasses_and_collapses_internal_failures(self):
        class StringSubclass(str):
            __slots__ = ()

        data = request_data()
        data["session_path"] = StringSubclass(data["session_path"])
        with mock.patch.object(adapters, "decode_event_graph_json", return_value=data):
            self.assert_invalid(encoded(), (data["session_path"],))

        for internal_error in (TypeError("PRIVATE"), ValueError("PRIVATE"), RecursionError("PRIVATE")):
            with self.subTest(kind=type(internal_error).__name__), mock.patch.object(
                adapters,
                "decode_event_graph_json",
                side_effect=internal_error,
            ):
                self.assert_invalid(encoded(), ("PRIVATE", "session-私密.jsonl"))


if __name__ == "__main__":
    unittest.main()
