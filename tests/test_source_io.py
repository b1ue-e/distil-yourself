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
