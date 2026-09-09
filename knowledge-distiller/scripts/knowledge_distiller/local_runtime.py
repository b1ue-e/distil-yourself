"""Strict decoding for standalone local Codex ingestion requests."""

from dataclasses import dataclass

from . import adapters, authorization, ingestion


REQUEST_SCHEMA = "knowledge-distiller.local-codex-ingestion-request/v1"
MAX_LOCAL_REQUEST_BYTES = ingestion.MAX_REQUEST_BYTES
READ_WINDOW_SECONDS = 300
MAX_DERIVED_SECONDS = 90 * 24 * 60 * 60
PURPOSE = "distill-knowledge"
LOCAL_OWNER_VERIFIER = "local-owner-verifier-v1"
REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "transaction_id",
        "expected_generation_id",
        "session_path",
        "project_id",
        "prefix_length",
        "prefix_digest",
        "derived_processing_until",
    }
)


class LocalRuntimeError(ValueError):
    """Allowlisted code-only local runtime diagnostic."""

    ALLOWED = frozenset(
        {
            "invalid-local-request",
            "invalid-derived-deadline",
            "local-identity-unavailable",
            "unsafe-redaction-key",
            "local-runtime-failed",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self.ALLOWED else "invalid-local-request"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class LocalCodexRequest:
    schema_version: str
    transaction_id: str
    expected_generation_id: str
    session_path: str
    project_id: str
    prefix_length: int
    prefix_digest: str
    derived_processing_until: int


def decode_local_codex_request(raw: bytes) -> LocalCodexRequest:
    """Decode one closed, pinned local Codex request without performing I/O."""

    if type(raw) is not bytes or not raw or len(raw) > MAX_LOCAL_REQUEST_BYTES:
        raise LocalRuntimeError("invalid-local-request")
    try:
        value = adapters.decode_event_graph_json(raw)
        request = adapters._object(value, REQUEST_FIELDS, "/")
        if type(request["schema_version"]) is not str or request["schema_version"] != REQUEST_SCHEMA:
            raise ValueError
        transaction_id = adapters._identifier(request["transaction_id"], "/transaction_id")
        expected_generation_id = adapters._identifier(
            request["expected_generation_id"], "/expected_generation_id"
        )
        session_path = authorization._exact(request["session_path"], "invalid-local-request")
        project_id = adapters._identifier(request["project_id"], "/project_id")
        prefix_length = request["prefix_length"]
        if type(prefix_length) is not int or not 1 <= prefix_length <= adapters.MAX_GRAPH_BYTES:
            raise ValueError
        prefix_digest = adapters._snapshot(request["prefix_digest"], "/prefix_digest")
        derived_processing_until = request["derived_processing_until"]
        if (
            type(derived_processing_until) is not int
            or not 0 <= derived_processing_until <= 2**63 - 1
        ):
            raise ValueError
        return LocalCodexRequest(
            request["schema_version"],
            transaction_id,
            expected_generation_id,
            session_path,
            project_id,
            prefix_length,
            prefix_digest,
            derived_processing_until,
        )
    except Exception:
        raise LocalRuntimeError("invalid-local-request") from None
