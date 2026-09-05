import hashlib
import importlib
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
