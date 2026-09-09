# Local Codex Standalone Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone CLI path that ingests one explicitly selected, effective-UID-owned Codex session prefix through the existing broker, adapter, redaction, provenance, and atomic-ingestion transaction, using only synthetic files for implementation and acceptance.

**Architecture:** Extend the existing descriptor reader with optional owner and owner-only mode checks, then carry the trusted effective UID through `SessionIdentity`. A new `local_runtime.py` strictly decodes the small private request, derives opaque local identity and short-lived authorization records, reads a `0600` redaction key, and delegates to `ingestion.ingest_source`; `kd.py` exposes this as a new command while preserving the injected `ingest-source` boundary. No discovery, username lookup, network access, real session read, key management, extraction, evaluation, export, installation, or publication is added.

**Tech Stack:** Python 3.9+ standard library (`argparse`, `dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `stat`, `time`, `unittest`), existing Knowledge Distiller modules and synthetic Codex `0.153.0 / rollout-jsonl-v1` fixture

---

## File map and fixed interfaces

- Modify `knowledge-distiller/scripts/knowledge_distiller/source_io.py`: add optional same-descriptor UID and exact-`0600` policy to `read_source`; retain the sole path walker and the existing stable-read algorithm.
- Modify `tests/test_source_io.py`: prove policy arguments fail before open and UID/mode changes fail on the opened descriptor.
- Modify `knowledge-distiller/scripts/knowledge_distiller/brokers.py`: add `expected_owner_uid` to trusted `SessionIdentity`, pass it to `read_source`, and support a domain-separated exact-path commitment so persisted grants do not contain the filesystem path.
- Modify `knowledge-distiller/scripts/knowledge_distiller/ingestion.py`: validate either the backward-compatible literal selector or the standalone runtime's exact-path commitment before normalization and persistence.
- Modify `tests/test_brokers.py` and `tests/test_ingestion.py`: update trusted identities and prove request/context/path/UID substitution cannot cross either binding mode.
- Create `knowledge-distiller/scripts/knowledge_distiller/local_runtime.py`: own strict request decoding, opaque local identity derivation, authorization materialization, redaction-key reading, and standalone Codex orchestration.
- Create `tests/test_local_runtime.py`: unit-test the new boundary and run one synthetic end-to-end transaction without real source access.
- Modify `knowledge-distiller/scripts/kd.py`: add `ingest-codex-session TASK_PATH REQUEST --redaction-key-file KEY` and bounded error mapping.
- Modify `tests/test_cli.py`: cover argument preflight, delegation, bounded output, code-only failures, and a real subprocess acceptance path over temporary synthetic files.
- Modify `knowledge-distiller/SKILL.md`, `knowledge-distiller/references/workflow.md`, `knowledge-distiller/references/authorization.md`, `README.md`, and `tests/test_skill_contract.py`: expose only the exact new standalone workflow and preserve all deferred boundaries.
- Modify `docs/specs/2026-09-09-local-codex-runtime-design.md`, `docs/status/2026-09-05-implementation-status.md`, and this plan: record implementation and review results after the gates pass.

The new private request is exactly:

```json
{
  "schema_version": "knowledge-distiller.local-codex-ingestion-request/v1",
  "transaction_id": "ingest-codex-1",
  "expected_generation_id": "g-current",
  "session_path": "/explicit/session.jsonl",
  "project_id": "project-1",
  "prefix_length": 1234,
  "prefix_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "derived_processing_until": 4102444800
}
```

It has no identity, UID, issuer, owner, grant, attestation, product, adapter, schema-selection, prefix-start, discovery, or environment fields. Unknown fields are rejected by the closed decoder.

### Task 1: Add same-descriptor owner and secret-mode checks

**Files:**

- Modify: `knowledge-distiller/scripts/knowledge_distiller/source_io.py` (`read_source` argument validation and first/final `fstat` policy)
- Modify: `tests/test_source_io.py` (`SourceIOTest`)

- [x] **Step 1: Write failing policy tests**

Add these methods to `SourceIOTest`; use its existing `self.path`, `self.read`, `self.reject`, and `self.metadata` helpers:

```python
def test_owner_and_owner_only_policy_use_open_descriptor_metadata(self):
    uid = os.geteuid()
    os.chmod(self.path, 0o600)
    self.assertEqual(
        self.read(expected_owner_uid=uid, owner_only=True), b"abcdef")

    wrong_uid = uid + 1
    self.reject(
        lambda: self.read(expected_owner_uid=wrong_uid),
        "source-owner-mismatch")
    os.chmod(self.path, 0o640)
    self.reject(
        lambda: self.read(expected_owner_uid=uid, owner_only=True),
        "unsafe-source-file")

def test_owner_policy_arguments_are_validated_before_open(self):
    invalid = (
        {"expected_owner_uid": True},
        {"expected_owner_uid": -1},
        {"expected_owner_uid": "0"},
        {"owner_only": 1},
        {"owner_only": True},
    )
    for kwargs in invalid:
        with self.subTest(kwargs=kwargs), mock.patch.object(os, "open") as opened:
            self.reject(lambda: self.read(**kwargs), "invalid-source-bound")
            opened.assert_not_called()

def test_owner_change_during_read_is_rejected(self):
    before = self.metadata(st_uid=os.geteuid(), st_mode=stat.S_IFREG | 0o600)
    after = self.metadata(st_uid=os.geteuid() + 1, st_mode=stat.S_IFREG | 0o600)
    with mock.patch.object(os, "fstat", side_effect=(before, after)):
        self.reject(
            lambda: self.read(expected_owner_uid=os.geteuid(), owner_only=True),
            "input-changed")
```

- [x] **Step 2: Run the focused test red**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_source_io.SourceIOTest.test_owner_and_owner_only_policy_use_open_descriptor_metadata tests.test_source_io.SourceIOTest.test_owner_policy_arguments_are_validated_before_open tests.test_source_io.SourceIOTest.test_owner_change_during_read_is_rejected -v`

Expected: FAIL because `read_source` does not accept the two policy arguments.

- [x] **Step 3: Implement the minimal shared policy**

Change the signature and validation at the start of `read_source` to:

```python
def read_source(path: str, *, max_bytes: int = MAX_GRAPH_BYTES,
                prefix_length: Optional[int] = None,
                expected_digest: Optional[str] = None,
                expected_owner_uid: Optional[int] = None,
                owner_only: bool = False) -> bytes:
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_GRAPH_BYTES:
        raise SourceIOError("invalid-source-bound")
    if (expected_owner_uid is not None
            and (type(expected_owner_uid) is not int
                 or not 0 <= expected_owner_uid <= 9223372036854775807)):
        raise SourceIOError("invalid-source-bound")
    if type(owner_only) is not bool or (owner_only and expected_owner_uid is None):
        raise SourceIOError("invalid-source-bound")
```

Immediately after the existing `_regular(metadata, ...)` call, add:

```python
        if (expected_owner_uid is not None
                and metadata.st_uid != expected_owner_uid):
            raise SourceIOError("source-owner-mismatch")
        if owner_only and stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SourceIOError("unsafe-source-file")
```

Replace the existing `identity_fields` assignment with:

```python
        identity_fields = ("st_dev", "st_ino", "st_nlink", "st_mode")
        if expected_owner_uid is not None:
            identity_fields += ("st_uid",)
```

Update the module docstring to say that callers may opt into same-descriptor owner and owner-only mode policy; ordinary event-graph callers still have no owner/mode restriction. Do not call `stat`, `lstat`, `Path.stat`, or `_open_source` a second time. Do not change behavior for callers that omit both new arguments.

- [x] **Step 4: Run source-I/O regressions green**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_source_io -v`

Expected: PASS, including the existing single-target-open, prefix-confirmation, append-exclusion, moving-file, descriptor-cleanup, symlink, hardlink, sparse, and special-file tests.

- [x] **Step 5: Commit the descriptor policy**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/source_io.py tests/test_source_io.py
git commit -m "feat: bind safe reads to file ownership"
```

### Task 2: Bind the local broker to the effective UID and a private path commitment

**Files:**

- Modify: `knowledge-distiller/scripts/knowledge_distiller/brokers.py` (`SessionIdentity`, `read_local_session`)
- Modify: `knowledge-distiller/scripts/knowledge_distiller/ingestion.py` (`_validated_snapshot` Codex selector check)
- Modify: `tests/test_brokers.py` (all `SessionIdentity` construction and local broker policy)
- Modify: `tests/test_ingestion.py` (literal-selector regression and committed-selector acceptance)

- [x] **Step 1: Write the failing broker ownership test**

Update the `local()` helper to construct the expanded trusted identity:

```python
identity = brokers.SessionIdentity(
    "owner-1", "tenant-1", "project-1", "collaborator-1",
    "synthetic-1", digest(b"synthetic-schema"), os.geteuid())
```

Add this test:

```python
def test_local_expected_owner_uid_is_trusted_identity_not_request_data(self):
    path, context, request, identity = self.local()
    with mock.patch.object(source_io, "read_source", return_value=b"abc") as read:
        result = self.read_session(context, request, identity)
    read.assert_called_once_with(
        request.path,
        max_bytes=brokers.MAX_GRAPH_BYTES,
        prefix_length=request.prefix_length,
        expected_digest=request.prefix_digest,
        expected_owner_uid=os.geteuid(),
    )
    self.assertEqual(result.raw, b"abc")

    with mock.patch.object(source_io, "read_source") as blocked:
        self.reject(
            lambda: self.read_session(
                context, request,
                replace(identity, expected_owner_uid=True)),
            "invalid-broker-request")
        self.reject(
            lambda: self.read_session(
                context, request,
                replace(identity, expected_owner_uid=-1)),
            "invalid-broker-request")
        blocked.assert_not_called()

def test_local_path_commitment_binds_exact_path_without_persisting_it(self):
    path, context, request, identity = self.local()
    selector_key = b"k" * 32
    commitment = brokers.session_selector_commitment(str(path), selector_key)
    committed_context = replace(context, selector=commitment)
    result = self.read_session(
        committed_context, request,
        replace(identity, selector_key=selector_key),
        records=self.records(committed_context))
    self.assertEqual(result.raw, b"abc")
    self.assertEqual(result.selector_digest, digest(commitment.encode("utf-8")))
    self.assertNotIn(str(path), commitment)

    substituted = replace(request, path=str(path.parent / "other.jsonl"))
    with mock.patch.object(source_io, "read_source") as blocked:
        self.reject(
            lambda: self.read_session(
                committed_context, substituted,
                replace(identity, selector_key=selector_key),
                records=self.records(committed_context)),
            "authorization-context-mismatch")
        blocked.assert_not_called()
```

Import `source_io` alongside the current package imports in this test file.

In `tests/test_ingestion.py`, extend the dataclass import to `from dataclasses import asdict, replace` and add this method to `IngestionTest`:

```python
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

    result, calls = self.ingest(request, acquired)

    self.assertEqual(calls, ["codex"])
    generation = self.root / "generations" / result.generation_id
    persisted = b"".join(
        path.read_bytes() for path in generation.rglob("*") if path.is_file())
    self.assertNotIn(private_path.encode("utf-8"), persisted)
    self.assertIn(commitment.encode("ascii"), persisted)
```

- [x] **Step 2: Run the focused broker test red**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_brokers.BrokerTest.test_local_expected_owner_uid_is_trusted_identity_not_request_data -v`

Expected: FAIL because `SessionIdentity` lacks `expected_owner_uid` and the commitment helper does not exist.

- [x] **Step 3: Add and validate the trusted UID**

Replace `SessionIdentity` with:

```python
@dataclass(frozen=True)
class SessionIdentity:
    active_principal: str
    tenant_account: str
    project_id: str
    content_owner: str
    product_version: str
    native_schema_digest: str
    expected_owner_uid: Optional[int] = field(default=None, repr=False)
    selector_key: Optional[bytes] = field(default=None, repr=False)
```

The existing `Optional` import in `brokers.py` supports these annotations. Both defaults preserve six- and seven-argument source compatibility for injected hosts; the standalone runtime always supplies the current effective UID and supplies the in-memory selector key only in committed mode. Neither secret-bearing field may be serialized or persisted.

Add this helper next to the existing `_digest` helper:

```python
def session_selector_commitment(path, key=None) -> str:
    if type(path) is not str:
        raise BrokerError("invalid-selector")
    try:
        raw = path.encode("utf-8")
    except UnicodeError:
        raise BrokerError("invalid-selector") from None
    if (not raw or len(raw) > 4096 or b"\x00" in raw
            or type(key) is not bytes or not 32 <= len(key) <= 64):
        raise BrokerError("invalid-selector")
    return "hmac-sha256:" + hmac.new(
        key, b"local-session-selector\x00" + raw,
        hashlib.sha256).hexdigest()
```

Add `import hmac` next to `hashlib`. Exact `hmac-sha256:<64 lowercase hex>` syntax selects committed mode; that tagged namespace is reserved and cannot also be interpreted as a literal relative filename.

After the identity identifier loop in `read_local_session`, add:

```python
    if (identity.expected_owner_uid is not None
            and (type(identity.expected_owner_uid) is not int
                 or not 0 <= identity.expected_owner_uid <= 9223372036854775807)):
        raise BrokerError("invalid-broker-request")
```

Replace the entire broker path/identity context-mismatch `if` block with:

```python
    if type(request.path) is not str:
        raise BrokerError("invalid-broker-request")
    committed_selector = re.fullmatch(
        r"hmac-sha256:[0-9a-f]{64}", context.selector) is not None
    if committed_selector:
        if (type(identity.selector_key) is not bytes
                or not 32 <= len(identity.selector_key) <= 64):
            raise BrokerError("invalid-broker-request")
        selector_matches = hmac.compare_digest(
            session_selector_commitment(
                request.path, identity.selector_key),
            context.selector)
    else:
        if identity.selector_key is not None:
            raise BrokerError("invalid-broker-request")
        selector_matches = request.path == context.selector
    if (not selector_matches
            or identity.active_principal != context.active_principal
            or identity.tenant_account != context.tenant_account
            or identity.content_owner != context.content_owner
            or identity.project_id != request.project_id):
        raise BrokerError("authorization-context-mismatch")
```

Then change the one `source_io.read_source` call to:

```python
        raw = source_io.read_source(
            request.path,
            max_bytes=MAX_GRAPH_BYTES,
            prefix_length=request.prefix_length,
            expected_digest=request.prefix_digest,
            expected_owner_uid=identity.expected_owner_uid,
        )
```

The request and authorization context receive no UID field.

In `ingestion._validated_snapshot`, accept `selector_key`, reject non-built-in path/length/digest scalar types, require the returned byte count to equal the authorized prefix, and make the same tagged-mode decision independently:

```python
        if (type(native_request.path) is not str
                or type(native_request.prefix_length) is not int
                or not 0 < native_request.prefix_length <= adapters.MAX_GRAPH_BYTES
                or type(native_request.prefix_digest) is not str
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    native_request.prefix_digest) is None):
            _fail("invalid-broker-request")
        committed_selector = re.fullmatch(
            r"hmac-sha256:[0-9a-f]{64}", context.selector) is not None
        if committed_selector:
            try:
                expected_selector = brokers.session_selector_commitment(
                    native_request.path, selector_key)
            except brokers.BrokerError:
                _fail("invalid-broker-request")
            selector_matches = hmac.compare_digest(
                expected_selector, context.selector)
        else:
            selector_matches = native_request.path == context.selector
        if (not selector_matches or context.revision is not None
                or source_range is None or source_range.start != 0
                or native_request.prefix_length != source_range.end + 1
                or native_request.prefix_digest != snapshot.raw_digest
                or snapshot.source_byte_count != native_request.prefix_length):
            _fail("broker-evidence-mismatch")
