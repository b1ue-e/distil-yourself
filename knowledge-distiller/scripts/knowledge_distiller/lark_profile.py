"""Pinned, synthetic-only compatibility data for the standalone Lark runtime."""

from dataclasses import dataclass
import copy
import hashlib
from typing import Tuple

from . import adapters, native_adapters
from .journal import canonical_json


STATUS = "synthetic-only"
PRODUCT = "lark"
ADAPTER_VERSION = "1.0.0"
PRODUCT_VERSION = "1.0.86"
NATIVE_SCHEMA_VERSION = "docx-v1-raw-content-v1"
READ_SCOPE_ALTERNATIVES = frozenset(
    {"docx:document:readonly", "docx:document"}
)
METADATA_SCOPE_ALTERNATIVES = frozenset(
    {"drive:drive.metadata:readonly", "drive:drive"}
)
TIMEOUT_SECONDS = 30
CONTROL_STDOUT_LIMIT = 1024 * 1024
RAW_STDOUT_LIMIT = adapters.MAX_GRAPH_BYTES
STDERR_LIMIT = 64 * 1024
ACTIVE_STATUS = "active"
VALID_TOKEN_STATUS = "valid"


def _string(*, const=None, minimum=0, maximum=4096):
    descriptor = {"type": "string", "min_length": minimum, "max_length": maximum}
    if const is not None:
        descriptor["const"] = const
    return descriptor


def _boolean(const=None):
    descriptor = {"type": "boolean"}
    if const is not None:
        descriptor["const"] = const
    return descriptor


def _integer(minimum, maximum):
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


def _array(items, minimum, maximum, *, unique=False):
    return {
        "type": "array",
        "items": items,
        "min_items": minimum,
        "max_items": maximum,
        "unique_items": unique,
    }


def _object(required, properties):
    return {
        "type": "object",
        "required": list(required),
        "properties": properties,
        "additional_properties": False,
    }


_SCOPES = _array(_string(minimum=1, maximum=256), 1, 256, unique=True)
_DISPLAY_SETTING = _object(
    ("show_authors", "show_create_time", "show_pv", "show_uv"),
    {
        "show_authors": _boolean(),
        "show_create_time": _boolean(),
        "show_pv": _boolean(),
        "show_uv": _boolean(),
    },
)
_AUTH_STATUS_SCHEMA = {
    "name": "knowledge-distiller.lark-control.auth-status/v1",
    "schema": _object(
        ("identity", "verified", "status", "token_status", "identities"),
        {
            "identity": _string(const="user"),
            "verified": _boolean(True),
            "status": _string(const=ACTIVE_STATUS),
            "token_status": _string(const=VALID_TOKEN_STATUS),
            "identities": _object(
                ("user",),
                {
                    "user": _object(
                        ("openId", "scope"),
                        {
                            "openId": _string(minimum=1, maximum=256),
                            "scope": _SCOPES,
                            "display_name": _string(maximum=256),
                        },
                    )
                },
            ),
        },
    ),
}
_DOCUMENT_INFO_SCHEMA = {
    "name": "knowledge-distiller.lark-control.document-info/v1",
    "schema": _object(
        ("ok", "identity", "data"),
        {
            "ok": _boolean(True),
            "identity": _string(const="user"),
            "data": _object(
                ("document",),
                {
                    "document": _object(
                        ("document_id", "revision_id"),
                        {
                            "document_id": _string(minimum=27, maximum=27),
                            "revision_id": _integer(1, 2**63 - 1),
                            "title": _string(maximum=4096),
                            "create_time": _string(maximum=32),
                            "update_time": _string(maximum=32),
                            "display_setting": _DISPLAY_SETTING,
                        },
                    )
                },
            ),
        },
    ),
}
_METADATA_ITEM = _object(
    ("doc_token", "doc_type", "owner_id"),
    {
        "doc_token": _string(minimum=27, maximum=27),
        "doc_type": _string(const="docx"),
        "owner_id": _string(minimum=1, maximum=256),
        "title": _string(maximum=4096),
        "create_time": _string(maximum=32),
        "modified_time": _string(maximum=32),
    },
)
_DRIVE_METADATA_SCHEMA = {
    "name": "knowledge-distiller.lark-control.drive-metadata/v1",
    "schema": _object(
        ("ok", "identity", "data"),
        {
            "ok": _boolean(True),
            "identity": _string(const="user"),
            "data": _object(
                ("metas", "failed_list"),
                {
                    "metas": _array(_METADATA_ITEM, 1, 1),
                    "failed_list": _array(
                        _object(("token",), {"token": _string(minimum=1, maximum=256)}),
                        0,
                        0,
                    ),
                },
            ),
        },
    ),
}
_MISSING_SCOPE_SCHEMA = {
    "name": "knowledge-distiller.lark-control.missing-scope/v1",
    "schema": _object(
        ("ok", "identity", "error"),
        {
            "ok": _boolean(False),
            "identity": _string(const="user"),
            "error": _object(
                ("type", "subtype", "code", "missing_scopes"),
                {
                    "type": _string(const="authorization"),
                    "subtype": _string(const="missing_scope"),
                    "code": _integer(1, 2**31 - 1),
                    "missing_scopes": _array(
                        _string(minimum=1, maximum=256), 1, 16, unique=True
                    ),
                },
            ),
        },
    ),
}
_CONTROL_SCHEMAS = {
    "auth-status": _AUTH_STATUS_SCHEMA,
    "document-info": _DOCUMENT_INFO_SCHEMA,
    "drive-metadata": _DRIVE_METADATA_SCHEMA,
    "missing-scope": _MISSING_SCOPE_SCHEMA,
}


