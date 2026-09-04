"""Closed-policy validation for generated domain-skill drafts."""

import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple
import unicodedata


MAX_ENTRIES = 128
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024

TEXT_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}

IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

PACKAGE_MANIFESTS = frozenset(
    {
        "cargo.toml",
        "composer.json",
        "gemfile",
        "go.mod",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pyproject.toml",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "yarn.lock",
    }
)

COMMAND_NAMES = (
    "bash|cargo|curl|docker|git|go|java|kubectl|make|node|npm|npx|perl|php|"
    "pip|pip3|powershell|pwsh|python|python3|ruby|sh|sudo|wget|zsh"
)

SHELL_COMMAND_NAMES = (
    "awk|cat|cd|chmod|chown|cp|cut|dd|echo|env|eval|exec|export|find|grep|head|"
    "kill|less|ln|ls|mkdir|mv|printf|pwd|read|rm|rmdir|sed|set|sort|source|tail|"
    "tar|tee|test|touch|tr|unset|xargs|" + COMMAND_NAMES
)

EXECUTABLE_INSTRUCTION_PATTERNS = (
    re.compile(
        r"\b(?:create|generate|synthesize|write|produce|build)\b.{0,80}"
        r"\b(?:code|script|program|package|macro|executable|function|class)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:run|execute|launch|invoke)\b.{0,80}"
        r"\b(?:code|script|command|shell|program|package|macro|executable|tests?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:implement|code|program)\b.{0,80}"
        r"\b(?:python|javascript|typescript|shell|function|class|script|program|package)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"\b(?:call|enter|run|execute|launch|invoke|type|use)\s+(?:the\s+)?(?:{COMMAND_NAMES})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:open|start)\s+(?:a\s+|the\s+)?terminal\b.{{0,80}}\b(?:{COMMAND_NAMES})\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"\b(?:download|fetch|install|invoke).{{0,40}}\b(?:with|using)\s+(?:{COMMAND_NAMES})\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"(?:创建|生成|合成|编写|实现).{0,40}(?:代码|脚本|程序|软件包|宏|函数|类)", re.DOTALL),
    re.compile(r"(?:运行|执行|启动|调用|使用).{0,40}(?:代码|脚本|命令|程序|软件包|宏|bash|shell)", re.DOTALL),
    re.compile(r"(?:用|使用).{0,20}(?:bash|curl|wget|python|node|npm|npx|pip).{0,40}(?:下载|执行|运行|安装)", re.DOTALL),
)

EXECUTABLE_FENCE = re.compile(r"```")

SHELL_PROMPT = re.compile(rf"(?m)^\s*(?:\$|>)\s*(?:{COMMAND_NAMES})\b", re.IGNORECASE)

EXECUTION_VERB = re.compile(
    r"\b(?:execute|install|invoke|launch|run)\b|(?:执行|安装|调用|启动|运行)",
    re.IGNORECASE,
)

SHELL_SYNTAX = (
    re.compile(r"```"),
    re.compile(r"`[^`\n]*\s+[^`\n]*`"),
    re.compile(
        r"(?m)^[ \t]*(?:sudo[ \t]+)?(?:[./~][^\s]+|[A-Za-z0-9_.-]+)"
        r"[ \t]+--?[A-Za-z]"
    ),
    re.compile(
        r"(?m)^[ \t]*[A-Za-z0-9_.~+/-]+[ \t]+(?:https?://|(?:\.|~)?/)"
        r"\S+[ \t]*[.;]?[ \t]*$",
        re.IGNORECASE,
    ),
    re.compile(rf"(?m)^[ \t]*(?:{SHELL_COMMAND_NAMES})\b[ \t]+\S+"),
    re.compile(
        r"(?m)^[ \t]*(?![#>*+-][ \t])(?![A-Za-z0-9_-]+:[ \t])"
        r"[a-z0-9_./~+-]+[ \t]+\S+(?:[ \t]+\S+)*[ \t]*$"
    ),
    re.compile(r"(?:^|\s)(?:&&|\|\||\$\(|#!)(?:\s|$)"),
)

DIRECT_NEGATION = re.compile(
    r"(?:\b(?:do\s+not|don't|never|must\s+not|cannot|can't)"
    r"(?:\s+(?:ever|directly))?\s*|(?:不要|不得|禁止|切勿|不可|不能)\s*)$",
    re.IGNORECASE,
)

DELEGATED_NEGATION = re.compile(
    r"\b(?:do\s+not|don't|never|must\s+not)\s+"
    r"(?:ask|tell|instruct|allow|permit)(?:\s+\w+){0,4}\s+to\s*$",
    re.IGNORECASE,
)

