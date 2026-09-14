"""Closed local Lark request decoder tests."""

import json
import hashlib
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import FrozenInstanceError, MISSING, asdict, fields, replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import (  # noqa: E402
    adapters, authorization, brokers, ingestion, lark_cli_transport,
    lark_profile, lark_runtime, native_adapters, redaction, runtime_support,
)
from knowledge_distiller.journal import canonical_json  # noqa: E402
from knowledge_distiller.persistence import (  # noqa: E402
    TaskCoordinator, create_task, inspect_task,
)
from knowledge_distiller.state import Event, TransitionFacts  # noqa: E402


TOKEN = "doxcn1234567890AbCdEfGhIjKl"
OTHER_TOKEN = "doxcn1234567890AbCdEfGhIjKm"
URL = "https://tenant.larkoffice.com/docx/" + TOKEN
OWNER = "ou_SYNTHETIC_OWNER"
OTHER_OWNER = "ou_SYNTHETIC_OTHER"
TITLE = "SYNTHETIC_PRIVATE_TITLE"
RAW_TEXT = "password=SYNTHETIC_PRIVATE_RAW_TEXT"
OPEN_ID_DIAGNOSTIC = "SYNTHETIC_PRIVATE_OPEN_ID_DIAGNOSTIC"
SCOPES = ("docx:document:readonly", "drive:drive.metadata:readonly")
EXPECTED_FLOW = (
    "version", "verify-before", "observe-before",
    "verify-acquire", "raw-content", "observe-after",
)


class _StringSubclass(str):
    pass


class _UnsafePath:
    def __init__(self, value):
        self.value = value

    def __fspath__(self):
        return self.value


def request_data():
    return {
        "schema_version": lark_runtime.REQUEST_SCHEMA,
        "transaction_id": "transaction-1",
        "expected_generation_id": "generation-1",
        "document_selector": "https://tenant.larkoffice.com/docx/" + TOKEN,
        "derived_processing_until": 2_000_000_000,
    }


