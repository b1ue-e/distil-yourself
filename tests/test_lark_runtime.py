"""Closed local Lark request decoder tests."""

import json
import socket
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, MISSING, fields
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import adapters, ingestion, lark_runtime  # noqa: E402


TOKEN = "doxcn1234567890AbCdEfGhIjKl"


def request_data():
    return {
        "schema_version": lark_runtime.REQUEST_SCHEMA,
        "transaction_id": "transaction-1",
        "expected_generation_id": "generation-1",
        "document_selector": "https://tenant.larkoffice.com/docx/" + TOKEN,
        "derived_processing_until": 2_000_000_000,
    }


def encoded(value=None):
    return json.dumps(
        request_data() if value is None else value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class LocalLarkRequestDecoderTest(unittest.TestCase):
    def assert_invalid(self, raw, secrets=()):
        with self.assertRaises(lark_runtime.LarkRuntimeError) as caught:
            lark_runtime.decode_local_lark_request(raw)
        error = caught.exception
        self.assertEqual(error.code, "invalid-lark-request")
        self.assertEqual(error.args, ("invalid-lark-request",))
        self.assertEqual(str(error), "invalid-lark-request")
        self.assertEqual(vars(error), {"code": "invalid-lark-request"})
        for secret in secrets:
            self.assertNotIn(secret, repr(error.args))
            self.assertNotIn(secret, repr(vars(error)))

    def test_decodes_only_the_normalized_token_into_immutable_private_record(self):
        request = lark_runtime.decode_local_lark_request(encoded())

        self.assertEqual(request.schema_version, lark_runtime.REQUEST_SCHEMA)
        self.assertEqual(request.transaction_id, "transaction-1")
        self.assertEqual(request.expected_generation_id, "generation-1")
        self.assertEqual(request.document_selector, TOKEN)
        self.assertEqual(request.derived_processing_until, 2_000_000_000)
        self.assertNotIn(TOKEN, repr(request))
        self.assertEqual(
            tuple(item.name for item in fields(lark_runtime.LocalLarkRequest)),
            ("schema_version", "transaction_id", "expected_generation_id",
             "document_selector", "derived_processing_until"),
        )
        self.assertTrue(all(
            item.default is MISSING and item.default_factory is MISSING
            for item in fields(lark_runtime.LocalLarkRequest)
        ))
        with self.assertRaises(FrozenInstanceError):
            request.document_selector = "private"

    def test_exports_the_closed_decoder_contract(self):
        self.assertEqual(
            lark_runtime.REQUEST_SCHEMA,
            "knowledge-distiller.local-lark-ingestion-request/v1",
        )
        self.assertEqual(lark_runtime.MAX_LOCAL_REQUEST_BYTES, ingestion.MAX_REQUEST_BYTES)
        self.assertEqual(
            lark_runtime.REQUEST_FIELDS,
            frozenset({"schema_version", "transaction_id", "expected_generation_id",
                       "document_selector", "derived_processing_until"}),
        )
        for invalid in ("private detail", None, 1):
            error = lark_runtime.LarkRuntimeError(invalid)
            self.assertEqual(error.code, "invalid-lark-request")
            self.assertEqual(error.args, ("invalid-lark-request",))

    def test_rejects_closed_schema_and_caller_supplied_authority_fields(self):
        invalid = (("schema_version", "knowledge-distiller.local-lark-ingestion-request/v2"),
                   ("transaction_id", ""), ("expected_generation_id", ""),
                   ("document_selector", "PRIVATE-NOT-A-SELECTOR"),
                   ("document_selector", True),
                   ("derived_processing_until", False),
                   ("derived_processing_until", -1),
                   ("derived_processing_until", 2**63))
        for field, value in invalid:
            with self.subTest(field=field):
                data = request_data()
                data[field] = value
                self.assert_invalid(encoded(data), ("PRIVATE-NOT-A-SELECTOR",))
        for field in ("identity", "owner", "revision", "profile", "schema_selection",
                      "active_principal", "caller_uid", "adapter"):
            with self.subTest(field=field):
                data = request_data()
                data[field] = "PRIVATE-CALLER-SUPPLIED"
                self.assert_invalid(encoded(data), ("PRIVATE-CALLER-SUPPLIED",))

    def test_rejects_missing_duplicate_bad_json_unicode_and_resource_bounds(self):
        missing = request_data()
        del missing["document_selector"]
        duplicate = encoded()[:-1] + b',"document_selector":"PRIVATE-DUPLICATE"}'
        depth = adapters.MAX_JSON_NESTING_DEPTH + 1
        too_deep = (b"[" * depth) + b"0" + (b"]" * depth)
        cases = (
            encoded(missing), duplicate, b"{", b"\xffPRIVATE-UTF8",
            b'{"schema_version":"\\ud800"}',
            too_deep, b" " * (lark_runtime.MAX_LOCAL_REQUEST_BYTES + 1),
        )
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                self.assert_invalid(raw, ("PRIVATE-DUPLICATE", "PRIVATE-UTF8"))

    def test_rejects_non_exact_raw_bytes_and_decoded_scalar_subclasses(self):
        class BytesSubclass(bytes):
            __slots__ = ()

        class StringSubclass(str):
            __slots__ = ()

        class IntSubclass(int):
            __slots__ = ()

        for raw in (b"", encoded().decode(), bytearray(encoded()), memoryview(encoded()), BytesSubclass(encoded())):
            with self.subTest(kind=type(raw).__name__):
                self.assert_invalid(raw)
        for field, subclass in (("schema_version", StringSubclass),
                                ("transaction_id", StringSubclass),
                                ("expected_generation_id", StringSubclass),
                                ("document_selector", StringSubclass),
                                ("derived_processing_until", IntSubclass)):
            with self.subTest(field=field), mock.patch.object(adapters, "decode_event_graph_json") as decode:
                data = request_data()
                data[field] = subclass(data[field])
                decode.return_value = data
                self.assert_invalid(encoded())

    def test_decoding_never_uses_network_subprocess_or_filesystem(self):
        forbidden = RuntimeError("forbidden I/O")
        with mock.patch("socket.create_connection", side_effect=forbidden), \
                mock.patch("subprocess.run", side_effect=forbidden), \
                mock.patch("builtins.open", side_effect=forbidden), \
                mock.patch.object(Path, "iterdir", side_effect=forbidden):
            self.assertEqual(
                lark_runtime.decode_local_lark_request(encoded()).document_selector,
                TOKEN,
            )


if __name__ == "__main__":
    unittest.main()
