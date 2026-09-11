import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import MISSING, FrozenInstanceError, asdict, fields
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import (
    adapters, authorization, brokers, ingestion, local_runtime, native_adapters,
    runtime_support, source_io,
)
from knowledge_distiller.journal import canonical_json
from knowledge_distiller.persistence import TaskCoordinator, create_task, inspect_task
from knowledge_distiller.state import Event, Phase, TransitionFacts


def digest(raw=b"pinned Codex session prefix"):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def request_data():
    return {
        "schema_version": local_runtime.REQUEST_SCHEMA,
        "transaction_id": "transaction-1",
        "expected_generation_id": "generation-1",
        "session_path": "/Users/example/.codex/sessions/2026/session-私密.jsonl",
        "project_id": "project-1",
        "prefix_length": 128,
        "prefix_digest": digest(),
        "derived_processing_until": 2_000_000_000,
    }


def encoded(value=None):
    return json.dumps(
        request_data() if value is None else value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class LocalRuntimeDecoderTest(unittest.TestCase):
    def assert_invalid(self, raw, secrets=()):
        with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
            local_runtime.decode_local_codex_request(raw)
        error = caught.exception
        self.assertEqual(error.code, "invalid-local-request")
        self.assertEqual(str(error), "invalid-local-request")
        self.assertEqual(error.args, ("invalid-local-request",))
        self.assertEqual(vars(error), {"code": "invalid-local-request"})
        for diagnostic in (str(error), repr(error.args), repr(vars(error))):
            self.assertLess(len(diagnostic), 160)
            for secret in secrets:
                self.assertNotIn(secret, diagnostic)

    def test_decodes_exact_request_into_immutable_private_record(self):
        request = local_runtime.decode_local_codex_request(encoded())

        self.assertEqual(
            request,
            local_runtime.LocalCodexRequest(
                schema_version=local_runtime.REQUEST_SCHEMA,
                transaction_id="transaction-1",
                expected_generation_id="generation-1",
                session_path="/Users/example/.codex/sessions/2026/session-私密.jsonl",
                project_id="project-1",
                prefix_length=128,
                prefix_digest=digest(),
                derived_processing_until=2_000_000_000,
            ),
        )
        request_fields = fields(local_runtime.LocalCodexRequest)
        self.assertEqual(
            tuple(item.name for item in request_fields),
            (
                "schema_version",
                "transaction_id",
                "expected_generation_id",
                "session_path",
                "project_id",
                "prefix_length",
                "prefix_digest",
                "derived_processing_until",
            ),
        )
        self.assertTrue(
            all(
                item.default is MISSING and item.default_factory is MISSING
                for item in request_fields
            )
        )
        self.assertNotIn("session-私密.jsonl", repr(request))
        with self.assertRaises(FrozenInstanceError):
            request.session_path = "/other/private/session.jsonl"

    def test_exports_fixed_decoder_contract(self):
        self.assertEqual(
            local_runtime.REQUEST_SCHEMA,
            "knowledge-distiller.local-codex-ingestion-request/v1",
        )
        self.assertEqual(local_runtime.MAX_LOCAL_REQUEST_BYTES, ingestion.MAX_REQUEST_BYTES)
        self.assertEqual(local_runtime.READ_WINDOW_SECONDS, 300)
        self.assertEqual(local_runtime.MAX_DERIVED_SECONDS, 90 * 24 * 60 * 60)
        self.assertEqual(local_runtime.PURPOSE, "distill-knowledge")
        self.assertEqual(local_runtime.LOCAL_OWNER_VERIFIER, "local-owner-verifier-v1")
        self.assertEqual(
            local_runtime.REQUEST_FIELDS,
            frozenset(
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
            ),
        )

    def test_error_codes_are_allowlisted_and_code_only(self):
        for code in (
            "invalid-local-request",
            "invalid-derived-deadline",
            "local-identity-unavailable",
            "unsafe-redaction-key",
            "local-runtime-failed",
        ):
            with self.subTest(code=code):
                error = local_runtime.LocalRuntimeError(code)
                self.assertEqual(error.code, code)
                self.assertEqual(error.args, (code,))
                self.assertEqual(str(error), code)

        private = "private-path-and-source-text"
        error = local_runtime.LocalRuntimeError(private)
        self.assertEqual(error.code, "invalid-local-request")
        self.assertEqual(error.args, ("invalid-local-request",))
        self.assertNotIn(private, repr(vars(error)))

    def test_rejects_invalid_field_values_without_private_diagnostics(self):
        invalid_values = (
            ("schema_version", "knowledge-distiller.local-codex-ingestion-request/v2"),
            ("transaction_id", ""),
            ("expected_generation_id", ""),
            ("project_id", ""),
            ("prefix_length", False),
            ("prefix_length", 0),
            ("prefix_length", adapters.MAX_GRAPH_BYTES + 1),
            ("prefix_digest", digest().upper()),
            ("prefix_digest", "sha256:" + ("a" * 63)),
            ("session_path", "/private/sessions/*.jsonl"),
            ("session_path", " /private/sessions/session.jsonl"),
            ("session_path", "/private/sessions/session.jsonl "),
            ("session_path", "latest"),
            ("session_path", "LATEST"),
            ("session_path", "/private/session\u202e.jsonl"),
            ("derived_processing_until", False),
            ("derived_processing_until", -1),
            ("derived_processing_until", 2**63),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=ascii(value)):
                data = request_data()
                data[field] = value
                self.assert_invalid(
                    encoded(data),
                    (data["session_path"], "private", "session-私密.jsonl"),
                )

    def test_rejects_every_unknown_caller_authority_and_adapter_field(self):
        unknown_fields = (
            "active_principal",
            "product_version",
            "caller_uid",
            "owner",
            "issuer",
            "grant",
            "content_grant",
            "attestation",
            "authority_attestation",
            "adapter",
            "native_schema_version",
            "start",
        )
        for field in unknown_fields:
            with self.subTest(field=field):
                data = request_data()
                data[field] = "PRIVATE-CALLER-SUPPLIED-VALUE"
                self.assert_invalid(
                    encoded(data),
                    (data["session_path"], "PRIVATE-CALLER-SUPPLIED-VALUE"),
                )

    def test_rejects_missing_duplicate_invalid_utf8_and_invalid_unicode(self):
        data = request_data()
        del data["project_id"]
        duplicate = encoded()[:-1] + b',"project_id":"PRIVATE-DUPLICATE"}'
        cases = (
            (encoded(data), ("session-私密.jsonl",)),
            (duplicate, ("PRIVATE-DUPLICATE", "session-私密.jsonl")),
            (b"\xffPRIVATE-SOURCE", ("PRIVATE-SOURCE",)),
            (b'{"schema_version":"\\ud800"}', ()),
            (b'{"\\udc00":0}', ()),
        )
        for raw, secrets in cases:
            with self.subTest(raw=raw[:40]):
                self.assert_invalid(raw, secrets)

    def test_rejects_empty_oversized_and_non_exact_bytes(self):
        self.assert_invalid(b"")
        self.assert_invalid(b" " * (local_runtime.MAX_LOCAL_REQUEST_BYTES + 1))

        class BytesSubclass(bytes):
            __slots__ = ()

        for raw in (encoded().decode("utf-8"), bytearray(encoded()), memoryview(encoded()), BytesSubclass(encoded())):
            with self.subTest(kind=type(raw).__name__):
                self.assert_invalid(raw, ("session-私密.jsonl",))

    def test_maps_reused_json_resource_gates_to_private_local_error(self):
        depth = adapters.MAX_JSON_NESTING_DEPTH + 1
        too_deep = (b"[" * depth) + b"0" + (b"]" * depth)
        value_limit = adapters.MAX_JSON_VALUE_TOKENS
        too_wide = b"[" + (b"0," * value_limit) + b"0]"
        for raw in (too_deep, too_wide):
            with self.subTest(size=len(raw)):
                self.assertLess(len(raw), local_runtime.MAX_LOCAL_REQUEST_BYTES)
                self.assert_invalid(raw)

    def test_rejects_decoded_scalar_subclasses(self):
        class StringSubclass(str):
            __slots__ = ()

        class IntSubclass(int):
            __slots__ = ()

        subclass_fields = (
            ("schema_version", StringSubclass),
            ("transaction_id", StringSubclass),
            ("expected_generation_id", StringSubclass),
            ("session_path", StringSubclass),
            ("project_id", StringSubclass),
            ("prefix_digest", StringSubclass),
            ("prefix_length", IntSubclass),
            ("derived_processing_until", IntSubclass),
        )
        for field, subclass in subclass_fields:
            with self.subTest(field=field):
                data = request_data()
                data[field] = subclass(data[field])
                with mock.patch.object(
                    adapters,
                    "decode_event_graph_json",
                    return_value=data,
                ):
                    self.assert_invalid(encoded(), (data["session_path"],))

    def test_collapses_internal_validation_failures(self):
        for internal_error in (TypeError("PRIVATE"), ValueError("PRIVATE"), RecursionError("PRIVATE")):
            with self.subTest(kind=type(internal_error).__name__), mock.patch.object(
                adapters,
                "decode_event_graph_json",
                side_effect=internal_error,
            ):
                self.assert_invalid(encoded(), ("PRIVATE", "session-私密.jsonl"))

    def test_collapses_unexpected_decoder_exceptions_without_private_details(self):
        with mock.patch.object(
            adapters,
            "decode_event_graph_json",
            side_effect=RuntimeError("PRIVATE-DECODER-DETAIL"),
        ):
            self.assert_invalid(encoded(), ("PRIVATE-DECODER-DETAIL",))

    def test_does_not_swallow_base_exception_control_flow(self):
        for control_flow in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(kind=type(control_flow).__name__), mock.patch.object(
                adapters,
                "decode_event_graph_json",
                side_effect=control_flow,
            ), self.assertRaises(type(control_flow)):
                local_runtime.decode_local_codex_request(encoded())


class LocalRuntimeMaterializationTest(unittest.TestCase):
    def test_materializes_exact_trusted_codex_request_deterministically(self):
        request = local_runtime.LocalCodexRequest(
            **{**request_data(), "derived_processing_until": 2000}
        )
        key = b"s" * 32

        first = local_runtime.materialize_local_codex_request(
            "/synthetic/private-task", request,
            selector_key=key, effective_uid=123, now=1000,
        )
        second = local_runtime.materialize_local_codex_request(
            "/synthetic/private-task", request,
            selector_key=key, effective_uid=123, now=1000,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            tuple(item.name for item in fields(local_runtime.MaterializedCodexRequest)),
            ("request", "identity"),
        )
        self.assertNotIn("session-私密.jsonl", repr(first))
        self.assertNotIn(key.hex(), repr(first))
        self.assertEqual(first.request.source_kind, "codex")
        self.assertEqual(first.request.transaction_id, "transaction-1")
        self.assertEqual(first.request.expected_generation_id, "generation-1")
        self.assertEqual(first.request.native_locator_id, "project-1")
        self.assertEqual(
            first.request.native_request,
            brokers.SessionRequest(
                request.session_path, request.project_id,
                request.prefix_length, request.prefix_digest,
            ),
        )
        context = first.request.authorization_context
        self.assertEqual(
            context.selector,
            brokers.session_selector_commitment(request.session_path, key),
        )
        self.assertEqual(context.purpose, "distill-knowledge")
        self.assertIsNone(context.revision)
        self.assertEqual(context.session_range, authorization.SessionRange(0, 127))
        self.assertEqual(context.now, 1000)
        self.assertIs(context.task_active, True)
        self.assertEqual(
            context.authenticated_issuers,
            (context.active_principal, local_runtime.LOCAL_OWNER_VERIFIER),
        )
        self.assertEqual(context.content_owner, context.active_principal)
        self.assertEqual(first.identity.active_principal, context.active_principal)
        self.assertEqual(first.identity.tenant_account, context.tenant_account)
        self.assertEqual(first.identity.project_id, "project-1")
        self.assertEqual(first.identity.content_owner, context.active_principal)
        self.assertEqual(
            first.identity.product_version,
            native_adapters.CODEX_ROLLOUT_ADAPTER.product_version,
        )
        self.assertEqual(
            first.identity.native_schema_digest,
            native_adapters.CODEX_NATIVE_SCHEMA_DIGEST,
        )
        self.assertEqual(first.identity.expected_owner_uid, 123)
        self.assertIs(first.identity.selector_key, key)

        grant = authorization.validate_content_grant(
            first.request.content_grant, context=context,
        )
        attestation = authorization.validate_authority_attestation(
            first.request.authority_attestation, context=context,
        )
        self.assertEqual((grant.issued_at, grant.expires_at), (1000, 1300))
        self.assertEqual(grant.derived_processing_until, 2000)
        self.assertEqual(grant.issuer, context.active_principal)
        self.assertEqual(attestation.issuer, local_runtime.LOCAL_OWNER_VERIFIER)
        self.assertEqual(attestation.operation, "process-third-party")
        self.assertEqual(attestation.authority_basis, "verified-ownership")
        self.assertEqual(attestation.content_owner, context.active_principal)
        auth_json = json.dumps(
            {
                "context": asdict(context),
                "grant": first.request.content_grant,
                "attestation": first.request.authority_attestation,
            },
            sort_keys=True,
        )
        self.assertNotIn("/synthetic/private-task", auth_json)
        self.assertNotIn(request.session_path, auth_json)
        for opaque in (
            context.task_id, context.active_principal, context.tenant_account,
            grant.record_id, attestation.record_id,
        ):
            self.assertRegex(opaque, r"^sha256:[0-9a-f]{64}$")


class LocalRuntimeBoundaryTest(unittest.TestCase):
    def test_runtime_helpers_delegate_and_map_runtime_support_errors(self):
        key = b"k" * 32
        for helper, shared_name, args, result, error_code in (
                (local_runtime._read_redaction_key, "read_redaction_key",
                 ("/PRIVATE/key", 123), key, "unsafe-redaction-key"),
                (local_runtime._effective_uid, "effective_uid", (), 123,
                 "local-identity-unavailable"),
                (local_runtime._runtime_time, "runtime_time", (), 1000,
                 "local-identity-unavailable")):
            with self.subTest(helper=helper.__name__), mock.patch.object(
                    runtime_support, shared_name, return_value=result) as shared:
                self.assertEqual(helper(*args), result)
            shared.assert_called_once_with(*args)

            with mock.patch.object(
                    runtime_support, shared_name,
                    side_effect=runtime_support.RuntimeSupportError(error_code)):
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    helper(*args)
            self.assertEqual(caught.exception.code, error_code)

    def _captured_codex_dispatch(self):
        raw = encoded({**request_data(), "derived_processing_until": 2000})
        key = b"k" * 32
        expected = object()
        with mock.patch.object(source_io, "read_source", return_value=key) as reader, \
                mock.patch.object(
                    ingestion, "ingest_source", return_value=expected
                ) as ingest:
            result = local_runtime.ingest_codex_session(
                "/PRIVATE/task", raw, "/PRIVATE/key"
            )

        self.assertIs(result, expected)
        ingest.assert_called_once()
        task_root, materialized_request = ingest.call_args.args
        self.assertEqual(task_root, Path("/PRIVATE/task"))
        dispatch = ingest.call_args.kwargs["acquire"]
        grant = authorization.validate_content_grant(
            materialized_request.content_grant,
            context=materialized_request.authorization_context,
        )
        attestation = authorization.validate_authority_attestation(
            materialized_request.authority_attestation,
            context=materialized_request.authorization_context,
        )
        return materialized_request, dispatch, grant, attestation, reader

    def test_redaction_key_reader_uses_exact_owner_only_policy_and_preserves_bytes(self):
        key = b"k" * 31 + b"\n"
        with mock.patch.object(source_io, "read_source", return_value=key) as reader:
            result = local_runtime._read_redaction_key("/PRIVATE/key", 123)

        self.assertIs(result, key)
        reader.assert_called_once_with(
            "/PRIVATE/key", max_bytes=64,
            expected_owner_uid=123, owner_only=True,
        )

    def test_redaction_key_reader_maps_source_errors_and_rejects_bad_lengths(self):
        private = "/PRIVATE/redaction-key"
        for failure in (
            source_io.SourceIOError("PRIVATE-SOURCE-DETAIL"),
            b"k" * 31,
            b"k" * 65,
        ):
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                source_io,
                "read_source",
                side_effect=failure if isinstance(failure, Exception) else None,
                return_value=None if isinstance(failure, Exception) else failure,
            ):
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    local_runtime._read_redaction_key(private, 123)
            self.assertEqual(caught.exception.code, "unsafe-redaction-key")
            self.assertNotIn(private, str(caught.exception))
            self.assertNotIn("PRIVATE-SOURCE-DETAIL", str(caught.exception))

    def test_public_runtime_preserves_source_errors_and_collapses_unexpected_key_failures(self):
        raw = encoded({**request_data(), "derived_processing_until": 2000})
        for failure, expected in (
                (source_io.SourceIOError("PRIVATE-SOURCE-DETAIL"), "unsafe-redaction-key"),
                (RuntimeError("PRIVATE-UNEXPECTED-DETAIL"), "local-runtime-failed")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(os, "geteuid", return_value=123), \
                    mock.patch.object(time, "time", return_value=1000), \
                    mock.patch.object(source_io, "read_source", side_effect=failure):
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    local_runtime.ingest_codex_session(
                        "/PRIVATE/task", raw, "/PRIVATE/key")
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn("PRIVATE", str(caught.exception))

    def test_public_runtime_decodes_before_identity_and_key_access(self):
        with mock.patch.object(os, "geteuid", side_effect=AssertionError("identity accessed")), \
                mock.patch.object(source_io, "read_source") as reader:
            with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                local_runtime.ingest_codex_session(
                    "/PRIVATE/task", b"not-json", "/PRIVATE/key"
                )

        self.assertEqual(caught.exception.code, "invalid-local-request")
        reader.assert_not_called()

    def test_public_runtime_rejects_untrusted_uid_and_time_before_key_read(self):
        bad_runtime_values = (
            (False, 1000),
            (-1, 1000),
            (2**63, 1000),
            (123, False),
            (123, float("nan")),
            (123, float("inf")),
            (123, 1e100),
            (123, object()),
        )
        raw = encoded({**request_data(), "derived_processing_until": 2000})
        for uid, timestamp in bad_runtime_values:
            with self.subTest(uid=uid, timestamp=timestamp), \
                    mock.patch.object(os, "geteuid", return_value=uid), \
                    mock.patch.object(time, "time", return_value=timestamp), \
                    mock.patch.object(source_io, "read_source") as reader:
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    local_runtime.ingest_codex_session(
                        "/PRIVATE/task", raw, "/PRIVATE/key"
                    )
            self.assertEqual(caught.exception.code, "local-identity-unavailable")
            reader.assert_not_called()

    def test_public_runtime_rejects_negative_fractional_time_before_key_read(self):
        raw = encoded({**request_data(), "derived_processing_until": 2000})
        with mock.patch.object(os, "geteuid", return_value=123), \
                mock.patch.object(time, "time", return_value=-0.5), \
                mock.patch.object(source_io, "read_source", return_value=b"k" * 32) as reader, \
                mock.patch.object(ingestion, "ingest_source", return_value=object()):
            with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                local_runtime.ingest_codex_session(
                    "/PRIVATE/task", raw, "/PRIVATE/key"
                )

        self.assertEqual(caught.exception.code, "local-identity-unavailable")
        reader.assert_not_called()

    def test_public_runtime_rejects_clock_that_cannot_fit_grant_expiry_before_key_read(self):
        raw = encoded({
            **request_data(),
            "derived_processing_until": 2**63 - 1,
        })
        with mock.patch.object(os, "geteuid", return_value=123), \
                mock.patch.object(time, "time", return_value=2**63 - 2), \
                mock.patch.object(source_io, "read_source", return_value=b"k" * 32) as reader, \
                mock.patch.object(ingestion, "ingest_source", return_value=object()):
            with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                local_runtime.ingest_codex_session(
                    "/PRIVATE/task", raw, "/PRIVATE/key"
                )

        self.assertEqual(caught.exception.code, "local-identity-unavailable")
        reader.assert_not_called()

    def test_public_runtime_rejects_invalid_deadline_before_key_read(self):
        for deadline in (999, 1000, 1000 + local_runtime.MAX_DERIVED_SECONDS + 1):
            data = {**request_data(), "derived_processing_until": deadline}
            with self.subTest(deadline=deadline), \
                    mock.patch.object(os, "geteuid", return_value=123), \
                    mock.patch.object(time, "time", return_value=1000), \
                    mock.patch.object(source_io, "read_source") as reader:
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    local_runtime.ingest_codex_session(
                        "/PRIVATE/task", encoded(data), "/PRIVATE/key"
                    )
            self.assertEqual(caught.exception.code, "invalid-derived-deadline")
            reader.assert_not_called()

    def test_public_runtime_delegates_once_with_generated_identity(self):
        raw = encoded({**request_data(), "derived_processing_until": 2000})
        key = b"k" * 32
        expected = object()
        with mock.patch.object(os, "geteuid", return_value=123), \
                mock.patch.object(time, "time", return_value=1000.75), \
                mock.patch.object(source_io, "read_source", return_value=key), \
                mock.patch.object(ingestion, "ingest_source", return_value=expected) as ingest:
            result = local_runtime.ingest_codex_session(
                "/PRIVATE/task", raw, "/PRIVATE/key"
            )

        self.assertIs(result, expected)
        ingest.assert_called_once()
        task_root, materialized_request = ingest.call_args.args
        self.assertEqual(task_root, Path("/PRIVATE/task"))
        self.assertEqual(materialized_request.source_kind, "codex")
        self.assertEqual(materialized_request.authorization_context.now, 1000)
        self.assertEqual(ingest.call_args.kwargs["redaction_key"], key)
        dispatch = ingest.call_args.kwargs["acquire"]
        grant = authorization.validate_content_grant(
            materialized_request.content_grant,
            context=materialized_request.authorization_context,
        )
        attestation = authorization.validate_authority_attestation(
            materialized_request.authority_attestation,
            context=materialized_request.authorization_context,
        )
        with mock.patch.object(os, "geteuid", return_value=123), \
                mock.patch.object(time, "time", return_value=1000.75), \
                mock.patch.object(
                    brokers, "read_local_session", return_value=object()
                ) as acquire:
            acquired = dispatch.codex(
                materialized_request.native_request,
                grant,
                attestation,
                materialized_request.authorization_context,
            )
        self.assertIsNotNone(acquired)
        self.assertEqual(acquire.call_count, 1)
        self.assertEqual(acquire.call_args.args, (materialized_request.native_request,))
        self.assertEqual(acquire.call_args.kwargs["grant"], grant)
        self.assertEqual(acquire.call_args.kwargs["attestation"], attestation)
        self.assertEqual(
            acquire.call_args.kwargs["context"],
            materialized_request.authorization_context,
        )
        identity = acquire.call_args.kwargs["identity"]
        self.assertEqual(identity.expected_owner_uid, 123)
        self.assertIs(identity.selector_key, key)
        with self.assertRaises(ingestion.IngestionError):
            dispatch.lark(None, None, None, None)

    def test_codex_acquisition_rechecks_effective_uid_before_broker(self):
        with mock.patch.object(os, "geteuid", side_effect=[123, 124]), \
                mock.patch.object(time, "time", return_value=1000):
            materialized_request, dispatch, grant, attestation, reader = (
                self._captured_codex_dispatch()
            )
            with mock.patch.object(
                brokers, "read_local_session", return_value=object()
            ) as acquire:
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    dispatch.codex(
                        materialized_request.native_request,
                        grant,
                        attestation,
                        materialized_request.authorization_context,
                    )

        self.assertEqual(caught.exception.code, "local-identity-unavailable")
        acquire.assert_not_called()
        reader.assert_called_once_with(
            "/PRIVATE/key", max_bytes=64, expected_owner_uid=123, owner_only=True,
        )

    def test_codex_acquisition_rechecks_time_before_broker(self):
        with mock.patch.object(os, "geteuid", return_value=123), \
                mock.patch.object(time, "time", side_effect=[1000, 1299]):
            materialized_request, dispatch, grant, attestation, reader = (
                self._captured_codex_dispatch()
            )
            broker_snapshot = object()
            with mock.patch.object(
                brokers, "read_local_session", return_value=broker_snapshot
            ) as acquire:
                acquired = dispatch.codex(
                    materialized_request.native_request,
                    grant,
                    attestation,
                    materialized_request.authorization_context,
                )

        self.assertIs(acquired, broker_snapshot)
        acquire.assert_called_once()
        self.assertEqual(
            acquire.call_args.kwargs["context"].now,
            1299,
        )
        self.assertEqual((grant.issued_at, grant.expires_at), (1000, 1300))
        self.assertEqual(
            materialized_request.authorization_context.now,
            1000,
        )
        reader.assert_called_once_with(
            "/PRIVATE/key", max_bytes=64, expected_owner_uid=123, owner_only=True,
        )

    def test_codex_acquisition_bounds_invalid_fresh_time_before_broker(self):
        with mock.patch.object(os, "geteuid", return_value=123), \
                mock.patch.object(time, "time", side_effect=[1000, object()]):
            materialized_request, dispatch, grant, attestation, reader = (
                self._captured_codex_dispatch()
            )
            with mock.patch.object(
                brokers, "read_local_session", return_value=object()
            ) as acquire:
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    dispatch.codex(
                        materialized_request.native_request,
                        grant,
                        attestation,
                        materialized_request.authorization_context,
                    )

        self.assertEqual(caught.exception.code, "local-identity-unavailable")
        acquire.assert_not_called()
        reader.assert_called_once_with(
            "/PRIVATE/key", max_bytes=64, expected_owner_uid=123, owner_only=True,
        )

    def test_codex_acquisition_bounds_fresh_uid_failure_before_broker(self):
        with mock.patch.object(os, "geteuid", side_effect=[123, OSError("PRIVATE-UID")]), \
                mock.patch.object(time, "time", side_effect=[1000, 1001]):
            materialized_request, dispatch, grant, attestation, reader = (
                self._captured_codex_dispatch()
            )
            with mock.patch.object(
                brokers, "read_local_session", return_value=object()
            ) as acquire:
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    dispatch.codex(
                        materialized_request.native_request,
                        grant,
                        attestation,
                        materialized_request.authorization_context,
                    )

        self.assertEqual(caught.exception.code, "local-identity-unavailable")
        self.assertNotIn("PRIVATE-UID", str(caught.exception))
        acquire.assert_not_called()
        reader.assert_called_once_with(
            "/PRIVATE/key", max_bytes=64, expected_owner_uid=123, owner_only=True,
        )

    def test_codex_acquisition_does_not_swallow_fresh_time_control_flow(self):
        with mock.patch.object(os, "geteuid", return_value=123), \
                mock.patch.object(time, "time", side_effect=[1000, KeyboardInterrupt()]):
            materialized_request, dispatch, grant, attestation, reader = (
                self._captured_codex_dispatch()
            )
            with mock.patch.object(
                brokers, "read_local_session", return_value=object()
            ) as acquire:
                with self.assertRaises(KeyboardInterrupt):
                    dispatch.codex(
                        materialized_request.native_request,
                        grant,
                        attestation,
                        materialized_request.authorization_context,
                    )

        acquire.assert_not_called()
        reader.assert_called_once_with(
            "/PRIVATE/key", max_bytes=64, expected_owner_uid=123, owner_only=True,
        )

    def test_codex_acquisition_does_not_swallow_fresh_uid_control_flow(self):
        with mock.patch.object(os, "geteuid", side_effect=[123, KeyboardInterrupt()]), \
                mock.patch.object(time, "time", side_effect=[1000, 1001]):
            materialized_request, dispatch, grant, attestation, reader = (
                self._captured_codex_dispatch()
            )
            with mock.patch.object(
                brokers, "read_local_session", return_value=object()
            ) as acquire:
                with self.assertRaises(KeyboardInterrupt):
                    dispatch.codex(
                        materialized_request.native_request,
                        grant,
                        attestation,
                        materialized_request.authorization_context,
                    )

        acquire.assert_not_called()
        reader.assert_called_once_with(
            "/PRIVATE/key", max_bytes=64, expected_owner_uid=123, owner_only=True,
        )

    def test_public_runtime_preserves_bounded_errors_and_collapses_unexpected(self):
        for failure in (
            local_runtime.LocalRuntimeError("unsafe-redaction-key"),
            ingestion.IngestionError("generation-lineage-mismatch"),
        ):
            with self.subTest(code=failure.code), mock.patch.object(
                local_runtime, "_ingest_codex_session", side_effect=failure,
            ):
                with self.assertRaises(type(failure)) as caught:
                    local_runtime.ingest_codex_session(
                        "/PRIVATE/task", encoded(), "/PRIVATE/key"
                    )
            self.assertIs(caught.exception, failure)

        private = "PRIVATE-INNER-DETAIL"
        with mock.patch.object(
            local_runtime, "_ingest_codex_session",
            side_effect=ValueError(private),
        ):
            with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                local_runtime.ingest_codex_session(
                    "/PRIVATE/task", encoded(), "/PRIVATE/key"
                )
        self.assertEqual(caught.exception.code, "local-runtime-failed")
        self.assertNotIn(private, str(caught.exception))

        with mock.patch.object(
            local_runtime, "_ingest_codex_session", side_effect=KeyboardInterrupt(),
        ), self.assertRaises(KeyboardInterrupt):
            local_runtime.ingest_codex_session(
                "/PRIVATE/task", encoded(), "/PRIVATE/key"
            )

    def test_actual_key_file_safety_uses_shared_descriptor_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            key_path = root / "key"
            key = b"k" * 31 + b"\n"
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            self.assertEqual(
                local_runtime._read_redaction_key(str(key_path), os.geteuid()),
                key,
            )

            key_path.chmod(0o640)
            with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                local_runtime._read_redaction_key(str(key_path), os.geteuid())
            self.assertEqual(caught.exception.code, "unsafe-redaction-key")

            key_path.chmod(0o600)
            symlink = root / "symlink"
            symlink.symlink_to(key_path)
            with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                local_runtime._read_redaction_key(str(symlink), os.geteuid())
            self.assertEqual(caught.exception.code, "unsafe-redaction-key")

            hardlink = root / "hardlink"
            os.link(key_path, hardlink)
            with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                local_runtime._read_redaction_key(str(key_path), os.geteuid())
            self.assertEqual(caught.exception.code, "unsafe-redaction-key")


class LocalRuntimeTransactionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        private_root = Path(self.directory.name).resolve()
        self.root = private_root / "task"
        create_task(self.root)
        with TaskCoordinator(self.root) as coordinator:
            coordinator.transition(
                Event.START_DISTILL,
                TransitionFacts(selected_capability=True),
            )
            self.ingest_state = coordinator.transition(
                Event.CONTENT_GRANTED,
                TransitionFacts(content_grant=True, authority_valid=True),
            )
        self.fixture = (
            ROOT / "tests/fixtures/adapters/codex/0.153.0/"
            "rollout-jsonl-v1/redacted-current.jsonl"
        ).read_bytes()
        self.later = b"LATER-BYTES-NOT-IN-PREFIX"
        self.session = private_root / "private-session.jsonl"
        self.session.write_bytes(self.fixture + self.later)
        self.key = b"k" * 32
        self.key_path = private_root / "private-key"
        self.key_path.write_bytes(self.key)
        self.key_path.chmod(0o600)

    def raw_request(self, generation_id=None, prefix_digest=None):
        request = local_runtime.LocalCodexRequest(
            schema_version=local_runtime.REQUEST_SCHEMA,
            transaction_id="local-ingestion",
            expected_generation_id=(
                self.ingest_state.generation_id
                if generation_id is None else generation_id
            ),
            session_path=str(self.session),
            project_id="project-1",
            prefix_length=len(self.fixture),
            prefix_digest=digest(self.fixture) if prefix_digest is None else prefix_digest,
            derived_processing_until=2000,
        )
        return canonical_json(asdict(request))

    def test_ingests_only_pinned_synthetic_prefix_through_shared_transaction(self):
        with mock.patch.object(time, "time", return_value=1000), \
                mock.patch("pwd.getpwuid", side_effect=AssertionError("username lookup")), \
                mock.patch.object(
                    subprocess, "Popen", side_effect=AssertionError("process launch")
                ):
            result = local_runtime.ingest_codex_session(
                self.root, self.raw_request(), str(self.key_path)
            )

        current = inspect_task(self.root)
        self.assertEqual(result.status, "snapshotted")
        self.assertEqual(result.source_kind, "session")
        self.assertEqual(result.source_byte_count, len(self.fixture))
        self.assertNotEqual(result.generation_id, self.ingest_state.generation_id)
        self.assertEqual(current.generation_id, result.generation_id)
        self.assertIs(current.state.phase, Phase.INGEST)
        self.assertEqual(self.session.read_bytes(), self.fixture + self.later)
        persisted = b"".join(
            path.read_bytes() for path in self.root.rglob("*") if path.is_file()
        )
        for private in (
            str(self.session).encode("utf-8"),
            str(self.key_path).encode("utf-8"),
            self.key,
            self.later,
        ):
            self.assertNotIn(private, persisted)

    def test_stale_generation_and_changed_prefix_do_not_commit(self):
        failures = (
            (self.raw_request(generation_id="stale-generation"),
             "generation-lineage-mismatch"),
            (self.raw_request(prefix_digest=digest(b"different-prefix")),
             "acquisition-failed"),
        )
        for raw, expected_code in failures:
            with self.subTest(code=expected_code), \
                    mock.patch.object(time, "time", return_value=1000):
                before = inspect_task(self.root).generation_id
                with self.assertRaises(ingestion.IngestionError) as caught:
                    local_runtime.ingest_codex_session(
                        self.root, raw, str(self.key_path)
                    )
            self.assertEqual(caught.exception.code, expected_code)
            self.assertEqual(inspect_task(self.root).generation_id, before)

    def test_expired_fresh_grant_before_session_read_does_not_commit(self):
        before = inspect_task(self.root).generation_id
        read_paths = []
        real_read_source = source_io.read_source

        def tracking_read_source(path, **kwargs):
            read_paths.append(os.fspath(path))
            return real_read_source(path, **kwargs)

        with mock.patch.object(time, "time", side_effect=[1000, 1300]), \
                mock.patch.object(
                    source_io, "read_source", side_effect=tracking_read_source
                ):
            with self.assertRaises(ingestion.IngestionError) as caught:
                local_runtime.ingest_codex_session(
                    self.root, self.raw_request(), str(self.key_path)
                )

        self.assertEqual(caught.exception.code, "acquisition-failed")
        self.assertEqual(inspect_task(self.root).generation_id, before)
        self.assertEqual(read_paths, [str(self.key_path)])


if __name__ == "__main__":
    unittest.main()
