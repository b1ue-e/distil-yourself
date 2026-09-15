"""End-to-end synthetic tests for per-source and dual-source atomic ingestion."""

from dataclasses import asdict, replace
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import (
    authorization, brokers, compiler, ingestion, lark_selector,
    native_adapters, sources,
)
from knowledge_distiller.journal import Journal, canonical_json
from knowledge_distiller.persistence import TaskCoordinator, create_task, inspect_task, recover_task
from knowledge_distiller.state import Event, Phase, TransitionFacts
from tests.test_knowledge import packet


def digest(value):
    raw = value if type(value) is bytes else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


TOKEN = "doxcn1234567890AbCdEfGhIjKl"
OTHER_TOKEN = "doxcn1234567890AbCdEfGhIjKm"
DOCUMENT_URL = "https://tenant.larkoffice.com/docx/" + TOKEN
LEGACY_TOKEN = "DocABC123"
LEGACY_DOCUMENT_URL = "https://tenant.larkoffice.com/docx/" + LEGACY_TOKEN


class IngestionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "task"
        initial = create_task(self.root)
        with TaskCoordinator(self.root) as coordinator:
            selected = coordinator.transition(
                Event.START_DISTILL, TransitionFacts(selected_capability=True))
            self.ingest_state = coordinator.transition(
                Event.CONTENT_GRANTED,
                TransitionFacts(content_grant=True, authority_valid=True))
        self.assertNotEqual(initial.generation_id, selected.generation_id)
        self.key = b"k" * 32
        self.owner = digest("owner")

    def authorization(
        self,
        selector,
        revision=None,
        session_range=None,
        revoked=False,
        expires_at=100,
        derived_processing_until=100,
    ):
        context = authorization.AuthorizationContext(
            task_id="task-1", active_principal="principal-1", tenant_account="tenant-1",
            selector=selector, purpose="distill-knowledge", revision=revision,
            session_range=session_range, now=20, task_active=True,
            authenticated_issuers=("principal-1", "authority-1"), content_owner=self.owner)
        common = {
            "task_id": "task-1", "active_principal": "principal-1",
            "tenant_account": "tenant-1", "selector": selector,
            "purpose": "distill-knowledge", "revision": revision,
            "session_range": None if session_range is None else asdict(session_range),
            "issued_at": 10, "expires_at": expires_at,
            "derived_processing_until": derived_processing_until,
            "revoked": revoked,
        }
        grant = dict(common, record_type="content-grant", record_id="grant-1",
                     issuer="principal-1", operation="read-content")
        grant["decision_digest"] = digest(canonical_json(grant))
        attestation = dict(common, record_type="authority-attestation",
                           record_id="authority-1", issuer="authority-1",
                           operation="process-third-party", content_owner=self.owner,
                           authority_basis="verified-ownership")
        attestation["decision_digest"] = digest(canonical_json(attestation))
        return context, grant, attestation

    def snapshot(self, raw, product, version, schema, project=None):
        selector = TOKEN if product == "lark" else "session.jsonl"
        return brokers.BrokerSnapshot(
            raw=raw, owner=sources.OwnerBinding("user", self.owner, "verified-principal"),
            selector_digest=digest(selector), raw_digest=digest(raw),
            source_snapshot_id=digest(raw), source_byte_count=len(raw),
            evidence=brokers.NativeEvidence(product, version, schema, project))

    def ingest(self, request, acquired):
        calls = []

        def handler(source_kind):
            def acquire(native_request, grant, attestation, context):
                calls.append(source_kind)
                return acquired
            return acquire

        result = ingestion.ingest_source(
            self.root, request, acquire=ingestion.AcquisitionDispatch(
                lark=handler("lark"), codex=handler("codex")),
            redaction_key=self.key)
        return result, calls

    def test_legacy_lark_bare_token_is_redacted_and_atomically_persisted(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"password=synthetic-secret"}}'
        context, grant, attestation = self.authorization(
            LEGACY_TOKEN, revision="3365")
        request = ingestion.IngestionRequest(
            source_kind="lark", transaction_id="ingest-lark",
            expected_generation_id=self.ingest_state.generation_id,
            authorization_context=context, content_grant=grant,
            authority_attestation=attestation,
            native_request=brokers.LarkRequest(LEGACY_TOKEN, "3365"),
            native_locator_id=digest("document-token"))
        acquired = replace(
            self.snapshot(
                raw, "lark", "1.0.86",
                native_adapters.LARK_NATIVE_SCHEMA_DIGEST),
            selector_digest=digest(LEGACY_TOKEN))

        with mock.patch.object(
                lark_selector, "parse_document_selector",
                side_effect=AssertionError("new parser used for legacy selector")) as parser:
            result, calls = self.ingest(request, acquired)
        parser.assert_not_called()

        self.assertEqual(calls, ["lark"])
        self.assertEqual((result.status, result.source_kind, result.source_item_count),
                         ("snapshotted", "document", 1))
        self.assertEqual(inspect_task(self.root).generation_id, result.generation_id)
        generation = self.root / "generations" / result.generation_id
        persisted = b"".join(path.read_bytes() for path in generation.rglob("*") if path.is_file())
        self.assertNotIn(b"synthetic-secret", persisted)
        self.assertIn(b"[redacted:credential:hmac-sha256:", persisted)
        provenance = json.loads(
            (generation / "provenance/lark-spans.json").read_text(encoding="utf-8"))
        self.assertEqual(len(provenance["spans"]), 1)
        self.assertEqual(len(provenance["spans"][0]["derivations"]), 5)
        evidence = json.loads(
            (generation / "evidence/lark-native.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["source_snapshot_id"], digest(raw))
        self.assertEqual(acquired.selector_digest, digest(LEGACY_TOKEN))
        self.assertNotIn(b"synthetic-secret", (self.root / "event-log.frames").read_bytes())

    def test_legacy_lark_url_preserves_old_selector_digest(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        context, grant, attestation = self.authorization(
            LEGACY_DOCUMENT_URL, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-lark-legacy-url",
            self.ingest_state.generation_id, context, grant, attestation,
            brokers.LarkRequest(LEGACY_DOCUMENT_URL, "3365"),
            digest("document-token"))
        acquired = replace(
            self.snapshot(
                raw, "lark", "1.0.86",
                native_adapters.LARK_NATIVE_SCHEMA_DIGEST),
            selector_digest=digest(LEGACY_DOCUMENT_URL))

        substituted = replace(
            request, transaction_id="ingest-lark-legacy-substitution",
            native_request=brokers.LarkRequest("OtherDoc456", "3365"))
        with self.assertRaises(ingestion.IngestionError) as caught:
            self.ingest(substituted, acquired)
        self.assertEqual(caught.exception.code, "broker-evidence-mismatch")
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

        with mock.patch.object(
                lark_selector, "parse_document_selector",
                side_effect=AssertionError("new parser used for legacy selector")) as parser:
            result, calls = self.ingest(request, acquired)
        parser.assert_not_called()

        self.assertEqual(calls, ["lark"])
        self.assertEqual(result.status, "snapshotted")
        self.assertEqual(
            acquired.selector_digest, digest(LEGACY_DOCUMENT_URL))

    def test_lark_committed_selector_hides_token(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        committed = lark_selector.parse_document_selector(DOCUMENT_URL)
        context, grant, attestation = self.authorization(
            committed.commitment, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-lark-committed", self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(DOCUMENT_URL, "3365"), digest("document-token"))
        acquired = replace(
            self.snapshot(
                raw, "lark", "1.0.86",
                native_adapters.LARK_NATIVE_SCHEMA_DIGEST),
            selector_digest=committed.commitment)

        rejected = (
            replace(
                request, transaction_id="ingest-lark-other-token",
                native_request=brokers.LarkRequest(OTHER_TOKEN, "3365")),
            request,
        )
        rejected_snapshots = (
            acquired,
            replace(acquired, selector_digest=digest(committed.commitment)),
        )
        for changed_request, changed_snapshot in zip(
                rejected, rejected_snapshots):
            with self.subTest(transaction_id=changed_request.transaction_id):
                with self.assertRaises(ingestion.IngestionError) as caught:
                    self.ingest(changed_request, changed_snapshot)
                self.assertEqual(caught.exception.code, "broker-evidence-mismatch")
                self.assertEqual(
                    inspect_task(self.root).generation_id,
                    self.ingest_state.generation_id)

        result, calls = self.ingest(request, acquired)

        self.assertEqual(calls, ["lark"])
        generation = self.root / "generations" / result.generation_id
        generation_bytes = b"".join(
            path.read_bytes() for path in generation.rglob("*") if path.is_file())
        self.assertNotIn(TOKEN.encode("ascii"), generation_bytes)
        self.assertNotIn(DOCUMENT_URL.encode("ascii"), generation_bytes)

    def test_lark_snapshot_binding_is_independent_of_broker_helper(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        committed = lark_selector.parse_document_selector(TOKEN)
        context, grant, attestation = self.authorization(
            committed.commitment, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-independent-binding",
            self.ingest_state.generation_id, context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        acquired = replace(
            self.snapshot(
                raw, "lark", "1.0.86",
                native_adapters.LARK_NATIVE_SCHEMA_DIGEST),
            selector_digest=digest(committed.commitment))

        with mock.patch.object(
                brokers, "_lark_selector_binding",
                side_effect=AssertionError("broker helper must not be used")) as helper, \
             mock.patch.object(
                 ingestion, "_normalize", wraps=ingestion._normalize) as normalize:
            with self.assertRaises(ingestion.IngestionError) as caught:
                self.ingest(request, acquired)

        self.assertEqual(caught.exception.code, "broker-evidence-mismatch")
        helper.assert_not_called()
        normalize.assert_not_called()
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

    def test_lark_revision_is_independently_typed_and_bound(self):
        class RevisionSubclass(str):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        committed = lark_selector.parse_document_selector(TOKEN)
        context, grant, attestation = self.authorization(
            committed.commitment, revision="3365")
        acquired = replace(
            self.snapshot(
                raw, "lark", "1.0.86",
                native_adapters.LARK_NATIVE_SCHEMA_DIGEST),
            selector_digest=committed.commitment)
        cases = (
            (True, "invalid-broker-request"),
            (3365, "invalid-broker-request"),
            (None, "invalid-broker-request"),
            ("", "invalid-broker-request"),
            ("03365", "invalid-broker-request"),
            ("-1", "invalid-broker-request"),
            ("1" * 21, "invalid-broker-request"),
            (RevisionSubclass("3365"), "invalid-broker-request"),
            ("3366", "broker-evidence-mismatch"),
        )

        for index, (revision, code) in enumerate(cases):
            with self.subTest(revision=repr(revision), code=code):
                request = ingestion.IngestionRequest(
                    "lark", "ingest-revision-%d" % index,
                    self.ingest_state.generation_id,
                    context, grant, attestation,
                    brokers.LarkRequest(TOKEN, revision),
                    digest("document-token"))
                with self.assertRaises(ingestion.IngestionError) as caught:
                    self.ingest(request, acquired)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(
                    inspect_task(self.root).generation_id,
                    self.ingest_state.generation_id)

    def test_private_request_runtime_decodes_then_uses_the_core_transaction(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-runtime", self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        acquired = self.snapshot(
            raw, "lark", "1.0.86", native_adapters.LARK_NATIVE_SCHEMA_DIGEST)
        calls = []
        runtime = ingestion.IngestionRuntime(
            request_decoder=lambda encoded: (calls.append(("decode", encoded)), request)[1],
            acquire=ingestion.AcquisitionDispatch(
                lark=lambda *args: (calls.append(("acquire", "lark")), acquired)[1],
                codex=lambda *args: (calls.append(("acquire", "codex")), acquired)[1]),
            redaction_key=self.key)

        result = ingestion.ingest_request_bytes(
            self.root, b'{"private":"request"}', runtime=runtime)

        self.assertEqual(calls, [("decode", b'{"private":"request"}'), ("acquire", "lark")])
        self.assertEqual(result.status, "snapshotted")

    def test_writer_lease_prevents_a_second_source_read(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-lease", self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        acquired = self.snapshot(
            raw, "lark", "1.0.86", native_adapters.LARK_NATIVE_SCHEMA_DIGEST)
        calls = []

        def nested_read(*args):
            calls.append("nested-read")
            return acquired

        nested_dispatch = ingestion.AcquisitionDispatch(
            lark=nested_read, codex=nested_read)

        def outer_read(*args):
            calls.append("outer-read")
            with self.assertRaises(ingestion.IngestionError) as caught:
                ingestion.ingest_source(
                    self.root, request, acquire=nested_dispatch,
                    redaction_key=self.key)
            self.assertEqual(caught.exception.code, "task-busy")
            return acquired

        result = ingestion.ingest_source(
            self.root, request,
            acquire=ingestion.AcquisitionDispatch(
                lark=outer_read, codex=lambda *args: acquired),
            redaction_key=self.key)

        self.assertEqual(result.status, "snapshotted")
        self.assertEqual(calls, ["outer-read"])

    def test_invalid_redaction_key_fails_before_source_read(self):
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-invalid-key", self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        calls = []

        with self.assertRaises(ingestion.IngestionError) as caught:
            ingestion.ingest_source(
                self.root, request,
                acquire=ingestion.AcquisitionDispatch(
                    lark=lambda *args: calls.append("lark"),
                    codex=lambda *args: calls.append("codex")),
                redaction_key=b"short")

        self.assertEqual(caught.exception.code, "ingestion-runtime-invalid")
        self.assertEqual(calls, [])

    def test_acquisition_cannot_mutate_pinned_authorization_or_request(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-mutation", self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        acquired = self.snapshot(
            raw, "lark", "1.0.86", native_adapters.LARK_NATIVE_SCHEMA_DIGEST)
        object.__setattr__(acquired, "selector_digest", digest("other-token"))

        def mutate(native_request, content_grant, authority_attestation, callback_context):
            object.__setattr__(native_request, "selector", "other-token")
            object.__setattr__(callback_context, "selector", "other-token")
            object.__setattr__(content_grant, "decision_digest", digest("poisoned-grant"))
            object.__setattr__(authority_attestation, "decision_digest",
                               digest("poisoned-attestation"))
            return acquired

        with self.assertRaises(ingestion.IngestionError) as caught:
            ingestion.ingest_source(
                self.root, request,
                acquire=ingestion.AcquisitionDispatch(
                    lark=mutate, codex=lambda *args: acquired),
                redaction_key=self.key)

        self.assertEqual(caught.exception.code, "broker-evidence-mismatch")
        self.assertEqual(context.selector, TOKEN)
        self.assertEqual(request.native_request.selector, TOKEN)
        self.assertEqual(inspect_task(self.root).generation_id,
                         self.ingest_state.generation_id)

    def test_codex_snapshot_uses_exact_adapter_and_persists_no_raw_content(self):
        raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/rollout-jsonl-v1/"
               "redacted-current.jsonl").read_bytes()
        bounds = authorization.SessionRange(0, len(raw) - 1)
        context, grant, attestation = self.authorization("session.jsonl", session_range=bounds)
        request = ingestion.IngestionRequest(
            source_kind="codex", transaction_id="ingest-codex",
            expected_generation_id=self.ingest_state.generation_id,
            authorization_context=context, content_grant=grant,
            authority_attestation=attestation,
            native_request=brokers.SessionRequest(
                "session.jsonl", "project-1", len(raw), digest(raw)),
            native_locator_id="project-1")
        acquired = self.snapshot(
            raw, "codex", "0.153.0", native_adapters.CODEX_NATIVE_SCHEMA_DIGEST,
            "project-1")

        result, calls = self.ingest(request, acquired)

        self.assertEqual(calls, ["codex"])
        self.assertEqual((result.status, result.source_kind, result.source_item_count),
                         ("snapshotted", "session", 5))
        generation = self.root / "generations" / result.generation_id
        evidence = json.loads(
            (generation / "evidence/codex-native.json").read_text(encoding="utf-8"))
        provenance = json.loads(
            (generation / "provenance/codex-spans.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["native"]["project_id"], "project-1")
        self.assertEqual(evidence["source_snapshot_id"], digest(raw))
        self.assertEqual(len(provenance["spans"]), 5)
        self.assertTrue(all(len(span["derivations"]) == 5
                            for span in provenance["spans"]))
        journal = (self.root / "event-log.frames").read_bytes()
        self.assertNotIn(b"Prefer a bounded synthetic check", journal)
        self.assertEqual(
            [record.payload["kind"] for record in Journal(self.root / "event-log.frames").scan().records]
            .count("commit"), 4)

    def test_codex_path_commitment_is_rechecked_and_hides_filesystem_path(self):
        raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
               "rollout-jsonl-v1/redacted-current.jsonl").read_bytes()
        private_path = "/PRIVATE/session.jsonl"
        commitment = brokers.session_selector_commitment(private_path, self.key)
        bounds = authorization.SessionRange(0, len(raw) - 1)
        context, grant, attestation = self.authorization(
            commitment, session_range=bounds)
        request = ingestion.IngestionRequest(
            "codex", "ingest-committed-selector",
            self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.SessionRequest(
                private_path, "project-1", len(raw), digest(raw)),
            "project-1")
        acquired = replace(
            self.snapshot(
                raw, "codex", "0.153.0",
                native_adapters.CODEX_NATIVE_SCHEMA_DIGEST, "project-1"),
            selector_digest=digest(commitment))

        changed_request = replace(
            request,
            transaction_id="ingest-substituted-selector",
            native_request=replace(
                request.native_request, path="/PRIVATE/other.jsonl"))
        with self.assertRaises(ingestion.IngestionError) as caught:
            self.ingest(changed_request, acquired)
        self.assertEqual(caught.exception.code, "broker-evidence-mismatch")
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

        result, calls = self.ingest(request, acquired)

        self.assertEqual(calls, ["codex"])
        generation = self.root / "generations" / result.generation_id
        persisted = b"".join(
            path.read_bytes() for path in self.root.rglob("*") if path.is_file())
        self.assertNotIn(private_path.encode("utf-8"), persisted)
        self.assertNotIn(self.key, persisted)
        generation_bytes = b"".join(
            path.read_bytes() for path in generation.rglob("*") if path.is_file())
        self.assertIn(commitment.encode("ascii"), generation_bytes)

    def test_codex_path_subclass_cannot_bypass_commitment_binding(self):
        class SubstitutedPath(str):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
               "rollout-jsonl-v1/redacted-current.jsonl").read_bytes()
        private_path = "/PRIVATE/session.jsonl"
        commitment = brokers.session_selector_commitment(private_path, self.key)
        bounds = authorization.SessionRange(0, len(raw) - 1)
        context, grant, attestation = self.authorization(
            commitment, session_range=bounds)
        request = ingestion.IngestionRequest(
            "codex", "ingest-subclass-selector",
            self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.SessionRequest(
                SubstitutedPath("/PRIVATE/other.jsonl"), "project-1",
                len(raw), digest(raw)),
            "project-1")
        acquired = replace(
            self.snapshot(
                raw, "codex", "0.153.0",
                native_adapters.CODEX_NATIVE_SCHEMA_DIGEST, "project-1"),
            selector_digest=digest(commitment))

        with mock.patch.object(
                ingestion, "_normalize", wraps=ingestion._normalize) as normalize:
            with self.assertRaises(ingestion.IngestionError) as caught:
                self.ingest(request, acquired)

        self.assertEqual(caught.exception.code, "invalid-broker-request")
        normalize.assert_not_called()
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

    def test_codex_over_returned_snapshot_is_rejected_before_normalization(self):
        raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
               "rollout-jsonl-v1/redacted-current.jsonl").read_bytes()
        prefix_length = len(raw) - 1
        bounds = authorization.SessionRange(0, prefix_length - 1)
        context, grant, attestation = self.authorization(
            "session.jsonl", session_range=bounds)
        request = ingestion.IngestionRequest(
            "codex", "ingest-over-returned-selector",
            self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.SessionRequest(
                "session.jsonl", "project-1", prefix_length, digest(raw)),
            "project-1")
        acquired = self.snapshot(
            raw, "codex", "0.153.0",
            native_adapters.CODEX_NATIVE_SCHEMA_DIGEST, "project-1")

        with mock.patch.object(
                ingestion, "_normalize", wraps=ingestion._normalize) as normalize:
            with self.assertRaises(ingestion.IngestionError) as caught:
                self.ingest(request, acquired)

        self.assertEqual(caught.exception.code, "broker-evidence-mismatch")
        normalize.assert_not_called()
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

    def test_codex_boolean_prefix_length_is_rejected_before_normalization(self):
        raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
               "rollout-jsonl-v1/redacted-current.jsonl").read_bytes()
        context, grant, attestation = self.authorization(
            "session.jsonl", session_range=authorization.SessionRange(0, 0))
        request = ingestion.IngestionRequest(
            "codex", "ingest-boolean-prefix",
            self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.SessionRequest(
                "session.jsonl", "project-1", True, digest(raw)),
            "project-1")
        acquired = self.snapshot(
            raw, "codex", "0.153.0",
            native_adapters.CODEX_NATIVE_SCHEMA_DIGEST, "project-1")

        with mock.patch.object(
                ingestion, "_normalize", wraps=ingestion._normalize) as normalize:
            with self.assertRaises(ingestion.IngestionError) as caught:
                self.ingest(request, acquired)

        self.assertEqual(caught.exception.code, "invalid-broker-request")
        normalize.assert_not_called()
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

    def test_codex_integer_subclass_prefix_length_is_rejected_before_normalization(self):
        class PrefixLength(int):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
               "rollout-jsonl-v1/redacted-current.jsonl").read_bytes()
        bounds = authorization.SessionRange(0, len(raw) - 1)
        context, grant, attestation = self.authorization(
            "session.jsonl", session_range=bounds)
        acquired = self.snapshot(
            raw, "codex", "0.153.0",
            native_adapters.CODEX_NATIVE_SCHEMA_DIGEST, "project-1")
        request = ingestion.IngestionRequest(
            "codex", "ingest-subclass-prefix-length",
            self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.SessionRequest(
                "session.jsonl", "project-1", PrefixLength(1), digest(raw)),
            "project-1")

        with mock.patch.object(
                ingestion, "_normalize", wraps=ingestion._normalize) as normalize:
            with self.assertRaises(ingestion.IngestionError) as caught:
                self.ingest(request, acquired)

        self.assertEqual(caught.exception.code, "invalid-broker-request")
        normalize.assert_not_called()
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

    def test_codex_string_subclass_prefix_digest_is_rejected_before_normalization(self):
        class PrefixDigest(str):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
               "rollout-jsonl-v1/redacted-current.jsonl").read_bytes()
        bounds = authorization.SessionRange(0, len(raw) - 1)
        context, grant, attestation = self.authorization(
            "session.jsonl", session_range=bounds)
        acquired = self.snapshot(
            raw, "codex", "0.153.0",
            native_adapters.CODEX_NATIVE_SCHEMA_DIGEST, "project-1")
        request = ingestion.IngestionRequest(
            "codex", "ingest-subclass-prefix-digest",
            self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.SessionRequest(
                "session.jsonl", "project-1", len(raw),
                PrefixDigest("sha256:" + "0" * 64)),
            "project-1")

        with mock.patch.object(
                ingestion, "_normalize", wraps=ingestion._normalize) as normalize:
            with self.assertRaises(ingestion.IngestionError) as caught:
                self.ingest(request, acquired)

        self.assertEqual(caught.exception.code, "invalid-broker-request")
        normalize.assert_not_called()
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

    def test_codex_commitment_tag_cannot_be_used_as_literal_path(self):
        raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
               "rollout-jsonl-v1/redacted-current.jsonl").read_bytes()
        private_path = "/PRIVATE/session.jsonl"
        commitment = "hmac-sha256:" + hmac.new(
            self.key,
            b"local-session-selector\x00" + private_path.encode("utf-8"),
            hashlib.sha256).hexdigest()
        bounds = authorization.SessionRange(0, len(raw) - 1)
        context, grant, attestation = self.authorization(
            commitment, session_range=bounds)
        request = ingestion.IngestionRequest(
            "codex", "ingest-commitment-as-literal",
            self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.SessionRequest(
                commitment, "project-1", len(raw), digest(raw)),
            "project-1")
        acquired = replace(
            self.snapshot(
                raw, "codex", "0.153.0",
                native_adapters.CODEX_NATIVE_SCHEMA_DIGEST, "project-1"),
            selector_digest=digest(commitment))

        with mock.patch.object(
                ingestion, "_normalize", wraps=ingestion._normalize) as normalize:
            with self.assertRaises(ingestion.IngestionError) as caught:
                self.ingest(request, acquired)

        self.assertEqual(caught.exception.code, "broker-evidence-mismatch")
        normalize.assert_not_called()
        self.assertEqual(
            inspect_task(self.root).generation_id,
            self.ingest_state.generation_id)

    def test_actual_dual_ingestion_outputs_compile_to_closed_draft(self):
        processing_until = 4_102_444_800
        lark_raw = (
            b'{"ok":true,"identity":"user","data":'
            b'{"content":"Synthetic situational context."}}')
        lark_context, lark_grant, lark_attestation = self.authorization(
            TOKEN, revision="3365",
            derived_processing_until=processing_until)
        lark_request = ingestion.IngestionRequest(
            "lark", "ingest-dual-lark", self.ingest_state.generation_id,
            lark_context, lark_grant, lark_attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        lark_snapshot = self.snapshot(
            lark_raw, "lark", "1.0.86", native_adapters.LARK_NATIVE_SCHEMA_DIGEST)

        first, first_calls = self.ingest(lark_request, lark_snapshot)

        first_state = inspect_task(self.root)
        self.assertEqual(first_calls, ["lark"])
        self.assertEqual(first_state.state.phase, Phase.INGEST)
        self.assertEqual(first_state.generation_id, first.generation_id)

        duplicate = ingestion.IngestionRequest(
            **{**vars(lark_request), "expected_generation_id": first.generation_id})
        duplicate_calls = []
        with self.assertRaises(ingestion.IngestionError) as caught:
            ingestion.preflight_ingestion_slot(
                self.root, first.generation_id, "lark")
        self.assertEqual(caught.exception.code, "source-already-ingested")

        with self.assertRaises(ingestion.IngestionError) as caught:
            ingestion.ingest_source(
                self.root, duplicate,
                acquire=ingestion.AcquisitionDispatch(
                    lark=lambda *args: duplicate_calls.append("lark"),
                    codex=lambda *args: duplicate_calls.append("codex")),
                redaction_key=self.key)
        self.assertEqual(caught.exception.code, "source-already-ingested")
        self.assertEqual(duplicate_calls, [])

        codex_raw = (ROOT / "tests/fixtures/adapters/codex/0.153.0/rollout-jsonl-v1/"
                     "redacted-current.jsonl").read_bytes()
        bounds = authorization.SessionRange(0, len(codex_raw) - 1)
        codex_context, codex_grant, codex_attestation = self.authorization(
            "session.jsonl", session_range=bounds,
            derived_processing_until=processing_until)
        codex_request = ingestion.IngestionRequest(
            "codex", "ingest-dual-codex", first.generation_id,
            codex_context, codex_grant, codex_attestation,
            brokers.SessionRequest(
                "session.jsonl", "project-1", len(codex_raw), digest(codex_raw)),
            "project-1")
        invalid_snapshot = self.snapshot(
            codex_raw, "codex", "0.153.0", digest("schema-drift"), "project-1")

        with self.assertRaises(ingestion.IngestionError):
            self.ingest(codex_request, invalid_snapshot)
        self.assertEqual(inspect_task(self.root).generation_id, first.generation_id)

        codex_snapshot = self.snapshot(
            codex_raw, "codex", "0.153.0",
            native_adapters.CODEX_NATIVE_SCHEMA_DIGEST, "project-1")
        second, second_calls = self.ingest(codex_request, codex_snapshot)

        completed = inspect_task(self.root)
        self.assertEqual(second_calls, ["codex"])
        self.assertEqual(completed.state.phase, Phase.CAPABILITY_REVIEW)
        generation = self.root / "generations" / second.generation_id
        expected = {
            "sources/lark-snapshot.json", "sources/codex-snapshot.json",
            "evidence/lark-native.json", "evidence/codex-native.json",
            "provenance/lark-spans.json", "provenance/codex-spans.json",
            "grants/lark-content-grant.json", "grants/lark-authority-attestation.json",
            "grants/codex-content-grant.json", "grants/codex-authority-attestation.json",
        }
        self.assertTrue(all((generation / path).is_file() for path in expected))

        lark_provenance = json.loads(
            (generation / "provenance/lark-spans.json").read_text(
                encoding="utf-8"))
        codex_provenance = json.loads(
            (generation / "provenance/codex-spans.json").read_text(
                encoding="utf-8"))
        lark_span = lark_provenance["spans"][0]
        codex_span = next(
            item for item in codex_provenance["spans"]
            if item["claim_eligible"])
        self.assertFalse(lark_span["claim_eligible"])

        with TaskCoordinator(self.root) as coordinator:
            selected = coordinator.transition(
                Event.CAPABILITY_SELECTED,
                TransitionFacts(selected_capability=True))
            review = coordinator.transition(
                Event.EVIDENCE_EXTRACTED,
                TransitionFacts(evidence_complete=True))
        self.assertNotEqual(selected.generation_id, review.generation_id)

        value = packet()
        value["questions"] = []
        source_records = (
            (value["evidence"][0], lark_provenance, lark_span, "observation"),
            (value["evidence"][1], codex_provenance, codex_span, "owner-statement"),
        )
        for evidence_record, provenance, span_record, evidence_type in source_records:
            evidence_record.update({
                "source_snapshot_id": provenance["source_snapshot_id"],
                "redacted_span_ids": [span_record["span_id"]],
                "evidence_type": evidence_type,
                "excerpt": span_record["text"],
            })
        for claim in value["claims"]:
            claim.update({
                "redacted_span_ids": [codex_span["span_id"]],
                "support_evidence_ids": ["ev-session"],
                "contradiction_evidence_ids": ["ev-document"],
            })
        raw_packet = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        adjudicated = compiler.adjudicate_knowledge_packet(
            self.root, raw_packet, "adjudicate-actual-ingestion",
            review.generation_id)
        compiled = compiler.compile_capability(
            self.root, raw_packet, "compile-actual-ingestion",
            adjudicated.generation_id)

        self.assertEqual(compiled.phase, "evaluate")
        self.assertNotIn(
            lark_span["text"],
            b"\n".join(compiled.draft_files.values()).decode("utf-8"),
        )
        compiled_generation = self.root / "generations" / compiled.generation_id
        self.assertTrue(
            (compiled_generation / "model/compiled-rule-provenance.json").is_file())

    def test_wrong_task_phase_fails_before_source_read(self):
        root = Path(self.directory.name) / "wrong-phase-task"
        create_task(root)
        with TaskCoordinator(root) as coordinator:
            review = coordinator.transition(
                Event.START_DISTILL,
                TransitionFacts(selected_capability=True, authorized_snapshots=True))
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-wrong-phase", review.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        calls = []

        with self.assertRaises(ingestion.IngestionError) as caught:
            ingestion.preflight_ingestion_slot(root, review.generation_id, "lark")
        self.assertEqual(caught.exception.code, "ingestion-invalid")

        with self.assertRaises(ingestion.IngestionError) as caught:
            ingestion.ingest_source(
                root, request,
                acquire=ingestion.AcquisitionDispatch(
                    lark=lambda *args: calls.append("lark"),
                    codex=lambda *args: calls.append("codex")),
                redaction_key=self.key)

        self.assertEqual(caught.exception.code, "ingestion-invalid")
        self.assertEqual(calls, [])

    def test_preflight_accepts_supported_kinds_and_releases_its_lease(self):
        before = inspect_task(self.root)
        for source_kind in ("lark", "codex"):
            with self.subTest(source_kind=source_kind):
                ingestion.preflight_ingestion_slot(
                    self.root, before.generation_id, source_kind)
        after = inspect_task(self.root)
        self.assertEqual(
            (after.generation_id, after.manifest_digest, after.state),
            (before.generation_id, before.manifest_digest, before.state),
        )

    def test_preflight_maps_malformed_generation_identifiers(self):
        for generation_id in ("", 123):
            with self.subTest(generation_id=generation_id):
                with self.assertRaises(ingestion.IngestionError) as caught:
                    ingestion.preflight_ingestion_slot(
                        self.root, generation_id, "lark")
            self.assertEqual(caught.exception.code, "ingestion-failed")

    def test_preflight_rejects_a_held_task_lease(self):
        with TaskCoordinator(self.root):
            with self.assertRaises(ingestion.IngestionError) as caught:
                ingestion.preflight_ingestion_slot(
                    self.root, self.ingest_state.generation_id, "lark")
        self.assertEqual(caught.exception.code, "task-busy")

    def test_revoked_grant_and_adapter_failure_never_advance_or_leave_staging(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        for revoked, failure in ((True, None), (False, RuntimeError("adapter-failed"))):
            with self.subTest(revoked=revoked):
                context, grant, attestation = self.authorization(
                    TOKEN, revision="3365", revoked=revoked)
                request = ingestion.IngestionRequest(
                    "lark", "ingest-failure", self.ingest_state.generation_id,
                    context, grant, attestation,
                    brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
                calls = []

                def acquire(*args):
                    calls.append("called")
                    if failure:
                        raise failure
                    return self.snapshot(
                        raw, "lark", "1.0.86", native_adapters.LARK_NATIVE_SCHEMA_DIGEST)

                with self.assertRaises(ingestion.IngestionError):
                    ingestion.ingest_source(
                        self.root, request, acquire=ingestion.AcquisitionDispatch(
                            lark=acquire, codex=acquire), redaction_key=self.key)
                self.assertEqual(calls, [] if revoked else ["called"])
                self.assertEqual(inspect_task(self.root).generation_id,
                                 self.ingest_state.generation_id)
                staging = self.root / "raw-staging"
                self.assertFalse(staging.exists() and any(staging.rglob("f-*")))

    def test_stale_generation_and_unknown_kind_fail_before_acquisition(self):
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        class SourceKindSubclass(str):
            def __radd__(self, other):
                return "evidence/substituted.json"

        rejection_cases = (
                ("lark", "g-stale", "generation-lineage-mismatch"),
                ("unknown", self.ingest_state.generation_id, "unsupported-source-kind"),
                (SourceKindSubclass("lark"), self.ingest_state.generation_id,
                 "unsupported-source-kind"))
        for kind, generation, code in rejection_cases:
            request = ingestion.IngestionRequest(
                kind, "ingest-preflight", generation, context, grant, attestation,
                brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
            calls = []
            with self.assertRaises(ingestion.IngestionError) as caught:
                ingestion.ingest_source(
                    self.root, request, acquire=ingestion.AcquisitionDispatch(
                        lark=lambda *args: calls.append(args),
                        codex=lambda *args: calls.append(args)),
                    redaction_key=self.key)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(calls, [])

        for kind, generation, code in rejection_cases:
            with self.subTest(preflight_kind=kind):
                with self.assertRaises(ingestion.IngestionError) as caught:
                    ingestion.preflight_ingestion_slot(self.root, generation, kind)
                self.assertEqual(caught.exception.code, code)

    def test_expiry_principal_schema_and_source_drift_never_commit(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        base = ingestion.IngestionRequest(
            "lark", "ingest-drift", self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        expired_context = authorization.AuthorizationContext(
            **{**asdict(context), "session_range": context.session_range, "now": 100})
        principal_context = authorization.AuthorizationContext(
            **{**asdict(context), "session_range": context.session_range,
               "active_principal": "different-principal"})
        bad_schema = self.snapshot(raw, "lark", "1.0.86", digest("schema-drift"))
        bad_source = self.snapshot(raw, "lark", "1.0.86",
                                   native_adapters.LARK_NATIVE_SCHEMA_DIGEST)
        object.__setattr__(bad_source, "raw_digest", digest("other-bytes"))
        cases = (
            (ingestion.IngestionRequest(**{**vars(base), "authorization_context": expired_context}),
             None, "authorization-expired"),
            (ingestion.IngestionRequest(**{**vars(base), "authorization_context": principal_context}),
             None, "authorization-context-mismatch"),
            (base, bad_schema, "unsupported-adapter-version"),
            (base, bad_source, "snapshot-binding-mismatch"),
        )
        for request, acquired, code in cases:
            with self.subTest(code=code):
                calls = []
                with self.assertRaises(ingestion.IngestionError) as caught:
                    ingestion.ingest_source(
                        self.root, request,
                        acquire=ingestion.AcquisitionDispatch(
                            lark=lambda *args, acquired=acquired: (
                                calls.append("called"), acquired)[1],
                            codex=lambda *args: None),
                        redaction_key=self.key)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(calls, [] if acquired is None else ["called"])
                self.assertEqual(inspect_task(self.root).generation_id,
                                 self.ingest_state.generation_id)

    def test_snapshot_scope_and_nested_evidence_are_closed_before_normalization(self):
        raw = b'{"ok":true,"identity":"user","data":{"content":"safe"}}'
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-scope", self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        for mutation, code in (
                ("selector", "broker-evidence-mismatch"),
                ("nested", "broker-response-invalid"),
                ("scalar-subclass", "broker-response-invalid")):
            acquired = self.snapshot(
                raw, "lark", "1.0.86", native_adapters.LARK_NATIVE_SCHEMA_DIGEST)
            if mutation == "selector":
                object.__setattr__(acquired, "selector_digest", digest("other-selector"))
            else:
                if mutation == "nested":
                    object.__setattr__(acquired.evidence, "private_extra", "synthetic-secret")
                else:
                    class ProductSubclass(str):
                        pass
                    object.__setattr__(acquired.evidence, "product", ProductSubclass("lark"))
            with self.subTest(mutation=mutation):
                with self.assertRaises(ingestion.IngestionError) as caught:
                    self.ingest(request, acquired)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(inspect_task(self.root).generation_id,
                                 self.ingest_state.generation_id)

    def test_partial_native_input_and_crash_recovery_preserve_prior_generation(self):
        context, grant, attestation = self.authorization(TOKEN, revision="3365")
        request = ingestion.IngestionRequest(
            "lark", "ingest-crash", self.ingest_state.generation_id,
            context, grant, attestation,
            brokers.LarkRequest(TOKEN, "3365"), digest("document-token"))
        partial = self.snapshot(
            b'{"ok":true', "lark", "1.0.86", native_adapters.LARK_NATIVE_SCHEMA_DIGEST)
        with self.assertRaises(ingestion.IngestionError):
            self.ingest(request, partial)
        self.assertEqual(inspect_task(self.root).generation_id, self.ingest_state.generation_id)

        acquired = self.snapshot(
            b'{"ok":true,"identity":"user","data":{"content":"safe"}}',
            "lark", "1.0.86", native_adapters.LARK_NATIVE_SCHEMA_DIGEST)

        def crash(transaction, event, facts):
            transaction._close()
            raise KeyboardInterrupt()

        from knowledge_distiller import private_store
        with mock.patch.object(private_store.ArtifactTransaction, "commit", crash):
            with self.assertRaises(KeyboardInterrupt):
                self.ingest(request, acquired)
        self.assertEqual(inspect_task(self.root).generation_id, self.ingest_state.generation_id)
        self.assertTrue(any((self.root / "raw-staging").rglob("f-*")))
        recover_task(self.root)
        self.assertFalse(any((self.root / "raw-staging").rglob("f-*")))
