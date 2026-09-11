"""Pure validation of pinned synthetic Lark control responses.

This module intentionally contains no process discovery, subprocess execution,
authentication, or network access. The checked-in contracts are synthetic until
a separately approved compatibility probe can establish real CLI shapes.
"""

from dataclasses import dataclass
from typing import Tuple

from . import adapters, lark_profile
from .lark_selector import TOKEN


class LarkTransportError(ValueError):
    """A code-only failure that never carries response data."""

    def __init__(self):
        self.code = "invalid-lark-control-response"
        super().__init__(self.code)


def _fail():
    raise LarkTransportError()


@dataclass(frozen=True, repr=False)
class VerifiedUser:
    open_id: str
    scopes: Tuple[str, ...]

    def __post_init__(self):
        if (
            type(self.open_id) is not str
            or not 1 <= len(self.open_id) <= 256
            or type(self.scopes) is not tuple
            or not 1 <= len(self.scopes) <= 256
            or any(type(scope) is not str or not 1 <= len(scope) <= 256 for scope in self.scopes)
            or len(set(self.scopes)) != len(self.scopes)
        ):
            _fail()


@dataclass(frozen=True, repr=False)
class LarkObservation:
    token: str
    revision: str
    owner_open_id: str

    def __post_init__(self):
        if (
            type(self.token) is not str
            or TOKEN.fullmatch(self.token) is None
            or type(self.revision) is not str
            or not self.revision.isascii()
            or not self.revision.isdecimal()
            or self.revision.startswith("0")
            or not 1 <= int(self.revision) <= 2**63 - 1
            or type(self.owner_open_id) is not str
            or not 1 <= len(self.owner_open_id) <= 256
        ):
            _fail()


def _validate(value, schema):
    kind = schema["type"]
    if kind == "object":
        if type(value) is not dict:
            _fail()
        keys = set(value)
        required = set(schema["required"])
        allowed = set(schema["properties"])
        if not required <= keys or not keys <= allowed:
            _fail()
        for key, item in value.items():
            if type(key) is not str:
                _fail()
            _validate(item, schema["properties"][key])
        return
    if kind == "array":
        if type(value) is not list:
            _fail()
        if not schema["min_items"] <= len(value) <= schema["max_items"]:
            _fail()
        for item in value:
            _validate(item, schema["items"])
        if schema["unique_items"]:
            try:
                if len(set(value)) != len(value):
                    _fail()
            except TypeError:
                _fail()
        return
    expected_type = {"string": str, "boolean": bool, "integer": int}.get(kind)
    if expected_type is None or type(value) is not expected_type:
        _fail()
    if "const" in schema and value != schema["const"]:
        _fail()
    if kind == "string" and not schema["min_length"] <= len(value) <= schema["max_length"]:
        _fail()
    if kind == "integer" and not schema["minimum"] <= value <= schema["maximum"]:
        _fail()


def _decode(raw, schema_name):
    if type(raw) is not bytes or not raw or len(raw) > lark_profile.CONTROL_STDOUT_LIMIT:
        _fail()
    try:
        value = adapters.decode_event_graph_json(raw)
        schema = lark_profile._load_control_schema(schema_name)
        _validate(value, schema)
        return value
    except LarkTransportError:
        raise
    except Exception:
        _fail()


def parse_verified_identity(raw):
    """Return the verified synthetic user projection from a closed response."""

    value = _decode(raw, "auth-status")
    user = value["identities"]["user"]
    return VerifiedUser(user["openId"], tuple(user["scope"]))


def has_required_scopes(scopes):
    """Check exact pinned Docx-read and Drive-metadata capabilities."""

    if (
        type(scopes) is not tuple
        or any(type(scope) is not str for scope in scopes)
        or len(set(scopes)) != len(scopes)
    ):
        return False
    available = set(scopes)
    return bool(available & lark_profile.READ_SCOPE_ALTERNATIVES) and bool(
        available & lark_profile.METADATA_SCOPE_ALTERNATIVES
    )


def parse_observation(document_raw, metadata_raw, expected_token):
    """Combine exact document revision and owner projections."""

    if type(expected_token) is not str or TOKEN.fullmatch(expected_token) is None:
        _fail()
    document = _decode(document_raw, "document-info")["data"]["document"]
    metadata = _decode(metadata_raw, "drive-metadata")["data"]["metas"][0]
    if document["document_id"] != expected_token or metadata["doc_token"] != expected_token:
        _fail()
    return LarkObservation(
        expected_token,
        str(document["revision_id"]),
        metadata["owner_id"],
    )


def parse_missing_scope(raw):
    """Return only scopes proven by the closed typed error response."""

    value = _decode(raw, "missing-scope")
    return tuple(value["error"]["missing_scopes"])