```

Pass the already-validated `redaction_key` from `ingest_source` into `_validated_snapshot` as `selector_key`. Add `hmac` and `re` imports in `ingestion.py`. The literal path branch preserves the existing injected API. The keyed commitment branch is used only by the standalone runtime; it cryptographically binds one exact path without leaving an offline path-guessing oracle in persisted authorization records.

- [x] **Step 4: Run broker and ingestion regressions green**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_brokers tests.test_ingestion -v`

Expected: PASS after adding explicit UID coverage and an ingestion regression for the committed selector. Existing six-argument injected-runtime `SessionIdentity` construction and literal selectors remain green through the optional default. No real session file is read.

- [x] **Step 5: Commit the broker binding**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/brokers.py knowledge-distiller/scripts/knowledge_distiller/ingestion.py tests/test_brokers.py tests/test_ingestion.py
git commit -m "feat: enforce Codex session owner identity"
```

Completion result: implementation and review fixes are in `0b3ea4a`, `b830331`, and `2c028bc`. TDD regressions cover over-returned bytes, bool/scalar-subclass bypasses, tagged-selector literal substitution, invalid/missing keys, UTF-8 commitments, and full-task persistence privacy. Specification review passed; quality/security review returned `READY` with no Critical or Important findings. The focused broker/ingestion/source-I/O set passed 59/59 and the full repository passed 392/392. Production review found no redundant or dead logic; the broker and ingestion checks remain intentionally independent.

### Task 3: Strictly decode the standalone private request

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/local_runtime.py`
- Create: `tests/test_local_runtime.py`