EXPECTED_PLATFORM_ATTRIBUTES = frozenset({"com.apple.provenance"})


@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    sha256: str
    size: int
    media_type: str


class DraftValidationError(ValueError):
    """A closed-policy rejection that never includes file contents."""

    def __init__(self, code: str, path: Optional[str] = None) -> None:
        self.code = code
        self.path = path
        message = code if path is None else f"{code}: {path}"
        super().__init__(message)


def _reject(code: str, relative_path: Optional[str] = None) -> None:
    raise DraftValidationError(code, relative_path)


def _relative(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent}/{name}"


def _register_path(relative_path: str, seen: set) -> None:
    try:
        relative_path.encode("utf-8")
    except UnicodeEncodeError:
        _reject("invalid-path-encoding")
    if unicodedata.normalize("NFC", relative_path) != relative_path:
        _reject("non-normalized-path", relative_path)
    collision_key = unicodedata.normalize("NFC", relative_path).casefold()
    if collision_key in seen:
        _reject("path-collision", relative_path)
    seen.add(collision_key)


def _normalize_attributes(attributes: Sequence[Any]) -> set:
    return {
        os.fsdecode(attribute) if isinstance(attribute, bytes) else attribute
        for attribute in attributes
    }


def _darwin_attributes(descriptor: int, relative_path: str) -> set:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        listxattr = libc.flistxattr
    except (AttributeError, OSError):
        _reject("xattr-check-unavailable", relative_path)
    listxattr.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    listxattr.restype = ctypes.c_ssize_t
    size = listxattr(descriptor, None, 0, 0)
    if size < 0:
        _reject("unreadable-metadata", relative_path)
    if size == 0:
        return set()
    buffer = ctypes.create_string_buffer(size)
    actual = listxattr(descriptor, ctypes.cast(buffer, ctypes.c_void_p), size, 0)
    if actual < 0:
        _reject("unreadable-metadata", relative_path)
    return {
        name.decode("utf-8", errors="surrogateescape")
        for name in buffer.raw[:actual].split(b"\x00")
        if name
    }


def _reject_extended_attributes(descriptor: int, relative_path: str) -> None:
    attributes = None
    if hasattr(os, "listxattr"):
        try:
            attributes = _normalize_attributes(os.listxattr(descriptor))
        except (TypeError, ValueError, NotImplementedError):
            attributes = None
        except OSError:
            _reject("unreadable-metadata", relative_path)
    if attributes is None and sys.platform == "darwin":
        attributes = _darwin_attributes(descriptor, relative_path)
    if attributes is None:
        _reject("xattr-check-unavailable", relative_path)
    if attributes - EXPECTED_PLATFORM_ATTRIBUTES:
        _reject("extended-attribute", relative_path)


def _lstat_at(directory_fd: int, name: str, relative_path: str) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        _reject("unreadable-path", relative_path)
    if stat.S_ISLNK(metadata.st_mode):
        _reject("symlink", relative_path)
    return metadata


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_root(root: Path) -> Tuple[int, os.stat_result]:
    try:
        before = root.lstat()
    except OSError:
        _reject("unreadable-path", ".")
    if stat.S_ISLNK(before.st_mode):
        _reject("symlink", ".")
    if not stat.S_ISDIR(before.st_mode):
        _reject("draft-not-directory")
    try:
        descriptor = os.open(root, _directory_flags())
    except OSError:
        _reject("unreadable-directory", ".")
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        os.close(descriptor)
        _reject("draft-substituted", ".")
    try:
        _reject_extended_attributes(descriptor, ".")
    except DraftValidationError:
        os.close(descriptor)
        raise
    return descriptor, opened


def _open_directory_at(
    parent_fd: int,
    name: str,
    relative_path: str,
) -> Tuple[int, os.stat_result]:
    before = _lstat_at(parent_fd, name, relative_path)
    if not stat.S_ISDIR(before.st_mode):
        _reject("expected-directory", relative_path)
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError:
        _reject("unreadable-directory", relative_path)
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        os.close(descriptor)
        _reject("directory-substituted", relative_path)
    try:
        _reject_extended_attributes(descriptor, relative_path)
    except DraftValidationError:
        os.close(descriptor)
        raise
    return descriptor, opened


