"""Shared, bounded privacy checks for outward-facing text."""

import re
from typing import Iterable, Iterator
import unicodedata


FORBIDDEN_PUBLIC_TEXT_PATTERNS = (
    re.compile(r"(?:sha256|hmac-sha256):[0-9a-f]{64}\b", re.IGNORECASE),
    re.compile(r"\[redacted:[^\]\n]{1,256}\]", re.IGNORECASE),
    re.compile(
        r"(?<![\w.+-])[\w.+-]{1,128}@[\w-]{1,128}"
        r"(?:\.[\w-]{1,63}){1,8}(?![\w.-])"
    ),
)
MIN_PRIVATE_TEXT_BYTES = 8
PRIVATE_FRAGMENT_CHARS = 24
MAX_FRAGMENT_WINDOWS = 10_000_000
_HASH_BASE = 1_000_003
_HASH_MASK = (1 << 64) - 1


class PrivacyBudgetExceeded(ValueError):
    pass


def normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def contains_forbidden_marker(value: str) -> bool:
    return any(pattern.search(value) for pattern in FORBIDDEN_PUBLIC_TEXT_PATTERNS)


def _window_hashes(value: str, width: int) -> Iterator[int]:
    if len(value) < width:
        return
    power = pow(_HASH_BASE, width - 1, 1 << 64)
    digest = 0
    for character in value[:width]:
        digest = (digest * _HASH_BASE + ord(character) + 1) & _HASH_MASK
    yield digest
    for index in range(width, len(value)):
        digest = (
            (digest - (ord(value[index - width]) + 1) * power) * _HASH_BASE
            + ord(value[index]) + 1
        ) & _HASH_MASK
        yield digest


def contains_private_fragment(
    public_values: Iterable[str],
    private_values: Iterable[str],
) -> bool:
    """Detect exact short values or any 24-character private fragment.

    Work is bounded by at most 24 distinct scalar window widths, rather than by
    public-value × private-value comparisons. A 64-bit hash collision fails
    closed by rejecting outward text.
    """

    public = tuple(normalized(item) for item in public_values)
    private = tuple(
        item for item in (normalized(value) for value in private_values)
        if len(item.encode("utf-8")) >= MIN_PRIVATE_TEXT_BYTES
    )
    by_width = {}
    for item in private:
        width = min(len(item), PRIVATE_FRAGMENT_CHARS)
        by_width.setdefault(width, []).append(item)

    projected = 0
    for width, items in by_width.items():
        projected += sum(max(0, len(item) - width + 1) for item in public)
        projected += sum(max(0, len(item) - width + 1) for item in items)
    if projected > MAX_FRAGMENT_WINDOWS:
        raise PrivacyBudgetExceeded()

    for width, items in by_width.items():
        public_hashes = {
            digest for item in public for digest in _window_hashes(item, width)
        }
        if any(
            digest in public_hashes
            for item in items
            for digest in _window_hashes(item, width)
        ):
            return True
    return False