- [ ] **Step 1: Write failing decoder tests**

Create `tests/test_local_runtime.py` with the imports and decoder fixture below:

```python
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import adapters, local_runtime


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def request_data(**changes):
    value = {
        "schema_version": local_runtime.REQUEST_SCHEMA,
        "transaction_id": "ingest-codex-1",
        "expected_generation_id": "generation-1",
        "session_path": "/synthetic/session.jsonl",
        "project_id": "project-1",
        "prefix_length": 3,
        "prefix_digest": digest(b"abc"),
        "derived_processing_until": 2000,
    }
    value.update(changes)
    return value


def encoded(**changes):
    return json.dumps(
        request_data(**changes),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class LocalRuntimeTest(unittest.TestCase):
    def reject_request(self, raw):
        with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
            local_runtime.decode_local_codex_request(raw)
        self.assertEqual(caught.exception.code, "invalid-local-request")
        self.assertEqual(caught.exception.args, ("invalid-local-request",))

    def test_request_decoder_accepts_only_the_closed_contract(self):
        result = local_runtime.decode_local_codex_request(encoded())
        self.assertEqual(result, local_runtime.LocalCodexRequest(
            local_runtime.REQUEST_SCHEMA,
            "ingest-codex-1",
            "generation-1",
            "/synthetic/session.jsonl",
            "project-1",
            3,
            digest(b"abc"),
            2000,
        ))
        self.assertNotIn("session.jsonl", repr(result))

        invalid_values = (
            encoded(schema_version="other"),
            encoded(prefix_length=True),
            encoded(prefix_length=0),
            encoded(prefix_length=adapters.MAX_GRAPH_BYTES + 1),
            encoded(prefix_digest="SHA256:" + "0" * 64),
            encoded(session_path="/synthetic/*.jsonl"),
            encoded(session_path=" latest "),
            encoded(project_id=""),
            encoded(derived_processing_until=True),
            json.dumps(dict(request_data(), active_principal="caller")).encode(),
            json.dumps(dict(request_data(), product_version="0.153.0")).encode(),
            b'{"schema_version":"knowledge-distiller.local-codex-ingestion-request/v1",'
            b'"schema_version":"knowledge-distiller.local-codex-ingestion-request/v1"}',
            b"\xff",
            b"{}",
            b"",
            b" " * (local_runtime.MAX_LOCAL_REQUEST_BYTES + 1),
        )
        for raw in invalid_values:
            with self.subTest(raw=raw[:80]):
                self.reject_request(raw)
```

- [ ] **Step 2: Run the decoder test red**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_local_runtime.LocalRuntimeTest.test_request_decoder_accepts_only_the_closed_contract -v`

Expected: FAIL because `knowledge_distiller.local_runtime` does not exist.

- [ ] **Step 3: Implement the closed decoder and public types**

Create `knowledge-distiller/scripts/knowledge_distiller/local_runtime.py` with this initial content:

```python
"""Standalone local Codex ingestion without discovery or caller identity input."""

from dataclasses import dataclass

from . import adapters, authorization, ingestion


REQUEST_SCHEMA = "knowledge-distiller.local-codex-ingestion-request/v1"
MAX_LOCAL_REQUEST_BYTES = ingestion.MAX_REQUEST_BYTES
READ_WINDOW_SECONDS = 5 * 60
MAX_DERIVED_SECONDS = 90 * 24 * 60 * 60
PURPOSE = "distill-knowledge"
LOCAL_OWNER_VERIFIER = "local-owner-verifier-v1"
REQUEST_FIELDS = frozenset({
    "schema_version", "transaction_id", "expected_generation_id",
    "session_path", "project_id", "prefix_length", "prefix_digest",
    "derived_processing_until",
})


class LocalRuntimeError(ValueError):
    """Allowlisted code-only failure for the standalone input boundary."""

    ALLOWED = frozenset({
        "invalid-local-request", "invalid-derived-deadline",
        "local-identity-unavailable", "unsafe-redaction-key",
        "local-runtime-failed",
    })

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


