"""Shared trusted local-runtime primitives."""

import os
import time

from . import source_io


READ_WINDOW_SECONDS = 300
MAX_DERIVED_SECONDS = 90 * 24 * 60 * 60


class RuntimeSupportError(ValueError):
    """Allowlisted code-only shared runtime diagnostic."""

    ALLOWED = frozenset({"local-identity-unavailable", "unsafe-redaction-key"})

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self.ALLOWED else "local-identity-unavailable"
        super().__init__(self.code)


def read_redaction_key(path, uid: int) -> bytes:
    try:
        raw = source_io.read_source(
            path,
            max_bytes=64,
            expected_owner_uid=uid,
            owner_only=True,
        )
    except source_io.SourceIOError:
        raise RuntimeSupportError("unsafe-redaction-key") from None
    if type(raw) is not bytes or not 32 <= len(raw) <= 64:
        raise RuntimeSupportError("unsafe-redaction-key")
    return raw


def effective_uid() -> int:
    try:
        uid = os.geteuid()
        if type(uid) is not int or not 0 <= uid <= 2**63 - 1:
            raise ValueError
        return uid
    except Exception:
        raise RuntimeSupportError("local-identity-unavailable") from None


def runtime_time() -> int:
    try:
        timestamp = time.time()
        if (
            type(timestamp) not in (int, float)
            or not 0 <= timestamp <= 2**63 - 1
        ):
            raise ValueError
        return int(timestamp)
    except Exception:
        raise RuntimeSupportError("local-identity-unavailable") from None
