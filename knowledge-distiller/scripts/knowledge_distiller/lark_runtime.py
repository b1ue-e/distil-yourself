"""Pure decoding of one closed local Lark ingestion request."""

from dataclasses import dataclass

from . import adapters, ingestion
from .lark_selector import parse_document_selector


REQUEST_SCHEMA = "knowledge-distiller.local-lark-ingestion-request/v1"
MAX_LOCAL_REQUEST_BYTES = ingestion.MAX_REQUEST_BYTES
REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "transaction_id",
        "expected_generation_id",
        "document_selector",
        "derived_processing_until",
    }
)


class LarkRuntimeError(ValueError):
    """A code-only local Lark request failure."""

    def __init__(self, code):
        self.code = "invalid-lark-request"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class LocalLarkRequest:
    schema_version: str
    transaction_id: str
    expected_generation_id: str
    document_selector: str
    derived_processing_until: int


def decode_local_lark_request(raw):
    """Decode a standalone local Lark request without I/O or deadline evaluation."""

    if type(raw) is not bytes or not raw or len(raw) > MAX_LOCAL_REQUEST_BYTES:
        raise LarkRuntimeError("invalid-lark-request")
    try:
        value = adapters.decode_event_graph_json(raw)
        request = adapters._object(value, REQUEST_FIELDS, "/")
        if (
            type(request["schema_version"]) is not str
            or request["schema_version"] != REQUEST_SCHEMA
        ):
            raise ValueError
        transaction_id = adapters._identifier(request["transaction_id"], "/transaction_id")
        expected_generation_id = adapters._identifier(
            request["expected_generation_id"], "/expected_generation_id"
        )
        selector = parse_document_selector(request["document_selector"])
        derived_processing_until = request["derived_processing_until"]
        if (
            type(derived_processing_until) is not int
            or not 0 <= derived_processing_until <= 2**63 - 1
        ):
            raise ValueError
        return LocalLarkRequest(
            request["schema_version"],
            transaction_id,
            expected_generation_id,
            selector.token,
            derived_processing_until,
        )
    except Exception:
        raise LarkRuntimeError("invalid-lark-request") from None