def _fail(code: str) -> None:
    raise LocalRuntimeError(code)


def decode_local_codex_request(raw: bytes) -> LocalCodexRequest:
    if type(raw) is not bytes or not raw or len(raw) > MAX_LOCAL_REQUEST_BYTES:
        _fail("invalid-local-request")
    try:
        value = adapters.decode_event_graph_json(raw)
        value = adapters._object(value, REQUEST_FIELDS, "/")
        schema_version = adapters._string(
            value["schema_version"], "/schema_version", 1, 128)
        transaction_id = adapters._identifier(
            value["transaction_id"], "/transaction_id")
        expected_generation_id = adapters._identifier(
            value["expected_generation_id"], "/expected_generation_id")
        session_path = authorization._exact(
            value["session_path"], "invalid-selector")
        project_id = adapters._identifier(value["project_id"], "/project_id")
        prefix_length = adapters._integer(
            value["prefix_length"], 1, adapters.MAX_GRAPH_BYTES,
            "/prefix_length")
        prefix_digest = adapters._snapshot(
            value["prefix_digest"], "/prefix_digest")
        derived_processing_until = adapters._integer(
            value["derived_processing_until"], 0, 9223372036854775807,
            "/derived_processing_until")
    except (TypeError, ValueError, RecursionError):
        _fail("invalid-local-request")
    if schema_version != REQUEST_SCHEMA:
        _fail("invalid-local-request")
    return LocalCodexRequest(
        schema_version, transaction_id, expected_generation_id, session_path,
        project_id, prefix_length, prefix_digest, derived_processing_until)
```

Keep imports needed by Task 4 in place so the next task adds orchestration without moving responsibilities.

- [ ] **Step 4: Run decoder tests green**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_local_runtime.LocalRuntimeTest.test_request_decoder_accepts_only_the_closed_contract -v`

Expected: PASS. Diagnostics contain only `invalid-local-request`, never a decoded field or path.

- [ ] **Step 5: Commit the decoder**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/local_runtime.py tests/test_local_runtime.py
git commit -m "feat: decode standalone Codex ingestion requests"
```

### Task 4: Derive local authorization and run the shared ingestion transaction

**Files:**

- Modify: `knowledge-distiller/scripts/knowledge_distiller/local_runtime.py`
- Modify: `tests/test_local_runtime.py`

- [ ] **Step 1: Write failing identity, authorization, and key tests**

Extend imports in `tests/test_local_runtime.py` with:

```python
from dataclasses import asdict

from knowledge_distiller import authorization, brokers, ingestion, native_adapters, source_io
from knowledge_distiller.journal import canonical_json
from knowledge_distiller.persistence import TaskCoordinator, create_task, inspect_task
from knowledge_distiller.state import Event, Phase, TransitionFacts
```

Add these methods to `LocalRuntimeTest`:

```python
def test_materialized_request_binds_exact_local_identity_scope_and_tuple(self):
    request = local_runtime.decode_local_codex_request(encoded())
    materialized = local_runtime.materialize_local_codex_request(
        Path("task-root"), request, selector_key=b"k" * 32,
        effective_uid=501, now=1000)
    core = materialized.request
    identity = materialized.identity

    self.assertEqual(core.source_kind, "codex")
    self.assertEqual(core.transaction_id, request.transaction_id)
    self.assertEqual(core.expected_generation_id, request.expected_generation_id)
    self.assertEqual(core.native_request, brokers.SessionRequest(
        request.session_path, request.project_id,
        request.prefix_length, request.prefix_digest))
    self.assertEqual(core.native_locator_id, request.project_id)
    self.assertEqual(identity.expected_owner_uid, 501)
    self.assertEqual(identity.selector_key, b"k" * 32)
    self.assertNotIn((b"k" * 32).decode("ascii"), repr(identity))
    self.assertEqual(identity.product_version,
                     native_adapters.CODEX_ROLLOUT_ADAPTER.product_version)
    self.assertEqual(identity.native_schema_digest,
                     native_adapters.CODEX_NATIVE_SCHEMA_DIGEST)
    self.assertEqual(identity.active_principal,
                     core.authorization_context.active_principal)
    self.assertEqual(identity.content_owner,
                     core.authorization_context.content_owner)
    self.assertNotIn("501", identity.active_principal)
    self.assertNotIn("task-root", core.authorization_context.task_id)
    self.assertEqual(
        core.authorization_context.selector,
        brokers.session_selector_commitment(
            request.session_path, b"k" * 32))
    self.assertNotIn(request.session_path,
                     json.dumps(core.content_grant, sort_keys=True))
    self.assertNotIn(request.session_path,
                     json.dumps(core.authority_attestation, sort_keys=True))

    grant = authorization.validate_content_grant(
        core.content_grant, context=core.authorization_context)
    attestation = authorization.validate_authority_attestation(
        core.authority_attestation, context=core.authorization_context)
    self.assertEqual(grant.issuer, identity.active_principal)
    self.assertEqual(attestation.issuer, local_runtime.LOCAL_OWNER_VERIFIER)
    self.assertEqual(attestation.authority_basis, "verified-ownership")
    self.assertEqual(grant.expires_at, 1300)
    self.assertEqual(grant.derived_processing_until, 2000)
    self.assertEqual(core.authorization_context.authenticated_issuers,
                     (identity.active_principal,
                      local_runtime.LOCAL_OWNER_VERIFIER))

def test_runtime_rejects_identity_deadline_and_key_before_source_io(self):
    for uid, now, deadline, code in (
            (True, 1000, 2000, "local-identity-unavailable"),
            (-1, 1000, 2000, "local-identity-unavailable"),
            (501, True, 2000, "local-identity-unavailable"),
            (501, 1000, 1000, "invalid-derived-deadline"),
            (501, 1000, 1000 + local_runtime.MAX_DERIVED_SECONDS + 1,
             "invalid-derived-deadline")):
        with self.assertRaises(local_runtime.LocalRuntimeError) as caught, \
                mock.patch.object(source_io, "read_source") as reader:
            with mock.patch.object(local_runtime.os, "geteuid", return_value=uid), \
                    mock.patch.object(local_runtime.time, "time", return_value=now):
                local_runtime.ingest_codex_session(
                    Path("task"), encoded(
                        derived_processing_until=deadline), "key")
        self.assertEqual(caught.exception.code, code)
        reader.assert_not_called()

    with mock.patch.object(
            source_io, "read_source",
            side_effect=source_io.SourceIOError("PRIVATE KEY PATH")) as reader:
        with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
            with mock.patch.object(local_runtime.os, "geteuid", return_value=501), \
                    mock.patch.object(local_runtime.time, "time", return_value=1000):
                local_runtime.ingest_codex_session(
                    Path("task"), encoded(), "PRIVATE-KEY")
    self.assertEqual(caught.exception.code, "unsafe-redaction-key")
    reader.assert_called_once_with(
        "PRIVATE-KEY", max_bytes=64,
        expected_owner_uid=501, owner_only=True)
    self.assertNotIn("PRIVATE", str(caught.exception))

