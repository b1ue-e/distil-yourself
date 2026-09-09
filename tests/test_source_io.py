"""Synthetic descriptor-boundary tests; never inspect real user sources."""

import hashlib
import importlib
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge-distiller" / "scripts"))
try:
    source_io = importlib.import_module("knowledge_distiller.source_io")
except ModuleNotFoundError:
    source_io = None


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class SourceIOTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(source_io, "reusable source_io boundary is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "private-source"
        self.path.write_bytes(b"abcdef")

    def read(self, **kwargs):
        return source_io.read_source(str(self.path), max_bytes=32, **kwargs)

    def reject(self, action, code):
        with self.assertRaises(source_io.SourceIOError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.args, (code,))
        self.assertNotIn(str(self.path), repr(vars(caught.exception)))

    def metadata(self, **updates):
        original = self.path.stat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
                  "st_mode", "st_nlink", "st_blocks", "st_uid")
        return SimpleNamespace(**dict({key: getattr(original, key) for key in fields}, **updates))

    def test_regular_read_preserves_existing_mode_owner_and_xattr_policy(self):
        # Event-graph inputs have no owner, mode, or xattr restriction. They are
        # explicitly authorized input files, not private persistence artifacts.
        self.path.chmod(0o644)
        with mock.patch.object(os, "fstat", return_value=self.metadata(st_uid=123456)):
            self.assertEqual(self.read(), b"abcdef")
        if hasattr(os, "listxattr"):
            with mock.patch.object(os, "listxattr", side_effect=AssertionError("unexpected metadata read")):
                self.assertEqual(self.read(), b"abcdef")

    def test_owner_and_owner_only_policy_use_open_descriptor_metadata(self):
        self.path.chmod(0o600)
        current_uid = os.geteuid()
        self.assertEqual(self.read(expected_owner_uid=current_uid, owner_only=True), b"abcdef")
        descriptor_metadata = self.metadata(st_uid=current_uid + 1, st_mode=stat.S_IFREG | 0o600)
        with mock.patch.object(os, "fstat", return_value=descriptor_metadata):
            self.reject(lambda: self.read(expected_owner_uid=current_uid), "source-owner-mismatch")
        self.reject(lambda: self.read(expected_owner_uid=current_uid + 1), "source-owner-mismatch")
        self.path.chmod(0o640)
        self.reject(lambda: self.read(expected_owner_uid=current_uid, owner_only=True), "unsafe-source-file")

    def test_owner_policy_arguments_are_validated_before_open(self):
        cases = (
            ("boolean owner UID", {"expected_owner_uid": True}),
            ("negative owner UID", {"expected_owner_uid": -1}),
            ("string owner UID", {"expected_owner_uid": "0"}),
            ("integer owner-only", {"owner_only": 1}),
            ("owner-only without UID", {"owner_only": True}),
        )
        for name, kwargs in cases:
            with self.subTest(name=name):
                with mock.patch.object(os, "open") as opened:
                    self.reject(lambda kwargs=kwargs: source_io.read_source(str(self.path), **kwargs),
                                "invalid-source-bound")
                    opened.assert_not_called()

    def test_owner_change_during_read_is_rejected(self):
        current_uid = os.geteuid()
        before = self.metadata(st_uid=current_uid, st_mode=stat.S_IFREG | 0o600)
        after = self.metadata(st_uid=current_uid + 1, st_mode=stat.S_IFREG | 0o600)
        with mock.patch.object(os, "fstat", side_effect=(before, after)):
            self.reject(lambda: self.read(expected_owner_uid=current_uid), "input-changed")

    def test_explicit_paths_types_and_ceiling_fail_before_open(self):
        for path in (None, [], True, "", "private-source/", "private-source/.", "private-source/..", "\ud800"):
            with mock.patch.object(os, "open") as opened:
                self.reject(lambda: source_io.read_source(path), "unsafe-source-file")
                opened.assert_not_called()
        for limit in (True, 0, -1, "4", 65 * 1024 * 1024):
            with mock.patch.object(os, "open") as opened:
                self.reject(lambda: source_io.read_source(str(self.path), max_bytes=limit), "invalid-source-bound")
                opened.assert_not_called()

    def test_component_walk_never_follows_final_or_ancestor_links(self):
        alias = self.root / "alias"
        alias.symlink_to(self.path)
        ancestor = self.root / "ancestor"
        ancestor.symlink_to(self.root, target_is_directory=True)
        for path in (alias, ancestor / self.path.name):
            self.reject(lambda: source_io.read_source(str(path)), "unsafe-source-file")
        with mock.patch.object(os, "open", wraps=os.open) as opened:
            self.assertEqual(self.read(), b"abcdef")
        for call in opened.call_args_list:
            self.assertTrue(call.args[1] & os.O_NOFOLLOW)
        self.assertTrue(opened.call_args_list[-1].args[1] & os.O_NONBLOCK)

    def test_missing_directory_hardlink_fifo_and_sparse_are_rejected(self):
        self.reject(lambda: source_io.read_source(str(self.root / "missing")), "source-file-unavailable")
        self.reject(lambda: source_io.read_source(str(self.root)), "unsafe-source-file")
        hard = self.root / "hard"
        os.link(self.path, hard)
        self.reject(self.read, "unsafe-source-file")
        hard.unlink()
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        self.reject(lambda: source_io.read_source(str(fifo)), "unsafe-source-file")
        for metadata in (self.metadata(st_blocks=0), self.metadata(st_mode=stat.S_IFCHR | 0o600)):
            with mock.patch.object(os, "fstat", return_value=metadata):
                self.reject(self.read, "unsafe-source-file")

    def test_byte_ceiling_bounded_reads_and_exact_limit(self):
        self.assertEqual(source_io.read_source(str(self.path), max_bytes=6), b"abcdef")
        self.reject(lambda: source_io.read_source(str(self.path), max_bytes=5), "source-file-too-large")
        with mock.patch.object(os, "read", wraps=os.read) as read:
            self.assertEqual(self.read(), b"abcdef")
        self.assertTrue(all(0 < call.args[1] <= 33 for call in read.call_args_list))
        with mock.patch.object(os, "read", side_effect=(b"a" * 33, b"")):
            self.reject(self.read, "source-file-too-large")

    def test_every_moving_identity_is_rejected_and_descriptor_closed(self):
        before = self.metadata()
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink", "st_mode"):
            after = self.metadata(**{field: getattr(before, field) + 1})
            with mock.patch.object(os, "fstat", side_effect=(before, after)), mock.patch.object(os, "read", wraps=os.read) as read, mock.patch.object(os, "close", wraps=os.close) as close:
                self.reject(self.read, "input-changed")
                self.assertIn(mock.call(read.call_args_list[0].args[0]), close.call_args_list)

    def test_read_and_stat_errors_are_private_and_close_all_descriptors(self):
        for operation in ("read", "fstat"):
            with mock.patch.object(os, operation, side_effect=OSError("PRIVATE SOURCE")), mock.patch.object(os, "open", wraps=os.open) as opened, mock.patch.object(os, "close", wraps=os.close) as close:
                self.reject(self.read, "source-file-unavailable" if operation == "read" else "unsafe-source-file")
                self.assertEqual(opened.call_count, close.call_count)

    def test_interrupted_directory_handoffs_close_owned_fds_without_reclosing_reused_fd(self):
        # A Python signal can raise after close(2) has consumed the descriptor.
        # Reusing its number before raising exposes an unsafe cleanup retry.
        for handoff in ("child-directory", "source-file"):
            opened = set()
            replacements = []
            original_open, original_close = os.open, os.close
            interrupt = KeyboardInterrupt("synthetic-interrupt")
            armed = False
            interrupted = False

            def record_open(name, flags, **kwargs):
                nonlocal armed
                descriptor = original_open(name, flags, **kwargs)
                opened.add(descriptor)
                if handoff == "child-directory":
                    armed = len(opened) == 2
                else:
                    armed = not flags & os.O_DIRECTORY
                return descriptor

            def interrupt_close(descriptor):
                nonlocal interrupted
                original_close(descriptor)
                opened.remove(descriptor)
                if armed and not interrupted:
                    interrupted = True
                    replacement = original_open(str(self.path), os.O_RDONLY)
                    replacements.append(replacement)
                    self.assertEqual(replacement, descriptor)
                    raise interrupt

            try:
                with mock.patch.object(os, "open", side_effect=record_open), mock.patch.object(os, "close", side_effect=interrupt_close):
                    try:
                        self.read()
                    except BaseException as error:
                        self.assertIs(error, interrupt)
                    else:
                        self.fail("interruption must propagate")
                self.assertTrue(interrupted)
                self.assertEqual(opened, set(), "owned file descriptors leaked")
                self.assertEqual(os.read(replacements[0], 6), b"abcdef")
                self.assertEqual(self.read(), b"abcdef")
            finally:
                # Clean even the intentionally reproduced RED leak.
                for descriptor in opened | set(replacements):
                    try:
                        original_close(descriptor)
                    except OSError:
                        pass

    def test_interruption_during_file_close_propagates_without_fd_growth(self):
        original_open, original_close = os.open, os.close
        target = None
        interrupt = KeyboardInterrupt("synthetic-file-close")

        def record_open(name, flags, **kwargs):
            nonlocal target
            descriptor = original_open(name, flags, **kwargs)
            if not flags & os.O_DIRECTORY:
                target = descriptor
            return descriptor

        def interrupt_close(descriptor):
            original_close(descriptor)
            if descriptor == target:
                raise interrupt

        with mock.patch.object(os, "open", side_effect=record_open), mock.patch.object(os, "close", side_effect=interrupt_close):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.read()
        self.assertIs(caught.exception, interrupt)
        with self.assertRaises(OSError):
            os.fstat(target)
        self.assertEqual(self.read(), b"abcdef")

    def test_closed_prefix_excludes_existing_and_mid_read_appends(self):
        original_read = os.read
        appended = False

        def append_after_read(fd, count):
            nonlocal appended
            chunk = original_read(fd, count)
            if not appended:
                with self.path.open("ab") as writer:
                    writer.write(b"PRIVATE LATER APPEND")
                appended = True
            return chunk

        with mock.patch.object(os, "read", side_effect=append_after_read) as read:
            self.assertEqual(self.read(prefix_length=3, expected_digest=digest(b"abc")), b"abc")
        self.assertTrue(all(call.args[1] <= 3 for call in read.call_args_list))

    def test_pinned_prefix_rejects_mutation_truncation_reordering(self):
        for raw in (b"ab", b"bacdef", b"xbcdef"):
            self.path.write_bytes(raw)
            self.reject(lambda: self.read(prefix_length=3, expected_digest=digest(b"abc")), "input-changed")
        self.path.write_bytes(b"abcdef")
        original_read = os.read
        mutated = False

        def mutate_after_read(fd, count):
            nonlocal mutated
            chunk = original_read(fd, count)
            if not mutated:
                self.path.write_bytes(b"xbcdef")
                mutated = True
            return chunk

        with mock.patch.object(os, "read", side_effect=mutate_after_read):
            self.reject(lambda: self.read(prefix_length=3, expected_digest=digest(b"abc")), "input-changed")

    def test_prefix_bounds_and_digest_are_validated_before_io(self):
        for length, expected in ((True, digest(b"a")), (0, digest(b"")), (33, digest(b"a")), (2, None), (None, digest(b"a")), (2, "private")):
            with mock.patch.object(os, "open") as opened:
                self.reject(lambda: self.read(prefix_length=length, expected_digest=expected), "invalid-source-bound")
                opened.assert_not_called()

    def test_prefix_mutation_after_confirmation_read_is_rejected(self):
        original_read = os.read
        reads = 0

        def mutate_after_confirmation(fd, count):
            nonlocal reads
            chunk = original_read(fd, count)
            reads += 1
            if reads == 2:
                self.path.write_bytes(b"xbcdef")
            return chunk

        with mock.patch.object(os, "read", side_effect=mutate_after_confirmation):
            self.reject(lambda: self.read(prefix_length=3, expected_digest=digest(b"abc")), "input-changed")
