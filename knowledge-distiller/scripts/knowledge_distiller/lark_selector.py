"""Pure, closed parsing of canonical Lark Docx selectors."""

from dataclasses import dataclass
import hashlib
import re


TOKEN = re.compile(r"[A-Za-z0-9]{27}\Z")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_URL = re.compile(
    r"https://(?P<host>(?:" + _LABEL + r"\.)+(?:larkoffice\.com|larksuite\.com|feishu\.cn))"
    r"/docx/(?P<token>[A-Za-z0-9]{27})\Z"
)
_COMMITMENT_PREFIX = b"lark-docx-selector/v1\0"


class SelectorError(ValueError):
    """A code-only selector validation failure."""

    def __init__(self, code):
        self.code = "invalid-selector"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class ParsedDocumentSelector:
    token: str
    commitment: str


def _token(value):
    if type(value) is not str or TOKEN.fullmatch(value) is None:
        raise SelectorError("invalid-selector")
    return value


def _commitment(token):
    return "sha256:" + hashlib.sha256(
        _COMMITMENT_PREFIX + token.encode("ascii")
    ).hexdigest()


def selector_commitment(token):
    """Return the fixed-domain SHA-256 commitment for one canonical token."""

    return _commitment(_token(token))


def parse_document_selector(value):
    """Parse exactly one token or canonical HTTPS Docx URL without dereferencing it."""

    if type(value) is not str:
        raise SelectorError("invalid-selector")
    if TOKEN.fullmatch(value) is not None:
        token = value
    elif value.isascii():
        match = _URL.fullmatch(value)
        if match is None or len(match.group("host")) > 253:
            raise SelectorError("invalid-selector")
        token = match.group("token")
    else:
        raise SelectorError("invalid-selector")
    return ParsedDocumentSelector(token, _commitment(token))