def test_redaction_key_is_raw_owner_only_32_to_64_bytes(self):
    for raw in (b"k" * 31, b"k" * 65, b"k" * 31 + b"\n"):
        with mock.patch.object(source_io, "read_source", return_value=raw), \
                mock.patch.object(ingestion, "ingest_source") as ingest:
            if len(raw) == 32:
                with mock.patch.object(local_runtime.os, "geteuid", return_value=501), \
                        mock.patch.object(local_runtime.time, "time", return_value=1000):
                    local_runtime.ingest_codex_session(
                        Path("task"), encoded(), "key")
                self.assertEqual(ingest.call_args.kwargs["redaction_key"], raw)
            else:
                with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
                    with mock.patch.object(local_runtime.os, "geteuid", return_value=501), \
                            mock.patch.object(local_runtime.time, "time", return_value=1000):
                        local_runtime.ingest_codex_session(
                            Path("task"), encoded(), "key")
                self.assertEqual(caught.exception.code, "unsafe-redaction-key")
                ingest.assert_not_called()

def test_unexpected_runtime_failure_collapses_without_private_detail(self):
    with mock.patch.object(
            local_runtime, "_ingest_codex_session",
            side_effect=ValueError("PRIVATE INTERNAL DETAIL")):
        with self.assertRaises(local_runtime.LocalRuntimeError) as caught:
            local_runtime.ingest_codex_session(
                Path("task"), encoded(), "key")
    self.assertEqual(caught.exception.code, "local-runtime-failed")
    self.assertNotIn("PRIVATE", str(caught.exception))
```

The newline case intentionally proves there is no trimming: 31 key bytes plus one newline is accepted as a distinct 32-byte key.

- [ ] **Step 2: Run the new runtime tests red**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_local_runtime -v`

Expected: FAIL because materialization and orchestration are absent.

- [ ] **Step 3: Implement opaque derivation and authorization materialization**

Extend the imports in `local_runtime.py` before appending the types and helpers:

```python
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import time

from . import adapters, authorization, brokers, ingestion, native_adapters, source_io
from .journal import canonical_json
```

Then append:

```python
@dataclass(frozen=True, repr=False)
class MaterializedCodexRequest:
    request: ingestion.IngestionRequest
    identity: brokers.SessionIdentity


def _digest(domain: str, raw: bytes) -> str:
    framed = domain.encode("ascii") + b"\x00" + raw
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _decision_digest(value: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _runtime_values(effective_uid, now, derived_processing_until):
    if (type(effective_uid) is not int or effective_uid < 0
            or effective_uid > 9223372036854775807
            or type(now) is not int or now < 0
            or now > 9223372036854775807):
        _fail("local-identity-unavailable")
    if (derived_processing_until <= now
            or derived_processing_until > now + MAX_DERIVED_SECONDS):
        _fail("invalid-derived-deadline")


def materialize_local_codex_request(
        task_root: Path, request: LocalCodexRequest, *,
        selector_key: bytes, effective_uid: int,
        now: int) -> MaterializedCodexRequest:
    if type(request) is not LocalCodexRequest:
        _fail("invalid-local-request")
    _runtime_values(effective_uid, now, request.derived_processing_until)
    try:
        task_path = os.path.abspath(os.fspath(task_root)).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OSError):
        _fail("local-identity-unavailable")
    uid_bytes = str(effective_uid).encode("ascii")
    generation = request.expected_generation_id.encode("utf-8")
    task_id = _digest("local-task", task_path + b"\x00" + generation)
    active_principal = _digest("local-principal", uid_bytes)
    tenant_account = _digest(
        "local-tenant", uid_bytes + b"\x00" + task_id.encode("ascii"))
    bounds = authorization.SessionRange(0, request.prefix_length - 1)
    context = authorization.AuthorizationContext(
        task_id=task_id,
        active_principal=active_principal,
        tenant_account=tenant_account,
        selector=brokers.session_selector_commitment(
            request.session_path, selector_key),
        purpose=PURPOSE,
        revision=None,
        session_range=bounds,
        now=now,
        task_active=True,
        authenticated_issuers=(active_principal, LOCAL_OWNER_VERIFIER),
        content_owner=active_principal,
    )
    common = {
        "task_id": task_id,
        "active_principal": active_principal,
        "tenant_account": tenant_account,
        "selector": context.selector,
        "purpose": PURPOSE,
        "revision": None,
        "session_range": {"start": 0, "end": request.prefix_length - 1},
        "issued_at": now,
        "expires_at": now + READ_WINDOW_SECONDS,
        "derived_processing_until": request.derived_processing_until,
        "revoked": False,
    }
    grant = dict(
        common,
        record_type="content-grant",
        record_id=_digest(
            "local-content-grant-id",
            canonical_json({
                "transaction_id": request.transaction_id,
                "task_id": task_id,
                "prefix_digest": request.prefix_digest,
            })),
        issuer=active_principal,
        operation="read-content",
    )
    grant["decision_digest"] = _decision_digest(grant)
    attestation = dict(
        common,
        record_type="authority-attestation",
        record_id=_digest(
            "local-authority-attestation-id",
            canonical_json({
                "transaction_id": request.transaction_id,
                "task_id": task_id,
                "prefix_digest": request.prefix_digest,
            })),
        issuer=LOCAL_OWNER_VERIFIER,
        operation="process-third-party",
        content_owner=active_principal,
        authority_basis="verified-ownership",
    )
    attestation["decision_digest"] = _decision_digest(attestation)
    native_request = brokers.SessionRequest(
        request.session_path, request.project_id,
        request.prefix_length, request.prefix_digest)
    identity = brokers.SessionIdentity(
        active_principal,
        tenant_account,
        request.project_id,
        active_principal,
        native_adapters.CODEX_ROLLOUT_ADAPTER.product_version,
        native_adapters.CODEX_NATIVE_SCHEMA_DIGEST,
        effective_uid,
        selector_key,
    )
    core_request = ingestion.IngestionRequest(
        "codex", request.transaction_id, request.expected_generation_id,
        context, grant, attestation, native_request, request.project_id)
    return MaterializedCodexRequest(core_request, identity)
```

The record decision digest intentionally uses the exact canonical JSON encoding already accepted by `authorization.py`; record IDs additionally domain-separate content grant and authority attestation.

- [ ] **Step 4: Implement secret read and shared transaction delegation**

Append these functions to `local_runtime.py`:

