"""Opaque private artifacts, confined to coordinator-owned generations and staging.

An artifact transaction declares a complete private snapshot. Callers supply bytes,
never filesystem paths to adopt or delete. Roles are fixed by the declared root.
"""

import hashlib
import os
import re
import stat
import uuid

from .artifacts import DraftValidationError, _reject_extended_attributes
from .journal import canonical_json
from .persistence import (
    TaskPersistenceError, _entry_metadata, _fsync_directory, _open_directory_at,
    _require_id, _verify_directory_descriptor, _verify_regular_descriptor, _write_new_file_at,
)

ROOTS = frozenset({"grants", "sources", "evidence", "provenance", "model", "decisions", "draft-skill"})
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILES = 10000
STAGING = "raw-staging"
_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _fail():
    raise TaskPersistenceError("private-artifact-invalid")


def _parts(path):
    if not isinstance(path, str) or len(path) > 512:
        _fail()
    parts = path.split("/")
    if len(parts) < 2 or len(parts) > 8 or parts[0] not in ROOTS:
        _fail()
    if any(part in {".", ".."} or not _PART.fullmatch(part) for part in parts):
        _fail()
    return parts


def _manifest_validate(entries):
    if not isinstance(entries, list) or len(entries) > MAX_FILES:
        _fail()
    paths = []
    names = {}
    files = set()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256", "content_class", "role"}:
            _fail()
        parts = _parts(entry["path"])
        if entry["content_class"] != "private" or entry["role"] != parts[0]:
            _fail()
        if type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= MAX_FILE_BYTES:
            _fail()
        if not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            _fail()
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            folded = prefix.casefold()
            if folded in names and names[folded] != prefix:
                _fail()
            if index < len(parts) and folded in files:
                _fail()
            names[folded] = prefix
        folded = entry["path"].casefold()
        if folded in files or any(name.startswith(folded + "/") for name in names):
            _fail()
        files.add(folded)
        paths.append(entry["path"])
        total += entry["bytes"]
    if total > MAX_TOTAL_BYTES or paths != sorted(paths):
        _fail()


def manifest_digest(entries):
    _manifest_validate(entries)
    return hashlib.sha256(canonical_json(entries)).hexdigest()


