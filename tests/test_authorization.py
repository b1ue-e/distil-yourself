import hashlib
import importlib
import json
import sys
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge-distiller" / "scripts"))
try:
    authorization = importlib.import_module("knowledge_distiller.authorization")
except ModuleNotFoundError:
    authorization = None


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def grant_data(kind="content-grant"):
    value = {
        "record_type": kind, "record_id": "grant-1", "task_id": "task-1",
        "issuer": "owner-1", "active_principal": "owner-1",
        "tenant_account": "tenant-1", "selector": "document:doc-1",
        "operation": "read-content", "purpose": "distill",
        "revision": "rev-1", "session_range": None,
        "issued_at": 1000, "expires_at": 2000,
        "derived_processing_until": 3000, "revoked": False,
    }
    if kind == "authority-attestation":
        value.update(issuer="authority-1", operation="process-third-party",
                     content_owner="collaborator-1", authority_basis="participant-consent")
    value["decision_digest"] = digest(value)
    return value


def reseal(value):
    value["decision_digest"] = digest({k: v for k, v in value.items() if k != "decision_digest"})
    return value


@contextmanager
def forbid_side_effects():
    """Guard real boundary primitives while leaving all validation code real."""
    boundaries = (
        "builtins.open", "io.open", "os.open", "os.read", "os.write",
        "os.listdir", "os.scandir", "os.stat", "os.lstat", "os.mkdir",
        "os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace",
        "os.chmod", "os.link", "os.symlink", "os.system", "os.fork",
        "subprocess.Popen", "socket.socket", "socket.getaddrinfo",
    )
    with ExitStack() as stack:
        guards = [stack.enter_context(mock.patch(
            boundary, side_effect=AssertionError("validation attempted a side effect"),
        )) for boundary in boundaries]
        yield
        # Also catch attempted effects that a validator might swallow.
        for guard in guards:
            guard.assert_not_called()


def assert_private_error(test, error, expected_code, secrets):
    test.assertEqual(str(error), expected_code)
    test.assertEqual(error.code, expected_code)
    test.assertEqual(error.args, (expected_code,))
    test.assertIsNone(getattr(error, "pointer", None))
    for diagnostic in (str(error), repr(error.args), repr(vars(error))):
        test.assertLess(len(diagnostic), 160)
        for secret in secrets:
            test.assertNotIn(secret, diagnostic)


class AuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(authorization, "authorization module must be implemented")
        self.context = authorization.AuthorizationContext(
            task_id="task-1", active_principal="owner-1", tenant_account="tenant-1",
            selector="document:doc-1", purpose="distill", revision="rev-1",
            session_range=None, now=1500, task_active=True,
            authenticated_issuers=("owner-1", "authority-1"),
            content_owner="collaborator-1",
        )

    def validate(self, value, context=None):
        validator = (authorization.validate_authority_attestation
                     if value["record_type"] == "authority-attestation"
                     else authorization.validate_content_grant)
        return validator(value, context=context or self.context)

    def reject(self, value, code, context=None):
        with self.assertRaises(authorization.AuthorizationError) as caught:
            self.validate(value, context)
        self.assertEqual(str(caught.exception), code)
        self.assertEqual(caught.exception.code, code)

    def test_distinct_immutable_records_and_canonical_digests(self):
        for kind, cls in (("content-grant", authorization.ContentGrant),
                          ("authority-attestation", authorization.AuthorityAttestation)):
            with self.subTest(kind=kind):
                value = grant_data(kind)
                record = self.validate(value)
                self.assertIsInstance(record, cls)
                self.assertEqual(record.decision_digest, value["decision_digest"])
                self.assertEqual(record, self.validate(dict(reversed(list(value.items())))))
                with self.assertRaises(FrozenInstanceError):
                    record.record_id = "other"
                value["selector"] = "changed"
                self.assertEqual(record.selector, "document:doc-1")

    def test_unknown_and_missing_fields_are_rejected_for_both_records(self):
        for kind in ("content-grant", "authority-attestation"):
            for field in tuple(grant_data(kind)):
                value = grant_data(kind)
                del value[field]
                validator = (authorization.validate_content_grant if kind == "content-grant"
                             else authorization.validate_authority_attestation)
                with self.assertRaises(authorization.AuthorizationError) as caught:
                    validator(value, context=self.context)
                self.assertEqual(str(caught.exception), "missing-field")
            value = grant_data(kind)
            value["private-selector-secret"] = "source text"
            self.reject(value, "unknown-field")

    def test_selectors_and_revisions_are_exact(self):
        for selector in ("", " ", "document:*", "dir/**", "doc?", "doc[ab]", "latest"):
            value = reseal(dict(grant_data(), selector=selector))
            self.reject(value, "invalid-selector")
        for revision in ("", "latest", "LATEST", "rev-*", "rev?"):
            self.reject(reseal(dict(grant_data(), revision=revision)), "invalid-revision")

    def test_session_ranges_are_pinned_closed_and_immutable(self):
        value = grant_data()
        value.update(selector="session:one", revision=None,
                     session_range={"start": 0, "end": 12})
        context = replace(self.context, selector="session:one", revision=None,
                          session_range=authorization.SessionRange(0, 12))
        record = self.validate(reseal(value), context)
        self.assertEqual(record.session_range, authorization.SessionRange(0, 12))
        for bounds in ({"start": 2, "end": 1}, {"start": False, "end": 12},
                       {"start": 0, "end": "latest"}, {"start": 0, "end": 12, "recursive": True}):
            invalid = reseal(dict(value, session_range=bounds))
            with self.assertRaises(authorization.AuthorizationError):
                self.validate(invalid, context)
        for revision, bounds in ((None, None), ("rev-1", {"start": 0, "end": 12})):
            self.reject(reseal(dict(grant_data(), revision=revision, session_range=bounds)), "invalid-source-bound")

    def test_context_binding_rejects_scope_substitution(self):
        for field in ("task_id", "active_principal", "tenant_account", "selector", "purpose", "revision"):
            with self.subTest(field=field):
                value = reseal(dict(grant_data(), **{field: "other"}))
                self.reject(value, "authorization-context-mismatch")

    def test_expiry_revocation_issue_and_derived_bounds(self):
        for kind in ("content-grant", "authority-attestation"):
            for fields, code in (({"expires_at": 1500}, "authorization-expired"),
                                 ({"revoked": True}, "authorization-revoked"),
                                 ({"issued_at": 1600}, "authorization-not-yet-valid"),
                                 ({"derived_processing_until": 999}, "invalid-time-bound"),
                                 ({"expires_at": 100000}, "invalid-time-bound"),
                                 ({"issued_at": True}, "invalid-time-bound")):
                self.reject(reseal(dict(grant_data(kind), **fields)), code)
            self.reject(grant_data(kind), "task-inactive", replace(self.context, task_active=False))
        self.reject(reseal(dict(grant_data(), derived_processing_until=1499)), "derived-processing-expired")

    def test_discovery_mutation_and_wrong_record_types_never_authorize_reads(self):
        for operation in ("discover", "search", "write", "delete", "read-content,write"):
            self.reject(reseal(dict(grant_data(), operation=operation)), "invalid-operation")
        with self.assertRaises(authorization.AuthorizationError) as caught:
            authorization.validate_content_grant(grant_data("authority-attestation"), context=self.context)
        self.assertIn(str(caught.exception), ("unknown-field", "invalid-record-type"))

    def test_third_party_authority_requires_external_authenticated_issuer(self):
        self.reject(reseal(dict(grant_data("authority-attestation"), issuer="owner-1")), "self-attestation")
        self.reject(reseal(dict(grant_data("authority-attestation"), issuer="unknown")), "unauthenticated-issuer")
        self.reject(reseal(dict(grant_data("authority-attestation"), content_owner="other")), "authorization-context-mismatch")
        self.reject(reseal(dict(grant_data("authority-attestation"), authority_basis="available-path")), "invalid-authority-basis")
        self.reject(reseal(dict(grant_data(), issuer="authority-1")), "invalid-grant-issuer")

    def test_digest_tampering_and_bad_types_have_code_only_errors(self):
        self.reject(dict(grant_data(), record_id="substituted"), "decision-digest-mismatch")
        for field, value in (("decision_digest", "private-source"), ("revoked", "false"),
                             ("record_id", ""), ("purpose", "\ud800")):
            with self.assertRaises(authorization.AuthorizationError) as caught:
                self.validate(dict(grant_data(), **{field: value}))
            self.assertLess(len(str(caught.exception)), 80)
            self.assertNotIn("private-source", str(caught.exception))

    def test_authorization_is_pure_for_valid_and_rejected_records(self):
        values = [grant_data(kind) for kind in ("content-grant", "authority-attestation")]
        with forbid_side_effects():
            for value in values:
                self.validate(value)
                self.reject(dict(value, revoked=True), "authorization-revoked")
                self.reject(dict(value, selector="private-source/*"), "invalid-selector")

    def test_every_authorization_diagnostic_is_bounded_and_private(self):
        secret = "SENSITIVE-CONTENT-DO-NOT-EMIT"
        selector = "document:PRIVATE-SELECTOR-DO-NOT-EMIT"
        for kind in ("content-grant", "authority-attestation"):
            value = reseal(dict(grant_data(kind), selector=selector, purpose=secret))
            context = replace(self.context, selector=selector, purpose=secret)
            validator = (authorization.validate_content_grant if kind == "content-grant"
                         else authorization.validate_authority_attestation)
            cases = [
                (dict(value, **{secret: selector}), context, "unknown-field"),
                ({key: item for key, item in value.items() if key != "task_id"}, context, "missing-field"),
                (dict(value, record_type=secret), context, "invalid-record-type"),
                (value, secret, "invalid-authorization-context"),
                (dict(value, selector=selector + "*"), context, "invalid-selector"),
                (dict(value, revision=secret + "*"), context, "invalid-revision"),
                (dict(value, revision=None), context, "invalid-source-bound"),
                (dict(value, operation=secret), context, "invalid-operation"),
                (dict(value, task_id=secret), context, "authorization-context-mismatch"),
                (dict(value, revoked=secret), context, "invalid-type"),
                (dict(value, record_id=secret * 20), context, "invalid-identifier"),
                (dict(value, decision_digest=secret), context, "invalid-snapshot-id"),
                (value, replace(context, task_active=False), "task-inactive"),
                (dict(value, revoked=True), context, "authorization-revoked"),
                (dict(value, issued_at=secret), context, "invalid-time-bound"),
                (dict(value, issued_at=1600), context, "authorization-not-yet-valid"),
                (dict(value, expires_at=1500), context, "authorization-expired"),
                (dict(value, derived_processing_until=1499), context, "derived-processing-expired"),
                (dict(value, issuer=secret), context, "unauthenticated-issuer"),
                (dict(value, record_id=secret), context, "decision-digest-mismatch"),
                (value, replace(context, authenticated_issuers=[secret]), "invalid-authorization-context"),
                (value, replace(context, session_range=secret), "invalid-authorization-context"),
                (value, replace(context, authenticated_issuers=(secret * 20,)), "invalid-identifier"),
                (dict(value, revision=None, session_range={"start": 3, "end": 2}), context, "invalid-source-bound"),
                (dict(value, revision=None, session_range={"start": 0, "end": 2, secret: selector}), context, "unknown-field"),
                (dict(value, revision=None, session_range={"start": 0, "end": secret}), context, "invalid-time-bound"),
                (dict(value, revision=None, session_range={"start": 0, "end": 2}),
                 replace(context, revision=None, session_range=authorization.SessionRange(0, 3)),
                 "authorization-context-mismatch"),
            ]
            if kind == "authority-attestation":
                cases.extend([
                    (dict(value, issuer="owner-1"), context, "self-attestation"),
                    (dict(value, authority_basis=secret), context, "invalid-authority-basis"),
                    (dict(value, content_owner=secret), context, "authorization-context-mismatch"),
                ])
            else:
                cases.append((dict(value, issuer="authority-1"), context, "invalid-grant-issuer"))
            for index, (invalid, request_context, code) in enumerate(cases):
                with self.subTest(kind=kind, case=index, code=code):
                    with self.assertRaises(authorization.AuthorizationError) as caught:
                        validator(invalid, context=request_context)
                    assert_private_error(self, caught.exception, code, (secret, selector))


if __name__ == "__main__":
    unittest.main()