```python
def _read_redaction_key(path: str, effective_uid: int) -> bytes:
    try:
        raw = source_io.read_source(
            path, max_bytes=64,
            expected_owner_uid=effective_uid,
            owner_only=True)
    except source_io.SourceIOError:
        _fail("unsafe-redaction-key")
    if not 32 <= len(raw) <= 64:
        _fail("unsafe-redaction-key")
    return raw


def _ingest_codex_session(
        task_root: Path, raw_request: bytes,
        redaction_key_file: str) -> ingestion.IngestionResult:
    decoded = decode_local_codex_request(raw_request)
    try:
        uid = os.geteuid()
        now_value = time.time()
        if type(now_value) not in (int, float):
            _fail("local-identity-unavailable")
        now = int(now_value)
    except LocalRuntimeError:
        raise
    except Exception:
        _fail("local-identity-unavailable")
    _runtime_values(uid, now, decoded.derived_processing_until)
    key = _read_redaction_key(redaction_key_file, uid)
    materialized = materialize_local_codex_request(
        Path(task_root), decoded, selector_key=key,
        effective_uid=uid, now=now)

    def acquire_codex(native_request, grant, attestation, context):
        return brokers.read_local_session(
            native_request, grant=grant, attestation=attestation,
            context=context, identity=materialized.identity)

    def reject_lark(*unused_arguments):
        raise brokers.BrokerError("invalid-broker-request")

    return ingestion.ingest_source(
        Path(task_root), materialized.request,
        acquire=ingestion.AcquisitionDispatch(
            lark=reject_lark, codex=acquire_codex),
        redaction_key=key)


def ingest_codex_session(
        task_root: Path, raw_request: bytes,
        redaction_key_file: str) -> ingestion.IngestionResult:
    try:
        return _ingest_codex_session(
            task_root, raw_request, redaction_key_file)
    except (LocalRuntimeError, ingestion.IngestionError):
        raise
    except Exception:
        _fail("local-runtime-failed")
```

Do not retain the key on a dataclass or return it. The outer wrapper passes `ingestion.IngestionError` through unchanged so it remains an ingestion rejection, and collapses every other unexpected exception to the code-only `local-runtime-failed` input error.

- [ ] **Step 5: Add the synthetic vertical runtime test**

Add this helper and test to `LocalRuntimeTest`:

```python
def ingest_task(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    task = Path(temporary.name) / "task"
    create_task(task)
    with TaskCoordinator(task) as coordinator:
        coordinator.transition(
            Event.START_DISTILL,
            TransitionFacts(selected_capability=True))
        ingest_state = coordinator.transition(
            Event.CONTENT_GRANTED,
            TransitionFacts(content_grant=True, authority_valid=True))
    return Path(temporary.name), task, ingest_state

def test_standalone_runtime_ingests_only_the_synthetic_closed_prefix(self):
    directory, task, ingest_state = self.ingest_task()
    source = directory / "synthetic-session.jsonl"
    fixture = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
               "rollout-jsonl-v1/redacted-current.jsonl")
    raw = fixture.read_bytes()
    source.write_bytes(raw + b"LATER-BYTES-NOT-IN-PREFIX")
    key = directory / "redaction.key"
    key.write_bytes(b"k" * 32)
    os.chmod(key, 0o600)
    request = local_runtime.LocalCodexRequest(
        local_runtime.REQUEST_SCHEMA,
        "ingest-local-codex",
        ingest_state.generation_id,
        str(source),
        "project-1",
        len(raw),
        digest(raw),
        2000,
    )
    with mock.patch("os.listdir", side_effect=AssertionError("discovery")), \
            mock.patch("os.scandir", side_effect=AssertionError("discovery")), \
            mock.patch("pwd.getpwuid", create=True,
                       side_effect=AssertionError("username lookup")), \
            mock.patch("subprocess.Popen", side_effect=AssertionError("process")):
        with mock.patch.object(local_runtime.time, "time", return_value=1000):
            result = local_runtime.ingest_codex_session(
                task, canonical_json(asdict(request)), str(key))
    self.assertEqual((result.status, result.source_kind),
                     ("snapshotted", "session"))
    self.assertEqual(result.source_byte_count, len(raw))
    self.assertEqual(inspect_task(task).generation_id, result.generation_id)
    generation = task / "generations" / result.generation_id
    persisted = b"".join(
        path.read_bytes() for path in generation.rglob("*") if path.is_file())
    for secret in (str(source).encode(), str(key).encode(), b"k" * 32,
                   b"LATER-BYTES-NOT-IN-PREFIX"):
        self.assertNotIn(secret, persisted)
    self.assertEqual(inspect_task(task).state.phase, Phase.INGEST)
```

Import `pwd` and `subprocess` at the top of the test file. This test uses only the checked-in synthetic fixture copied to a temporary file.

- [ ] **Step 6: Run runtime and shared-core tests green**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest tests.test_local_runtime tests.test_source_io tests.test_brokers tests.test_ingestion -v`

Expected: PASS with no directory enumeration, username lookup, process execution, network access, or real Codex session read.

- [ ] **Step 7: Commit the runtime**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/local_runtime.py tests/test_local_runtime.py
git commit -m "feat: add standalone local Codex runtime"
```

### Task 5: Expose the standalone command and prove the real CLI boundary

**Files:**

- Modify: `knowledge-distiller/scripts/kd.py` (imports, parser, `_run`, `main`)
- Modify: `tests/test_cli.py` (`CliTest`)

- [ ] **Step 1: Write failing parser and delegation tests**

Import `local_runtime` in `tests/test_cli.py`, then add:

```python
def test_ingest_codex_session_requires_all_arguments_before_request_read(self):
    invalid_commands = (
        ("ingest-codex-session",),
        ("ingest-codex-session", "task", "request.json"),
        ("ingest-codex-session", "task", "request.json",
         "--redaction-key-file"),
    )
    for command in invalid_commands:
        with self.subTest(command=command), \
                mock.patch.object(source_io, "read_source") as reader, \
                mock.patch.object(kd_cli.sys, "stderr", io.StringIO()):
            self.assertEqual(kd_cli.main(command), 2)
            reader.assert_not_called()

def test_ingest_codex_session_delegates_and_emits_bounded_result(self):
    result = ingestion.IngestionResult(
        "snapshotted", "session", "sha256:" + "a" * 64,
        "sha256:" + "b" * 64, 100, 2, "g-next", "c" * 64)
    with mock.patch.object(
            source_io, "read_source", return_value=b"private-request") as read, \
            mock.patch.object(
                local_runtime, "ingest_codex_session",
                return_value=result) as ingest:
        payload = kd_cli._run((
            "ingest-codex-session", "task-root", "request.json",
            "--redaction-key-file", "redaction.key"))
    read.assert_called_once_with(
        "request.json", max_bytes=local_runtime.MAX_LOCAL_REQUEST_BYTES)
    ingest.assert_called_once_with(
        Path("task-root"), b"private-request", "redaction.key")
    self.assertEqual(payload, {"ok": True, "ingestion": {
        "status": "snapshotted",
        "source_kind": "session",
        "source_snapshot_id": "sha256:" + "a" * 64,
        "canonical_digest": "sha256:" + "b" * 64,
        "source_byte_count": 100,
        "source_item_count": 2,
        "generation_id": "g-next",
        "manifest_digest": "c" * 64,
    }})
```

- [ ] **Step 2: Run the CLI tests red**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_cli.CliTest.test_ingest_codex_session_requires_all_arguments_before_request_read tests.test_cli.CliTest.test_ingest_codex_session_delegates_and_emits_bounded_result -v`

Expected: FAIL because the parser does not know `ingest-codex-session`.

- [ ] **Step 3: Implement parser, delegation, and input-error mapping**

Change the package import in `kd.py` to:

```python
from knowledge_distiller import compiler, ingestion, knowledge, local_runtime, source_io
```

After the `ingest-source` parser definition, add:

```python
    ingest_codex = commands.add_parser("ingest-codex-session")
    ingest_codex.add_argument("task_path")
    ingest_codex.add_argument("request_path")
    ingest_codex.add_argument("--redaction-key-file", required=True)