def _verify_binding(
    parent_fd: int,
    name: str,
    opened: os.stat_result,
    relative_path: str,
    code: str,
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        _reject(code, relative_path)
    if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        _reject(code, relative_path)


def _verify_root_binding(root: Path, opened: os.stat_result) -> None:
    try:
        current = root.lstat()
    except OSError:
        _reject("draft-substituted", ".")
    if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        _reject("draft-substituted", ".")


def _directory_names(directory_fd: int, relative_path: str, remaining: int) -> List[str]:
    names = []
    try:
        with os.scandir(directory_fd) as iterator:
            for entry in iterator:
                names.append(entry.name)
                if len(names) > remaining:
                    _reject("too-many-entries", relative_path)
    except DraftValidationError:
        raise
    except OSError:
        _reject("unreadable-directory", relative_path)
    return sorted(names, key=os.fsencode)


def _verify_directory_snapshot(
    directory_fd: int,
    relative_path: str,
    opened: os.stat_result,
    expected_names: Sequence[str],
    remaining: int,
) -> None:
    current_names = _directory_names(directory_fd, relative_path, remaining)
    current = os.fstat(directory_fd)
    if (
        list(expected_names) != current_names
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        _reject("directory-mutated", relative_path)


def _read_regular_at(
    directory_fd: int,
    name: str,
    relative_path: str,
    remaining_bytes: int,
) -> bytes:
    before = _lstat_at(directory_fd, name, relative_path)
    if not stat.S_ISREG(before.st_mode):
        _reject("special-file", relative_path)
    if before.st_nlink != 1:
        _reject("hardlink", relative_path)
    if before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        _reject("executable-file", relative_path)
    if before.st_size > MAX_FILE_BYTES:
        _reject("file-too-large", relative_path)
    if before.st_size > remaining_bytes:
        _reject("bundle-too-large", relative_path)
    if (
        before.st_size >= 4096
        and hasattr(before, "st_blocks")
        and before.st_blocks * 512 < before.st_size
    ):
        _reject("sparse-file", relative_path)

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError:
        _reject("unreadable-file", relative_path)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            _reject("special-file", relative_path)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            _reject("file-substituted", relative_path)
        if opened.st_nlink != 1:
            _reject("hardlink", relative_path)
        _reject_extended_attributes(descriptor, relative_path)

        chunks = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                _reject("file-too-large", relative_path)
            if size > remaining_bytes:
                _reject("bundle-too-large", relative_path)
        after = os.fstat(descriptor)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or size != after.st_size
        ):
            _reject("file-substituted", relative_path)
        _verify_binding(
            directory_fd,
            name,
            opened,
            relative_path,
            "file-substituted",
        )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _decode_utf8(content: bytes, relative_path: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        _reject("invalid-utf8", relative_path)


def _match_is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 80) : start]
    prefix = re.split(r"[.!?:,。！？：，\n;；]", prefix)[-1]
    return DIRECT_NEGATION.search(prefix) is not None or DELEGATED_NEGATION.search(prefix) is not None


def _reject_executable_instruction(text: str, relative_path: str) -> None:
    if (
        EXECUTABLE_FENCE.search(text)
        or SHELL_PROMPT.search(text)
        or any(pattern.search(text) for pattern in SHELL_SYNTAX)
    ):
        _reject("executable-instruction", relative_path)
    normalized = re.sub(r"[`*_]", "", text)
    for match in EXECUTION_VERB.finditer(normalized):
        if not _match_is_negated(normalized, match.start()):
            _reject("executable-instruction", relative_path)
    for pattern in EXECUTABLE_INSTRUCTION_PATTERNS:
        for match in pattern.finditer(normalized):
            if not _match_is_negated(normalized, match.start()):
                _reject("executable-instruction", relative_path)


def _reject_invalid_structured_text(text: str, relative_path: str) -> None:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-standard JSON constant")

    try:
        json.loads(text, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError):
        _reject("invalid-structured-reference", relative_path)


def _image_magic_matches(extension: str, content: bytes) -> bool:
    if extension == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return content.startswith(b"\xff\xd8\xff")
    if extension == ".webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _record(relative_path: str, content: bytes, media_type: str) -> ArtifactRecord:
    return ArtifactRecord(
        path=relative_path,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        media_type=media_type,
    )


def _verify_record_content(
    directory_fd: int,
    name: str,
    record: ArtifactRecord,
) -> None:
    content = _read_regular_at(
        directory_fd,
        name,
        record.path,
        MAX_FILE_BYTES,
    )
    if len(content) != record.size or hashlib.sha256(content).hexdigest() != record.sha256:
        _reject("file-substituted", record.path)


def _validate_file_name(name: str, relative_path: str) -> None:
    if name.casefold() in PACKAGE_MANIFESTS:
        _reject("package-manifest", relative_path)


