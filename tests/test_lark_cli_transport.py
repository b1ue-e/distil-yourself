"""Synthetic-only tests for pinned Lark control-response parsing."""

import copy
import dataclasses
import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "lark_runtime" / "1.0.86"
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import adapters, lark_cli_transport as transport
from knowledge_distiller import lark_profile, native_adapters
from knowledge_distiller.lark_selector import parse_document_selector


TOKEN = "doxcn1234567890AbCdEfGhIjKl"
OWNER = "ou_SYNTHETIC_OWNER"
AUTH_FIXTURE = (FIXTURES / "auth-status.json").read_bytes()
DOCUMENT_FIXTURE = (FIXTURES / "document-info.json").read_bytes()
METADATA_FIXTURE = (FIXTURES / "drive-metadata.json").read_bytes()
MISSING_SCOPE_FIXTURE = (FIXTURES / "missing-scope.json").read_bytes()
RAW_FIXTURE = b'{"ok":true,"identity":"user","data":{"content":"opaque synthetic"}}'

EXPECTED_COMMANDS = (
    ("--version",),
    ("auth", "status", "--json", "--verify"),
    ("api", "GET", "/open-apis/docx/v1/documents/" + TOKEN, "--as", "user"),
    (
        "drive", "metas", "batch_query", "--user-id-type", "open_id",
        "--data", "-", "--as", "user", "--format", "json",
    ),
    (
        "api", "GET",
        "/open-apis/docx/v1/documents/" + TOKEN + "/raw_content",
        "--as", "user",
    ),
)
DRIVE_STDIN = (
    b'{"request_docs":[{"doc_token":"' + TOKEN.encode("ascii")
    + b'","doc_type":"docx"}],"with_url":false}'
)


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

    def test_profile_is_derived_from_the_verified_record_without_parallel_cache(self):
        self.assertFalse(hasattr(lark_profile, "_PINNED_PROFILE"))
        self.assertFalse(hasattr(lark_profile, "_COMPUTED_PROFILE_DIGEST"))
        record = lark_profile.canonical_profile_record()
        profile = lark_profile.load_pinned_profile()
        self.assertEqual(profile.status, record["status"])
        self.assertEqual(profile.product, record["product"])
        self.assertEqual(profile.adapter_version, record["adapter_version"])
        self.assertEqual(profile.product_version, record["product_version"])
        self.assertEqual(profile.native_schema_version, record["native_schema_version"])
        self.assertEqual(
            profile.control_schema_digests,
            tuple(sorted(record["control_schema_digests"].items())),
        )
        self.assertEqual(profile.native_schema_digest, record["native_schema_digest"])
        self.assertEqual(
            profile.read_scope_alternatives,
            tuple(record["scope_alternatives"]["read"]),
        )
        self.assertEqual(
            profile.metadata_scope_alternatives,
            tuple(record["scope_alternatives"]["metadata"]),
        )
        self.assertEqual(profile.timeout_seconds, record["limits"]["timeout_seconds"])
        self.assertEqual(
            profile.control_stdout_limit,
            record["limits"]["control_stdout_bytes"],
        )
        self.assertEqual(profile.raw_stdout_limit, record["limits"]["raw_stdout_bytes"])
        self.assertEqual(profile.stderr_limit, record["limits"]["stderr_bytes"])
        self.assertEqual(
            profile.endpoint_integrity_status,
            record["endpoint_integrity"]["status"],
        )
        self.assertEqual(profile.consistency_mode, record["consistency"]["mode"])
        self.assertEqual(profile.canonical_digest, lark_profile.canonical_digest(record))

    def test_internal_profile_record_mutation_fails_closed_and_is_restored(self):
        original = lark_profile._PROFILE_RECORD
        mutated = copy.deepcopy(original)
        mutated["status"] = "live-enabled"
        with mock.patch.object(lark_profile, "_PROFILE_RECORD", mutated):
            with self.assertRaises(RuntimeError) as caught:
                lark_profile.load_pinned_profile()
            self.assertEqual(caught.exception.args, ("invalid-lark-profile",))
        self.assertIs(lark_profile._PROFILE_RECORD, original)
        self.assertIs(
            lark_profile._PROFILE_RECORD["control_schema_digests"],
            lark_profile._CONTROL_SCHEMA_DIGESTS,
        )
        self.assertEqual(lark_profile.load_pinned_profile().status, "synthetic-only")

    def test_public_profile_and_schema_copies_are_isolated(self):
        record = lark_profile.canonical_profile_record()
        record["status"] = "live-enabled"
        record["limits"]["timeout_seconds"] = 1
        schemas = lark_profile.control_schema_descriptors()
        schemas["auth-status"]["schema"]["properties"]["identity"]["const"] = "bot"
        self.assertEqual(lark_profile.load_pinned_profile().status, "synthetic-only")
        self.assertEqual(lark_profile.load_pinned_profile().timeout_seconds, 30)
        self.assertEqual(transport.parse_verified_identity(AUTH_FIXTURE).open_id, OWNER)

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

    def test_internal_control_schema_mutation_fails_closed_and_is_restored(self):
        original = lark_profile._CONTROL_SCHEMAS
        mutated = copy.deepcopy(original)
        schema = mutated["auth-status"]["schema"]
        user = schema["properties"]["identities"]["properties"]["user"]
        user["properties"]["display_name"]["max_length"] = 512
        with mock.patch.object(lark_profile, "_CONTROL_SCHEMAS", mutated):
            self.reject(lambda: transport.parse_verified_identity(AUTH_FIXTURE))
        self.assertIs(lark_profile._CONTROL_SCHEMAS, original)
        self.assertIs(
            lark_profile._CONTROL_SCHEMAS["auth-status"],
            lark_profile._AUTH_STATUS_SCHEMA,
        )
        self.assertEqual(transport.parse_verified_identity(AUTH_FIXTURE).open_id, OWNER)

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


class LarkProcessTransportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.bin = self.root / "path-bin"
        self.bin.mkdir(mode=0o700)
        self.executable = self.bin / "lark-cli"
        self.command_path = self.root / "commands.jsonl"
        self.stdin_path = self.root / "drive.stdin"
        self.environment_path = self.root / "environment.json"
        self.pid_path = self.root / "descendant.pid"
        self.ready_path = self.root / "descendant.ready"
        self.profile = lark_profile.load_pinned_profile()
        self.selector = parse_document_selector(TOKEN)
        self.environment = {
            "HOME": str(self.root),
            "PATH": str(self.bin),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(self.root),
            "HTTPS_PROXY": "http://SYNTHETIC_PRIVATE_VALUE",
            "LARK_API_BASE_URL": "https://SYNTHETIC_PRIVATE_VALUE",
            "ACCESS_TOKEN": "SYNTHETIC_PRIVATE_VALUE",
            "CLIENT_SECRET": "SYNTHETIC_PRIVATE_VALUE",
            "CUSTOM_ENDPOINT": "SYNTHETIC_PRIVATE_VALUE",
        }
        self.addCleanup(self.cleanup_descendant)
        self.write_fake()

    def write_fake(self, mode="normal", path=None):
        target = self.executable if path is None else Path(path)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        responses = {
            "auth": base64.b64encode(AUTH_FIXTURE).decode("ascii"),
            "document": base64.b64encode(DOCUMENT_FIXTURE).decode("ascii"),
            "metadata": base64.b64encode(METADATA_FIXTURE).decode("ascii"),
            "raw": base64.b64encode(RAW_FIXTURE).decode("ascii"),
        }
        source = f'''#!{sys.executable}
import base64
import json
import os
import signal
import subprocess
import sys
import time

MODE = {mode!r}
COMMAND_PATH = {str(self.command_path)!r}
STDIN_PATH = {str(self.stdin_path)!r}
ENVIRONMENT_PATH = {str(self.environment_path)!r}
PID_PATH = {str(self.pid_path)!r}
READY_PATH = {str(self.ready_path)!r}
RESPONSES = {responses!r}

args = tuple(sys.argv[1:])
stdin = sys.stdin.buffer.read()
with open(COMMAND_PATH, "ab") as stream:
    stream.write(json.dumps(args, separators=(",", ":")).encode("utf-8") + b"\\n")
with open(ENVIRONMENT_PATH, "w", encoding="utf-8") as stream:
    json.dump(dict(os.environ), stream, sort_keys=True)
if args[:3] == ("drive", "metas", "batch_query"):
    with open(STDIN_PATH, "wb") as stream:
        stream.write(stdin)

if MODE in {{"timeout", "partial", "descendant"}}:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child_ready_path = PID_PATH + ".child-ready"
    child_program = """import os
import signal
import sys
import time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
temporary = sys.argv[1] + '.tmp'
with open(temporary, 'w', encoding='ascii') as stream:
    stream.write('ready')
os.replace(temporary, sys.argv[1])
time.sleep(60)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_program, child_ready_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    temporary_pid_path = PID_PATH + ".tmp"
    with open(temporary_pid_path, "w", encoding="ascii") as stream:
        stream.write(str(child.pid))
    os.replace(temporary_pid_path, PID_PATH)
    while not os.path.exists(child_ready_path):
        if child.poll() is not None:
            raise SystemExit(7)
        time.sleep(0.005)
    temporary_ready_path = READY_PATH + ".tmp"
    with open(temporary_ready_path, "w", encoding="ascii") as stream:
        stream.write("ready")
    os.replace(temporary_ready_path, READY_PATH)
    if MODE == "partial":
        sys.stdout.buffer.write(b"lark-cli version 1.0.86")
        sys.stdout.buffer.flush()
    if MODE != "descendant":
        time.sleep(60)
if MODE == "stdout-overflow":
    sys.stdout.buffer.write(b"SYNTHETIC_PRIVATE_VALUE" * 50000)
    raise SystemExit(0)
if MODE == "stderr-overflow":
    sys.stderr.buffer.write(b"SYNTHETIC_PRIVATE_VALUE" * 5000)
    raise SystemExit(0)
if MODE == "nonzero":
    sys.stdout.buffer.write(b"SYNTHETIC_PRIVATE_VALUE")
    sys.stderr.buffer.write(b"SYNTHETIC_PRIVATE_VALUE")
    raise SystemExit(9)
if MODE == "replace-self":
    replacement = sys.argv[0] + ".replacement"
    with open(replacement, "w", encoding="utf-8") as stream:
        stream.write("#!/bin/sh\\nexit 99\\n")
    os.chmod(replacement, 0o700)
    os.replace(replacement, sys.argv[0])

if args == ("--version",):
    output = b"lark-cli version 9.9.9\\n" if MODE == "bad-version" else b"lark-cli version 1.0.86\\n"
elif args == ("auth", "status", "--json", "--verify"):
    output = base64.b64decode(RESPONSES["auth"])
elif args[:3] == ("api", "GET", "/open-apis/docx/v1/documents/{TOKEN}"):
    output = base64.b64decode(RESPONSES["document"])
elif args[:3] == ("drive", "metas", "batch_query"):
    output = base64.b64decode(RESPONSES["metadata"])
elif args[:2] == ("api", "GET") and args[2].endswith("/raw_content"):
    output = base64.b64decode(RESPONSES["raw"])
else:
    raise SystemExit(8)
sys.stdout.buffer.write(output)
'''
        target.write_text(source, encoding="utf-8")
        target.chmod(0o700)
        return target

    def client(self):
        return transport.LarkCliTransport(self.profile)

    def commands(self):
        return tuple(
            tuple(json.loads(line))
            for line in self.command_path.read_text(encoding="utf-8").splitlines()
        )

    def assert_code_only(self, action, code):
        with self.assertRaises(transport.LarkTransportError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.args, (code,))
        rendered = str(caught.exception) + repr(caught.exception) + repr(vars(caught.exception))
        for private in (
            "SYNTHETIC_PRIVATE_VALUE", TOKEN, OWNER, str(self.root),
            "Synthetic Document", "/open-apis/", "--version",
        ):
            self.assertNotIn(private, rendered)

    def published_pid(self, timeout=3, require_ready=True):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                pid = int(self.pid_path.read_text(encoding="ascii"))
                if pid > 1 and (not require_ready or self.ready_path.exists()):
                    return pid
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(0.01)
        return None

    def cleanup_descendant(self):
        pid = self.published_pid(timeout=0.05, require_ready=False)
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def clear_descendant_state(self):
        for suffix in ("", ".tmp", ".child-ready", ".child-ready.tmp"):
            path = Path(str(self.pid_path) + suffix)
            if path.exists():
                path.unlink()
        for path in (self.ready_path, Path(str(self.ready_path) + ".tmp")):
            if path.exists():
                path.unlink()

    def assert_pid_gone(self):
        pid = self.published_pid()
        self.assertIsNotNone(pid)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                self.clear_descendant_state()
                return
            time.sleep(0.01)
        self.fail("synthetic descendant survived process-group cleanup")

    def test_transport_uses_only_fixed_commands_and_canonical_drive_stdin(self):
        with mock.patch.dict(os.environ, self.environment, clear=True):
            with mock.patch.object(transport.shutil, "which", wraps=shutil.which) as which:
                client = self.client()
                self.assertEqual(client.version(), "1.0.86")
                self.assertEqual(client.verify_user().open_id, OWNER)
                self.assertEqual(client.observe(self.selector).revision, "7")
                self.assertEqual(client.raw_content(self.selector), RAW_FIXTURE)
        which.assert_called_once_with("lark-cli")
        self.assertEqual(self.commands(), EXPECTED_COMMANDS)
        self.assertEqual(self.stdin_path.read_bytes(), DRIVE_STDIN)

    def test_transport_invokes_native_directly_without_shell_or_ambient_secrets(self):
        calls = []
        real_popen = subprocess.Popen

        def record(*args, **kwargs):
            calls.append((args, kwargs))
            return real_popen(*args, **kwargs)

        with mock.patch.dict(os.environ, self.environment, clear=True):
            with mock.patch.object(transport.subprocess, "Popen", side_effect=record):
                self.assertEqual(self.client().version(), "1.0.86")
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], (str(self.executable.resolve()), "--version"))
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["close_fds"], True)
        self.assertIs(kwargs["start_new_session"], True)
        self.assertEqual(kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        expected_environment = {
            "HOME", "PATH", "LANG", "LC_ALL", "TMPDIR",
            "LARKSUITE_CLI_NO_UPDATE_NOTIFIER",
            "LARKSUITE_CLI_NO_SKILLS_NOTIFIER",
        }
        self.assertEqual(set(kwargs["env"]), expected_environment)
        child_env = json.loads(self.environment_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(child_env) - {"__CF_USER_TEXT_ENCODING"},
            expected_environment,
        )
        self.assertFalse(any(
            marker in name
            for name in child_env
            for marker in ("TOKEN", "SECRET", "PROXY", "BASE_URL", "ENDPOINT")
        ))

    def test_packaged_wrapper_is_resolved_but_never_executed(self):
        package = self.root / "package"
        native = package / "bin" / "lark-cli"
        self.write_fake(path=native)
        wrapper = package / "scripts" / "run.js"
        marker = self.root / "wrapper-executed"
        wrapper.parent.mkdir(mode=0o700)
        wrapper.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(99)\n", encoding="utf-8")
        wrapper.chmod(0o700)
        self.executable.unlink()
        self.executable.symlink_to(wrapper)
        with mock.patch.dict(os.environ, self.environment, clear=True):
            self.assertEqual(self.client().version(), "1.0.86")
        self.assertFalse(marker.exists())

    def test_resolution_rejects_missing_relative_dangling_and_unsafe_targets(self):
        cases = []
        cases.append((None, "lark-cli-not-found"))
        cases.append(("relative/lark-cli", "lark-cli-unsafe"))
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "absent")
        cases.append((str(dangling), "lark-cli-not-found"))
        directory = self.root / "directory"
        directory.mkdir(mode=0o700)
        cases.append((str(directory), "lark-cli-unsafe"))
        unsafe = self.write_fake(path=self.root / "unsafe")
        unsafe.chmod(0o722)
        cases.append((str(unsafe), "lark-cli-unsafe"))
        for result, code in cases:
            with self.subTest(result=result, code=code):
                with mock.patch.dict(os.environ, self.environment, clear=True):
                    with mock.patch.object(transport.shutil, "which", return_value=result):
                        self.assert_code_only(self.client, code)

    def test_resolution_rejects_unrecognized_wrapper_and_missing_native(self):
        package = self.root / "bad-package"
        other = package / "scripts" / "other-launcher"
        self.write_fake(path=other)
        entry = self.root / "other-entry"
        entry.symlink_to(other)
        missing_wrapper = self.root / "missing-package" / "scripts" / "run.js"
        self.write_fake(path=missing_wrapper)
        for result, code in (
            (str(entry), "lark-cli-unsafe"),
            (str(missing_wrapper), "lark-cli-not-found"),
        ):
            with self.subTest(result=result):
                with mock.patch.dict(os.environ, self.environment, clear=True):
                    with mock.patch.object(transport.shutil, "which", return_value=result):
                        self.assert_code_only(self.client, code)

    def test_version_selector_and_fingerprint_checks_fail_closed(self):
        with mock.patch.dict(os.environ, self.environment, clear=True):
            self.write_fake("bad-version")
            self.assert_code_only(self.client().version, "lark-cli-incompatible")

            self.write_fake()
            client = self.client()
            self.write_fake("normal")
            self.assert_code_only(client.version, "lark-cli-fingerprint-changed")

            self.write_fake("replace-self")
            client = self.client()
            self.assert_code_only(client.version, "lark-cli-fingerprint-changed")

            self.write_fake()
            client = self.client()
            self.assert_code_only(lambda: client.observe(TOKEN), "invalid-selector")
            self.assert_code_only(lambda: client.raw_content(TOKEN), "invalid-selector")

    def test_timeout_and_partial_output_kill_the_process_group(self):
        owner = self

        class ReadyTimeoutProfile:
            @property
            def timeout_seconds(inner_self):
                if owner.published_pid(timeout=10) is None:
                    raise AssertionError("synthetic descendant was not ready")
                return 0.2

            def __getattr__(inner_self, name):
                return getattr(owner.profile, name)

        quick = ReadyTimeoutProfile()
        for mode in ("timeout", "partial"):
            with self.subTest(mode=mode):
                self.clear_descendant_state()
                self.write_fake(mode)
                with mock.patch.dict(os.environ, self.environment, clear=True):
                    self.assert_code_only(
                        transport.LarkCliTransport(quick).version,
                        "lark-cli-timeout",
                    )
                self.assert_pid_gone()

    def test_output_limits_discard_partial_data_and_reap_the_process(self):
        for mode in ("stdout-overflow", "stderr-overflow"):
            with self.subTest(mode=mode):
                self.write_fake(mode)
                processes = []
                real_popen = subprocess.Popen

                def start(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    processes.append(process)
                    return process

                with mock.patch.dict(os.environ, self.environment, clear=True):
                    with mock.patch.object(transport.subprocess, "Popen", side_effect=start):
                        self.assert_code_only(
                            self.client().version,
                            "lark-cli-output-limit",
                        )
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())

    def test_nonzero_exit_is_code_only_and_does_not_leak_descriptors(self):
        self.write_fake("nonzero")
        before = len(os.listdir("/dev/fd"))
        with mock.patch.dict(os.environ, self.environment, clear=True):
            client = self.client()
            for _ in range(4):
                self.assert_code_only(client.version, "lark-cli-failed")
        self.assertEqual(len(os.listdir("/dev/fd")), before)

    def test_keyboard_interrupt_propagates_after_child_cleanup(self):
        self.write_fake("timeout")
        real_selector = transport.selectors.DefaultSelector
        real_popen = subprocess.Popen
        processes = []

        def start(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        class InterruptingSelector:
            def __init__(inner_self):
                inner_self.delegate = real_selector()

            def register(inner_self, *args, **kwargs):
                return inner_self.delegate.register(*args, **kwargs)

            def unregister(inner_self, *args, **kwargs):
                return inner_self.delegate.unregister(*args, **kwargs)

            def get_map(inner_self):
                return inner_self.delegate.get_map()

            def select(inner_self, timeout=None):
                if self.published_pid(timeout=10) is None:
                    raise AssertionError("synthetic descendant did not publish its PID")
                raise KeyboardInterrupt

            def close(inner_self):
                return inner_self.delegate.close()

        with mock.patch.dict(os.environ, self.environment, clear=True):
            with mock.patch.object(transport.subprocess, "Popen", side_effect=start):
                with mock.patch.object(transport.selectors, "DefaultSelector", InterruptingSelector):
                    with self.assertRaises(KeyboardInterrupt):
                        self.client().version()
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assert_pid_gone()

    def test_primary_exception_survives_cleanup_and_post_fingerprint_failures(self):
        cases = (
            (KeyboardInterrupt(), KeyboardInterrupt, None),
            (SystemExit(17), SystemExit, None),
            (RuntimeError("SYNTHETIC_PRIVATE_VALUE"), transport.LarkTransportError, "lark-cli-failed"),
            (
                transport.LarkTransportError("lark-cli-timeout"),
                transport.LarkTransportError,
                "lark-cli-timeout",
            ),
        )
        real_selector = transport.selectors.DefaultSelector
        real_popen = subprocess.Popen
        for poll_error_type in (RuntimeError, KeyboardInterrupt):
            for primary, expected_type, expected_code in cases:
                with self.subTest(
                    poll_error=poll_error_type.__name__,
                    primary_type=type(primary).__name__,
                ):
                    processes = []

                    class PollFaultProcess:
                        def __init__(inner_self, delegate):
                            inner_self.delegate = delegate
                            inner_self.kill_calls = 0
                            inner_self.wait_calls = 0

                        def __getattr__(inner_self, name):
                            return getattr(inner_self.delegate, name)

                        def poll(inner_self):
                            raise poll_error_type("SYNTHETIC_PRIVATE_VALUE")

                        def kill(inner_self):
                            inner_self.kill_calls += 1
                            return inner_self.delegate.kill()

                        def wait(inner_self, *args, **kwargs):
                            inner_self.wait_calls += 1
                            return inner_self.delegate.wait(*args, **kwargs)

                    def start(*args, **kwargs):
                        process = PollFaultProcess(real_popen(*args, **kwargs))
                        processes.append(process)
                        return process

                    class FaultingSelector:
                        def __init__(inner_self):
                            inner_self.delegate = real_selector()

                        def register(inner_self, *args, **kwargs):
                            return inner_self.delegate.register(*args, **kwargs)

                        def unregister(inner_self, *args, **kwargs):
                            return inner_self.delegate.unregister(*args, **kwargs)

                        def get_map(inner_self):
                            return inner_self.delegate.get_map()

                        def select(inner_self, timeout=None):
                            deadline = time.monotonic() + 1
                            while not self.command_path.exists() and time.monotonic() < deadline:
                                time.sleep(0.01)
                            raise primary

                        def close(inner_self):
                            inner_self.delegate.close()
                            raise OSError("SYNTHETIC_PRIVATE_VALUE")

                    if self.command_path.exists():
                        self.command_path.unlink()
                    with mock.patch.dict(os.environ, self.environment, clear=True):
                        client = self.client()
                        changed = transport.LarkTransportError(
                            "lark-cli-fingerprint-changed"
                        )
                        with mock.patch.object(
                            client, "_check_fingerprint", side_effect=(None, changed)
                        ) as fingerprint:
                            with mock.patch.object(
                                transport.subprocess, "Popen", side_effect=start
                            ):
                                with mock.patch.object(
                                    transport.selectors,
                                    "DefaultSelector",
                                    FaultingSelector,
                                ):
                                    with self.assertRaises(expected_type) as caught:
                                        client.version()
                    fingerprint.assert_has_calls((mock.call(), mock.call()))
                    self.assertEqual(len(processes), 1)
                    process = processes[0]
                    self.assertIsNotNone(process.delegate.poll())
                    self.assertGreaterEqual(process.kill_calls, 1)
                    self.assertGreaterEqual(process.wait_calls, 1)
                    if expected_code is None:
                        self.assertIs(caught.exception, primary)
                    else:
                        self.assertEqual(caught.exception.code, expected_code)

    def test_rejects_invalid_home_tmpdir_and_path_environment_values(self):
        cases = (
            ("HOME", str(self.root / "missing-home")),
            ("TMPDIR", str(self.root / "missing-tmp")),
            ("PATH", ""),
            ("PATH", "relative/path"),
        )
        for name, value in cases:
            with self.subTest(name=name, value=value):
                environment = dict(self.environment)
                environment[name] = value
                with mock.patch.dict(os.environ, environment, clear=True):
                    with mock.patch.object(
                        transport.shutil, "which", return_value=str(self.executable)
                    ):
                        self.assert_code_only(self.client, "lark-cli-unsafe")

    def test_successful_parent_with_live_descendant_is_rejected_and_cleaned(self):
        self.write_fake("descendant")
        with mock.patch.dict(os.environ, self.environment, clear=True):
            self.assert_code_only(self.client().version, "lark-cli-failed")
        self.assert_pid_gone()


if __name__ == "__main__":
    unittest.main()