```

Add a request reader dedicated only to the new bounded constant:

```python
def _read_local_codex_request(path: str) -> bytes:
    try:
        return source_io.read_source(
            path, max_bytes=local_runtime.MAX_LOCAL_REQUEST_BYTES)
    except source_io.SourceIOError as error:
        raise CliInputError(error.code) from None
```

After the existing `ingest-source` branch in `_run`, add:

```python
    if options.command == "ingest-codex-session":
        raw = _read_local_codex_request(options.request_path)
        result = local_runtime.ingest_codex_session(
            Path(options.task_path), raw, options.redaction_key_file)
        return {"ok": True, "ingestion": asdict(result)}
```

Add this handler immediately after `except CliInputError` in `main`:

```python
    except local_runtime.LocalRuntimeError as error:
        _emit(
            {"ok": False, "error": {
                "code": "invalid-input", "reason": error.code}},
            sys.stderr,
        )
        return 2
```

Leave the existing `ingestion.IngestionError` handler unchanged so source-owner, adapter, task, and transaction failures remain exit code 3.

- [ ] **Step 4: Add code-only CLI error tests**

Add this test:

```python
def test_ingest_codex_session_errors_never_echo_private_arguments(self):
    secret = "PRIVATE-SESSION-PATH"
    with mock.patch.object(
            source_io, "read_source", return_value=secret.encode()), \
            mock.patch.object(
                local_runtime, "ingest_codex_session",
                side_effect=local_runtime.LocalRuntimeError(
                    "unsafe-redaction-key")), \
            mock.patch.object(kd_cli.sys, "stderr", io.StringIO()) as stderr:
        code = kd_cli.main((
            "ingest-codex-session", "task", secret + ".json",
            "--redaction-key-file", secret + ".key"))
    self.assertEqual(code, 2)
    self.assertEqual(json.loads(stderr.getvalue())["error"], {
        "code": "invalid-input", "reason": "unsafe-redaction-key"})
    self.assertNotIn(secret, stderr.getvalue())

    with mock.patch.object(
            source_io, "read_source", return_value=b"request"), \
            mock.patch.object(
                local_runtime, "ingest_codex_session",
                side_effect=ingestion.IngestionError(
                    "generation-lineage-mismatch")), \
            mock.patch.object(kd_cli.sys, "stderr", io.StringIO()) as stderr:
        code = kd_cli.main((
            "ingest-codex-session", "task", "request.json",
            "--redaction-key-file", "key"))
    self.assertEqual(code, 3)
    self.assertEqual(json.loads(stderr.getvalue())["error"], {
        "code": "source-ingestion-rejected",
        "reason": "generation-lineage-mismatch"})
```

- [ ] **Step 5: Add one subprocess acceptance test**

Reuse the task setup pattern from `tests/test_local_runtime.py`. Write the private request with `canonical_json`, set the key mode to `0600`, run `self.run_cli`, and assert only bounded fields are emitted:

```python
def test_standalone_codex_subprocess_reaches_atomic_ingestion(self):
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        task = directory / "task"
        create_task(task)
        with TaskCoordinator(task) as coordinator:
            coordinator.transition(
                Event.START_DISTILL,
                TransitionFacts(selected_capability=True))
            ingest_state = coordinator.transition(
                Event.CONTENT_GRANTED,
                TransitionFacts(content_grant=True, authority_valid=True))
        fixture = (ROOT / "tests/fixtures/adapters/codex/0.153.0/"
                   "rollout-jsonl-v1/redacted-current.jsonl")
        raw = fixture.read_bytes()
        session = directory / "session.jsonl"
        session.write_bytes(raw)
        key = directory / "redaction.key"
        key.write_bytes(b"k" * 32)
        os.chmod(key, 0o600)
        request = directory / "request.json"
        request.write_bytes(canonical_json({
            "schema_version": local_runtime.REQUEST_SCHEMA,
            "transaction_id": "standalone-codex",
            "expected_generation_id": ingest_state.generation_id,
            "session_path": str(session),
            "project_id": "project-1",
            "prefix_length": len(raw),
            "prefix_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "derived_processing_until": int(time.time()) + 3600,
        }))
        completed = self.run_cli(
            "ingest-codex-session", str(task), str(request),
            "--redaction-key-file", str(key))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["ingestion"]["status"], "snapshotted")
        self.assertEqual(payload["ingestion"]["source_kind"], "session")
        self.assertEqual(
            set(payload["ingestion"]), {
                "status", "source_kind", "source_snapshot_id",
                "canonical_digest", "source_byte_count",
                "source_item_count", "generation_id", "manifest_digest",
            })
        for private in (str(session), str(request), str(key), "project-1",
                        (b"k" * 32).decode()):
            self.assertNotIn(private, completed.stdout + completed.stderr)
        self.assertEqual(
            inspect_task(task).generation_id,
            payload["ingestion"]["generation_id"])
```

Add the explicit imports used by the test: `hashlib`, `os`, `time`, `TaskCoordinator`, `create_task`, `inspect_task`, `Event`, `TransitionFacts`, and `canonical_json`.

- [ ] **Step 6: Run CLI and runtime tests green**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest tests.test_cli tests.test_local_runtime -v`

Expected: PASS. Existing standalone `ingest-source` still rejects before reading its request when no injected runtime is supplied.

- [ ] **Step 7: Commit the command**

```bash
git add knowledge-distiller/scripts/kd.py tests/test_cli.py
git commit -m "feat: expose standalone Codex session ingestion"
```

### Task 6: Update skill guidance, verify, simplify, and independently review

**Files:**

- Modify: `knowledge-distiller/SKILL.md`
- Modify: `knowledge-distiller/references/workflow.md`
- Modify: `knowledge-distiller/references/authorization.md`
- Modify: `README.md`
- Modify: `tests/test_skill_contract.py`
- Modify: `docs/specs/2026-09-09-local-codex-runtime-design.md`
- Modify: `docs/status/2026-09-05-implementation-status.md`
- Modify: `docs/plans/2026-09-09-local-codex-runtime.md`

- [ ] **Step 1: Write failing guidance-contract tests**

Add contract assertions that require these exact concepts in the rendered skill guidance:

```python
def test_standalone_codex_guidance_is_exact_and_does_not_expand_scope(self):
    combined = "\n".join((
        (ROOT / "knowledge-distiller/SKILL.md").read_text(encoding="utf-8"),
        (ROOT / "knowledge-distiller/references/workflow.md").read_text(
            encoding="utf-8"),
        (ROOT / "knowledge-distiller/references/authorization.md").read_text(
            encoding="utf-8"),
        (ROOT / "README.md").read_text(encoding="utf-8"),
    ))
    required = (
        "ingest-codex-session",
        "--redaction-key-file",
        "knowledge-distiller.local-codex-ingestion-request/v1",
        "one explicit session file",
        "exact byte-0 prefix",
        "effective UID",
        "0600",
        "does not authorize a real session read",
        "ingest-source",
        "ingestion-runtime-unavailable",
    )
    for phrase in required:
        self.assertIn(phrase, combined)
    prohibited_claims = (
        "automatically discovers Codex sessions",
        "supports every Codex version",
        "creates the redaction key",
        "installs the distilled skill",
        "publishes the distilled skill",
    )
    for phrase in prohibited_claims:
        self.assertNotIn(phrase, combined)
```