def canonical_digest(value):
    """Digest one canonical profile/schema dictionary."""

    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def control_schema_descriptors():
    """Return an isolated copy of the four pinned synthetic contracts."""

    return copy.deepcopy(_CONTROL_SCHEMAS)


_CONTROL_SCHEMA_DIGESTS = {
    name: canonical_digest(descriptor)
    for name, descriptor in _CONTROL_SCHEMAS.items()
}
_PROFILE_RECORD = {
    "profile_version": "knowledge-distiller.lark-compatibility-profile/v1",
    "status": STATUS,
    "product": PRODUCT,
    "adapter_version": ADAPTER_VERSION,
    "product_version": PRODUCT_VERSION,
    "native_schema_version": NATIVE_SCHEMA_VERSION,
    "control_schema_digests": _CONTROL_SCHEMA_DIGESTS,
    "native_schema_digest": native_adapters.LARK_NATIVE_SCHEMA_DIGEST,
    "scope_alternatives": {
        "read": sorted(READ_SCOPE_ALTERNATIVES),
        "metadata": sorted(METADATA_SCOPE_ALTERNATIVES),
    },
    "limits": {
        "timeout_seconds": TIMEOUT_SECONDS,
        "control_stdout_bytes": CONTROL_STDOUT_LIMIT,
        "raw_stdout_bytes": RAW_STDOUT_LIMIT,
        "stderr_bytes": STDERR_LIMIT,
    },
    "literals": {
        "active_status": ACTIVE_STATUS,
        "valid_token_status": VALID_TOKEN_STATUS,
    },
    "endpoint_integrity": {
        "status": "unverified",
        "evidence_reference": None,
        "evidence_digest": None,
    },
    "consistency": {"mode": "unaccepted"},
}


def canonical_profile_record():
    """Return an isolated copy of the machine-readable profile record."""

    return copy.deepcopy(_PROFILE_RECORD)


_COMPUTED_PROFILE_DIGEST = canonical_digest(_PROFILE_RECORD)
PINNED_PROFILE_DIGEST = (
    "sha256:cf7407c4e2f4b2f71f8352ad19eaaaf96b6fbec12a365e7730ff939814439b82"
)


@dataclass(frozen=True, repr=False)
class PinnedLarkProfile:
    status: str
    product: str
    adapter_version: str
    product_version: str
    native_schema_version: str
    control_schema_digests: Tuple[Tuple[str, str], ...]
    native_schema_digest: str
    read_scope_alternatives: Tuple[str, ...]
    metadata_scope_alternatives: Tuple[str, ...]
    timeout_seconds: int
    control_stdout_limit: int
    raw_stdout_limit: int
    stderr_limit: int
    endpoint_integrity_status: str
    consistency_mode: str
    canonical_digest: str


_PINNED_PROFILE = PinnedLarkProfile(
    status=STATUS,
    product=PRODUCT,
    adapter_version=ADAPTER_VERSION,
    product_version=PRODUCT_VERSION,
    native_schema_version=NATIVE_SCHEMA_VERSION,
    control_schema_digests=tuple(sorted(_CONTROL_SCHEMA_DIGESTS.items())),
    native_schema_digest=native_adapters.LARK_NATIVE_SCHEMA_DIGEST,
    read_scope_alternatives=tuple(sorted(READ_SCOPE_ALTERNATIVES)),
    metadata_scope_alternatives=tuple(sorted(METADATA_SCOPE_ALTERNATIVES)),
    timeout_seconds=TIMEOUT_SECONDS,
    control_stdout_limit=CONTROL_STDOUT_LIMIT,
    raw_stdout_limit=RAW_STDOUT_LIMIT,
    stderr_limit=STDERR_LIMIT,
    endpoint_integrity_status="unverified",
    consistency_mode="unaccepted",
    canonical_digest=_COMPUTED_PROFILE_DIGEST,
)


def load_pinned_profile():
    """Load the code-pinned profile, refusing an unreviewed record change."""

    if _COMPUTED_PROFILE_DIGEST != PINNED_PROFILE_DIGEST:
        raise RuntimeError("invalid-lark-profile")
    return _PINNED_PROFILE
