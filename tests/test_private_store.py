import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))
from knowledge_distiller import persistence
from knowledge_distiller.journal import Journal, canonical_json
from knowledge_distiller.persistence import TaskCoordinator, TaskPersistenceError, create_task, inspect_task, recover_task
from knowledge_distiller.state import Event, TransitionFacts


class PrivateStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "task"
        self.initial = create_task(self.root)

    def transaction(self, coordinator, transaction_id="private-1", prior=None):
        return coordinator.artifact_transaction(transaction_id, prior or self.initial.generation_id)

    def commit(self, payload=b"synthetic-private-selector-excerpt", transaction_id="private-1"):
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator, transaction_id) as transaction:
                transaction.add("sources/snapshot.json", payload)
                return transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))

    def staging_files(self):
        return list((self.root / "raw-staging").glob("*/*"))

    def test_commit_is_owner_only_canonical_and_journal_contains_only_digest(self):
        snapshot = self.commit()
        generation = self.root / "generations" / snapshot.generation_id
        manifest_bytes = (generation / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest_bytes, canonical_json(manifest))
        artifacts = manifest["artifacts"]
        self.assertEqual(artifacts, [{"path": "sources/snapshot.json", "bytes": 34,
                                     "sha256": hashlib.sha256(b"synthetic-private-selector-excerpt").hexdigest(),
                                     "content_class": "private", "role": "sources"}])
        prepare = [r.payload for r in Journal(self.root / "event-log.frames").scan().records
                   if r.payload["kind"] == "prepare"][-1]
        self.assertEqual(prepare["artifact_manifest_digest"], hashlib.sha256(canonical_json(artifacts)).hexdigest())
        self.assertNotIn(b"synthetic-private", (self.root / "event-log.frames").read_bytes())
        for path in generation.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        self.assertFalse(self.staging_files())
        self.assertEqual(inspect_task(self.root), snapshot)

    def test_caller_replay_key_is_hashed_before_journaling(self):
        self.commit(transaction_id="synthetic-private-replay-key")
        self.assertNotIn(b"synthetic-private-replay-key", (self.root / "event-log.frames").read_bytes())

    def test_paths_collisions_and_limits_fail_closed_without_content_in_error(self):
        invalid = ["/sources/x", "sources/../x", "sources/./x", "sources//x", "sources", ".", "../x", "reports/x", "sources/x/../../outside", "sources/\\x"]
        with TaskCoordinator(self.root) as coordinator:
            for path in invalid:
                with self.subTest(path=path), self.transaction(coordinator) as transaction:
                    with self.assertRaises(TaskPersistenceError) as caught:
                        transaction.add(path, b"synthetic-private")
                    self.assertNotIn(path, str(caught.exception))
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/A.json", b"x")
                for path in ("sources/a.json", "sources/A.json/child", "sources/A.json"):
                    with self.assertRaises(TaskPersistenceError):
                        transaction.add(path, b"x")
            from knowledge_distiller import private_store
            for limit in ("MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "MAX_FILES"):
                with self.subTest(limit=limit), mock.patch.object(private_store, limit, 0):
                    with self.transaction(coordinator) as transaction:
                        with self.assertRaises(TaskPersistenceError):
                            transaction.add("sources/x", b"x")
        self.assertFalse(self.staging_files())

    def test_private_parent_translates_os_errors_and_closes_descriptors(self):
        from knowledge_distiller import private_store
        root = os.open(self.root, os.O_RDONLY)
        self.addCleanup(os.close, root)
        with mock.patch("os.dup", side_effect=OSError("synthetic-private-dup")):
            with self.assertRaises(TaskPersistenceError) as caught:
                private_store._parent(root, ["sources"])
        self.assertEqual(str(caught.exception), "private-artifact-invalid")
        original_dup = os.dup
        for target, kwargs in (("os.mkdir", {"create": True}),
                               ("knowledge_distiller.private_store._private_directory", {})):
            duplicated = []
            def duplicate(descriptor):
                copy = original_dup(descriptor)
                duplicated.append(copy)
                return copy
            with mock.patch("os.dup", side_effect=duplicate), mock.patch(target, side_effect=OSError("synthetic-private-io")):
                with self.assertRaises(TaskPersistenceError) as caught:
                    private_store._parent(root, ["sources"], **kwargs)
            self.assertEqual(str(caught.exception), "private-artifact-invalid")
            self.assertEqual(len(duplicated), 1)
            with self.assertRaises(OSError):
                os.fstat(duplicated[0])
        os.fstat(root)

    def test_all_declared_roots_and_nested_paths_are_supported(self):
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                for role in ("grants", "sources", "evidence", "provenance", "model", "decisions", "draft-skill"):
                    transaction.add(role + "/nested/file.json", b"synthetic")
                snapshot = transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
        self.assertEqual(inspect_task(self.root).generation_id, snapshot.generation_id)

    def test_staged_file_tampering_rejects_links_special_permissions_and_changes(self):
        for mutation in ("symlink", "hardlink", "fifo", "permissions", "executable", "content"):
            with self.subTest(mutation=mutation):
                with TaskCoordinator(self.root) as coordinator:
                    transaction = self.transaction(coordinator)
                    transaction.__enter__()
                    transaction.add("sources/x", b"synthetic")
                    path = self.staging_files()[0]
                    original = path.read_bytes()
                    outside = Path(self.directory.name) / "input"
                    outside.write_bytes(original)
                    outside.chmod(0o600)
                    if mutation in ("symlink", "hardlink", "fifo"):
                        path.unlink()
                        if mutation == "symlink":
                            path.symlink_to(outside)
                        elif mutation == "hardlink":
                            os.link(outside, path)
                        else:
                            os.mkfifo(path, 0o600)
                    elif mutation == "permissions":
                        path.chmod(0o644)
                    elif mutation == "executable":
                        path.chmod(0o700)
                    else:
                        path.write_bytes(b"changed")
                    with self.assertRaises(TaskPersistenceError):
                        transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
                    self.assertEqual(inspect_task(self.root).generation_id, self.initial.generation_id)
                    self.assertEqual(outside.read_bytes(), original)
                    # Restore the reserved slot after simulating an external attacker;
                    # cleanup must never follow or delete an unverified replacement.
                    if path.exists() or path.is_symlink():
                        path.unlink()
                    path.write_bytes(original)
                    path.chmod(0o600)
                    transaction.__exit__(None, None, None)

    def test_cleanup_on_failure_cancellation_and_next_start(self):
        for failure in (ValueError, KeyboardInterrupt):
            with TaskCoordinator(self.root) as coordinator:
                with self.assertRaises(failure):
                    with self.transaction(coordinator) as transaction:
                        transaction.add("sources/x", b"synthetic-raw")
                        raise failure()
            self.assertFalse(self.staging_files())
        with TaskCoordinator(self.root) as coordinator:
            transaction = self.transaction(coordinator)
            transaction.__enter__()
            transaction.add("sources/x", b"synthetic-orphan")
            # Simulate process death by releasing descriptors without context cleanup.
            transaction._close()
        self.assertTrue(self.staging_files())
        recover_task(self.root)
        self.assertFalse(self.staging_files())

    def test_exact_replay_requires_current_lineage_and_identical_artifacts(self):
        first = self.commit()
        replay = self.commit()
        self.assertEqual(replay.generation_id, first.generation_id)
        with self.assertRaises(TaskPersistenceError):
            self.commit(b"different")
        with TaskCoordinator(self.root) as coordinator:
            coordinator.transition(Event.CANCEL, TransitionFacts())
        with self.assertRaises(TaskPersistenceError):
            self.commit()

    def test_replay_substitutions_do_not_change_committed_storage(self):
        snapshot = self.commit()
        with TaskCoordinator(self.root) as coordinator:
            def committed_storage():
                return {
                    str(path.relative_to(self.root)): path.read_bytes()
                    for path in self.root.rglob("*")
                    if path.is_file() and "raw-staging" not in path.parts
                }
            before = committed_storage()
            substitutions = (
                (Event.CANCEL, TransitionFacts(has_seed=True), self.initial.generation_id),
                (Event.START_DISCOVER, TransitionFacts(has_seed=False), self.initial.generation_id),
                (Event.START_DISCOVER, TransitionFacts(has_seed=True), snapshot.generation_id),
            )
            for event, facts, prior in substitutions:
                with self.subTest(event=event, prior=prior), self.transaction(coordinator, prior=prior) as transaction:
                    transaction.add("sources/snapshot.json", b"synthetic-private-selector-excerpt")
                    with self.assertRaises(TaskPersistenceError) as caught:
                        transaction.commit(event, facts)
                    self.assertEqual(str(caught.exception), "private-replay-mismatch")
                self.assertEqual(committed_storage(), before)
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/snapshot.json", b"synthetic-private-selector-excerpt")
                replay = transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
            self.assertEqual(replay.generation_id, snapshot.generation_id)
            self.assertEqual(committed_storage(), before)

    def test_orphan_cleanup_checks_aggregate_limit_before_reading_payloads(self):
        from knowledge_distiller import private_store
        with TaskCoordinator(self.root) as coordinator:
            transaction = self.transaction(coordinator)
            transaction.__enter__()
            transaction.add("sources/a", b"1234")
            transaction.add("sources/b", b"5678")
            transaction._close()
        pointer = (self.root / "current-generation").read_bytes()
        with mock.patch.object(private_store, "MAX_TOTAL_BYTES", 7):
            with mock.patch.object(private_store, "read_private", wraps=private_store.read_private) as reads:
                with self.assertRaises(TaskPersistenceError) as caught:
                    recover_task(self.root)
                self.assertEqual(str(caught.exception), "private-staging-limit")
                self.assertEqual(reads.call_count, 0)
            self.assertFalse(self.staging_files())
            self.assertEqual((self.root / "current-generation").read_bytes(), pointer)
            self.assertEqual(recover_task(self.root).generation_id, self.initial.generation_id)

    def test_orphan_cleanup_accepts_exact_aggregate_limit(self):
        from knowledge_distiller import private_store
        with TaskCoordinator(self.root) as coordinator:
            transaction = self.transaction(coordinator)
            transaction.__enter__()
            transaction.add("sources/a", b"1234")
            transaction.add("sources/b", b"5678")
            transaction._close()
        with mock.patch.object(private_store, "MAX_TOTAL_BYTES", 8):
            self.assertEqual(recover_task(self.root).generation_id, self.initial.generation_id)
        self.assertFalse(self.staging_files())

    def test_orphan_directories_share_recovery_byte_budget(self):
        from knowledge_distiller import private_store
        with TaskCoordinator(self.root) as coordinator:
            for index in range(2):
                transaction = self.transaction(coordinator, transaction_id="orphan-" + str(index))
                transaction.__enter__()
                transaction.add("sources/a", b"1234")
                transaction._close()
        with mock.patch.object(private_store, "MAX_TOTAL_BYTES", 7):
            with mock.patch.object(private_store, "read_private", wraps=private_store.read_private) as reads:
                with self.assertRaises(TaskPersistenceError) as caught:
                    recover_task(self.root)
                self.assertEqual(str(caught.exception), "private-staging-limit")
                self.assertLessEqual(reads.call_count, 1)
            self.assertFalse(self.staging_files())
            self.assertEqual(recover_task(self.root).generation_id, self.initial.generation_id)

    def test_normal_transition_preserves_private_bytes_without_mutating_old_generation(self):
        first = self.commit()
        old = self.root / "generations" / first.generation_id
        before = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}
        with TaskCoordinator(self.root) as coordinator:
            latest = coordinator.transition(Event.CANCEL, TransitionFacts())
        new = self.root / "generations" / latest.generation_id
        self.assertEqual((new / "sources/snapshot.json").read_bytes(), before["sources/snapshot.json"])
        self.assertEqual(before, {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()})

    def test_committed_artifact_digest_and_unlisted_entries_are_verified(self):
        snapshot = self.commit()
        generation = self.root / "generations" / snapshot.generation_id
        artifact = generation / "sources/snapshot.json"
        original = artifact.read_bytes()
        artifact.write_bytes(b"tampered")
        with self.assertRaises(TaskPersistenceError):
            inspect_task(self.root)
        artifact.write_bytes(original)
        (generation / "sources/extra").write_bytes(b"extra")
        with self.assertRaises(TaskPersistenceError):
            inspect_task(self.root)

    def test_rejects_file_moved_during_read(self):
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/x", b"synthetic")
                staged = self.staging_files()[0]
                original_read = os.read
                moved = False
                def move_after_read(descriptor, limit):
                    nonlocal moved
                    content = original_read(descriptor, limit)
                    if content == b"synthetic" and not moved:
                        moved = True
                        staged.rename(staged.with_name("moved"))
                        staged.write_bytes(b"synthetic")
                        staged.chmod(0o600)
                    return content
                with mock.patch("os.read", side_effect=move_after_read):
                    with self.assertRaises(TaskPersistenceError):
                        transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
                staged.with_name("moved").unlink()

    def test_rejects_xattrs_on_private_files_and_directories(self):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        def set_attribute(target, remove=False):
            if hasattr(os, "setxattr"):
                if remove:
                    os.removexattr(target, "user.synthetic")
                else:
                    os.setxattr(target, "user.synthetic", b"synthetic")
                return
            descriptor = os.open(target, os.O_RDONLY)
            try:
                if remove:
                    result = libc.fremovexattr(descriptor, b"user.synthetic", 0)
                else:
                    result = libc.fsetxattr(descriptor, b"user.synthetic", b"synthetic", 9, 0, 0)
                self.assertEqual(result, 0)
            finally:
                os.close(descriptor)
        for directory in (False, True):
            with self.subTest(directory=directory), TaskCoordinator(self.root) as coordinator:
                with self.transaction(coordinator) as transaction:
                    transaction.add("sources/x", b"synthetic")
                    target = self.staging_files()[0]
                    if directory:
                        target = target.parent
                    set_attribute(target)
                    try:
                        with self.assertRaises(TaskPersistenceError):
                            transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
                    finally:
                        if target.exists():
                            set_attribute(target, remove=True)
                    self.assertEqual(inspect_task(self.root).generation_id, self.initial.generation_id)

    def test_rejects_xattrs_on_committed_generation_root(self):
        import ctypes
        snapshot = self.commit()
        generation = self.root / "generations" / snapshot.generation_id
        libc = ctypes.CDLL(None, use_errno=True)
        if hasattr(os, "setxattr"):
            os.setxattr(generation, "user.synthetic", b"synthetic")
            remove = lambda: os.removexattr(generation, "user.synthetic")
        else:
            descriptor = os.open(generation, os.O_RDONLY)
            try:
                self.assertEqual(libc.fsetxattr(descriptor, b"user.synthetic", b"synthetic", 9, 0, 0), 0)
            finally:
                os.close(descriptor)
            def remove():
                descriptor = os.open(generation, os.O_RDONLY)
                try:
                    self.assertEqual(libc.fremovexattr(descriptor, b"user.synthetic", 0), 0)
                finally:
                    os.close(descriptor)
        try:
            with self.assertRaises(TaskPersistenceError):
                inspect_task(self.root)
            with self.assertRaises(TaskPersistenceError):
                with TaskCoordinator(self.root) as coordinator:
                    coordinator.transition(Event.CANCEL, TransitionFacts())
        finally:
            remove()

    def test_rejects_staging_directory_permission_change(self):
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/x", b"synthetic")
                staged = self.staging_files()[0].parent
                staged.chmod(0o755)
                try:
                    with self.assertRaises(TaskPersistenceError):
                        transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
                finally:
                    staged.chmod(0o700)
                self.assertEqual(inspect_task(self.root).generation_id, self.initial.generation_id)

    def test_manifest_class_role_and_path_are_closed(self):
        self.assertTrue(callable(getattr(TaskCoordinator, "artifact_transaction", None)))
        from knowledge_distiller.private_store import manifest_digest
        entry = {"path": "sources/a", "bytes": 1, "sha256": "0" * 64,
                 "role": "sources", "content_class": "private"}
        for field, value in (("role", "synthetic-secret"), ("content_class", "synthetic-secret"),
                             ("selector", "synthetic-secret"), ("bytes", True), ("sha256", "A" * 64)):
            invalid = dict(entry)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(TaskPersistenceError):
                manifest_digest([invalid])

    def test_pinned_descriptors_survive_workspace_path_replacement(self):
        detached = self.root.parent / "detached"
        outside = self.root.parent / "outside"
        create_task(outside)
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/x", b"synthetic")
                self.root.rename(detached)
                self.root.symlink_to(outside, target_is_directory=True)
                result = transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
        self.assertEqual(inspect_task(detached).generation_id, result.generation_id)
        self.assertEqual(inspect_task(outside).state, self.initial.state)

    def test_transaction_cannot_outlive_or_switch_writer(self):
        with TaskCoordinator(self.root) as coordinator:
            transaction = self.transaction(coordinator)
            transaction.__enter__()
            transaction.add("sources/x", b"synthetic")
        with TaskCoordinator(self.root):
            with self.assertRaises(TaskPersistenceError):
                transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
        transaction._close()

    def test_moving_staging_directory_is_rejected_before_commit(self):
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/x", b"synthetic")
                staged = self.staging_files()[0].parent
                moved = staged.with_name("moved")
                staged.rename(moved)
                try:
                    with self.assertRaises(TaskPersistenceError):
                        transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
                    self.assertEqual(inspect_task(self.root).generation_id, self.initial.generation_id)
                finally:
                    moved.rename(staged)

    def test_fencing_change_rejects_staged_transaction(self):
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/x", b"synthetic")
                Journal(self.root / "event-log.frames").append(
                    {"kind": "lease-acquired", "process_id": os.getpid()}, coordinator._fencing_epoch + 1)
                with self.assertRaises(TaskPersistenceError):
                    transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
        self.assertFalse(self.staging_files())
        self.assertEqual(recover_task(self.root).generation_id, self.initial.generation_id)

    def test_commit_rejects_tampered_input_generation(self):
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/x", b"synthetic")
                manifest = self.root / "generations" / self.initial.generation_id / "manifest.json"
                original = manifest.read_bytes()
                manifest.write_bytes(original + b" ")
                try:
                    with self.assertRaises(TaskPersistenceError):
                        transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
                finally:
                    manifest.write_bytes(original)

    def test_private_directory_symlink_is_rejected_without_following(self):
        snapshot = self.commit()
        generation = self.root / "generations" / snapshot.generation_id
        sources = generation / "sources"
        moved = self.root.parent / "user-input"
        sources.rename(moved)
        sources.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(TaskPersistenceError):
            inspect_task(self.root)
        self.assertTrue((moved / "snapshot.json").exists())

    def test_socket_and_device_metadata_are_rejected_before_open(self):
        from knowledge_distiller.private_store import read_private
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/x", b"synthetic")
                path = self.staging_files()[0]
                metadata = list(path.stat())
                # Device creation and socket binding are prohibited by the sandbox.
                # Supply only the file type at the lstat boundary; no open may occur.
                for kind in (stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK):
                    metadata[0] = kind | 0o600
                    with self.subTest(kind=kind), mock.patch("os.stat", return_value=os.stat_result(metadata)):
                        with mock.patch("os.open", side_effect=AssertionError("must reject before open")):
                            with self.assertRaises(TaskPersistenceError):
                                read_private(transaction.descriptor, path.name)

    def test_sparse_staging_file_is_rejected(self):
        from knowledge_distiller.private_store import read_private
        with TaskCoordinator(self.root) as coordinator:
            with self.transaction(coordinator) as transaction:
                transaction.add("sources/x", b"synthetic")
                path = self.staging_files()[0]
                with path.open("wb") as stream:
                    stream.truncate(1024 * 1024)
                try:
                    with self.assertRaises(TaskPersistenceError):
                        read_private(transaction.descriptor, path.name)
                finally:
                    path.write_bytes(b"synthetic")

    def test_crashes_before_and_after_commit_use_existing_recovery_protocol(self):
        for stage in ("before", "after"):
            with self.subTest(stage=stage):
                target = "_write_generation" if stage == "before" else "_replace_pointer"
                original = getattr(persistence, target)
                def crash(*args, **kwargs):
                    if stage == "before":
                        original(*args, **kwargs)
                    raise KeyboardInterrupt()
                with mock.patch.object(persistence, target, side_effect=crash):
                    with self.assertRaises(KeyboardInterrupt):
                        self.commit(transaction_id="crash-" + stage)
                self.assertFalse(self.staging_files())
                recovered = recover_task(self.root)
                if stage == "before":
                    self.assertEqual(recovered.generation_id, self.initial.generation_id)
                    self.assertEqual(len(list((self.root / "quarantine").iterdir())), 1)
                else:
                    self.assertNotEqual(recovered.generation_id, self.initial.generation_id)
                    self.assertFalse(recovered.pointer_stale)


if __name__ == "__main__":
    unittest.main()
