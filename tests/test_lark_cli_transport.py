"""Synthetic-only tests for pinned Lark control-response parsing."""

import copy
import dataclasses
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "lark_runtime" / "1.0.86"
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import adapters, lark_cli_transport as transport
from knowledge_distiller import lark_profile, native_adapters


TOKEN = "doxcn1234567890AbCdEfGhIjKl"
OWNER = "ou_SYNTHETIC_OWNER"
AUTH_FIXTURE = (FIXTURES / "auth-status.json").read_bytes()
DOCUMENT_FIXTURE = (FIXTURES / "document-info.json").read_bytes()
METADATA_FIXTURE = (FIXTURES / "drive-metadata.json").read_bytes()
MISSING_SCOPE_FIXTURE = (FIXTURES / "missing-scope.json").read_bytes()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decoded(raw):
    return json.loads(raw.decode("utf-8"))


class LarkProfileTest(unittest.TestCase):
    def test_checked_in_profile_is_synthetic_only_and_self_consistent(self):
        profile = lark_profile.load_pinned_profile()
        self.assertEqual(profile.status, "synthetic-only")
        self.assertEqual(profile.product, "lark")
        self.assertEqual(profile.adapter_version, "1.0.0")
        self.assertEqual(profile.product_version, "1.0.86")
        self.assertEqual(profile.native_schema_version, "docx-v1-raw-content-v1")
        self.assertEqual(
            profile.native_schema_digest,
            native_adapters.LARK_NATIVE_SCHEMA_DIGEST,
        )
        self.assertEqual(profile.timeout_seconds, 30)
        self.assertEqual(profile.control_stdout_limit, 1024 * 1024)
        self.assertEqual(profile.raw_stdout_limit, adapters.MAX_GRAPH_BYTES)
        self.assertEqual(profile.stderr_limit, 64 * 1024)
        self.assertEqual(profile.endpoint_integrity_status, "unverified")
        self.assertEqual(profile.consistency_mode, "unaccepted")
        self.assertRegex(profile.canonical_digest, r"sha256:[0-9a-f]{64}\Z")
        self.assertEqual(
            profile.canonical_digest,
            "sha256:cf7407c4e2f4b2f71f8352ad19eaaaf96b6fbec12a365e7730ff939814439b82",
        )
        self.assertEqual(profile.canonical_digest, lark_profile.PINNED_PROFILE_DIGEST)

    def test_profile_record_and_control_schemas_are_canonical_and_closed(self):
        record = lark_profile.canonical_profile_record()
        self.assertEqual(record["status"], "synthetic-only")
        self.assertEqual(
            lark_profile.canonical_digest(record),
            lark_profile.PINNED_PROFILE_DIGEST,
        )
        descriptors = lark_profile.control_schema_descriptors()
        self.assertEqual(set(descriptors), {
            "auth-status", "document-info", "drive-metadata", "missing-scope"
        })
        self.assertEqual(
            set(record["control_schema_digests"]), set(descriptors)
        )

        def assert_closed(schema):
            if schema["type"] == "object":
                self.assertFalse(schema["additional_properties"])
                self.assertLessEqual(set(schema["required"]), set(schema["properties"]))
                for child in schema["properties"].values():
                    assert_closed(child)
            elif schema["type"] == "array":
                assert_closed(schema["items"])

        for name, descriptor in descriptors.items():
            self.assertRegex(descriptor["name"], r"^knowledge-distiller\.lark-control\..+/v1$")
            assert_closed(descriptor["schema"])
            self.assertEqual(
                record["control_schema_digests"][name],
                lark_profile.canonical_digest(descriptor),
            )

    def test_scope_alternatives_are_exact_and_immutable(self):
        self.assertEqual(lark_profile.READ_SCOPE_ALTERNATIVES, frozenset({
            "docx:document:readonly", "docx:document"
        }))
        self.assertEqual(lark_profile.METADATA_SCOPE_ALTERNATIVES, frozenset({
            "drive:drive.metadata:readonly", "drive:drive"
        }))
        profile = lark_profile.load_pinned_profile()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            profile.status = "live-enabled"
        self.assertNotIn(OWNER, repr(profile))

    def test_fixtures_are_synthetic_and_contain_no_live_selector(self):
        for path in sorted(FIXTURES.iterdir()):
            raw = path.read_bytes()
            self.assertNotIn(b"bytedance", raw.lower())
            self.assertNotIn(b"larkoffice.com", raw.lower())
            self.assertNotIn(b"feishu.cn", raw.lower())
            self.assertNotIn(b"larksuite.com", raw.lower())
        self.assertEqual(decoded(DOCUMENT_FIXTURE)["data"]["document"]["document_id"], TOKEN)


