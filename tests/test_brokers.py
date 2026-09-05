"""Broker policy tests use synthetic sources and injected trusted launchers only."""

from dataclasses import FrozenInstanceError, asdict, replace
import importlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.test_authorization import grant_data, reseal
from tests.test_source_io import digest
from knowledge_distiller import authorization as auth

try:
    brokers = importlib.import_module("knowledge_distiller.brokers")
except ModuleNotFoundError:
    brokers = None


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(brokers, "broker policy module is missing")
        self.context = auth.AuthorizationContext(
            "task-1", "owner-1", "tenant-1", "https://example.larkoffice.com/docx/DocABC123",
            "distill", "42", None, 1500, True, ("owner-1", "authority-1"), "collaborator-1")
        self.request = brokers.LarkRequest(self.context.selector, "42")
        self.credentials = brokers.CredentialBinding(
            "user", "owner-1", "tenant-1", (("LARK_TEST_USER_TOKEN", "SECRET-CREDENTIAL"),))
        self.response = brokers.LarkResponse(
            returncode=0, raw=b'{"native":"PRIVATE CONTENT"}', media_type="application/json", principal_kind="user",
            active_principal="owner-1", tenant_account="tenant-1",
            selector_digest=digest(self.context.selector.encode()), purpose="distill",
            revision_before="42", revision_after="42", content_owner="collaborator-1",
            product_version="synthetic-1", native_schema_digest=digest(b"synthetic-schema"),
            redirected=False, fallback_principal=False)
        self.runner = mock.Mock(return_value=self.response)
        self.resolver = mock.Mock(return_value=self.credentials)

    def records(self, context=None):
        context = context or self.context
        values = []
        for kind, validator in (("content-grant", auth.validate_content_grant),
                                ("authority-attestation", auth.validate_authority_attestation)):
            value = grant_data(kind)
            value.update({field: getattr(context, field) for field in (
                "task_id", "active_principal", "tenant_account", "selector", "purpose", "revision")})
            value["session_range"] = None if context.session_range is None else asdict(context.session_range)
            values.append(validator(reseal(value), context=context))
        return tuple(values)

    def fetch(self, request=None, context=None, records=None, **kwargs):
        grant, attestation = records or self.records()
        return brokers.fetch_lark(
            request or self.request, grant=grant, attestation=attestation,
            context=context or self.context, runner=self.runner, credential_resolver=self.resolver,
            required_auth_variables=("LARK_TEST_USER_TOKEN",), **kwargs)

    def reject(self, action, code=None):
        with self.assertRaises(brokers.BrokerError) as caught:
            action()
        error = caught.exception
        if code is not None:
            self.assertEqual(error.code, code)
        self.assertEqual(error.args, (error.code,))
        for diagnostic in (str(error), repr(error.args), repr(vars(error))):
            self.assertLess(len(diagnostic), 160)
            for secret in ("PRIVATE", "SECRET-CREDENTIAL", "example.larkoffice.com", "DocABC123"):
                self.assertNotIn(secret, diagnostic)

    def test_lark_exact_argv_minimal_env_limits_and_immutable_envelope(self):
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "AMBIENT", "HOME": "PRIVATE"}):
            result = self.fetch()
        args, kwargs = self.runner.call_args
        self.assertEqual(args, (("lark-cli", "docs", "+fetch", "--doc", self.context.selector,
                                 "--scope", "full", "--detail", "with-ids", "--format", "json",
                                 "--as", "user", "--revision-id", "42"),))
        self.assertEqual(kwargs, {"env": {"LARK_TEST_USER_TOKEN": "SECRET-CREDENTIAL"},
                                  "shell": False, "timeout": 30, "max_bytes": brokers.MAX_GRAPH_BYTES,
                                  "allow_redirects": False, "allow_fallback_principal": False})
        self.resolver.assert_called_once_with(active_principal="owner-1", tenant_account="tenant-1",
                                              required_variables=("LARK_TEST_USER_TOKEN",))
        self.assertEqual(result.raw, self.response.raw)
        self.assertEqual(result.raw_digest, digest(self.response.raw))
        self.assertEqual(result.source_snapshot_id, result.raw_digest)
        self.assertEqual(result.selector_digest, digest(self.context.selector.encode()))
        self.assertEqual(result.owner.id, "collaborator-1")
        self.assertEqual(result.source_byte_count, len(self.response.raw))
        self.assertEqual(result.evidence.native_schema_digest, self.response.native_schema_digest)
        self.assertFalse(hasattr(result, "adapter"))
        self.assertNotIn("PRIVATE CONTENT", repr(result))
        self.assertNotIn(self.context.selector, repr(result))
        self.assertNotIn("SECRET-CREDENTIAL", repr(result))
        with self.assertRaises(FrozenInstanceError):
            result.raw = b"changed"
        with self.assertRaises(FrozenInstanceError):
            result.evidence.product_version = "changed"

    def test_normalized_token_allowed_without_url_rewriting(self):
        context = replace(self.context, selector="DocABC123")
        self.response = replace(self.response, selector_digest=digest(b"DocABC123"))
        self.runner.return_value = self.response
        self.fetch(request=brokers.LarkRequest("DocABC123", "42"), context=context,
                   records=self.records(context))
        self.assertEqual(self.runner.call_args.args[0][4], "DocABC123")

    def test_snapshot_owner_is_independently_attested_content_owner_for_both_sources(self):
        lark = self.fetch()
        _, context, request, identity = self.local()
        session = self.read_session(context, request, identity)
        for snapshot in (lark, session):
            self.assertEqual(snapshot.owner.id, self.context.content_owner)
            self.assertNotEqual(snapshot.owner.id, self.context.active_principal)
            self.assertEqual(snapshot.owner.kind, "user")
            self.assertEqual(snapshot.owner.verification, "verified-principal")
        self.assertEqual(self.resolver.call_args.kwargs["active_principal"], "owner-1")

    def test_reader_and_content_owner_substitution_is_rejected_before_io(self):
        grant, attestation = self.records()
        _, context, request, identity = self.local()
        local_records = self.records(context)
        with mock.patch("os.open") as opened:
            for field, value in (("active_principal", "collaborator-1"), ("content_owner", "owner-1")):
                self.reject(lambda: self.fetch(context=replace(self.context, **{field: value}),
                                                records=(grant, attestation)))
                self.reject(lambda: self.read_session(replace(context, **{field: value}),
                                                       request, identity, local_records))
            self.reject(lambda: self.fetch(records=(grant, replace(attestation, content_owner="owner-1"))))
            self.reject(lambda: self.read_session(context, request, identity,
                         (local_records[0], replace(local_records[1], content_owner="owner-1"))))
            opened.assert_not_called()
        self.resolver.assert_not_called()
        self.runner.assert_not_called()

    def test_invalid_authorization_always_precedes_credential_and_source_io(self):
        grant, attestation = self.records()
        invalid_records = ((replace(grant, revoked=True), attestation),
                           (grant, replace(attestation, revoked=True)),
                           (asdict(grant), attestation), (grant, asdict(attestation)))
        for records in invalid_records:
            self.reject(lambda: self.fetch(records=records))
        for changes in ({"task_active": False}, {"now": 2000}, {"now": True},
                        {"authenticated_issuers": []}, {"content_owner": "other"}):
            self.reject(lambda: self.fetch(context=replace(self.context, **changes)))
        self.runner.assert_not_called()
        self.resolver.assert_not_called()

    def test_scope_substitution_and_unsafe_selectors_rejected_before_runner(self):
        for field in ("selector", "revision", "tenant_account", "purpose", "active_principal"):
            self.reject(lambda: self.fetch(context=replace(self.context, **{field: "other"})))
        for selector in ("--all", "doc one", "https://evil.example/docx/DocABC123",
                         "https://example.larkoffice.com/wiki/DocABC123", "https://example.larkoffice.com/docx/DocABC123?x=1",
                         "https://user@example.larkoffice.com/docx/DocABC123", "https://example.larkoffice.com:443/docx/DocABC123",
                         "https://EXAMPLE.larkoffice.com/docx/DocABC123", "DocABC123/other", "DocABC123\u202e"):
            self.reject(lambda: self.fetch(request=brokers.LarkRequest(selector, "42")))
        for revision in ("latest", "LATEST", "-1", "--all", "", "042", True, 42, "42\ud800"):
            self.reject(lambda: self.fetch(request=brokers.LarkRequest(self.context.selector, revision)))
        self.reject(lambda: self.fetch(request={"selector": self.context.selector, "revision": "42", "endpoint": "PRIVATE"}))
        self.runner.assert_not_called()
        self.resolver.assert_not_called()

    def test_credential_binding_and_declared_variables_are_fail_closed(self):
        for changes in ({"principal_kind": "bot"}, {"principal_kind": "default"},
                        {"active_principal": "other"}, {"tenant_account": "other"},
                        {"environment": (("HOME", "PRIVATE"),)},
                        {"environment": (("LARK_TEST_USER_TOKEN", "SECRET-CREDENTIAL"), ("AWS_SECRET_ACCESS_KEY", "PRIVATE"))},
                        {"environment": (("LARK_TEST_USER_TOKEN", "SECRET-CREDENTIAL"),) * 2},
                        {"environment": (("LARK_TEST_USER_TOKEN", "\ud800"),)},
                        {"environment": []}):
            self.resolver.return_value = replace(self.credentials, **changes)
            self.reject(self.fetch, "invalid-credentials")
        self.runner.assert_not_called()

    def test_lark_result_evidence_cannot_substitute_scope_or_principal(self):
        for changes in ({"principal_kind": "bot"}, {"active_principal": "other"},
                        {"tenant_account": "other"}, {"selector_digest": digest(b"other")},
                        {"purpose": "other"}, {"revision_before": "41"}, {"revision_after": "43"},
                        {"content_owner": "other"}, {"redirected": True}, {"redirected": 0},
                        {"fallback_principal": True}, {"product_version": ""},
                        {"native_schema_digest": "PRIVATE"}):
            self.runner.return_value = replace(self.response, **changes)
            self.reject(self.fetch)

    def test_runner_failure_timeout_oversize_and_malformed_are_code_only(self):
        for failure, code in ((subprocess.TimeoutExpired("PRIVATE", 30, output=b"PRIVATE"), "broker-timeout"),
                              (subprocess.CalledProcessError(1, "PRIVATE", output=b"PRIVATE", stderr=b"PRIVATE"), "broker-command-failed"),
                              (BufferError("PRIVATE STREAM LIMIT"), "broker-response-too-large"),
                              (OSError("PRIVATE"), "broker-unavailable"),
                              (ValueError("PRIVATE"), "broker-response-invalid")):
            self.runner.side_effect = failure
            self.reject(self.fetch, code)
        self.runner.side_effect = None
        for response, code in ((replace(self.response, returncode=1), "broker-command-failed"),
                               (replace(self.response, returncode=True), "broker-response-invalid"),
                               (replace(self.response, raw="PRIVATE"), "broker-response-invalid"),
                               ({"raw": b"{}", "extra": "PRIVATE"}, "broker-response-invalid")):
            self.runner.return_value = response
            self.reject(self.fetch, code)
        self.runner.return_value = replace(self.response, raw=b" " * 33)
        with mock.patch.object(brokers, "MAX_GRAPH_BYTES", 32):
            self.reject(self.fetch, "broker-response-too-large")

    def local(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name).resolve() / "synthetic-session"
        path.write_bytes(b"abcDEF")
        context = replace(self.context, selector=str(path), revision=None, session_range=auth.SessionRange(0, 2))
        request = brokers.SessionRequest(str(path), "project-1", 3, digest(b"abc"))
        identity = brokers.SessionIdentity("owner-1", "tenant-1", "project-1", "collaborator-1",
                                           "synthetic-1", digest(b"synthetic-schema"))
        return path, context, request, identity

    def read_session(self, context, request, identity, records=None):
        grant, attestation = records or self.records(context)
        return brokers.read_local_session(request, grant=grant, attestation=attestation,
                                           context=context, identity=identity)

    def test_local_exact_closed_prefix_no_discovery_subprocess_or_later_bytes(self):
        path, context, request, identity = self.local()
        with mock.patch("os.listdir", side_effect=AssertionError("discovery")), mock.patch("os.scandir", side_effect=AssertionError("discovery")), mock.patch("subprocess.Popen", side_effect=AssertionError("process")), mock.patch("os.open", wraps=os.open) as opened:
            result = self.read_session(context, request, identity)
        self.assertEqual(result.raw, b"abc")
        self.assertEqual(result.source_byte_count, 3)
        self.assertEqual(result.raw_digest, request.prefix_digest)
        self.assertEqual(result.evidence.project_id, "project-1")
        self.assertEqual(opened.call_args_list[-1].args[0], path.name)
        self.assertNotIn(str(path), repr(result))
        with path.open("ab") as writer:
            writer.write(b"LATER")
        self.assertEqual(self.read_session(context, request, identity), result)
        path.write_bytes(b"bacDEF")
        self.reject(lambda: self.read_session(context, request, identity), "input-changed")

    def test_local_grant_context_project_account_and_bounds_before_open(self):
        path, context, request, identity = self.local()
        grant, attestation = self.records(context)
        with mock.patch("os.open") as opened:
            for records in ((replace(grant, revoked=True), attestation), (grant, replace(attestation, revoked=True))):
                self.reject(lambda: self.read_session(context, request, identity, records))
            for changes in ({"path": str(path.parent)}, {"project_id": "other"},
                            {"prefix_length": 2}, {"prefix_length": True}, {"prefix_digest": "PRIVATE"}):
                self.reject(lambda: self.read_session(context, replace(request, **changes), identity))
            for changes in ({"active_principal": "other"}, {"tenant_account": "other"}, {"content_owner": "other"}):
                self.reject(lambda: self.read_session(context, request, replace(identity, **changes)))
            # Only a full prefix is implemented. Nonzero starts cannot authorize
            # reading earlier bytes to establish a whole-prefix digest (11.3).
            ranged = replace(context, session_range=auth.SessionRange(1, 2))
            self.reject(lambda: self.read_session(ranged, request, identity), "invalid-source-bound")
            opened.assert_not_called()

    def test_acquisition_keeps_response_bytes_opaque_and_never_calls_content_decoder(self):
        # Transport type is attested by the trusted launcher. The isolated
        # parser owns syntax, duplicate keys, UTF-8, and native document shape.
        payloads = (b"{}", b"null", b"[]", b"true", b'"PRIVATE"',
                    b"PRIVATE NOT JSON", b'{"x":1,"x":2}', b"\xff")
        with mock.patch("knowledge_distiller.adapters.decode_event_graph_json",
                        side_effect=AssertionError("broker called a content decoder")) as decoder:
            for raw in payloads:
                self.runner.return_value = replace(self.response, raw=raw)
                try:
                    snapshot = self.fetch()
                except (AssertionError, brokers.BrokerError):
                    self.fail("acquisition must preserve opaque bytes without content decoding")
                self.assertEqual(snapshot.raw, raw)
                self.assertEqual(snapshot.raw_digest, digest(raw))
            decoder.assert_not_called()

    def test_trusted_lark_receipt_declares_json_transport_type(self):
        self.assertTrue(hasattr(self.response, "media_type"), "trusted receipt must declare transport type")
        self.assertEqual(self.response.media_type, "application/json")
        for media_type in (None, True, "text/html", "application/octet-stream"):
            self.runner.return_value = replace(self.response, media_type=media_type)
            self.reject(self.fetch, "broker-response-invalid")

    def test_local_prefix_confirmation_uses_one_target_open_and_one_descriptor(self):
        path, context, request, identity = self.local()
        original_open = os.open
        target_fds = []

        def open_once(name, flags, **kwargs):
            descriptor = original_open(name, flags, **kwargs)
            if not flags & os.O_DIRECTORY:
                self.assertEqual(name, path.name)
                target_fds.append(descriptor)
            return descriptor

        with mock.patch("os.open", side_effect=open_once), mock.patch("os.read", wraps=os.read) as read, mock.patch("os.lseek", wraps=os.lseek) as seek, mock.patch("os.close", wraps=os.close) as close, mock.patch("os.listdir", side_effect=AssertionError("enumeration")), mock.patch("os.scandir", side_effect=AssertionError("enumeration")):
            self.assertEqual(self.read_session(context, request, identity).raw, b"abc")
        self.assertEqual(len(target_fds), 1)
        descriptor = target_fds[0]
        self.assertEqual(read.call_args_list, [mock.call(descriptor, 3), mock.call(descriptor, 3)])
        seek.assert_called_once_with(descriptor, 0, os.SEEK_SET)
        self.assertIn(mock.call(descriptor), close.call_args_list)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_string_subclasses_cannot_impersonate_verified_identities(self):
        class Impersonator(str):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        for field in ("principal_kind", "active_principal", "tenant_account", "purpose",
                      "selector_digest", "revision_before", "revision_after", "content_owner"):
            self.runner.return_value = replace(self.response, **{field: Impersonator("PRIVATE")})
            self.reject(self.fetch, "broker-response-invalid")
        self.runner.return_value = self.response
        self.resolver.return_value = replace(self.credentials, active_principal=Impersonator("PRIVATE"))
        self.reject(self.fetch, "invalid-credentials")
        _, context, request, identity = self.local()
        with mock.patch("os.open") as opened:
            self.reject(lambda: self.read_session(context, request, replace(identity, active_principal=Impersonator("PRIVATE"))))
            opened.assert_not_called()

    def test_tampered_dataclass_extra_fields_and_mutable_collections_fail_closed(self):
        request = replace(self.request)
        object.__setattr__(request, "endpoint", "PRIVATE")
        self.reject(lambda: self.fetch(request=request), "invalid-broker-request")
        grant, attestation = self.records()
        object.__setattr__(grant, "native_tuple", ("codex", "1"))
        self.reject(lambda: self.fetch(records=(grant, attestation)), "invalid-authorization-record")
        self.runner.assert_not_called()
        self.resolver.assert_not_called()

    def test_local_exact_one_byte_boundary_and_prefix_ceiling(self):
        _, context, request, identity = self.local()
        context = replace(context, session_range=auth.SessionRange(0, 0))
        request = replace(request, prefix_length=1, prefix_digest=digest(b"a"))
        self.assertEqual(self.read_session(context, request, identity).raw, b"a")
        with mock.patch("os.open") as opened:
            context = replace(context, session_range=auth.SessionRange(0, brokers.MAX_GRAPH_BYTES))
            request = replace(request, prefix_length=brokers.MAX_GRAPH_BYTES + 1)
            self.reject(lambda: self.read_session(context, request, identity), "invalid-source-bound")
            opened.assert_not_called()
