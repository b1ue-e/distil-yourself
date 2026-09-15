"""Framed, checksummed, hash-chained journal for durable task coordination."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
from typing import Any, Dict, List, Optional, Tuple


SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 1024 * 1024
_ZERO_HASH = "0" * 64
_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "sequence",
        "fencing_epoch",
        "previous_record_hash",
        "payload_digest",
        "payload",
    }
)


class JournalError(ValueError):
    """Raised when a journal operation would violate an integrity invariant."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JournalRecord:
    schema_version: int
    sequence: int
    fencing_epoch: int
    previous_record_hash: str
    payload_digest: str
    payload: Dict[str, Any]
    record_hash: str


@dataclass(frozen=True)
class JournalScan:
    records: Tuple[JournalRecord, ...]
    valid_bytes: int
    torn_tail: bool


def canonical_json(value: Any) -> bytes:
    """Encode JSON without whitespace or platform-dependent ordering."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise JournalError("invalid-json-value") from error


def crc32c(data: bytes) -> int:
    """Return the Castagnoli CRC-32C of *data*."""

    checksum = 0xFFFFFFFF
    for byte in data:
        checksum ^= byte
        for _ in range(8):
            checksum = (checksum >> 1) ^ (
                0x82F63B78 if checksum & 1 else 0
            )
    return checksum ^ 0xFFFFFFFF


def _no_duplicate_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JournalError("duplicate-json-key")
        result[key] = value
    return result


def _decode_body(body_bytes: bytes) -> Dict[str, Any]:
    try:
        body = json.loads(body_bytes.decode("utf-8"), object_pairs_hook=_no_duplicate_object)
    except JournalError:
        raise
    except (json.JSONDecodeError, UnicodeError) as error:
        raise JournalError("invalid-record-json") from error
    if not isinstance(body, dict):
        raise JournalError("record-object-required")
    if canonical_json(body) != body_bytes:
        raise JournalError("noncanonical-record")
    return body


def _validate_hex_digest(value: Any, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise JournalError(code)
    try:
        bytes.fromhex(value)
    except ValueError as error:
        raise JournalError(code) from error
    return value


def _parse_record(
    body_bytes: bytes,
    expected_sequence: int,
    previous_hash: str,
    previous_fencing_epoch: int,
) -> JournalRecord:
    body = _decode_body(body_bytes)
    if set(body) != _RECORD_FIELDS:
        raise JournalError("invalid-record-fields")
    if body["schema_version"] != SCHEMA_VERSION:
        raise JournalError("unsupported-schema-version")
    if type(body["sequence"]) is not int or body["sequence"] != expected_sequence:
        raise JournalError("sequence-mismatch")
    fencing_epoch = body["fencing_epoch"]
    if type(fencing_epoch) is not int or fencing_epoch < 1:
        raise JournalError("invalid-fencing-epoch")
    if fencing_epoch < previous_fencing_epoch:
        raise JournalError("fencing-regression")
    record_previous_hash = _validate_hex_digest(
        body["previous_record_hash"], "invalid-previous-hash"
    )
    if record_previous_hash != previous_hash:
        raise JournalError("hash-chain-mismatch")
    payload = body["payload"]
    if not isinstance(payload, dict):
        raise JournalError("payload-object-required")
    payload_digest = _validate_hex_digest(
        body["payload_digest"], "invalid-payload-digest"
    )
    if hashlib.sha256(canonical_json(payload)).hexdigest() != payload_digest:
        raise JournalError("payload-digest-mismatch")
    return JournalRecord(
        schema_version=SCHEMA_VERSION,
        sequence=expected_sequence,
        fencing_epoch=fencing_epoch,
        previous_record_hash=record_previous_hash,
        payload_digest=payload_digest,
        payload=payload,
        record_hash=hashlib.sha256(body_bytes).hexdigest(),
    )


def _verify_regular_file(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise JournalError("journal-not-regular")
    if metadata.st_nlink != 1:
        raise JournalError("journal-link-count")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise JournalError("journal-permissions")


def _fsync_parent(path: Path, directory_fd: Optional[int] = None) -> None:
    if directory_fd is not None:
        try:
            os.fsync(directory_fd)
        except OSError as error:
            raise JournalError("journal-parent-sync-failed") from error
        return
    descriptor = None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path.parent), flags)
        os.fsync(descriptor)
    except OSError as error:
        raise JournalError("journal-parent-sync-failed") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


class Journal:
    """Append and verify secret-free task coordination records."""

    def __init__(self, path: Path, directory_fd: Optional[int] = None) -> None:
        self.path = Path(path)
        self._directory_fd = directory_fd
        if directory_fd is not None and (
            self.path.name != str(self.path) or self.path.name in {"", ".", ".."}
        ):
            raise JournalError("invalid-journal-name")

    def _stat(self) -> os.stat_result:
        if self._directory_fd is None:
            return os.lstat(str(self.path))
        return os.stat(
            str(self.path), dir_fd=self._directory_fd, follow_symlinks=False
        )

    def _open(self, flags: int, mode: int = 0o600) -> int:
        if self._directory_fd is None:
            return os.open(str(self.path), flags, mode)
        return os.open(str(self.path), flags, mode, dir_fd=self._directory_fd)

    def scan(self, repair_torn_tail: bool = False) -> JournalScan:
        try:
            self._stat()
        except FileNotFoundError:
            return JournalScan(records=(), valid_bytes=0, torn_tail=False)
        except OSError as error:
            raise JournalError("journal-open-failed") from error
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            descriptor = self._open(flags)
        except OSError as error:
            raise JournalError("journal-open-failed") from error
        records: List[JournalRecord] = []
        valid_bytes = 0
        torn_tail = False
        try:
            _verify_regular_file(descriptor)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                while True:
                    header = stream.read(4)
                    if not header:
                        break
                    if len(header) != 4:
                        torn_tail = True
                        break
                    length = struct.unpack(">I", header)[0]
                    if length == 0 or length > MAX_RECORD_BYTES:
                        raise JournalError("invalid-frame-length")
                    body_bytes = stream.read(length)
                    trailer = stream.read(4)
                    if len(body_bytes) != length or len(trailer) != 4:
                        torn_tail = True
                        break
                    expected_checksum = struct.unpack(">I", trailer)[0]
                    if crc32c(body_bytes) != expected_checksum:
                        raise JournalError("checksum-mismatch")
                    previous_hash = records[-1].record_hash if records else _ZERO_HASH
                    previous_fencing = records[-1].fencing_epoch if records else 0
                    records.append(
                        _parse_record(
                            body_bytes,
                            len(records) + 1,
                            previous_hash,
                            previous_fencing,
                        )
                    )
                    valid_bytes = stream.tell()
        finally:
            if descriptor is not None:
                os.close(descriptor)

        if torn_tail and repair_torn_tail:
            flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            repair_descriptor = None
            try:
                repair_descriptor = self._open(flags)
                _verify_regular_file(repair_descriptor)
                os.ftruncate(repair_descriptor, valid_bytes)
                os.fsync(repair_descriptor)
            except OSError as error:
                raise JournalError("journal-repair-failed") from error
            finally:
                if repair_descriptor is not None:
                    os.close(repair_descriptor)
            torn_tail = False
        return JournalScan(tuple(records), valid_bytes, torn_tail)

    def append(self, payload: Dict[str, Any], fencing_epoch: int) -> JournalRecord:
        if not isinstance(payload, dict):
            raise JournalError("payload-object-required")
        if type(fencing_epoch) is not int or fencing_epoch < 1:
            raise JournalError("invalid-fencing-epoch")
        scan = self.scan()
        if scan.torn_tail:
            raise JournalError("torn-tail-requires-recovery")
        if scan.records and fencing_epoch < scan.records[-1].fencing_epoch:
            raise JournalError("fencing-regression")

        payload_bytes = canonical_json(payload)
        previous_hash = scan.records[-1].record_hash if scan.records else _ZERO_HASH
        body = {
            "schema_version": SCHEMA_VERSION,
            "sequence": len(scan.records) + 1,
            "fencing_epoch": fencing_epoch,
            "previous_record_hash": previous_hash,
            "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
            "payload": payload,
        }
        body_bytes = canonical_json(body)
        if len(body_bytes) > MAX_RECORD_BYTES:
            raise JournalError("record-too-large")
        frame = (
            struct.pack(">I", len(body_bytes))
            + body_bytes
            + struct.pack(">I", crc32c(body_bytes))
        )

        nofollow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        created = False
        try:
            try:
                descriptor = self._open(
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | nofollow,
                )
                created = True
                os.fchmod(descriptor, 0o600)
            except FileExistsError:
                descriptor = self._open(os.O_WRONLY | os.O_APPEND | nofollow)
            _verify_regular_file(descriptor)
            view = memoryview(frame)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise JournalError("journal-write-failed")
                view = view[written:]
            os.fsync(descriptor)
            if created:
                _fsync_parent(self.path, self._directory_fd)
        except JournalError:
            raise
        except OSError as error:
            raise JournalError("journal-write-failed") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return _parse_record(
            body_bytes,
            len(scan.records) + 1,
            previous_hash,
            scan.records[-1].fencing_epoch if scan.records else 0,
        )
