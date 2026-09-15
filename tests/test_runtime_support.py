import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import runtime_support, source_io


class RuntimeSupportTest(unittest.TestCase):
    def test_reads_a_private_owner_only_redaction_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = (Path(directory) / "key").resolve()
            key = b"k" * 31 + b"\n"
            path.write_bytes(key)
            path.chmod(0o600)

            self.assertEqual(
                runtime_support.read_redaction_key(str(path), os.geteuid()), key)

    def test_rejects_unsafe_redaction_keys_without_private_details(self):
        private = "/PRIVATE/redaction-key"
        for failure in (
                source_io.SourceIOError("PRIVATE-SOURCE-DETAIL"),
                b"k" * 31, b"k" * 65):
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                    source_io, "read_source",
                    side_effect=failure if isinstance(failure, Exception) else None,
                    return_value=None if isinstance(failure, Exception) else failure):
                with self.assertRaises(runtime_support.RuntimeSupportError) as caught:
                    runtime_support.read_redaction_key(private, 123)
            self.assertEqual(caught.exception.code, "unsafe-redaction-key")
            self.assertNotIn(private, str(caught.exception))
            self.assertNotIn("PRIVATE", str(caught.exception))

        unexpected = RuntimeError("PRIVATE-UNEXPECTED-DETAIL")
        with mock.patch.object(source_io, "read_source", side_effect=unexpected), \
                self.assertRaises(RuntimeError) as caught:
            runtime_support.read_redaction_key(private, 123)
        self.assertIs(caught.exception, unexpected)

    def test_reads_only_valid_builtin_uid_and_time_values(self):
        with mock.patch.object(runtime_support.os, "geteuid", return_value=123), \
                mock.patch.object(runtime_support.time, "time", return_value=1000.75):
            self.assertEqual(runtime_support.effective_uid(), 123)
            self.assertEqual(runtime_support.runtime_time(), 1000)

        for uid in (False, -1, 2**63):
            with self.subTest(uid=uid), mock.patch.object(
                    runtime_support.os, "geteuid", return_value=uid):
                with self.assertRaises(runtime_support.RuntimeSupportError) as uid_error:
                    runtime_support.effective_uid()
            self.assertEqual(uid_error.exception.code, "local-identity-unavailable")

        for timestamp in (False, -0.5, float("nan"), float("inf"), 2**63):
            with self.subTest(timestamp=timestamp), mock.patch.object(
                    runtime_support.time, "time", return_value=timestamp):
                with self.assertRaises(runtime_support.RuntimeSupportError) as time_error:
                    runtime_support.runtime_time()
            self.assertEqual(time_error.exception.code, "local-identity-unavailable")

    def test_errors_are_allowlisted_and_code_only(self):
        for code in ("local-identity-unavailable", "unsafe-redaction-key"):
            error = runtime_support.RuntimeSupportError(code)
            self.assertEqual((error.code, error.args, str(error)), (code, (code,), code))

        error = runtime_support.RuntimeSupportError("PRIVATE-DETAIL")
        self.assertEqual(error.code, "local-identity-unavailable")
        self.assertNotIn("PRIVATE", str(error))