Place the method in the existing `SkillContractTest` class and use its current `ROOT` constant.

- [ ] **Step 2: Run the guidance contract red**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL because the current guidance still says all standalone ingestion is unavailable.

- [ ] **Step 3: Update progressive-disclosure guidance precisely**

Document this exact supported command in `README.md` and `references/workflow.md`:

```bash
python3 knowledge-distiller/scripts/kd.py ingest-codex-session \
  /absolute/path/to/task-workspace \
  /absolute/path/to/private-codex-request.json \
  --redaction-key-file /absolute/path/to/redaction.key
```

State all of the following in `SKILL.md`, `workflow.md`, and `authorization.md` where their existing routing text belongs:

- the request selects one explicit session file and exact byte-0 prefix;
- explicit invocation is the read decision for that selector only;
- the runtime derives identity from the effective UID and requires the opened source to have the same UID;
- the key is exactly a stable single-link regular `0600` file containing 32–64 raw bytes;
- callers never supply UID, owner, issuer, grant, attestation, adapter version, or native schema;
- only `codex / 1.0.0 / 0.153.0 / rollout-jsonl-v1` is supported;
- `ingest-source` remains an embedded injection boundary and still returns `ingestion-runtime-unavailable` without a host runtime;
- synthetic acceptance does not authorize any real Codex session read;
- the filesystem selector is persisted only as a domain-separated commitment; the opaque `project_id` remains a required private provenance binding but is never printed by the command;
- discovery, sibling reads, arbitrary versions, Lark standalone access, automatic extraction, evaluation, export, installation, and publication remain unavailable.

Do not document a key-generation command because key lifecycle is outside this milestone.

- [ ] **Step 4: Run focused and full verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest tests.test_local_runtime tests.test_source_io tests.test_brokers tests.test_ingestion tests.test_cli tests.test_skill_contract -v
```

Expected: PASS using temporary or checked-in synthetic files only.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s tests -v
```

Expected: every test passes; no command reads `~/.codex`, invokes Lark, opens a network connection, or touches a real session.

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/distil-yourself-pyc-local-runtime python3 -m compileall -q knowledge-distiller/scripts
git diff --check
find . -type d -name __pycache__ -print
```

Expected: `compileall` and `git diff --check` exit 0; `find` prints nothing.

- [ ] **Step 5: Run the explicit simplification and redundancy review**

Answer this review question independently: `是否有可以精简的代码，是否存在冗余、重复或无用的代码/测试/文档？`

Inspect these concrete risks:

- a second path walker, pre-open `stat`, or duplicate stable-read implementation;
- custom JSON parsing that duplicates `adapters.decode_event_graph_json`;
- a runtime wrapper type or helper used only to rename one call without enforcing a boundary;
- duplicate digest encoders that disagree with `canonical_json`;
- separate local broker logic outside `brokers.read_local_session`;
- repeated CLI output construction instead of `asdict(IngestionResult)`;
- caller-controlled identity/version/schema fields accidentally retained in tests or docs;
- dead Lark callbacks, environment probes, key storage, discovery, export, or installation branches.

Remove only demonstrable redundancy. Preserve repeated validation at independent trust boundaries when it prevents caller substitution or time-of-check/time-of-use gaps. Every behavioral cleanup starts with or retains a regression test.

- [ ] **Step 6: Run independent specification and quality/security reviews**

Ask two independent reviewers to inspect the complete diff against `docs/specs/2026-09-09-local-codex-runtime-design.md`:

- specification reviewer: return `SPEC PASS` or findings grouped as Critical, Important, and Minor, checking every milestone exclusion and synthetic-only acceptance condition;
- quality/security reviewer: return `READY` or findings grouped as Critical, Important, Minor, and Simplification, explicitly answering the redundancy question and checking UID/descriptor/key/privacy/atomicity/error boundaries.

For every accepted Critical or Important finding, first add a failing regression test, implement the smallest correction, rerun the focused suite, then rerun the full suite. Do not mark the milestone complete while either reviewer has an unresolved Critical or Important finding.

- [ ] **Step 7: Record verified results and commit locally**

Change the design status to `implemented` only after Step 6 passes. Append to `docs/status/2026-09-05-implementation-status.md`:

- the exact standalone command and request schema;
- the effective-UID and `0600` key boundaries;
- focused and full test counts from actual output;
- `compileall`, `git diff --check`, and no-`__pycache__` results;
- `SPEC PASS` and `READY` reviewer verdicts plus all resolved findings;
- the explicit simplification/redundancy verdict;
- confirmation that acceptance used only synthetic files;
- remaining work: Lark standalone runtime, automatic extraction/mapping, evaluation, export, installation, and publication;
- confirmation that no push, merge, install, export, publish, real session read, or wider environment change occurred.

Mark completed checkboxes in this plan and commit the completion record:

```bash
git add knowledge-distiller/SKILL.md knowledge-distiller/references/workflow.md knowledge-distiller/references/authorization.md README.md tests/test_skill_contract.py docs/specs/2026-09-09-local-codex-runtime-design.md docs/status/2026-09-05-implementation-status.md docs/plans/2026-09-09-local-codex-runtime.md
git commit -m "docs: complete standalone Codex runtime milestone"
```

Do not push, merge, install, export, publish, read real Codex sessions, invoke Lark, or make a broader environment change without a new explicit user approval.

## Acceptance checklist

- [ ] `ingest-codex-session` works in a real subprocess over a temporary copy of the checked-in synthetic fixture, without Python runtime injection.
- [ ] The request decoder is closed, duplicate-key-safe, bounded, and excludes every caller identity/authority/version field.
- [ ] Principal, tenant, task, record, and decision identifiers are domain-separated opaque SHA-256 values.
- [ ] Grant and attestation independently revalidate immediately before session I/O.
- [ ] Source UID is checked on the same descriptor used for the two pinned-prefix reads.
- [ ] The key is read from one stable owner-matching single-link regular descriptor with exact `0600` mode and 32–64 untrimmed bytes.
- [ ] Existing injected `ingest-source` behavior is unchanged.
- [ ] Failures leave the previous task generation authoritative and emit only allowlisted codes.
- [ ] CLI output, stderr, journal, and telemetry contain no request/source/key paths, raw key, raw session text, UID, or project ID; private generations contain no filesystem/key path, raw key, unredacted session text, or UID, while retaining only the required opaque project binding and path commitment.
- [ ] Tests prove no discovery, sibling read, username lookup, Lark call, shell/process launch, network access, key management, extraction, evaluation, export, installation, or publication.
- [ ] Focused suite, strict full suite, static compilation, whitespace check, cache check, specification review, quality/security review, and simplification review all pass.
