"""One descriptor boundary for explicitly selected regular input files.

Preserves the event-graph input policy: no links, sparse or special files; no
additional owner/mode/xattr policy (private task persistence is a different
boundary). Paths are walked without normalization or symlink traversal. Closed
prefixes are checked twice against an externally pinned digest on one descriptor.
Appends beyond that prefix are never read; they may change size/timestamps.
"""

import hashlib
import os
import re
import stat
from typing import List, Optional, Tuple

from .adapters import MAX_GRAPH_BYTES


class SourceIOError(ValueError):
    """Code-only error, independent of the CLI and source locator."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _source_path(path: str) -> Tuple[str, List[str]]:
    if type(path) is not str or not path:
        raise SourceIOError("unsafe-source-file")
    try:
        path.encode("utf-8")
    except UnicodeError:
        raise SourceIOError("unsafe-source-file") from None
    if "\x00" in path:
        raise SourceIOError("unsafe-source-file")
    absolute = path.startswith(os.sep)
    raw_components = path.split(os.sep)[1:] if absolute else path.split(os.sep)
    if raw_components[-1] in {"", ".", ".."}:
        raise SourceIOError("unsafe-source-file")
    components = [part for part in raw_components if part not in {"", "."}]
    return (os.sep if absolute else "."), components


def _open_source(path: str) -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise SourceIOError("unsafe-source-file")
    root, components = _source_path(path)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory = None
    descriptor = None
    try:
        directory = os.open(root, directory_flags)
        for component in components[:-1]:
            child = os.open(component, directory_flags, dir_fd=directory)
            try:
                os.close(directory)
            except OSError:
                os.close(child)
                raise
            directory = child
        file_flags = os.O_RDONLY | os.O_NOFOLLOW
        file_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(components[-1], file_flags, dir_fd=directory)
        return descriptor
    except FileNotFoundError:
        raise SourceIOError("source-file-unavailable") from None
    except (OSError, TypeError, ValueError):
        raise SourceIOError("unsafe-source-file") from None
    finally:
        if directory is not None:
            try:
                os.close(directory)
            except OSError:
                if descriptor is not None:
                    os.close(descriptor)
                raise SourceIOError("unsafe-source-file") from None


def _metadata(descriptor: int, code: str):
    try:
        return os.fstat(descriptor)
    except OSError:
        raise SourceIOError(code) from None


def _regular(metadata, max_bytes: Optional[int]) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SourceIOError("unsafe-source-file")
    if max_bytes is not None and metadata.st_size > max_bytes:
        raise SourceIOError("source-file-too-large")
    if metadata.st_nlink != 1 or (metadata.st_size > 0 and hasattr(metadata, "st_blocks")
                                and metadata.st_blocks * 512 < metadata.st_size):
        raise SourceIOError("unsafe-source-file")


def _read(descriptor: int, limit: int) -> bytes:
    content = bytearray()
    while len(content) < limit:
        try:
            chunk = os.read(descriptor, min(1024 * 1024, limit - len(content)))
        except OSError:
            raise SourceIOError("source-file-unavailable") from None
        if not chunk:
            break
        content.extend(chunk)
    return bytes(content)


def read_source(path: str, *, max_bytes: int = MAX_GRAPH_BYTES,
                prefix_length: Optional[int] = None,
                expected_digest: Optional[str] = None) -> bytes:
    """Read a stable file, or exactly [0, prefix_length) without later appends.

    Prefix mode requires both a positive length and a pinned SHA-256 digest.
    Size and timestamp changes alone cannot distinguish an append from an edit,
    so two bounded prefix reads must match that digest, with a stable metadata
    interval for the confirmation pass. A concurrent append during confirmation
    is conservatively rejected; an append before confirmation is excluded.
    Truncation, identity, link and mode changes still fail closed.
    """
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_GRAPH_BYTES:
        raise SourceIOError("invalid-source-bound")
    prefix = prefix_length is not None
    if prefix:
        if (type(prefix_length) is not int or not 0 < prefix_length <= max_bytes
                or type(expected_digest) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest) is None):
            raise SourceIOError("invalid-source-bound")
    elif expected_digest is not None:
        raise SourceIOError("invalid-source-bound")
    descriptor = _open_source(path)
    try:
        metadata = _metadata(descriptor, "unsafe-source-file")
        _regular(metadata, None if prefix else max_bytes)
        if prefix and metadata.st_size < prefix_length:
            raise SourceIOError("input-changed")
        content = _read(descriptor, prefix_length if prefix else max_bytes + 1)
        if len(content) > max_bytes:
            raise SourceIOError("source-file-too-large")
        identity_fields = ("st_dev", "st_ino", "st_nlink", "st_mode")
        if prefix:
            if len(content) != prefix_length or "sha256:" + hashlib.sha256(content).hexdigest() != expected_digest:
                raise SourceIOError("input-changed")
            confirmation_metadata = _metadata(descriptor, "input-changed")
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
            except OSError:
                raise SourceIOError("input-changed") from None
            # Compare digests instead of retaining a second long-lived payload.
            confirmation = _read(descriptor, prefix_length)
            if len(confirmation) != prefix_length or "sha256:" + hashlib.sha256(confirmation).hexdigest() != expected_digest:
                raise SourceIOError("input-changed")
        else:
            identity_fields += ("st_size", "st_mtime_ns", "st_ctime_ns")
            if len(content) != metadata.st_size:
                raise SourceIOError("input-changed")
        current = _metadata(descriptor, "input-changed")
        if any(getattr(current, field) != getattr(metadata, field) for field in identity_fields):
            raise SourceIOError("input-changed")
        if prefix and current.st_size < metadata.st_size:
            raise SourceIOError("input-changed")
        if prefix and any(getattr(current, name) != getattr(confirmation_metadata, name)
                          for name in identity_fields + ("st_size", "st_mtime_ns", "st_ctime_ns")):
            raise SourceIOError("input-changed")
        return content
    finally:
        try:
            os.close(descriptor)
        except OSError:
            raise SourceIOError("unsafe-source-file") from None