def _signature(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
            metadata.st_uid, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _no_xattrs(descriptor):
    # Reuse the existing platform policy (including macOS system provenance),
    # but never expose a private artifact path through its diagnostics.
    try:
        _reject_extended_attributes(descriptor, "")
    except DraftValidationError:
        raise TaskPersistenceError("private-artifact-invalid") from None


def read_private(parent, name, entry=None):
    """Read a bounded regular file; reject substitution or movement during read."""
    descriptor = None
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            _fail()
        if getattr(before, "st_blocks", 0) * 512 < before.st_size:
            _fail()
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        _verify_regular_descriptor(descriptor)
        _no_xattrs(descriptor)
        if _signature(os.fstat(descriptor)) != _signature(before) or before.st_size > MAX_FILE_BYTES:
            _fail()
        content = bytearray()
        while len(content) <= MAX_FILE_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_FILE_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if (len(content) > MAX_FILE_BYTES
                or _signature(os.fstat(descriptor)) != _signature(before)
                or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _signature(before)):
            _fail()
        if entry is not None and (len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]):
            _fail()
        return bytes(content)
    except (OSError, TaskPersistenceError):
        raise TaskPersistenceError("private-artifact-invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _private_directory(parent, name):
    descriptor = None
    try:
        descriptor = _open_directory_at(parent, name)
        _no_xattrs(descriptor)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise TaskPersistenceError("private-artifact-invalid") from None
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise
    return descriptor


def _parent(root, parts, create=False):
    descriptor = os.dup(root)
    try:
        for part in parts:
            if create and _entry_metadata(descriptor, part) is None:
                os.mkdir(part, 0o700, dir_fd=descriptor)
                _fsync_directory(descriptor)
            child = _private_directory(descriptor, part)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_artifacts(generation, entries):
    """Validate exact directory membership and every declared digest."""
    _manifest_validate(entries)
    expected = {(): {"state.json", "manifest.json"}}
    for entry in entries:
        parts = _parts(entry["path"])
        for index, part in enumerate(parts):
            expected.setdefault(tuple(parts[:index]), set()).add(part)
    for parts, names in expected.items():
        descriptor = _parent(generation, parts)
        try:
            if set(os.listdir(descriptor)) != names:
                _fail()
        finally:
            os.close(descriptor)
    result = []
    for entry in entries:
        parts = _parts(entry["path"])
        descriptor = _parent(generation, parts[:-1])
        try:
            result.append((entry, read_private(descriptor, parts[-1], entry)))
        finally:
            os.close(descriptor)
    return result


def write_artifacts(generation, artifacts):
    for entry, content in artifacts:
        parts = _parts(entry["path"])
        descriptor = _parent(generation, parts[:-1], create=True)
        try:
            _write_new_file_at(descriptor, parts[-1], content)
            _fsync_directory(descriptor)
        finally:
            os.close(descriptor)


def _remove_staging(parent, name):
    """Delete only verified generated slots; never recurse through user paths."""
    if not re.fullmatch(r"s-[0-9a-f]{32}", name):
        _fail()
    descriptor = _private_directory(parent, name)
    try:
        identity = os.fstat(descriptor)
        entries = os.listdir(descriptor)
        if len(entries) > MAX_FILES:
            _fail()
        verified = {}
        for slot in entries:
            if not re.fullmatch(r"f-[0-9a-f]{32}", slot):
                _fail()
            read_private(descriptor, slot)
            verified[slot] = _signature(os.stat(slot, dir_fd=descriptor, follow_symlinks=False))
        for slot, signature in verified.items():
            if _signature(os.stat(slot, dir_fd=descriptor, follow_symlinks=False)) != signature:
                _fail()
            os.unlink(slot, dir_fd=descriptor)
        if os.listdir(descriptor):
            _fail()
        _fsync_directory(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            _fail()
        os.rmdir(name, dir_fd=parent)
        _fsync_directory(parent)
    except OSError:
        raise TaskPersistenceError("private-cleanup-failed") from None
    finally:
        os.close(descriptor)


def recover_staging(root):
    if _entry_metadata(root, STAGING) is None:
        return
    descriptor = _private_directory(root, STAGING)
    try:
        for name in os.listdir(descriptor):
            _remove_staging(descriptor, name)
    finally:
        os.close(descriptor)


class ArtifactTransaction:
    """Same-writer private snapshot, used only inside its context manager.

    The replay key is hashed before journaling. A replay is accepted only for
    the latest committed transaction with identical inputs, state and artifacts.
    Pending transactions fail closed; recovery quarantines their generation.
    """

    def __init__(self, coordinator, transaction_id, expected_generation_id):
        self.coordinator = coordinator
        self.transaction_id = "t-" + hashlib.sha256(_require_id(transaction_id).encode("ascii")).hexdigest()
        self.expected_generation_id = _require_id(expected_generation_id)
        self.epoch = coordinator._fencing_epoch
        self.workspace = coordinator._workspace
        self.parent = None
        self.descriptor = None
        self.name = "s-" + uuid.uuid4().hex
        self.entries = {}

    def _check(self):
        self.coordinator._assert_writer()
        if self.workspace is not self.coordinator._workspace or self.epoch != self.coordinator._fencing_epoch:
            raise TaskPersistenceError("private-writer-mismatch")
        for descriptor in (self.parent, self.descriptor):
            if descriptor is not None:
                _verify_directory_descriptor(descriptor)
                _no_xattrs(descriptor)
        for descriptor, parent, name in (
                (self.parent, self.workspace.root_descriptor, STAGING),
                (self.descriptor, self.parent, self.name)):
            if descriptor is not None:
                metadata = _entry_metadata(parent, name)
                pinned = os.fstat(descriptor)
                if metadata is None or (metadata.st_dev, metadata.st_ino) != (pinned.st_dev, pinned.st_ino):
                    _fail()

    def __enter__(self):
        self._check()
        if self.parent is not None:
            _fail()
        root = self.workspace.root_descriptor
        if _entry_metadata(root, STAGING) is None:
            os.mkdir(STAGING, 0o700, dir_fd=root)
            _fsync_directory(root)
        self.parent = _private_directory(root, STAGING)
        try:
            os.mkdir(self.name, 0o700, dir_fd=self.parent)
            _fsync_directory(self.parent)
            self.descriptor = _private_directory(self.parent, self.name)
        except BaseException:
            self._close()
            raise
        return self

    def add(self, path, content):
        self._check()
        if self.descriptor is None or not isinstance(content, bytes):
            _fail()
        parts = _parts(path)
        entry = {"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                 "content_class": "private", "role": parts[0]}
        proposed = [item[0] for item in self.entries.values()] + [entry]
        manifest_digest(sorted(proposed, key=lambda item: item["path"]))
        slot = "f-" + uuid.uuid4().hex
        _write_new_file_at(self.descriptor, slot, content)
        _fsync_directory(self.descriptor)
        self.entries[path] = (entry, slot)

    def commit(self, event, facts):
        self._check()
        if self.descriptor is None:
            _fail()
        artifacts = [(entry, read_private(self.descriptor, slot, entry))
                     for entry, slot in sorted(self.entries.values(), key=lambda item: item[0]["path"])]
        result = self.coordinator._commit_artifacts(self, event, facts, artifacts)
        self.__exit__(None, None, None)
        return result

    def _close(self):
        for name in ("descriptor", "parent"):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.parent is not None:
                _remove_staging(self.parent, self.name)
        finally:
            self._close()
