import json
import os
import stat
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock
import warnings
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller.journal import Journal  # noqa: E402
from knowledge_distiller.persistence import (  # noqa: E402
    TaskBusyError,
    TaskCoordinator,
    TaskPersistenceError,
    create_task,
    inspect_task,
    recover_task,
    transition_task,
)
import knowledge_distiller.persistence as persistence_module  # noqa: E402
from knowledge_distiller.state import (  # noqa: E402
    Event,
    Mode,
    Phase,
    TransitionFacts,
)


def _frame_boundaries(path: Path):
    data = path.read_bytes()
    offsets = []
    cursor = 0
    while cursor < len(data):
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        cursor += 4 + length + 4
        offsets.append(cursor)
    return offsets


class PersistenceTest(unittest.TestCase):
    def test_private_generation_is_revalidated_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            initial = create_task(root)
            self.assertTrue(callable(getattr(TaskCoordinator, "artifact_transaction", None)))
            original = persistence_module._write_generation
            def substitute_after_write(workspace, generation_id, state, artifacts=None):
                result = original(workspace, generation_id, state, artifacts)
                path = root / "generations" / generation_id / "sources/x"
                path.write_bytes(b"substituted")
                return result
            with TaskCoordinator(root) as coordinator:
                with coordinator.artifact_transaction("private-test", initial.generation_id) as transaction:
                    transaction.add("sources/x", b"synthetic")
                    with mock.patch.object(persistence_module, "_write_generation", side_effect=substitute_after_write):
                        with self.assertRaises(TaskPersistenceError):
                            transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
            commits = [record for record in Journal(root / "event-log.frames").scan().records
                       if record.payload["kind"] == "commit"]
            self.assertEqual(len(commits), 1)

    def test_private_manifest_is_verified_against_commit_before_carry_forward(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            initial = create_task(root)
            self.assertTrue(callable(getattr(TaskCoordinator, "artifact_transaction", None)))
            with TaskCoordinator(root) as coordinator:
                with coordinator.artifact_transaction("private-test", initial.generation_id) as transaction:
                    transaction.add("sources/x", b"synthetic")
                    snapshot = transaction.commit(Event.START_DISCOVER, TransitionFacts(has_seed=True))
                manifest = root / "generations" / snapshot.generation_id / "manifest.json"
                manifest.write_bytes(manifest.read_bytes().replace(b'"schema_version":1', b'"schema_version":2'))
                with self.assertRaises(TaskPersistenceError):
                    coordinator.transition(Event.CANCEL, TransitionFacts())

    def test_create_and_transition_persist_inspectable_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            initial = create_task(root)
            advanced = transition_task(
                root,
                Event.START_DISCOVER,
                TransitionFacts(has_seed=True),
            )
            inspected = inspect_task(root)

            self.assertEqual(initial.state.phase, Phase.INIT)
            self.assertEqual(advanced.state.mode, Mode.DISCOVER)
            self.assertEqual(advanced.state.phase, Phase.SCOUT)
            self.assertEqual(inspected, advanced)
            self.assertNotEqual(initial.generation_id, advanced.generation_id)
            self.assertGreater(advanced.fencing_epoch, initial.fencing_epoch)
            self.assertFalse(inspected.pointer_stale)
            self.assertFalse(inspected.recovery_required)

    def test_each_writer_acquisition_advances_fencing_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            created = create_task(root)
            recovered = recover_task(root)
            recovered_again = recover_task(root)

            self.assertEqual(recovered.fencing_epoch, created.fencing_epoch + 1)
            self.assertEqual(recovered_again.fencing_epoch, recovered.fencing_epoch + 1)

    def test_second_writer_is_rejected_while_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            create_task(root)

            with TaskCoordinator(root):
                with self.assertRaises(TaskBusyError) as caught:
                    recover_task(root)

            self.assertEqual(caught.exception.code, "task-busy")

    def test_workspace_uses_owner_only_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            snapshot = create_task(root)
            generation = root / "generations" / snapshot.generation_id

            for path in (root, root / "generations", root / "quarantine", generation):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700, path)
            for path in (
                root / "workspace.json",
                root / "event-log.frames",
                root / "lease",
                root / "current-generation",
                generation / "state.json",
                generation / "manifest.json",
            ):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)

    def test_read_only_inspection_does_not_advance_log_or_fence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            created = create_task(root)
            before = (root / "event-log.frames").read_bytes()

            first = inspect_task(root)
            second = inspect_task(root)

            self.assertEqual(first, created)
            self.assertEqual(second, created)
            self.assertEqual((root / "event-log.frames").read_bytes(), before)

    def test_recovery_quarantines_prepared_but_uncommitted_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            initial = create_task(root)
            advanced = transition_task(
                root,
                Event.START_DISCOVER,
                TransitionFacts(has_seed=True),
            )
            log = root / "event-log.frames"
            boundaries = _frame_boundaries(log)
            log.write_bytes(log.read_bytes()[: boundaries[-2]])
            os.chmod(log, 0o600)
            (root / "current-generation").write_text(
                initial.generation_id + "\n", encoding="ascii"
            )
            os.chmod(root / "current-generation", 0o600)

            recovered = recover_task(root)

            self.assertEqual(recovered.generation_id, initial.generation_id)
            self.assertFalse((root / "generations" / advanced.generation_id).exists())
            quarantined = list((root / "quarantine").iterdir())
            self.assertEqual(len(quarantined), 1)
            self.assertTrue(quarantined[0].name.startswith(advanced.generation_id + "."))

    def test_recovery_repairs_missing_or_stale_pointer_from_commit(self) -> None:
        for pointer_mode in ("missing", "stale"):
            with self.subTest(pointer_mode=pointer_mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "task"
                initial = create_task(root)
                advanced = transition_task(
                    root,
                    Event.START_DISCOVER,
                    TransitionFacts(has_seed=True),
                )
                pointer = root / "current-generation"
                if pointer_mode == "missing":
                    pointer.unlink()
                else:
                    pointer.write_text(initial.generation_id + "\n", encoding="ascii")
                    os.chmod(pointer, 0o600)

                inspected = inspect_task(root)
                self.assertTrue(inspected.pointer_stale)
                recovered = recover_task(root)

                self.assertEqual(recovered.generation_id, advanced.generation_id)
                self.assertEqual(
                    pointer.read_text(encoding="ascii"), advanced.generation_id + "\n"
                )

    def test_pointer_to_uncommitted_generation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            create_task(root)
            pointer = root / "current-generation"
            pointer.write_text("g-not-committed\n", encoding="ascii")
            os.chmod(pointer, 0o600)

            with self.assertRaises(TaskPersistenceError) as caught:
                inspect_task(root)

            self.assertEqual(caught.exception.code, "pointer-not-committed")

    def test_generation_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            snapshot = create_task(root)
            state_path = root / "generations" / snapshot.generation_id / "state.json"
            state_path.write_text('{"phase":"cancelled"}', encoding="utf-8")
            os.chmod(state_path, 0o600)

            with self.assertRaises(TaskPersistenceError) as caught:
                inspect_task(root)

            self.assertEqual(caught.exception.code, "generation-digest-mismatch")

    def test_symlinked_control_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            create_task(root)
            pointer = root / "current-generation"
            target = root.parent / "pointer-target"
            target.write_text("g-anything\n", encoding="ascii")
            os.chmod(target, 0o600)
            pointer.unlink()
            pointer.symlink_to(target)

            with self.assertRaises(TaskPersistenceError) as caught:
                inspect_task(root)

            self.assertEqual(caught.exception.code, "control-file-invalid")

    def test_recovery_truncates_torn_tail_but_inspection_stays_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            created = create_task(root)
            log = root / "event-log.frames"
            clean_size = log.stat().st_size
            with log.open("ab") as stream:
                stream.write(struct.pack(">I", 50) + b"partial")

            inspected = inspect_task(root)
            self.assertEqual(inspected.generation_id, created.generation_id)
            self.assertTrue(inspected.recovery_required)
            self.assertGreater(log.stat().st_size, clean_size)

            recovered = recover_task(root)
            self.assertFalse(recovered.recovery_required)
            self.assertGreater(log.stat().st_size, clean_size)
            self.assertFalse(Journal(log).scan().torn_tail)

    def test_create_rejects_nonempty_or_symlink_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            occupied = parent / "occupied"
            occupied.mkdir()
            (occupied / "private.txt").write_text("do-not-touch", encoding="utf-8")
            with self.assertRaises(TaskPersistenceError) as caught:
                create_task(occupied)
            self.assertEqual(caught.exception.code, "workspace-not-empty")
            self.assertTrue((occupied / "private.txt").exists())

            target = parent / "target"
            target.mkdir()
            alias = parent / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaises(TaskPersistenceError) as caught:
                create_task(alias)
            self.assertEqual(caught.exception.code, "workspace-symlink")

    def test_nonempty_workspace_check_closes_directory_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            occupied = Path(directory) / "occupied"
            occupied.mkdir()
            (occupied / "entry").write_text("x", encoding="utf-8")

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                with self.assertRaises(TaskPersistenceError):
                    create_task(occupied)

            self.assertEqual(
                [item for item in caught if item.category is ResourceWarning], []
            )

    def test_existing_lease_with_broad_permissions_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            create_task(root)
            os.chmod(root / "lease", 0o644)

            with self.assertRaises(TaskPersistenceError) as caught:
                recover_task(root)

            self.assertEqual(caught.exception.code, "control-file-invalid")
            self.assertEqual(stat.S_IMODE((root / "lease").stat().st_mode), 0o644)

    def test_committed_generation_lineage_must_match_previous_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            create_task(root)
            transition_task(
                root,
                Event.START_DISCOVER,
                TransitionFacts(has_seed=True),
            )
            log = root / "event-log.frames"
            scan = Journal(log).scan()
            prepare = next(
                record
                for record in reversed(scan.records)
                if record.payload["kind"] == "prepare"
            )
            prepare.payload["prior_generation_id"] = None

            # Rebuild through the public Journal API so CRC, sequence, and hash-chain
            # integrity remain valid; only the transaction lineage is malformed.
            payloads = [dict(record.payload) for record in scan.records]
            log.unlink()
            journal = Journal(log)
            for record, payload in zip(scan.records, payloads):
                journal.append(payload, record.fencing_epoch)

            with self.assertRaises(TaskPersistenceError) as caught:
                inspect_task(root)

            self.assertEqual(caught.exception.code, "generation-lineage-mismatch")

    def test_open_coordinator_remains_confined_if_workspace_path_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "task"
            detached = parent / "detached-task"
            external = parent / "external-task"
            create_task(root)
            create_task(external)

            with TaskCoordinator(root) as coordinator:
                root.rename(detached)
                root.symlink_to(external, target_is_directory=True)
                coordinator.transition(
                    Event.START_DISCOVER,
                    TransitionFacts(has_seed=True),
                )

            self.assertEqual(inspect_task(detached).state.phase, Phase.SCOUT)
            self.assertEqual(inspect_task(external).state.phase, Phase.INIT)

    def test_inspection_retries_when_writer_commits_between_log_and_pointer_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            create_task(root)
            original_read_pointer = persistence_module._read_pointer
            transition_finished = threading.Event()
            invoked = False

            def commit_before_pointer_read(task_root):
                nonlocal invoked
                if not invoked:
                    invoked = True
                    transition_task(
                        root,
                        Event.START_DISCOVER,
                        TransitionFacts(has_seed=True),
                    )
                    transition_finished.set()
                return original_read_pointer(task_root)

            with mock.patch.object(
                persistence_module, "_read_pointer", side_effect=commit_before_pointer_read
            ):
                snapshot = inspect_task(root)

            self.assertTrue(transition_finished.is_set())
            self.assertEqual(snapshot.state.phase, Phase.SCOUT)

    def test_create_is_idempotent_after_crash_before_initial_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            abandoned = create_task(root)
            log = root / "event-log.frames"
            boundaries = _frame_boundaries(log)
            log.write_bytes(log.read_bytes()[: boundaries[-2]])
            os.chmod(log, 0o600)
            (root / "current-generation").unlink()

            recovered = create_task(root)

            self.assertEqual(recovered.state.phase, Phase.INIT)
            self.assertNotEqual(recovered.generation_id, abandoned.generation_id)
            self.assertEqual(len(list((root / "quarantine").iterdir())), 1)

    def test_reinitialization_rejects_unrecognized_workspace_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task"
            create_task(root)
            (root / "private.txt").write_text("do-not-adopt", encoding="utf-8")
            os.chmod(root / "private.txt", 0o600)

            with self.assertRaises(TaskPersistenceError) as caught:
                create_task(root)

            self.assertEqual(caught.exception.code, "workspace-layout-invalid")

    def test_create_recovers_incomplete_bootstrap_marker(self) -> None:
        for marker_content in (b"", b'{"schema_version":'):
            with self.subTest(marker_content=marker_content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "task"
                root.mkdir(mode=0o700)
                marker = root / "workspace.json"
                marker.write_bytes(marker_content)
                os.chmod(marker, 0o600)

                snapshot = create_task(root)

                self.assertEqual(snapshot.state.phase, Phase.INIT)
                self.assertEqual(
                    json.loads(marker.read_text(encoding="utf-8")),
                    {
                        "schema_version": 1,
                        "workspace_type": "knowledge-distiller-task",
                    },
                )


if __name__ == "__main__":
    unittest.main()