class _StringSubclass(str):
    pass


class _IntSubclass(int):
    pass


class LarkControlParserTest(unittest.TestCase):
    def reject(self, action):
        with self.assertRaises(transport.LarkTransportError) as caught:
            action()
        self.assertEqual(caught.exception.code, "invalid-lark-control-response")
        self.assertEqual(caught.exception.args, ("invalid-lark-control-response",))
        leaked = "SYNTHETIC_PRIVATE_VALUE"
        self.assertNotIn(leaked, str(caught.exception))
        self.assertNotIn(leaked, repr(caught.exception))
        self.assertNotIn(leaked, repr(vars(caught.exception)))

    def test_parses_verified_user_and_exact_scope_capabilities(self):
        identity = transport.parse_verified_identity(AUTH_FIXTURE)
        self.assertEqual(identity.open_id, OWNER)
        self.assertEqual(identity.scopes, (
            "docx:document:readonly", "drive:drive.metadata:readonly"
        ))
        self.assertTrue(transport.has_required_scopes(identity.scopes))
        self.assertEqual(repr(identity), object.__repr__(identity))
        self.assertNotIn(OWNER, repr(identity))

    def test_scope_check_requires_one_exact_member_from_each_set(self):
        accepted = (
            ("docx:document:readonly", "drive:drive.metadata:readonly"),
            ("docx:document", "drive:drive"),
            ("drive:drive", "docx:document:readonly", "unrelated:scope"),
        )
        rejected = (
            (),
            ("docx:document:readonly",),
            ("drive:drive.metadata:readonly",),
            ("docx:document:read", "drive:drive.metadata:readonly"),
            ("docx:document:readonly", "drive:drive.metadata"),
            ("docx:*", "drive:*"),
            ("DOCX:DOCUMENT", "drive:drive"),
        )
        for scopes in accepted:
            self.assertTrue(transport.has_required_scopes(scopes))
        for scopes in rejected:
            self.assertFalse(transport.has_required_scopes(scopes))
        self.assertFalse(transport.has_required_scopes(["docx:document", "drive:drive"]))
        self.assertFalse(transport.has_required_scopes((
            _StringSubclass("docx:document"), "drive:drive"
        )))

    def test_observation_combines_exact_document_and_owner(self):
        observation = transport.parse_observation(
            DOCUMENT_FIXTURE, METADATA_FIXTURE, TOKEN
        )
        self.assertEqual(
            observation,
            transport.LarkObservation(TOKEN, "7", OWNER),
        )
        self.assertEqual(repr(observation), object.__repr__(observation))
        self.assertNotIn(TOKEN, repr(observation))
        self.assertNotIn(OWNER, repr(observation))

    def test_verified_identity_rejects_closed_shape_and_literal_mutations(self):
        original = decoded(AUTH_FIXTURE)
        mutations = []
        for key in original:
            value = copy.deepcopy(original)
            del value[key]
            mutations.append(value)
        for key, replacement in (
            ("identity", "bot"), ("verified", False), ("verified", 1),
            ("status", "inactive"), ("token_status", "expired"),
        ):
            value = copy.deepcopy(original)
            value[key] = replacement
            mutations.append(value)
        value = copy.deepcopy(original)
        value["unknown"] = "SYNTHETIC_PRIVATE_VALUE"
        mutations.append(value)
        for scopes in ([], ["docx:document:readonly", "docx:document:readonly"], [1]):
            value = copy.deepcopy(original)
            value["identities"]["user"]["scope"] = scopes
            mutations.append(value)
        for open_id in ("", 1):
            value = copy.deepcopy(original)
            value["identities"]["user"]["openId"] = open_id
            mutations.append(value)
        value = copy.deepcopy(original)
        value["identities"]["user"]["display_name"] = 1
        mutations.append(value)
        value = copy.deepcopy(original)
        value["identities"]["bot"] = {}
        mutations.append(value)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.reject(lambda mutation=mutation: transport.parse_verified_identity(encoded(mutation)))

    def test_document_info_rejects_closed_shape_literals_bounds_and_discarded_types(self):
        original = decoded(DOCUMENT_FIXTURE)
        mutations = []
        for key in original:
            value = copy.deepcopy(original)
            del value[key]
            mutations.append(value)
        for key, replacement in (("ok", False), ("ok", 1), ("identity", "bot")):
            value = copy.deepcopy(original)
            value[key] = replacement
            mutations.append(value)
        for revision in (0, True, 2**63, "7"):
            value = copy.deepcopy(original)
            value["data"]["document"]["revision_id"] = revision
            mutations.append(value)
        for key in ("title", "create_time", "update_time"):
            value = copy.deepcopy(original)
            value["data"]["document"][key] = 1
            mutations.append(value)
        value = copy.deepcopy(original)
        value["data"]["document"]["display_setting"]["show_pv"] = 1
        mutations.append(value)
        value = copy.deepcopy(original)
        value["data"]["document"]["unknown"] = "SYNTHETIC_PRIVATE_VALUE"
        mutations.append(value)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.reject(lambda mutation=mutation: transport.parse_observation(
                    encoded(mutation), METADATA_FIXTURE, TOKEN
                ))

    def test_drive_metadata_rejects_shape_cardinality_binding_and_discarded_types(self):
        original = decoded(METADATA_FIXTURE)
        mutations = []
        for metas in ([], original["data"]["metas"] * 2):
            value = copy.deepcopy(original)
            value["data"]["metas"] = metas
            mutations.append(value)
        value = copy.deepcopy(original)
        value["data"]["failed_list"] = [{"token": TOKEN}]
        mutations.append(value)
        for key, replacement in (
            ("doc_token", "doxcn0000000000000000000000"),
            ("doc_type", "sheet"), ("owner_id", ""),
            ("title", 1), ("create_time", 1), ("modified_time", 1),
        ):
            value = copy.deepcopy(original)
            value["data"]["metas"][0][key] = replacement
            mutations.append(value)
        value = copy.deepcopy(original)
        value["data"]["metas"][0]["unknown"] = "SYNTHETIC_PRIVATE_VALUE"
        mutations.append(value)
        value = copy.deepcopy(original)
        del value["data"]["metas"][0]["owner_id"]
        mutations.append(value)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.reject(lambda mutation=mutation: transport.parse_observation(
                    DOCUMENT_FIXTURE, encoded(mutation), TOKEN
                ))

    def test_duplicate_unknown_and_invalid_json_are_code_only(self):
        cases = (
            AUTH_FIXTURE[:-2] + b',"identity":"user"}',
            b'{"SYNTHETIC_PRIVATE_VALUE":true}',
            b'not-json-SYNTHETIC_PRIVATE_VALUE',
            b'\xffSYNTHETIC_PRIVATE_VALUE',
        )
        for raw in cases:
            self.reject(lambda raw=raw: transport.parse_verified_identity(raw))

    def test_each_control_parser_rejects_duplicate_and_missing_fields(self):
        duplicate_document = DOCUMENT_FIXTURE[:-2] + b',"identity":"user"}'
        duplicate_metadata = METADATA_FIXTURE[:-2] + b',"identity":"user"}'
        duplicate_missing_scope = MISSING_SCOPE_FIXTURE[:-2] + b',"identity":"user"}'
        self.reject(lambda: transport.parse_observation(
            duplicate_document, METADATA_FIXTURE, TOKEN
        ))
        self.reject(lambda: transport.parse_observation(
            DOCUMENT_FIXTURE, duplicate_metadata, TOKEN
        ))
        self.reject(lambda: transport.parse_missing_scope(duplicate_missing_scope))

        document = decoded(DOCUMENT_FIXTURE)
        del document["data"]["document"]["revision_id"]
        self.reject(lambda: transport.parse_observation(
            encoded(document), METADATA_FIXTURE, TOKEN
        ))
        metadata = decoded(METADATA_FIXTURE)
        del metadata["data"]["failed_list"]
        self.reject(lambda: transport.parse_observation(
            DOCUMENT_FIXTURE, encoded(metadata), TOKEN
        ))
        missing_scope = decoded(MISSING_SCOPE_FIXTURE)
        del missing_scope["error"]["subtype"]
        self.reject(lambda: transport.parse_missing_scope(encoded(missing_scope)))

    def test_decoded_scalar_subclasses_are_rejected(self):
        auth = decoded(AUTH_FIXTURE)
        auth["identities"]["user"]["openId"] = _StringSubclass(OWNER)
        with mock.patch.object(transport.adapters, "decode_event_graph_json", return_value=auth):
            self.reject(lambda: transport.parse_verified_identity(b"{}"))
        document = decoded(DOCUMENT_FIXTURE)
        document["data"]["document"]["revision_id"] = _IntSubclass(7)
        with mock.patch.object(transport.adapters, "decode_event_graph_json", side_effect=(document, decoded(METADATA_FIXTURE))):
            self.reject(lambda: transport.parse_observation(b"{}", b"{}", TOKEN))

    def test_typed_missing_scope_is_accepted_without_free_text_matching(self):
        self.assertEqual(
            transport.parse_missing_scope(MISSING_SCOPE_FIXTURE),
            ("docx:document:readonly",),
        )
        free_text = (
            b'missing docx:document:readonly',
            encoded({"message": "missing docx:document:readonly"}),
            encoded({"ok": False, "identity": "user", "error": {
                "type": "authorization", "subtype": "other", "code": 4242,
                "missing_scopes": ["docx:document:readonly"]
            }}),
        )
        for raw in free_text:
            self.reject(lambda raw=raw: transport.parse_missing_scope(raw))

    def test_missing_scope_rejects_bounds_duplicates_and_scalar_subclasses(self):
        original = decoded(MISSING_SCOPE_FIXTURE)
        mutations = []
        for code in (True, 0, 2**31, "4242"):
            value = copy.deepcopy(original)
            value["error"]["code"] = code
            mutations.append(value)
        for scopes in (
            [],
            ["docx:document:readonly", "docx:document:readonly"],
            [""],
            ["x" * 257],
        ):
            value = copy.deepcopy(original)
            value["error"]["missing_scopes"] = scopes
            mutations.append(value)
        value = copy.deepcopy(original)
        value["error"]["unknown"] = "SYNTHETIC_PRIVATE_VALUE"
        mutations.append(value)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.reject(lambda mutation=mutation: transport.parse_missing_scope(encoded(mutation)))
        value = decoded(MISSING_SCOPE_FIXTURE)
        value["error"]["code"] = _IntSubclass(4242)
        with mock.patch.object(transport.adapters, "decode_event_graph_json", return_value=value):
            self.reject(lambda: transport.parse_missing_scope(b"{}"))


if __name__ == "__main__":
    unittest.main()