def validate_draft(
    root: Path,
    allowed_asset_digests: FrozenSet[str] = frozenset(),
) -> Tuple[ArtifactRecord, ...]:
    """Validate a generated domain draft and return its deterministic manifest."""

    root = Path(root)
    root_fd, root_metadata = _open_root(root)
    try:
        root_names = _directory_names(root_fd, ".", MAX_ENTRIES)
        if "SKILL.md" not in root_names:
            _reject("missing-skill")

        seen = set()
        records: List[ArtifactRecord] = []
        allowed_digests = frozenset(digest.lower() for digest in allowed_asset_digests)
        entry_count = len(root_names)
        total_bytes = 0

        for name in root_names:
            relative_path = name
            _register_path(relative_path, seen)
            _validate_file_name(name, relative_path)

            if name == "scripts":
                _reject("script-directory", relative_path)
            if name == "SKILL.md":
                content = _read_regular_at(
                    root_fd,
                    name,
                    relative_path,
                    MAX_TOTAL_BYTES - total_bytes,
                )
                text = _decode_utf8(content, relative_path)
                _reject_executable_instruction(text, relative_path)
                records.append(_record(relative_path, content, "text/markdown"))
                total_bytes += len(content)
                continue
            if name not in {"references", "assets"}:
                _lstat_at(root_fd, name, relative_path)
                _reject("unknown-root-entry", relative_path)

            directory_fd, directory_metadata = _open_directory_at(
                root_fd,
                name,
                relative_path,
            )
            try:
                child_records = []
                child_names = _directory_names(
                    directory_fd,
                    relative_path,
                    MAX_ENTRIES - entry_count,
                )
                entry_count += len(child_names)
                for child_name in child_names:
                    child_relative = _relative(relative_path, child_name)
                    _register_path(child_relative, seen)
                    _validate_file_name(child_name, child_relative)
                    metadata = _lstat_at(directory_fd, child_name, child_relative)
                    if stat.S_ISDIR(metadata.st_mode):
                        code = "nested-reference" if name == "references" else "nested-asset"
                        _reject(code, child_relative)

                    extension = Path(child_name).suffix
                    content = _read_regular_at(
                        directory_fd,
                        child_name,
                        child_relative,
                        MAX_TOTAL_BYTES - total_bytes,
                    )
                    if name == "references":
                        if extension not in TEXT_MEDIA_TYPES:
                            _reject("unsupported-reference", child_relative)
                        text = _decode_utf8(content, child_relative)
                        if extension in {".json", ".yaml", ".yml"}:
                            _reject_invalid_structured_text(text, child_relative)
                        _reject_executable_instruction(text, child_relative)
                        record = _record(child_relative, content, TEXT_MEDIA_TYPES[extension])
                    else:
                        if extension not in IMAGE_MEDIA_TYPES:
                            _reject("unsupported-asset", child_relative)
                        if not _image_magic_matches(extension, content):
                            _reject("image-magic-mismatch", child_relative)
                        digest = hashlib.sha256(content).hexdigest()
                        if digest not in allowed_digests:
                            _reject("asset-not-allowlisted", child_relative)
                        record = _record(child_relative, content, IMAGE_MEDIA_TYPES[extension])
                    records.append(record)
                    child_records.append(record)
                    total_bytes += len(content)

                _verify_directory_snapshot(
                    directory_fd,
                    relative_path,
                    directory_metadata,
                    child_names,
                    MAX_ENTRIES,
                )
                for record in child_records:
                    _verify_record_content(directory_fd, Path(record.path).name, record)
                _verify_directory_snapshot(
                    directory_fd,
                    relative_path,
                    directory_metadata,
                    child_names,
                    MAX_ENTRIES,
                )
            finally:
                try:
                    _verify_binding(
                        root_fd,
                        name,
                        directory_metadata,
                        relative_path,
                        "directory-substituted",
                    )
                finally:
                    os.close(directory_fd)

        _verify_directory_snapshot(
            root_fd,
            ".",
            root_metadata,
            root_names,
            MAX_ENTRIES,
        )
        skill_record = next(record for record in records if record.path == "SKILL.md")
        _verify_record_content(root_fd, "SKILL.md", skill_record)
        _verify_directory_snapshot(
            root_fd,
            ".",
            root_metadata,
            root_names,
            MAX_ENTRIES,
        )
        _verify_root_binding(root, root_metadata)
        return tuple(sorted(records, key=lambda record: record.path.encode("utf-8")))
    finally:
        os.close(root_fd)
