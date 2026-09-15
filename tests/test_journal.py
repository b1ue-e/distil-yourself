import hashlib
import json
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller.journal import (  # noqa: E402
    MAX_RECORD_BYTES,
    Journal,
    JournalError,
    canonical_json,
    crc32c,
)


class JournalTest(unittest.TestCase):
    def test_crc32c_matches_standard_check_value(self) -> None:
        self.assertEqual(crc32c(b"123456789"), 0xE3069283)

    def test_append_and_scan_verify_canonical_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            journal = Journal(path)

            first = journal.append({"kind": "lease-acquired", "worker": "local"}, 1)
            second = journal.append({"kind": "prepare", "value": True}, 1)
            scan = journal.scan()

            self.assertEqual([record.sequence for record in scan.records], [1, 2])
            self.assertEqual(scan.records[1].previous_record_hash, first.record_hash)
            self.assertEqual(scan.records[1].record_hash, second.record_hash)
            self.assertEqual(
                scan.records[0].payload_digest,
                hashlib.sha256(
                    canonical_json({"kind": "lease-acquired", "worker": "local"})
                ).hexdigest(),
            )
            self.assertFalse(scan.torn_tail)
            self.assertEqual(scan.valid_bytes, path.stat().st_size)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_scan_reports_and_can_truncate_only_a_torn_final_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            journal = Journal(path)
            journal.append({"kind": "one"}, 1)
            valid_size = path.stat().st_size
            with path.open("ab") as stream:
                stream.write(struct.pack(">I", 40) + b'{"incomplete":')

            scan = journal.scan()
            self.assertTrue(scan.torn_tail)
            self.assertEqual(scan.valid_bytes, valid_size)
            self.assertGreater(path.stat().st_size, valid_size)

            repaired = journal.scan(repair_torn_tail=True)
            self.assertFalse(repaired.torn_tail)
            self.assertEqual(path.stat().st_size, valid_size)

    def test_complete_frame_with_bad_checksum_is_corrupt_not_torn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            journal = Journal(path)
            journal.append({"kind": "one"}, 1)
            data = bytearray(path.read_bytes())
            data[-1] ^= 0x01
            path.write_bytes(data)

            with self.assertRaisesRegex(JournalError, "checksum") as caught:
                journal.scan(repair_torn_tail=True)

            self.assertEqual(caught.exception.code, "checksum-mismatch")
            self.assertEqual(path.read_bytes(), data)

    def test_body_tampering_is_rejected_even_with_recomputed_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            journal = Journal(path)
            journal.append({"kind": "one"}, 1)
            raw = path.read_bytes()
            length = struct.unpack(">I", raw[:4])[0]
            body = json.loads(raw[4 : 4 + length])
            body["payload"] = {"kind": "changed"}
            changed = canonical_json(body)
            rewritten = (
                struct.pack(">I", len(changed))
                + changed
                + struct.pack(">I", crc32c(changed))
            )
            path.write_bytes(rewritten)

            with self.assertRaises(JournalError) as caught:
                journal.scan()

            self.assertEqual(caught.exception.code, "payload-digest-mismatch")

    def test_sequence_hash_chain_and_fencing_are_enforced(self) -> None:
        cases = (
            ("sequence", 3, "sequence-mismatch"),
            ("previous_record_hash", "f" * 64, "hash-chain-mismatch"),
            ("fencing_epoch", 0, "invalid-fencing-epoch"),
        )
        for field, value, code in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "events.log"
                journal = Journal(path)
                journal.append({"kind": "one"}, 1)
                raw = path.read_bytes()
                length = struct.unpack(">I", raw[:4])[0]
                body = json.loads(raw[4 : 4 + length])
                body[field] = value
                changed = canonical_json(body)
                path.write_bytes(
                    struct.pack(">I", len(changed))
                    + changed
                    + struct.pack(">I", crc32c(changed))
                )

                with self.assertRaises(JournalError) as caught:
                    journal.scan()

                self.assertEqual(caught.exception.code, code)

    def test_append_rejects_fencing_regression_and_non_object_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "events.log")
            journal.append({"kind": "one"}, 2)
            with self.assertRaises(JournalError) as caught:
                journal.append({"kind": "two"}, 1)
            self.assertEqual(caught.exception.code, "fencing-regression")

            with self.assertRaises(JournalError) as caught:
                journal.append(["not", "an", "object"], 2)  # type: ignore[arg-type]
            self.assertEqual(caught.exception.code, "payload-object-required")

    def test_oversized_record_is_rejected_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            journal = Journal(path)

            with self.assertRaises(JournalError) as caught:
                journal.append({"padding": "x" * MAX_RECORD_BYTES}, 1)

            self.assertEqual(caught.exception.code, "record-too-large")
            self.assertFalse(path.exists())

    def test_unknown_fields_and_trailing_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            payload = {"kind": "one"}
            body = {
                "schema_version": 1,
                "sequence": 1,
                "fencing_epoch": 1,
                "previous_record_hash": "0" * 64,
                "payload_digest": hashlib.sha256(canonical_json(payload)).hexdigest(),
                "payload": payload,
                "unexpected": True,
            }
            encoded = canonical_json(body)
            path.write_bytes(
                struct.pack(">I", len(encoded))
                + encoded
                + struct.pack(">I", crc32c(encoded))
            )
            os.chmod(path, 0o600)

            with self.assertRaises(JournalError) as caught:
                Journal(path).scan()

            self.assertEqual(caught.exception.code, "invalid-record-fields")

    def test_dangling_journal_symlink_is_not_treated_as_an_empty_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.log"
            path.symlink_to(Path(directory) / "missing-target")

            with self.assertRaises(JournalError) as caught:
                Journal(path).scan()

            self.assertEqual(caught.exception.code, "journal-open-failed")


if __name__ == "__main__":
    unittest.main()
