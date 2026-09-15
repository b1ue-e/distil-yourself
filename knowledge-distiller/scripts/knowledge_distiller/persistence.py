"""Crash-consistent local persistence for Knowledge Distiller task state."""

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import uuid

from .journal import Journal, JournalError, JournalRecord, JournalScan, canonical_json
from .state import (
    Event,
    TaskState,
    TransitionFacts,
    facts_to_dict,
    state_from_dict,
    state_to_dict,
    transition,
)


_LOG_NAME = "event-log.frames"
_LEASE_NAME = "lease"
_POINTER_NAME = "current-generation"
_GENERATIONS_NAME = "generations"
_QUARANTINE_NAME = "quarantine"
_MARKER_NAME = "workspace.json"
_MARKER_TEMP_PREFIX = ".workspace.json."
_MARKER_CONTENT = canonical_json(
    {"schema_version": 1, "workspace_type": "knowledge-distiller-task"}
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EMPTY_DIGEST = hashlib.sha256(canonical_json({})).hexdigest()
_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_ROOT_ENTRIES = {
    _MARKER_NAME,
    _LOG_NAME,
    _LEASE_NAME,
    _POINTER_NAME,
    _GENERATIONS_NAME,
    _QUARANTINE_NAME,
    "raw-staging",
}


class TaskPersistenceError(ValueError):
    """Raised when a task workspace fails a persistence invariant."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TaskBusyError(TaskPersistenceError):
    """Raised when another writer currently owns the task lease."""


@dataclass(frozen=True)
class TaskSnapshot:
    state: TaskState
    generation_id: str
    manifest_digest: str
    fencing_epoch: int
    pointer_stale: bool = False
    recovery_required: bool = False


@dataclass(frozen=True)
class _Prepared:
    transaction_id: str
    generation_id: str
    prior_generation_id: Optional[str]
    prior_state_digest: Optional[str]
    expected_state_digest: str
    fencing_epoch: int
    artifact_manifest_digest: Optional[str] = None
    event: str = ""
    facts_digest: str = ""


@dataclass(frozen=True)
class _Committed:
    prepared: _Prepared
    manifest_digest: str
    state_digest: str
    sequence: int


def _raise_from_journal(error: JournalError) -> TaskPersistenceError:
    return TaskPersistenceError("journal-" + error.code)


def _verify_directory_descriptor(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise TaskPersistenceError("workspace-permissions")


def _verify_regular_descriptor(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise TaskPersistenceError("control-file-invalid")


def _fsync_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise TaskPersistenceError("directory-sync-failed") from error


def _open_directory_at(parent_descriptor: int, name: str) -> int:
    descriptor = None
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        _verify_directory_descriptor(descriptor)
        return descriptor
    except TaskPersistenceError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise TaskPersistenceError("workspace-layout-invalid") from error


def _read_file_at(parent_descriptor: int, name: str, limit: int) -> bytes:
    descriptor = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        _verify_regular_descriptor(descriptor)
        content = os.read(descriptor, limit + 1)
    except TaskPersistenceError:
        raise
    except OSError as error:
        raise TaskPersistenceError("control-file-invalid") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(content) > limit:
        raise TaskPersistenceError("control-file-invalid")
    return content


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise TaskPersistenceError("file-write-failed")
        view = view[written:]


def _write_new_file_at(parent_descriptor: int, name: str, content: bytes) -> None:
    descriptor = None
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
        os.fchmod(descriptor, 0o600)
        _verify_regular_descriptor(descriptor)
        _write_all(descriptor, content)
        os.fsync(descriptor)
    except TaskPersistenceError:
        raise
    except OSError as error:
        raise TaskPersistenceError("file-write-failed") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_marker_atomically(root_descriptor: int) -> None:
    temporary = _MARKER_TEMP_PREFIX + uuid.uuid4().hex
    try:
        _write_new_file_at(root_descriptor, temporary, _MARKER_CONTENT)
        os.replace(
            temporary,
            _MARKER_NAME,
            src_dir_fd=root_descriptor,
            dst_dir_fd=root_descriptor,
        )
        _fsync_directory(root_descriptor)
    except TaskPersistenceError:
        raise
    except OSError as error:
        raise TaskPersistenceError("workspace-marker-write-failed") from error
    finally:
        try:
            os.unlink(temporary, dir_fd=root_descriptor)
        except FileNotFoundError:
            pass


def _entry_metadata(parent_descriptor: int, name: str) -> Optional[os.stat_result]:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise TaskPersistenceError("workspace-layout-invalid") from error


class _Workspace:
    """Pinned directory descriptors that confine all task I/O."""

    def __init__(
        self,
        root: Path,
        root_descriptor: int,
        generations_descriptor: int,
        quarantine_descriptor: int,
    ) -> None:
        self.root = root
        self.root_descriptor = root_descriptor
        self.generations_descriptor = generations_descriptor
        self.quarantine_descriptor = quarantine_descriptor

    @classmethod
    def open(cls, root: Path) -> "_Workspace":
        root_descriptor = None
        generations_descriptor = None
        quarantine_descriptor = None
        try:
            root_descriptor = os.open(str(root), _DIRECTORY_FLAGS)
            _verify_directory_descriptor(root_descriptor)
            if _read_file_at(root_descriptor, _MARKER_NAME, 256) != _MARKER_CONTENT:
                raise TaskPersistenceError("workspace-marker-invalid")
            entries = set(os.listdir(root_descriptor))
            if any(
                entry not in _ROOT_ENTRIES
                and not entry.startswith(".current-generation.")
                for entry in entries
            ):
                raise TaskPersistenceError("workspace-layout-invalid")
            generations_descriptor = _open_directory_at(
                root_descriptor, _GENERATIONS_NAME
            )
            quarantine_descriptor = _open_directory_at(
                root_descriptor, _QUARANTINE_NAME
            )
            return cls(
                Path(root),
                root_descriptor,
                generations_descriptor,
                quarantine_descriptor,
            )
        except TaskPersistenceError:
            raise
        except FileNotFoundError as error:
            raise TaskPersistenceError("workspace-missing") from error
        except OSError as error:
            raise TaskPersistenceError("workspace-layout-invalid") from error
        finally:
            if root_descriptor is not None:
                if generations_descriptor is None or quarantine_descriptor is None:
                    os.close(root_descriptor)
            if generations_descriptor is not None and quarantine_descriptor is None:
                os.close(generations_descriptor)

    def close(self) -> None:
        os.close(self.quarantine_descriptor)
        os.close(self.generations_descriptor)
        os.close(self.root_descriptor)

    def journal(self) -> Journal:
        return Journal(Path(_LOG_NAME), directory_fd=self.root_descriptor)


def _read_pointer(workspace: _Workspace) -> Optional[str]:
    if _entry_metadata(workspace.root_descriptor, _POINTER_NAME) is None:
        return None
    content = _read_file_at(workspace.root_descriptor, _POINTER_NAME, 256)
    try:
        value = content.decode("ascii")
    except UnicodeError as error:
        raise TaskPersistenceError("control-file-invalid") from error
    if not value.endswith("\n") or value.count("\n") != 1:
        raise TaskPersistenceError("control-file-invalid")
    generation_id = value[:-1]
    if not _SAFE_ID.fullmatch(generation_id):
        raise TaskPersistenceError("control-file-invalid")
    return generation_id


def _replace_pointer(workspace: _Workspace, generation_id: str) -> None:
    temporary = ".current-generation." + uuid.uuid4().hex
    try:
        _write_new_file_at(
            workspace.root_descriptor,
            temporary,
            (generation_id + "\n").encode("ascii"),
        )
        os.replace(
            temporary,
            _POINTER_NAME,
            src_dir_fd=workspace.root_descriptor,
            dst_dir_fd=workspace.root_descriptor,
        )
        _fsync_directory(workspace.root_descriptor)
    except TaskPersistenceError:
        raise
    except OSError as error:
        raise TaskPersistenceError("pointer-replace-failed") from error
    finally:
        try:
            os.unlink(temporary, dir_fd=workspace.root_descriptor)
        except FileNotFoundError:
            pass


def _parse_json_object(content: bytes, code: str) -> Dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise TaskPersistenceError(code) from error
    if not isinstance(value, dict) or canonical_json(value) != content:
        raise TaskPersistenceError(code)
    return value


def _require_digest(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise TaskPersistenceError("transaction-record-invalid")
    try:
        bytes.fromhex(value)
    except ValueError as error:
        raise TaskPersistenceError("transaction-record-invalid") from error
    return value


def _require_id(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise TaskPersistenceError("transaction-record-invalid")
    return value


def _transactions(
    records: Iterable[JournalRecord],
) -> Tuple[List[_Committed], Dict[str, _Prepared]]:
    prepared: Dict[str, _Prepared] = {}
    committed: List[_Committed] = []
    committed_ids: Set[str] = set()
    generation_ids: Set[str] = set()
    authority_epoch = 0
    for record in records:
        payload = record.payload
        kind = payload.get("kind")
        if kind == "lease-acquired":
            if (
                set(payload) != {"kind", "process_id"}
                or type(payload["process_id"]) is not int
                or record.fencing_epoch != authority_epoch + 1
            ):
                raise TaskPersistenceError("transaction-record-invalid")
            authority_epoch = record.fencing_epoch
            continue
        if authority_epoch == 0 or record.fencing_epoch != authority_epoch:
            raise TaskPersistenceError("transaction-fencing-mismatch")
        if kind == "prepare":
            if set(payload) - {"artifact_manifest_digest"} != {
                "kind",
                "transaction_id",
                "generation_id",
                "prior_generation_id",
                "prior_state_digest",
                "expected_state_digest",
                "event",
                "facts_digest",
            }:
                raise TaskPersistenceError("transaction-record-invalid")
            transaction_id = _require_id(payload["transaction_id"])
            generation_id = _require_id(payload["generation_id"])
            if transaction_id in prepared or generation_id in generation_ids:
                raise TaskPersistenceError("transaction-record-invalid")
            prior_generation = payload["prior_generation_id"]
            prior_digest = payload["prior_state_digest"]
            if prior_generation is not None:
                prior_generation = _require_id(prior_generation)
            if prior_digest is not None:
                prior_digest = _require_digest(prior_digest)
            if not isinstance(payload["event"], str):
                raise TaskPersistenceError("transaction-record-invalid")
            _require_digest(payload["facts_digest"])
            item = _Prepared(
                transaction_id=transaction_id,
                generation_id=generation_id,
                prior_generation_id=prior_generation,
                prior_state_digest=prior_digest,
                expected_state_digest=_require_digest(payload["expected_state_digest"]),
                fencing_epoch=record.fencing_epoch,
                artifact_manifest_digest=(
                    _require_digest(payload["artifact_manifest_digest"])
                    if "artifact_manifest_digest" in payload else None
                ),
                event=payload["event"],
                facts_digest=payload["facts_digest"],
            )
            prepared[transaction_id] = item
            generation_ids.add(generation_id)
            continue
        if kind == "commit":
            if set(payload) != {
                "kind",
                "transaction_id",
                "generation_id",
                "manifest_digest",
                "state_digest",
            }:
                raise TaskPersistenceError("transaction-record-invalid")
            transaction_id = _require_id(payload["transaction_id"])
            generation_id = _require_id(payload["generation_id"])
            item = prepared.get(transaction_id)
            if (
                item is None
                or transaction_id in committed_ids
                or generation_id != item.generation_id
            ):
                raise TaskPersistenceError("commit-without-prepare")
            if record.fencing_epoch != item.fencing_epoch:
                raise TaskPersistenceError("transaction-fencing-mismatch")
            state_digest = _require_digest(payload["state_digest"])
            if state_digest != item.expected_state_digest:
                raise TaskPersistenceError("commit-state-mismatch")
            previous = committed[-1] if committed else None
            expected_prior_generation = (
                previous.prepared.generation_id if previous is not None else None
            )
            expected_prior_digest = previous.state_digest if previous is not None else None
            if (
                item.prior_generation_id != expected_prior_generation
                or item.prior_state_digest != expected_prior_digest
            ):
                raise TaskPersistenceError("generation-lineage-mismatch")
            committed.append(
                _Committed(
                    prepared=item,
                    manifest_digest=_require_digest(payload["manifest_digest"]),
                    state_digest=state_digest,
                    sequence=record.sequence,
                )
            )
            committed_ids.add(transaction_id)
            continue
        raise TaskPersistenceError("transaction-record-invalid")
    pending = {
        transaction_id: item
        for transaction_id, item in prepared.items()
        if transaction_id not in committed_ids
    }
    return committed, pending


def _validate_generation(workspace: _Workspace, committed: _Committed) -> TaskState:
    descriptor = _open_directory_at(
        workspace.generations_descriptor, committed.prepared.generation_id
    )
    try:
        if (committed.prepared.artifact_manifest_digest is None
                and set(os.listdir(descriptor)) != {"state.json", "manifest.json"}):
            raise TaskPersistenceError("generation-layout-invalid")
        state_bytes = _read_file_at(descriptor, "state.json", 1024 * 1024)
        manifest_bytes = _read_file_at(descriptor, "manifest.json", 16 * 1024 * 1024)
        if committed.prepared.artifact_manifest_digest is not None:
            from .private_store import manifest_digest, read_artifacts
            private_manifest = _parse_json_object(manifest_bytes, "generation-manifest-invalid")
            entries = private_manifest.get("artifacts")
            if manifest_digest(entries) != committed.prepared.artifact_manifest_digest:
                raise TaskPersistenceError("generation-digest-mismatch")
            read_artifacts(descriptor, entries)
    finally:
        os.close(descriptor)
    if hashlib.sha256(state_bytes).hexdigest() != committed.state_digest:
        raise TaskPersistenceError("generation-digest-mismatch")
    if hashlib.sha256(manifest_bytes).hexdigest() != committed.manifest_digest:
        raise TaskPersistenceError("generation-digest-mismatch")
    manifest = _parse_json_object(manifest_bytes, "generation-manifest-invalid")
    expected_fields = {"schema_version", "generation_id", "files"}
    if committed.prepared.artifact_manifest_digest is not None:
        expected_fields.add("artifacts")
    if set(manifest) != expected_fields:
        raise TaskPersistenceError("generation-manifest-invalid")
    if (
        manifest["schema_version"] != 1
        or manifest["generation_id"] != committed.prepared.generation_id
    ):
        raise TaskPersistenceError("generation-manifest-invalid")
    expected_file = {
        "path": "state.json",
        "sha256": committed.state_digest,
        "bytes": len(state_bytes),
    }
    if manifest["files"] != [expected_file]:
        raise TaskPersistenceError("generation-manifest-invalid")
    state_payload = _parse_json_object(state_bytes, "generation-state-invalid")
    try:
        return state_from_dict(state_payload)
    except (TypeError, ValueError) as error:
        raise TaskPersistenceError("generation-state-invalid") from error


def _write_generation(
    workspace: _Workspace, generation_id: str, state: TaskState, artifacts=None
) -> Tuple[str, str]:
    try:
        os.mkdir(generation_id, 0o700, dir_fd=workspace.generations_descriptor)
    except OSError as error:
        raise TaskPersistenceError("generation-create-failed") from error
    descriptor = _open_directory_at(workspace.generations_descriptor, generation_id)
    try:
        state_bytes = canonical_json(state_to_dict(state))
        state_digest = hashlib.sha256(state_bytes).hexdigest()
        manifest = {
            "schema_version": 1,
            "generation_id": generation_id,
            "files": [
                {
                    "path": "state.json",
                    "sha256": state_digest,
                    "bytes": len(state_bytes),
                }
            ],
        }
        if artifacts is not None:
            from .private_store import manifest_digest, write_artifacts
            manifest["artifacts"] = [entry for entry, content in artifacts]
            manifest_digest(manifest["artifacts"])
            write_artifacts(descriptor, artifacts)
        manifest_bytes = canonical_json(manifest)
        _write_new_file_at(descriptor, "state.json", state_bytes)
        _write_new_file_at(descriptor, "manifest.json", manifest_bytes)
        if artifacts is not None:
            from .private_store import read_artifacts
            read_artifacts(descriptor, manifest["artifacts"])
        _fsync_directory(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(workspace.generations_descriptor)
    return state_digest, hashlib.sha256(manifest_bytes).hexdigest()


def _quarantine_pending(workspace: _Workspace, item: _Prepared) -> bool:
    if _entry_metadata(workspace.generations_descriptor, item.generation_id) is None:
        return False
    target = item.generation_id + "." + item.transaction_id
    if _entry_metadata(workspace.quarantine_descriptor, target) is not None:
        raise TaskPersistenceError("quarantine-collision")
    try:
        os.rename(
            item.generation_id,
            target,
            src_dir_fd=workspace.generations_descriptor,
            dst_dir_fd=workspace.quarantine_descriptor,
        )
    except OSError as error:
        raise TaskPersistenceError("quarantine-failed") from error
    _fsync_directory(workspace.generations_descriptor)
    _fsync_directory(workspace.quarantine_descriptor)
    return True


def _snapshot_from_records(
    workspace: _Workspace,
    records: Tuple[JournalRecord, ...],
    torn_tail: bool,
    repair: bool,
) -> Optional[TaskSnapshot]:
    committed, pending = _transactions(records)
    committed_generations = {item.prepared.generation_id for item in committed}
    pointer = _read_pointer(workspace)
    if pointer is not None and pointer not in committed_generations:
        raise TaskPersistenceError("pointer-not-committed")
    recovery_required = torn_tail
    for item in pending.values():
        if repair:
            _quarantine_pending(workspace, item)
        elif _entry_metadata(workspace.generations_descriptor, item.generation_id):
            recovery_required = True
    if not committed:
        if pointer is not None:
            raise TaskPersistenceError("pointer-not-committed")
        return None
    states = [_validate_generation(workspace, item) for item in committed]
    latest = committed[-1]
    pointer_stale = pointer != latest.prepared.generation_id
    if repair and pointer_stale:
        _replace_pointer(workspace, latest.prepared.generation_id)
        pointer_stale = False
    return TaskSnapshot(
        state=states[-1],
        generation_id=latest.prepared.generation_id,
        manifest_digest=latest.manifest_digest,
        fencing_epoch=records[-1].fencing_epoch,
        pointer_stale=pointer_stale,
        recovery_required=False if repair else recovery_required,
    )


def _scan_identity(scan: JournalScan) -> Tuple[int, Optional[str], bool]:
    return (
        scan.valid_bytes,
        scan.records[-1].record_hash if scan.records else None,
        scan.torn_tail,
    )


class TaskCoordinator:
    """Exclusive, short-lived writer for one pinned local task workspace."""

    def __init__(self, root: Path, _workspace: Optional[_Workspace] = None) -> None:
        self.root = Path(root)
        self._workspace = _workspace
        self._owns_workspace = _workspace is None
        self._lease_descriptor: Optional[int] = None
        self._fencing_epoch = 0
        self._snapshot: Optional[TaskSnapshot] = None
        self._process_id = os.getpid()

    def __enter__(self) -> "TaskCoordinator":
        if self._workspace is None:
            self._workspace = _Workspace.open(self.root)
        try:
            self._lease_descriptor = self._acquire_lease()
            journal = self._workspace.journal()
            scan = journal.scan(repair_torn_tail=True)
            self._fencing_epoch = (
                scan.records[-1].fencing_epoch + 1 if scan.records else 1
            )
            journal.append(
                {"kind": "lease-acquired", "process_id": os.getpid()},
                self._fencing_epoch,
            )
            self._write_lease_identity()
            current = journal.scan()
            self._snapshot = _snapshot_from_records(
                self._workspace, current.records, current.torn_tail, repair=True
            )
            from .private_store import recover_staging
            recover_staging(self._workspace.root_descriptor)
        except JournalError as error:
            self.__exit__(None, None, None)
            raise _raise_from_journal(error) from error
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._lease_descriptor is not None:
            try:
                fcntl.flock(self._lease_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self._lease_descriptor)
                self._lease_descriptor = None
        if self._workspace is not None and self._owns_workspace:
            self._workspace.close()
            self._workspace = None

    def _acquire_lease(self) -> int:
        if self._workspace is None:
            raise TaskPersistenceError("workspace-not-open")
        descriptor = None
        created = False
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            try:
                descriptor = os.open(
                    _LEASE_NAME,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
                    0o600,
                    dir_fd=self._workspace.root_descriptor,
                )
                created = True
                os.fchmod(descriptor, 0o600)
            except FileExistsError:
                descriptor = os.open(
                    _LEASE_NAME,
                    os.O_RDWR | nofollow,
                    dir_fd=self._workspace.root_descriptor,
                )
            _verify_regular_descriptor(descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise TaskBusyError("task-busy") from error
            if created:
                _fsync_directory(self._workspace.root_descriptor)
            return descriptor
        except TaskPersistenceError:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise TaskPersistenceError("lease-open-failed") from error

    def _write_lease_identity(self) -> None:
        if self._lease_descriptor is None:
            raise TaskPersistenceError("lease-not-held")
        content = canonical_json(
            {"fencing_epoch": self._fencing_epoch, "process_id": os.getpid()}
        )
        try:
            os.ftruncate(self._lease_descriptor, 0)
            os.lseek(self._lease_descriptor, 0, os.SEEK_SET)
            _write_all(self._lease_descriptor, content)
            os.fsync(self._lease_descriptor)
        except OSError as error:
            raise TaskPersistenceError("lease-write-failed") from error

    @property
    def snapshot(self) -> Optional[TaskSnapshot]:
        return self._snapshot

    def initialize(self) -> TaskSnapshot:
        if self._snapshot is not None:
            raise TaskPersistenceError("task-already-initialized")
        return self._commit(TaskState(), "initialize", _EMPTY_DIGEST)

    def transition(self, event: Event, facts: TransitionFacts) -> TaskSnapshot:
        if self._snapshot is None:
            raise TaskPersistenceError("task-not-initialized")
        next_state = transition(self._snapshot.state, event, facts)
        facts_digest = hashlib.sha256(canonical_json(facts_to_dict(facts))).hexdigest()
        return self._commit(next_state, event.value, facts_digest, self._current_artifacts())

    def _assert_writer(self) -> None:
        if (self._workspace is None or self._lease_descriptor is None
                or self._process_id != os.getpid()):
            raise TaskPersistenceError("lease-not-held")
        try:
            scan = self._workspace.journal().scan()
        except JournalError as error:
            raise _raise_from_journal(error) from error
        if (scan.torn_tail or not scan.records
                or scan.records[-1].fencing_epoch != self._fencing_epoch):
            raise TaskPersistenceError("transaction-fencing-mismatch")
        committed, unused = _transactions(scan.records)
        latest = committed[-1].prepared.generation_id if committed else None
        if latest != (self._snapshot.generation_id if self._snapshot else None):
            raise TaskPersistenceError("generation-lineage-mismatch")

    def artifact_transaction(self, transaction_id: str, expected_generation_id: str):
        """Stage a complete private snapshot bound to an explicit input generation."""
        self._assert_writer()
        from .private_store import ArtifactTransaction
        return ArtifactTransaction(self, transaction_id, expected_generation_id)

    def _current_artifacts(self):
        self._assert_writer()
        if self._snapshot is None:
            return None
        committed, unused = _transactions(self._workspace.journal().scan().records)
        _validate_generation(self._workspace, committed[-1])
        descriptor = _open_directory_at(self._workspace.generations_descriptor, self._snapshot.generation_id)
        try:
            manifest = _parse_json_object(
                _read_file_at(descriptor, "manifest.json", 16 * 1024 * 1024),
                "generation-manifest-invalid",
            )
            if "artifacts" not in manifest:
                return None
            from .private_store import read_artifacts
            return read_artifacts(descriptor, manifest["artifacts"])
        finally:
            os.close(descriptor)

    def _commit_artifacts(self, transaction, event, facts, artifacts):
        from .private_store import manifest_digest
        self._assert_writer()
        digest = manifest_digest([entry for entry, content in artifacts])
        facts_digest = hashlib.sha256(canonical_json(facts_to_dict(facts))).hexdigest()
        committed, pending = _transactions(self._workspace.journal().scan().records)
        if committed:
            _validate_generation(self._workspace, committed[-1])
        for item in committed:
            if item.prepared.transaction_id != transaction.transaction_id:
                continue
            prepared = item.prepared
            if (item != committed[-1]
                    or prepared.prior_generation_id != transaction.expected_generation_id
                    or prepared.artifact_manifest_digest != digest
                    or prepared.event != event.value or prepared.facts_digest != facts_digest):
                raise TaskPersistenceError("private-replay-mismatch")
            prior = next((record for record in committed
                          if record.prepared.generation_id == prepared.prior_generation_id), None)
            if prior is None:
                raise TaskPersistenceError("private-replay-mismatch")
            state = transition(_validate_generation(self._workspace, prior), event, facts)
            if hashlib.sha256(canonical_json(state_to_dict(state))).hexdigest() != item.state_digest:
                raise TaskPersistenceError("private-replay-mismatch")
            _validate_generation(self._workspace, item)
            return self._snapshot
        if transaction.transaction_id in pending:
            raise TaskPersistenceError("private-replay-pending")
        if self._snapshot is None or transaction.expected_generation_id != self._snapshot.generation_id:
            raise TaskPersistenceError("generation-lineage-mismatch")
        state = transition(self._snapshot.state, event, facts)
        return self._commit(state, event.value, facts_digest, artifacts, transaction.transaction_id)

    def _commit(self, state: TaskState, event: str, facts_digest: str,
                artifacts=None, transaction_id=None) -> TaskSnapshot:
        if self._workspace is None:
            raise TaskPersistenceError("workspace-not-open")
        self._assert_writer()
        transaction_id = transaction_id or "t-" + uuid.uuid4().hex
        generation_id = "g-" + uuid.uuid4().hex
        state_digest = hashlib.sha256(canonical_json(state_to_dict(state))).hexdigest()
        prior_generation = self._snapshot.generation_id if self._snapshot else None
        prior_state_digest = (
            hashlib.sha256(canonical_json(state_to_dict(self._snapshot.state))).hexdigest()
            if self._snapshot
            else None
        )
        journal = self._workspace.journal()
        try:
            prepare = {
                "kind": "prepare",
                "transaction_id": transaction_id,
                "generation_id": generation_id,
                "prior_generation_id": prior_generation,
                "prior_state_digest": prior_state_digest,
                "expected_state_digest": state_digest,
                "event": event,
                "facts_digest": facts_digest,
            }
            if artifacts is not None:
                from .private_store import manifest_digest
                prepare["artifact_manifest_digest"] = manifest_digest([entry for entry, content in artifacts])
            journal.append(prepare, self._fencing_epoch)
            written_state_digest, manifest_digest = _write_generation(
                self._workspace, generation_id, state, artifacts
            )
            if written_state_digest != state_digest:
                raise TaskPersistenceError("generation-digest-mismatch")
            if artifacts is not None:
                prepared = _Prepared(
                    transaction_id, generation_id, prior_generation, prior_state_digest,
                    state_digest, self._fencing_epoch, prepare["artifact_manifest_digest"],
                    event, facts_digest,
                )
                _validate_generation(self._workspace, _Committed(prepared, manifest_digest, state_digest, 0))
            self._assert_writer()
            journal.append(
                {
                    "kind": "commit",
                    "transaction_id": transaction_id,
                    "generation_id": generation_id,
                    "manifest_digest": manifest_digest,
                    "state_digest": state_digest,
                },
                self._fencing_epoch,
            )
            _replace_pointer(self._workspace, generation_id)
        except JournalError as error:
            raise _raise_from_journal(error) from error
        self._snapshot = TaskSnapshot(
            state=state,
            generation_id=generation_id,
            manifest_digest=manifest_digest,
            fencing_epoch=self._fencing_epoch,
        )
        return self._snapshot


def _prepare_workspace(root: Path) -> _Workspace:
    root = Path(root)
    if root.name in {"", ".", ".."}:
        raise TaskPersistenceError("workspace-path-invalid")
    parent_descriptor = None
    root_descriptor = None
    generations_descriptor = None
    quarantine_descriptor = None
    needs_marker = False
    try:
        parent_descriptor = os.open(str(root.parent), _DIRECTORY_FLAGS)
        metadata = _entry_metadata(parent_descriptor, root.name)
        if metadata is None:
            os.mkdir(root.name, 0o700, dir_fd=parent_descriptor)
            needs_marker = True
        elif stat.S_ISLNK(metadata.st_mode):
            raise TaskPersistenceError("workspace-symlink")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise TaskPersistenceError("workspace-not-directory")
        root_descriptor = os.open(root.name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        entries = set(os.listdir(root_descriptor))
        if not entries:
            needs_marker = True
        marker_temps = {
            entry for entry in entries if entry.startswith(_MARKER_TEMP_PREFIX)
        }
        non_marker_entries = entries - marker_temps - {_MARKER_NAME}
        if not needs_marker and _MARKER_NAME in entries:
            marker_metadata = _entry_metadata(root_descriptor, _MARKER_NAME)
            if marker_metadata is None or (
                not stat.S_ISREG(marker_metadata.st_mode)
                or marker_metadata.st_nlink != 1
                or marker_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(marker_metadata.st_mode) != 0o600
            ):
                raise TaskPersistenceError("workspace-marker-invalid")
            try:
                marker_valid = (
                    _read_file_at(root_descriptor, _MARKER_NAME, 256)
                    == _MARKER_CONTENT
                )
            except TaskPersistenceError:
                marker_valid = False
            if not marker_valid:
                if non_marker_entries:
                    raise TaskPersistenceError("workspace-marker-invalid")
                needs_marker = True
        elif not needs_marker and marker_temps and not non_marker_entries:
            needs_marker = True
        if needs_marker:
            os.fchmod(root_descriptor, 0o700)
            for temporary in marker_temps:
                metadata = _entry_metadata(root_descriptor, temporary)
                if metadata is None or (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise TaskPersistenceError("workspace-marker-invalid")
                os.unlink(temporary, dir_fd=root_descriptor)
            _write_marker_atomically(root_descriptor)
        else:
            if _MARKER_NAME not in entries:
                raise TaskPersistenceError("workspace-not-empty")
            _verify_directory_descriptor(root_descriptor)
            if any(
                entry not in _ROOT_ENTRIES
                and not entry.startswith(".current-generation.")
                for entry in entries
            ):
                raise TaskPersistenceError("workspace-layout-invalid")
        for name in (_GENERATIONS_NAME, _QUARANTINE_NAME):
            if _entry_metadata(root_descriptor, name) is None:
                os.mkdir(name, 0o700, dir_fd=root_descriptor)
            descriptor = _open_directory_at(root_descriptor, name)
            os.close(descriptor)
        _fsync_directory(root_descriptor)
        _fsync_directory(parent_descriptor)
        generations_descriptor = _open_directory_at(
            root_descriptor, _GENERATIONS_NAME
        )
        quarantine_descriptor = _open_directory_at(
            root_descriptor, _QUARANTINE_NAME
        )
        workspace = _Workspace(
            root,
            root_descriptor,
            generations_descriptor,
            quarantine_descriptor,
        )
        root_descriptor = None
        generations_descriptor = None
        quarantine_descriptor = None
        return workspace
    except TaskPersistenceError:
        raise
    except OSError as error:
        raise TaskPersistenceError("workspace-create-failed") from error
    finally:
        if quarantine_descriptor is not None:
            os.close(quarantine_descriptor)
        if generations_descriptor is not None:
            os.close(generations_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def create_task(root: Path) -> TaskSnapshot:
    workspace = _prepare_workspace(Path(root))
    try:
        with TaskCoordinator(Path(root), _workspace=workspace) as coordinator:
            return coordinator.snapshot or coordinator.initialize()
    finally:
        workspace.close()


def inspect_task(root: Path) -> TaskSnapshot:
    workspace = _Workspace.open(Path(root))
    try:
        journal = workspace.journal()
        for _ in range(8):
            before = journal.scan()
            failure = None
            snapshot = None
            try:
                snapshot = _snapshot_from_records(
                    workspace, before.records, before.torn_tail, repair=False
                )
            except TaskPersistenceError as error:
                failure = error
            after = journal.scan()
            if _scan_identity(before) != _scan_identity(after):
                continue
            if failure is not None:
                raise failure
            if snapshot is None:
                raise TaskPersistenceError("task-not-initialized")
            return snapshot
        raise TaskPersistenceError("inspection-concurrent-update")
    except JournalError as error:
        raise _raise_from_journal(error) from error
    finally:
        workspace.close()


def recover_task(root: Path) -> TaskSnapshot:
    with TaskCoordinator(Path(root)) as coordinator:
        return coordinator.snapshot or coordinator.initialize()


def transition_task(
    root: Path, event: Event, facts: TransitionFacts
) -> TaskSnapshot:
    with TaskCoordinator(Path(root)) as coordinator:
        return coordinator.transition(event, facts)