def encoded(value=None):
    return json.dumps(
        request_data() if value is None else value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class LarkRequestDecoderTest(unittest.TestCase):
    def assert_invalid(self, raw, secrets=()):
        with self.assertRaises(lark_runtime.LarkRuntimeError) as caught:
            lark_runtime.decode_local_lark_request(raw)
        error = caught.exception
        self.assertEqual(error.code, "invalid-lark-request")
        self.assertEqual(error.args, ("invalid-lark-request",))
        self.assertEqual(str(error), "invalid-lark-request")
        self.assertEqual(vars(error), {"code": "invalid-lark-request"})
        for secret in secrets:
            self.assertNotIn(secret, repr(error.args))
            self.assertNotIn(secret, repr(vars(error)))

    def test_decodes_only_the_normalized_token_into_immutable_private_record(self):
        request = lark_runtime.decode_local_lark_request(encoded())

        self.assertEqual(request.schema_version, lark_runtime.REQUEST_SCHEMA)
        self.assertEqual(request.transaction_id, "transaction-1")
        self.assertEqual(request.expected_generation_id, "generation-1")
        self.assertEqual(request.document_selector, TOKEN)
        self.assertEqual(request.derived_processing_until, 2_000_000_000)
        self.assertNotIn(TOKEN, repr(request))
        self.assertEqual(
            tuple(item.name for item in fields(lark_runtime.LocalLarkRequest)),
            ("schema_version", "transaction_id", "expected_generation_id",
             "document_selector", "derived_processing_until"),
        )
        self.assertTrue(all(
            item.default is MISSING and item.default_factory is MISSING
            for item in fields(lark_runtime.LocalLarkRequest)
        ))
        with self.assertRaises(FrozenInstanceError):
            request.document_selector = "private"

    def test_exports_the_closed_decoder_contract(self):
        self.assertEqual(
            lark_runtime.REQUEST_SCHEMA,
            "knowledge-distiller.local-lark-ingestion-request/v1",
        )
        self.assertEqual(lark_runtime.MAX_LOCAL_REQUEST_BYTES, ingestion.MAX_REQUEST_BYTES)
        self.assertEqual(
            lark_runtime.REQUEST_FIELDS,
            frozenset({"schema_version", "transaction_id", "expected_generation_id",
                       "document_selector", "derived_processing_until"}),
        )
        for invalid in ("private detail", None, 1):
            error = lark_runtime.LarkRuntimeError(invalid)
            self.assertEqual(error.code, "invalid-lark-request")
            self.assertEqual(error.args, ("invalid-lark-request",))

    def test_rejects_closed_schema_and_caller_supplied_authority_fields(self):
        invalid = (("schema_version", "knowledge-distiller.local-lark-ingestion-request/v2"),
                   ("transaction_id", ""), ("expected_generation_id", ""),
                   ("document_selector", "PRIVATE-NOT-A-SELECTOR"),
                   ("document_selector", True),
                   ("derived_processing_until", False),
                   ("derived_processing_until", -1),
                   ("derived_processing_until", 2**63))
        for field, value in invalid:
            with self.subTest(field=field):
                data = request_data()
                data[field] = value
                self.assert_invalid(encoded(data), ("PRIVATE-NOT-A-SELECTOR",))
        for field in ("identity", "owner", "revision", "profile", "schema_selection",
                      "active_principal", "caller_uid", "adapter"):
            with self.subTest(field=field):
                data = request_data()
                data[field] = "PRIVATE-CALLER-SUPPLIED"
                self.assert_invalid(encoded(data), ("PRIVATE-CALLER-SUPPLIED",))

    def test_rejects_missing_duplicate_bad_json_unicode_and_resource_bounds(self):
        missing = request_data()
        del missing["document_selector"]
        duplicate = encoded()[:-1] + b',"document_selector":"PRIVATE-DUPLICATE"}'
        depth = adapters.MAX_JSON_NESTING_DEPTH + 1
        too_deep = (b"[" * depth) + b"0" + (b"]" * depth)
        cases = (
            encoded(missing), duplicate, b"{", b"\xffPRIVATE-UTF8",
            b'{"schema_version":"\\ud800"}',
            too_deep, b" " * (lark_runtime.MAX_LOCAL_REQUEST_BYTES + 1),
        )
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                self.assert_invalid(raw, ("PRIVATE-DUPLICATE", "PRIVATE-UTF8"))

    def test_rejects_non_exact_raw_bytes_and_decoded_scalar_subclasses(self):
        class BytesSubclass(bytes):
            __slots__ = ()

        class StringSubclass(str):
            __slots__ = ()

        class IntSubclass(int):
            __slots__ = ()

        for raw in (b"", encoded().decode(), bytearray(encoded()), memoryview(encoded()), BytesSubclass(encoded())):
            with self.subTest(kind=type(raw).__name__):
                self.assert_invalid(raw)
        for field, subclass in (("schema_version", StringSubclass),
                                ("transaction_id", StringSubclass),
                                ("expected_generation_id", StringSubclass),
                                ("document_selector", StringSubclass),
                                ("derived_processing_until", IntSubclass)):
            with self.subTest(field=field), mock.patch.object(adapters, "decode_event_graph_json") as decode:
                data = request_data()
                data[field] = subclass(data[field])
                decode.return_value = data
                self.assert_invalid(encoded())

    def test_rejects_ids_outside_the_persistence_safe_grammar(self):
        invalid_ids = (
            "contains/slash", "contains\0nul", "-leading", ".leading",
            "x" * 129, _StringSubclass("generation-1"),
        )
        for field in ("transaction_id", "expected_generation_id"):
            for value in invalid_ids:
                with self.subTest(field=field, kind=type(value).__name__):
                    data = request_data()
                    if type(value) is _StringSubclass:
                        with mock.patch.object(
                            adapters, "decode_event_graph_json", return_value={
                                **data, field: value,
                            },
                        ):
                            self.assert_invalid(encoded())
                    else:
                        data[field] = value
                        self.assert_invalid(encoded(data))

    def test_collapses_unexpected_exception_without_private_details(self):
        with mock.patch.object(
            adapters,
            "decode_event_graph_json",
            side_effect=RuntimeError("PRIVATE-DECODER-DETAIL"),
        ):
            self.assert_invalid(encoded(), ("PRIVATE-DECODER-DETAIL", TOKEN))

    def test_propagates_base_exception_control_flow(self):
        for control_flow in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(kind=type(control_flow).__name__), mock.patch.object(
                adapters,
                "decode_event_graph_json",
                side_effect=control_flow,
            ), self.assertRaises(type(control_flow)):
                lark_runtime.decode_local_lark_request(encoded())

    def test_decoding_never_uses_network_subprocess_or_filesystem(self):
        forbidden = RuntimeError("forbidden I/O")
        with mock.patch("socket.create_connection", side_effect=forbidden), \
                mock.patch("subprocess.run", side_effect=forbidden), \
                mock.patch("builtins.open", side_effect=forbidden), \
                mock.patch.object(Path, "iterdir", side_effect=forbidden):
            self.assertEqual(
                lark_runtime.decode_local_lark_request(encoded()).document_selector,
                TOKEN,
            )


def opaque_digest(domain, raw):
    return "sha256:" + hashlib.sha256(
        domain.encode("ascii") + b"\0" + raw
    ).hexdigest()


def live_profile(**changes):
    values = {
        "status": "live-enabled",
        "endpoint_integrity_status": "verified",
        "consistency_mode": "observational",
    }
    values.update(changes)
    return replace(lark_profile.load_pinned_profile(), **values)


class SyntheticTransport:
    def __init__(self, *, profile=None, version=None, identities=None,
                 observations=None, raw=None):
        self.profile = profile or live_profile()
        self.version_value = version or self.profile.product_version
        self.identities = list(identities or (
            lark_cli_transport.VerifiedUser(
                OWNER, SCOPES,
            ),
        ) * 2)
        self.observations = list(observations or (
            lark_cli_transport.LarkObservation(TOKEN, "7", OWNER),
        ) * 2)
        self.raw = raw or canonical_json({
            "ok": True,
            "identity": "user",
            "data": {"content": RAW_TEXT},
        })
        self.calls = []
        self.raw_calls = 0

    def version(self):
        self.calls.append("version")
        return self.version_value

    def verify_user(self):
        self.calls.append(
            "verify-before" if len(self.identities) == 2 else "verify-acquire"
        )
        value = self.identities.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def observe(self, selector):
        self.calls.append(
            "observe-before" if len(self.observations) == 2 else "observe-after"
        )
        value = self.observations.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def raw_content(self, selector):
        self.calls.append("raw-content")
        self.raw_calls += 1
        if isinstance(self.raw, BaseException):
            raise self.raw
        return self.raw


class LarkMaterializationTest(unittest.TestCase):
    def materialize(self, *, root="/synthetic/private-task", request=None,
                    selector=None, identity=None, observation=None, now=1000):
        request = request or lark_runtime.LocalLarkRequest(
            lark_runtime.REQUEST_SCHEMA,
            "transaction-1",
            "generation-1",
            TOKEN,
            2000,
        )
        selector = selector or lark_runtime.parse_document_selector(TOKEN)
        identity = identity or lark_cli_transport.VerifiedUser(
            OWNER, SCOPES,
        )
        observation = observation or lark_cli_transport.LarkObservation(
            TOKEN, "7", OWNER,
        )
        return lark_runtime.materialize_local_lark_request(
            root, request, selector, identity, observation, now,
        )

    def test_materializes_owner_only_authorization_with_exact_opaque_derivations(self):
        first = self.materialize()
        second = self.materialize()
        self.assertEqual(first, second)
        self.assertEqual(
            tuple(item.name for item in fields(lark_runtime.MaterializedLarkRequest)),
            ("request", "expected_open_id", "principal", "account"),
        )
        self.assertNotIn(TOKEN, repr(first))
        self.assertNotIn(OWNER, repr(first))
        with self.assertRaises(FrozenInstanceError):
            first.principal = "changed"

        native = first.request
        context = native.authorization_context
        expected_principal = opaque_digest(
            "lark-user-principal/v1", OWNER.encode("utf-8")
        )
        expected_account = opaque_digest(
            "lark-account-scope/v1", OWNER.encode("utf-8")
        )
        expected_task = opaque_digest(
            "local-lark-task/v1",
            os.path.abspath("/synthetic/private-task").encode("utf-8")
            + b"\0generation-1",
        )
        self.assertEqual((first.principal, first.account),
                         (expected_principal, expected_account))
        self.assertEqual(context.task_id, expected_task)
        self.assertEqual(context.active_principal, expected_principal)
        self.assertEqual(context.tenant_account, expected_account)
        self.assertEqual(context.content_owner, expected_principal)
        self.assertEqual(context.selector, lark_runtime.parse_document_selector(TOKEN).commitment)
        self.assertEqual(context.revision, "7")
        self.assertIsNone(context.session_range)
        self.assertEqual(context.purpose, "distill-knowledge")
        self.assertEqual(context.authenticated_issuers,
                         (expected_principal, "lark-owner-verifier-v1"))
        self.assertEqual(native.source_kind, "lark")
        self.assertEqual(native.native_request, brokers.LarkRequest(TOKEN, "7"))
        self.assertEqual(native.native_locator_id, context.selector)

        grant = authorization.validate_content_grant(
            native.content_grant, context=context,
        )
        attestation = authorization.validate_authority_attestation(
            native.authority_attestation, context=context,
        )
        self.assertEqual((grant.issued_at, grant.expires_at), (1000, 1300))
        self.assertEqual(grant.derived_processing_until, 2000)
        self.assertEqual(grant.issuer, expected_principal)
        self.assertEqual(attestation.issuer, "lark-owner-verifier-v1")
        self.assertEqual(attestation.content_owner, expected_principal)
        self.assertEqual(attestation.authority_basis, "verified-ownership")
        record_binding = (
            b"transaction-1\0" + expected_task.encode("ascii") + b"\0"
            + context.selector.encode("ascii") + b"\0" + b"7"
        )
        self.assertEqual(
            grant.record_id,
            opaque_digest("local-lark-content-grant", record_binding),
        )
        self.assertEqual(
            attestation.record_id,
            opaque_digest("local-lark-authority-attestation", record_binding),
        )

        authoritative = canonical_json({
            "context": asdict(context),
            "grant": native.content_grant,
            "attestation": native.authority_attestation,
        })
        for private in (TOKEN, URL, OWNER, TITLE, RAW_TEXT):
            self.assertNotIn(private.encode("utf-8"), authoritative)

    def test_materialization_rejects_owner_binding_closed_types_and_deadlines(self):
        base = lark_runtime.LocalLarkRequest(
            lark_runtime.REQUEST_SCHEMA, "transaction-1", "generation-1",
            TOKEN, 2000,
        )
        invalid = (
            (base, lark_runtime.parse_document_selector(TOKEN),
             lark_cli_transport.VerifiedUser(OTHER_OWNER, ("scope",)),
             lark_cli_transport.LarkObservation(TOKEN, "7", OWNER), 1000,
             "lark-owner-mismatch"),
            (replace(base, derived_processing_until=1000),
             lark_runtime.parse_document_selector(TOKEN),
             lark_cli_transport.VerifiedUser(OWNER, ("scope",)),
             lark_cli_transport.LarkObservation(TOKEN, "7", OWNER), 1000,
             "invalid-derived-deadline"),
            (replace(base, derived_processing_until=1000 + 90 * 24 * 60 * 60 + 1),
             lark_runtime.parse_document_selector(TOKEN),
             lark_cli_transport.VerifiedUser(OWNER, ("scope",)),
             lark_cli_transport.LarkObservation(TOKEN, "7", OWNER), 1000,
             "invalid-derived-deadline"),
            (base, lark_runtime.parse_document_selector(OTHER_TOKEN),
             lark_cli_transport.VerifiedUser(OWNER, ("scope",)),
             lark_cli_transport.LarkObservation(TOKEN, "7", OWNER), 1000,
             "invalid-lark-request"),
            (base, lark_runtime.parse_document_selector(TOKEN),
             lark_cli_transport.VerifiedUser(OWNER, ("scope",)),
             lark_cli_transport.LarkObservation(TOKEN, "7", OWNER), True,
             "local-identity-unavailable"),
        )
        for request, selector, identity, observation, now, code in invalid:
            with self.subTest(code=code), self.assertRaises(
                lark_runtime.LarkRuntimeError
            ) as caught:
                lark_runtime.materialize_local_lark_request(
                    "/synthetic/task", request, selector, identity,
                    observation, now,
                )
            self.assertEqual(caught.exception.code, code)

        for malformed in (object(), {"request": "PRIVATE"}):
            with self.subTest(malformed=type(malformed).__name__), self.assertRaises(
                lark_runtime.LarkRuntimeError
            ) as caught:
                lark_runtime.materialize_local_lark_request(
                    "/synthetic/task", malformed,
                    lark_runtime.parse_document_selector(TOKEN),
                    lark_cli_transport.VerifiedUser(OWNER, ("scope",)),
                    lark_cli_transport.LarkObservation(TOKEN, "7", OWNER),
                    1000,
                )
            self.assertEqual(caught.exception.code, "invalid-lark-request")

    def test_rejects_unsafe_ids_and_ambiguous_nul_framing_when_called_directly(self):
        base = lark_runtime.LocalLarkRequest(
            lark_runtime.REQUEST_SCHEMA, "transaction-1", "generation-1",
            TOKEN, 2000,
        )
        selector = lark_runtime.parse_document_selector(TOKEN)
        identity = lark_cli_transport.VerifiedUser(OWNER, ("scope",))
        observation = lark_cli_transport.LarkObservation(TOKEN, "7", OWNER)
        old_left = (
            os.path.abspath("/synthetic/task\0suffix").encode("utf-8")
            + b"\0g"
        )
        old_right = (
            os.path.abspath("/synthetic/task").encode("utf-8")
            + b"\0suffix\0g"
        )
        self.assertEqual(old_left, old_right)
        cases = (
            ("/synthetic/task\0suffix", replace(base, expected_generation_id="g")),
            ("/synthetic/task", replace(base, expected_generation_id="suffix\0g")),
            ("/synthetic/task", replace(base, transaction_id="unsafe/tx")),
            ("/synthetic/task", replace(base, transaction_id="unsafe\0tx")),
            (_UnsafePath(b"/synthetic/bytes"), base),
            (_UnsafePath(_StringSubclass("/synthetic/subclass")), base),
        )
        for root, request in cases:
            with self.subTest(root_type=type(root).__name__), self.assertRaises(
                lark_runtime.LarkRuntimeError
            ) as caught:
                lark_runtime.materialize_local_lark_request(
                    root, request, selector, identity, observation, 1000,
                )
            self.assertIn(caught.exception.code, {
                "invalid-lark-request", "local-identity-unavailable",
            })
            for private in ("suffix", "unsafe/tx", "subclass"):
                self.assertNotIn(private, str(caught.exception))

        tampered_request = replace(base, schema_version="PRIVATE-SCHEMA")
        tampered_identity = lark_cli_transport.VerifiedUser(OWNER, ("scope",))
        object.__setattr__(tampered_identity, "open_id", True)
        tampered_observation = lark_cli_transport.LarkObservation(
            TOKEN, "7", OWNER,
        )
        object.__setattr__(tampered_observation, "revision", "latest")
        for request, identity, observation in (
            (tampered_request,
             lark_cli_transport.VerifiedUser(OWNER, ("scope",)),
             lark_cli_transport.LarkObservation(TOKEN, "7", OWNER)),
            (base, tampered_identity,
             lark_cli_transport.LarkObservation(TOKEN, "7", OWNER)),
            (base, lark_cli_transport.VerifiedUser(OWNER, ("scope",)),
             tampered_observation),
        ):
            with self.subTest(tampered=True), self.assertRaises(
                lark_runtime.LarkRuntimeError
            ) as caught:
                lark_runtime.materialize_local_lark_request(
                    "/synthetic/task", request,
                    lark_runtime.parse_document_selector(TOKEN), identity,
                    observation, 1000,
                )
            self.assertEqual(caught.exception.code, "invalid-lark-request")


class LarkStableIngestionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.private_root = Path(temporary.name).resolve()
        self.root = self.private_root / "task"
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
        self.key_path = self.private_root / "private-redaction-key"
        self.key_path.write_bytes(b"k" * 32)
        self.key_path.chmod(0o600)

    def raw_request(self, *, generation=None, deadline=2000,
                    transaction="lark-ingestion"):
        return canonical_json({
            "schema_version": lark_runtime.REQUEST_SCHEMA,
            "transaction_id": transaction,
            "expected_generation_id": (
                self.ingest_state.generation_id if generation is None else generation
            ),
            "document_selector": URL,
            "derived_processing_until": deadline,
        })

    def ingest(self, transport=None, *, allow=True, profile=None, raw=None):
        transport = transport or SyntheticTransport(profile=profile)
        with mock.patch.object(runtime_support, "effective_uid", return_value=os.geteuid()), \
                mock.patch.object(runtime_support, "runtime_time", return_value=1000), \
                mock.patch.object(lark_runtime, "_require_live_ingestion",
                                  return_value=profile or transport.profile), \
                mock.patch.object(lark_runtime, "LarkCliTransport",
                                  return_value=transport):
            result = lark_runtime.ingest_lark_document(
                self.root, self.raw_request() if raw is None else raw,
                str(self.key_path), allow_live_read=allow,
            )
        return result, transport

    def assert_failed_without_commit(self, action, expected_code=None):
        before = inspect_task(self.root).generation_id
        with self.assertRaises((lark_runtime.LarkRuntimeError,
                                ingestion.IngestionError)) as caught:
            action()
        if expected_code is not None:
            self.assertEqual(caught.exception.code, expected_code)
        self.assertEqual(inspect_task(self.root).generation_id, before)
        self.assertLess(len(str(caught.exception)), 160)
        for private in (TOKEN, URL, OWNER, OTHER_OWNER, TITLE, RAW_TEXT,
                        OPEN_ID_DIAGNOSTIC):
            self.assertNotIn(private, str(caught.exception))
        return caught.exception

    def test_owner_only_stable_sandwich_ingests_through_shared_transaction(self):
        result, transport = self.ingest()

        self.assertEqual(tuple(transport.calls), EXPECTED_FLOW)
        self.assertEqual((result.status, result.source_kind),
                         ("snapshotted", "document"))
        self.assertEqual(inspect_task(self.root).generation_id, result.generation_id)
        generation = self.root / "generations" / result.generation_id
        authoritative = b"".join(
            path.read_bytes() for path in generation.rglob("*") if path.is_file()
        )
        for private in (
            TOKEN.encode(), URL.encode(), OWNER.encode(), TITLE.encode(),
            RAW_TEXT.encode(), OPEN_ID_DIAGNOSTIC.encode(),
        ):
            self.assertNotIn(private, authoritative)
        self.assertIn(b"[redacted:credential:hmac-sha256:", authoritative)

    def test_production_profile_and_missing_consent_fail_before_transport(self):
        for allow in (False, 0, None):
            with self.subTest(allow=allow), mock.patch.object(
                lark_runtime, "LarkCliTransport"
            ) as constructor:
                self.assert_failed_without_commit(
                    lambda allow=allow: lark_runtime.ingest_lark_document(
                        self.root, self.raw_request(), str(self.key_path),
                        allow_live_read=allow,
                    ),
                    "lark-live-disabled",
                )
                constructor.assert_not_called()

        with mock.patch.object(lark_runtime, "LarkCliTransport") as constructor:
            self.assert_failed_without_commit(
                lambda: lark_runtime.ingest_lark_document(
                    self.root, self.raw_request(), str(self.key_path),
                    allow_live_read=True,
                ),
                "lark-live-disabled",
            )
            constructor.assert_not_called()

        with mock.patch.object(lark_profile, "load_pinned_profile",
                               return_value=object()), mock.patch.object(
            lark_runtime, "LarkCliTransport"
        ) as constructor:
            self.assert_failed_without_commit(
                lambda: lark_runtime.ingest_lark_document(
                    self.root, self.raw_request(), str(self.key_path),
                    allow_live_read=True,
                ),
                "lark-live-disabled",
            )
            constructor.assert_not_called()

    def test_preflight_rejections_happen_before_transport_or_raw(self):
        stale = self.raw_request(generation="stale-generation")
        with mock.patch.object(lark_runtime, "_require_live_ingestion") as live, \
                mock.patch.object(lark_runtime, "LarkCliTransport") as constructor:
            self.assert_failed_without_commit(
                lambda: lark_runtime.ingest_lark_document(
                    self.root, stale, str(self.key_path), allow_live_read=True,
                ),
                "generation-lineage-mismatch",
            )
            live.assert_not_called()
            constructor.assert_not_called()

        other_root = self.private_root / "wrong-phase"
        wrong = create_task(other_root)
        wrong_raw = self.raw_request(generation=wrong.generation_id)
        with mock.patch.object(lark_runtime, "LarkCliTransport") as constructor:
            before = inspect_task(other_root).generation_id
            with self.assertRaises(ingestion.IngestionError) as caught:
                lark_runtime.ingest_lark_document(
                    other_root, wrong_raw, str(self.key_path),
                    allow_live_read=True,
                )
            self.assertEqual(caught.exception.code, "ingestion-invalid")
            self.assertEqual(inspect_task(other_root).generation_id, before)
            constructor.assert_not_called()

        _, first_transport = self.ingest()
        with mock.patch.object(lark_runtime, "_require_live_ingestion") as live, \
                mock.patch.object(lark_runtime, "LarkCliTransport") as constructor:
            self.assert_failed_without_commit(
                lambda: lark_runtime.ingest_lark_document(
                    self.root,
                    self.raw_request(generation=inspect_task(self.root).generation_id,
                                     transaction="duplicate-lark"),
                    str(self.key_path), allow_live_read=True,
                ),
                "source-already-ingested",
            )
            live.assert_not_called()
            constructor.assert_not_called()
        self.assertEqual(first_transport.raw_calls, 1)

    def test_invalid_ids_and_task_paths_fail_before_transport_and_keep_generation(self):
        invalid_requests = []
        for field, value in (
            ("transaction_id", "unsafe/transaction"),
            ("transaction_id", "unsafe\0transaction"),
            ("expected_generation_id", "unsafe/generation"),
            ("expected_generation_id", "suffix\0generation"),
        ):
            data = json.loads(self.raw_request().decode("utf-8"))
            data[field] = value
            invalid_requests.append((self.root, canonical_json(data)))
        invalid_requests.append((
            _UnsafePath(str(self.root) + "\0suffix"), self.raw_request(),
        ))

        before = inspect_task(self.root).generation_id
        for task_root, raw in invalid_requests:
            with self.subTest(root_type=type(task_root).__name__), mock.patch.object(
                lark_runtime, "LarkCliTransport",
            ) as constructor:
                with self.assertRaises(lark_runtime.LarkRuntimeError) as caught:
                    lark_runtime.ingest_lark_document(
                        task_root, raw, str(self.key_path), allow_live_read=True,
                    )
                self.assertIn(caught.exception.code, {
                    "invalid-lark-request", "local-identity-unavailable",
                })
                constructor.assert_not_called()
                self.assertEqual(inspect_task(self.root).generation_id, before)

    def test_identity_version_scope_owner_deadline_and_key_reject_before_raw(self):
        cases = (
            (SyntheticTransport(version="9.9.9"), None, "lark-cli-incompatible"),
            (SyntheticTransport(identities=(
                lark_cli_transport.VerifiedUser(OWNER, ("unrelated",)),
                lark_cli_transport.VerifiedUser(OWNER, ("unrelated",)),
            )), None, "lark-missing-scope"),
            (SyntheticTransport(observations=(
                lark_cli_transport.LarkObservation(TOKEN, "7", OTHER_OWNER),
                lark_cli_transport.LarkObservation(TOKEN, "7", OTHER_OWNER),
            )), None, "lark-owner-mismatch"),
            (SyntheticTransport(), self.raw_request(deadline=1000),
             "invalid-derived-deadline"),
        )
        for transport, raw, code in cases:
            with self.subTest(code=code):
                self.assert_failed_without_commit(
                    lambda transport=transport, raw=raw: self.ingest(
                        transport, raw=raw,
                    ),
                    code,
                )
                self.assertEqual(transport.raw_calls, 0)

        transport = SyntheticTransport()
        with mock.patch.object(
            runtime_support, "read_redaction_key",
            side_effect=runtime_support.RuntimeSupportError("unsafe-redaction-key"),
        ), mock.patch.object(lark_runtime, "LarkCliTransport") as constructor:
            self.assert_failed_without_commit(
                lambda: lark_runtime.ingest_lark_document(
                    self.root, self.raw_request(), str(self.key_path),
                    allow_live_read=True,
                ),
                "unsafe-redaction-key",
            )
            constructor.assert_not_called()
        self.assertEqual(transport.raw_calls, 0)

    def test_acquisition_rejects_identity_or_observation_change_without_commit(self):
        tampered_identity = lark_cli_transport.VerifiedUser(OWNER, SCOPES)
        object.__setattr__(tampered_identity, "open_id", _StringSubclass(OWNER))
        tampered_observation = lark_cli_transport.LarkObservation(
            TOKEN, "7", OWNER,
        )
        object.__setattr__(tampered_observation, "revision", _StringSubclass("7"))
        cases = (
            SyntheticTransport(identities=(
                lark_cli_transport.VerifiedUser(OWNER, SCOPES),
                lark_cli_transport.VerifiedUser(OTHER_OWNER, SCOPES),
            )),
            SyntheticTransport(identities=(
                lark_cli_transport.VerifiedUser(OWNER, SCOPES),
                tampered_identity,
            )),
            SyntheticTransport(observations=(
                lark_cli_transport.LarkObservation(TOKEN, "7", OWNER),
                lark_cli_transport.LarkObservation(TOKEN, "8", OWNER),
            )),
            SyntheticTransport(observations=(
                lark_cli_transport.LarkObservation(TOKEN, "7", OWNER),
                lark_cli_transport.LarkObservation(TOKEN, "7", OTHER_OWNER),
            )),
            SyntheticTransport(observations=(
                lark_cli_transport.LarkObservation(TOKEN, "7", OWNER),
                lark_cli_transport.LarkObservation(OTHER_TOKEN, "7", OWNER),
            )),
            SyntheticTransport(observations=(
                lark_cli_transport.LarkObservation(TOKEN, "7", OWNER),
                lark_cli_transport.LarkTransportError(
                    "invalid-lark-control-response"
                ),
            )),
            SyntheticTransport(observations=(
                lark_cli_transport.LarkObservation(TOKEN, "7", OWNER),
                tampered_observation,
            )),
        )
        for transport in cases:
            with self.subTest(calls=transport.calls):
                self.assert_failed_without_commit(
                    lambda transport=transport: self.ingest(transport),
                    "acquisition-failed",
                )
                if transport.calls[-1] == "verify-acquire":
                    self.assertEqual(transport.raw_calls, 0)
                else:
                    self.assertEqual(transport.raw_calls, 1)

    def test_acquisition_failures_and_adapter_mismatch_leave_generation_authoritative(self):
        cases = (
            (SyntheticTransport(raw=BufferError(OPEN_ID_DIAGNOSTIC)), None),
            (SyntheticTransport(raw=b"x" * (adapters.MAX_GRAPH_BYTES + 1)), None),
            (SyntheticTransport(profile=live_profile(product_version="9.9.9"),
                                version="9.9.9"), None),
            (SyntheticTransport(raw=canonical_json({
                "ok": True, "identity": "user",
                "data": {"content": "not a credential"},
            })), redaction.RedactionError("PRIVATE-REDACTION")),
        )
        for transport, redaction_failure in cases:
            with self.subTest(version=transport.version_value):
                patcher = (
                    mock.patch.object(redaction.Redactor, "redact",
                                      side_effect=redaction_failure)
                    if redaction_failure is not None else nullcontext()
                )
                with patcher:
                    self.assert_failed_without_commit(
                        lambda transport=transport: self.ingest(transport),
                    )

    def test_transport_evidence_gate_and_commit_crash_never_publish_partial_generation(self):
        for profile in (
            live_profile(endpoint_integrity_status="unverified"),
            live_profile(consistency_mode="unaccepted"),
        ):
            with self.subTest(profile=profile), mock.patch.object(
                lark_profile, "load_pinned_profile", return_value=profile,
            ), mock.patch.object(lark_runtime, "LarkCliTransport") as constructor:
                self.assert_failed_without_commit(
                    lambda: lark_runtime.ingest_lark_document(
                        self.root, self.raw_request(), str(self.key_path),
                        allow_live_read=True,
                    ),
                    "lark-live-disabled",
                )
                constructor.assert_not_called()

        transport = SyntheticTransport()
        with mock.patch.object(
            TaskCoordinator, "artifact_transaction",
            side_effect=RuntimeError(OPEN_ID_DIAGNOSTIC),
        ):
            self.assert_failed_without_commit(lambda: self.ingest(transport))
        self.assertEqual(transport.raw_calls, 1)

    def test_broker_callback_accepts_only_fixed_raw_command_and_zero_ambient_credentials(self):
        transport = SyntheticTransport()
        captured = {}
        original = brokers.fetch_lark

        def inspect_fetch(request, **kwargs):
            trusted_runner = kwargs["runner"]

            def capture_receipt(*args, **options):
                receipt = trusted_runner(*args, **options)
                captured["receipt"] = receipt
                return receipt

            captured.update(kwargs)
            kwargs["runner"] = capture_receipt
            return original(request, **kwargs)

        with mock.patch.object(brokers, "fetch_lark", side_effect=inspect_fetch):
            self.ingest(transport)

        context = captured["context"]
        binding = captured["credential_resolver"](
            active_principal=context.active_principal,
            tenant_account=context.tenant_account,
            required_variables=(),
        )
        self.assertEqual(
            binding,
            brokers.CredentialBinding(
                "user", context.active_principal, context.tenant_account, (),
            ),
        )
        self.assertEqual(captured["required_auth_variables"], ())
        expected_argv = (
            "lark-cli", "api", "GET",
            "/open-apis/docx/v1/documents/" + TOKEN + "/raw_content",
            "--as", "user",
        )
        self.assertEqual(
            lark_cli_transport._broker_raw_argv(
                lark_runtime.parse_document_selector(TOKEN)
            ),
            expected_argv,
        )
        receipt = captured["receipt"]
        self.assertEqual(receipt.active_principal, context.active_principal)
        self.assertEqual(receipt.tenant_account, context.tenant_account)
        self.assertEqual(receipt.content_owner, context.content_owner)
        self.assertEqual(receipt.selector_digest, context.selector)
        self.assertEqual((receipt.revision_before, receipt.revision_after),
                         (context.revision, context.revision))
        self.assertEqual(receipt.product_version, "1.0.86")
        self.assertEqual(receipt.native_schema_digest,
                         native_adapters.LARK_NATIVE_SCHEMA_DIGEST)
        self.assertIs(receipt.redirected, False)
        self.assertIs(receipt.fallback_principal, False)
        with self.assertRaises(lark_runtime.LarkRuntimeError):
            captured["runner"](
                ("lark-cli", "api", "GET", "/PRIVATE/arbitrary", "--as", "user"),
                env={}, shell=False, timeout=30,
                max_bytes=adapters.MAX_GRAPH_BYTES,
                allow_redirects=False, allow_fallback_principal=False,
            )
        raw_calls = transport.raw_calls
        valid_options = {
            "env": {}, "shell": False, "timeout": 30,
            "max_bytes": adapters.MAX_GRAPH_BYTES,
            "allow_redirects": False, "allow_fallback_principal": False,
        }
        for name, value in (
            ("shell", 0), ("timeout", 30.0),
            ("max_bytes", float(adapters.MAX_GRAPH_BYTES)),
            ("allow_redirects", 0), ("allow_fallback_principal", 0),
        ):
            with self.subTest(option=name), self.assertRaises(
                lark_runtime.LarkRuntimeError
            ):
                captured["runner"](
                    expected_argv, **{**valid_options, name: value},
                )
            self.assertEqual(transport.raw_calls, raw_calls)

    def test_expired_fresh_grant_fails_before_raw_content(self):
        transport = SyntheticTransport()
        with mock.patch.object(runtime_support, "effective_uid",
                               return_value=os.geteuid()), mock.patch.object(
            runtime_support, "runtime_time", side_effect=(1000, 1300)
        ), mock.patch.object(
            lark_runtime, "_require_live_ingestion",
            return_value=transport.profile,
        ), mock.patch.object(
            lark_runtime, "LarkCliTransport", return_value=transport,
        ):
            self.assert_failed_without_commit(
                lambda: lark_runtime.ingest_lark_document(
                    self.root, self.raw_request(), str(self.key_path),
                    allow_live_read=True,
                ),
                "acquisition-failed",
            )
        self.assertEqual(transport.raw_calls, 0)

    def test_codex_dispatch_is_rejected_without_session_read(self):
        captured = {}

        def stop_before_acquisition(root, request, *, acquire, redaction_key):
            captured["dispatch"] = acquire
            raise ingestion.IngestionError("acquisition-failed")

        transport = SyntheticTransport()
        with mock.patch.object(ingestion, "ingest_source",
                               side_effect=stop_before_acquisition):
            self.assert_failed_without_commit(lambda: self.ingest(transport),
                                              "acquisition-failed")
        with mock.patch.object(brokers, "read_local_session") as read:
            with self.assertRaises(ingestion.IngestionError) as caught:
                captured["dispatch"].codex(None, None, None, None)
        self.assertEqual(caught.exception.code, "unsupported-source-kind")
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
