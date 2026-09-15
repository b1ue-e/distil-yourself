# Local Lark Standalone Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-fail-closed standalone CLI path that can ingest one owner-verified, observationally stable Docx raw-content response through the existing Knowledge Distiller transaction using synthetic Lark subprocesses, while keeping all real Lark reads disabled.

**Architecture:** A closed request decoder and canonical selector parser feed a pinned synthetic-only live profile. A narrow `lark-cli` transport validates identity and owner/revision control responses, while the existing broker retains ownership of the opaque raw-content envelope; the runtime derives short-lived authorization records and delegates persistence to `ingestion.ingest_source`. Production rejects before any Lark subprocess until a later reviewed profile activation, while tests replace only the private gate and execute a real temporary fake `lark-cli` process through the normal transport.

**Tech Stack:** Python 3.9+ standard library (`argparse`, `dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `selectors`, `signal`, `stat`, `subprocess`, `time`, `urllib.parse`, `unittest`), existing Knowledge Distiller authorization/broker/adapter/redaction/persistence modules, synthetic Lark `1.0.86 / docx-v1-raw-content-v1` fixtures

---

## File map and fixed interfaces

- Create `knowledge-distiller/scripts/knowledge_distiller/runtime_support.py`: shared effective-UID, runtime-clock, and owner-only redaction-key boundary extracted without changing Codex behavior.
- Modify `knowledge-distiller/scripts/knowledge_distiller/local_runtime.py`: delegate only those three shared operations and preserve its public constants/errors.
- Modify `knowledge-distiller/scripts/knowledge_distiller/ingestion.py`: extract one reusable task-slot predicate and expose a read-only preflight that is always rechecked by the atomic transaction.
- Create `tests/test_runtime_support.py` and modify `tests/test_local_runtime.py`, `tests/test_ingestion.py`: prove the extraction and preflight preserve behavior and reject before acquisition.
- Create `knowledge-distiller/scripts/knowledge_distiller/lark_selector.py`: own exact token/URL normalization and selector commitment.
- Create `knowledge-distiller/scripts/knowledge_distiller/lark_profile.py`: own the canonical synthetic-only compatibility record, closed control-schema descriptors, scope alternatives, limits, and private live gate.
- Create `knowledge-distiller/scripts/knowledge_distiller/lark_cli_transport.py`: own fixed executable resolution, fingerprinting, bounded subprocess execution, control-response parsing, and exact Lark operations.
- Create `knowledge-distiller/scripts/knowledge_distiller/lark_runtime.py`: own closed request decoding, identity/owner materialization, authorization derivation, stable-read orchestration, and ingestion delegation.
- Create `tests/test_lark_selector.py`, `tests/test_lark_cli_transport.py`, and `tests/test_lark_runtime.py`: cover all new pure and runtime boundaries.
- Modify `knowledge-distiller/scripts/knowledge_distiller/brokers.py`: accept the standalone selector commitment and an empty ambient-user environment only when the trusted runner supplies the existing evidence receipt.
- Modify `knowledge-distiller/scripts/knowledge_distiller/ingestion.py`: independently validate the committed Lark selector before normalization.
- Modify `tests/test_brokers.py` and `tests/test_ingestion.py`: prove backward compatibility and substitution rejection.
- Modify `knowledge-distiller/scripts/kd.py` and `tests/test_cli.py`: add `ingest-lark-document` with mandatory `--allow-live-read`, bounded request loading, code-only errors, production-gate rejection, and synthetic command-handler acceptance.
- Add synthetic control fixtures under `tests/fixtures/lark_runtime/1.0.86/` and continue using the existing redacted raw-content fixture.
- Modify `knowledge-distiller/SKILL.md`, `knowledge-distiller/references/workflow.md`, `knowledge-distiller/references/authorization.md`, `knowledge-distiller/references/adapter-compatibility.md`, `README.md`, `tests/test_skill_contract.py`, `docs/status/2026-09-05-implementation-status.md`, the design document, and this plan after verification.

The private request remains exactly:

```json
{
  "schema_version": "knowledge-distiller.local-lark-ingestion-request/v1",
  "transaction_id": "ingest-lark-1",
  "expected_generation_id": "g-current",
  "document_selector": "doxcn1234567890AbCdEfGhIjKl",
  "derived_processing_until": 4102444800
}
```

No public request, CLI option, environment variable, or profile-data edit can provide an identity, owner, revision, grant, attestation, schema selection, executable, endpoint, credential, or production gate bypass.

### Task 1: Extract shared runtime primitives and task-slot preflight

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/runtime_support.py`
- Create: `tests/test_runtime_support.py`
- Modify: `knowledge-distiller/scripts/knowledge_distiller/local_runtime.py`
- Modify: `knowledge-distiller/scripts/knowledge_distiller/ingestion.py`
- Modify: `tests/test_local_runtime.py`
- Modify: `tests/test_ingestion.py`

- [x] **Step 1: Write failing shared-helper and preflight tests**

Add tests that require the same owner-only key policy and prove no acquisition occurs for an invalid task slot:

```python
class RuntimeSupportTest(unittest.TestCase):
    def test_reads_owner_only_redaction_key(self):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory).resolve() / "key"
            key_path.write_bytes(b"k" * 32)
            key_path.chmod(0o600)
            self.assertEqual(
                runtime_support.read_redaction_key(
                    str(key_path), os.geteuid()),
                b"k" * 32,
            )

    def test_runtime_identity_and_time_are_builtin_nonnegative_ints(self):
        self.assertEqual(runtime_support.effective_uid(), os.geteuid())
        self.assertLessEqual(runtime_support.runtime_time(), int(time.time()))

def test_preflight_rejects_stale_generation_without_acquisition(self):
    with self.assertRaises(ingestion.IngestionError) as caught:
        ingestion.preflight_ingestion_slot(
            self.root, "stale-generation", "lark")
    self.assertEqual(caught.exception.code, "generation-lineage-mismatch")
    self.assertEqual(self.calls, [])
```

Extend the existing local-runtime mocks to assert that `runtime_support` is called and that all existing `LocalRuntimeError` codes remain unchanged.

- [x] **Step 2: Run the new tests red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_runtime_support \
  tests.test_ingestion.IngestionTest.test_preflight_rejects_stale_generation_without_acquisition \
  -v
```

Expected: FAIL because `runtime_support` and `preflight_ingestion_slot` do not exist.

- [x] **Step 3: Implement the minimal shared helpers and one slot predicate**

Create the shared module with these public values and functions:

```python
READ_WINDOW_SECONDS = 300
MAX_DERIVED_SECONDS = 90 * 24 * 60 * 60

class RuntimeSupportError(ValueError):
    ALLOWED = frozenset({"local-identity-unavailable", "unsafe-redaction-key"})

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self.ALLOWED else "local-identity-unavailable"
        super().__init__(self.code)

def read_redaction_key(path, uid: int) -> bytes:
    try:
        raw = source_io.read_source(
            path, max_bytes=64, expected_owner_uid=uid, owner_only=True)
    except source_io.SourceIOError:
        raise RuntimeSupportError("unsafe-redaction-key") from None
    if type(raw) is not bytes or not 32 <= len(raw) <= 64:
        raise RuntimeSupportError("unsafe-redaction-key")
    return raw

def effective_uid() -> int:
    try:
        value = os.geteuid()
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise ValueError
        return value
    except Exception:
        raise RuntimeSupportError("local-identity-unavailable") from None

def runtime_time() -> int:
    try:
        value = time.time()
        if type(value) not in (int, float) or not 0 <= value <= 2**63 - 1:
            raise ValueError
        return int(value)
    except Exception:
        raise RuntimeSupportError("local-identity-unavailable") from None
```

In `local_runtime.py`, keep `READ_WINDOW_SECONDS` and `MAX_DERIVED_SECONDS` as aliases, replace the three implementations with calls to the shared module, and translate `RuntimeSupportError` back to the same `LocalRuntimeError(error.code)`.

In `ingestion.py`, extract the current coordinator checks without changing their order:

```python
def _require_ingestion_slot(coordinator, expected_generation_id, source_kind):
    if (coordinator.snapshot is None
            or coordinator.snapshot.generation_id != expected_generation_id):
        _fail("generation-lineage-mismatch")
    if coordinator.snapshot.state.phase is not Phase.INGEST:
        _fail("ingestion-invalid")
    existing_artifacts = coordinator._current_artifacts() or []
    existing_sources = _existing_source_kinds(existing_artifacts)
    if source_kind in existing_sources:
        _fail("source-already-ingested")
    return existing_artifacts, existing_sources

def preflight_ingestion_slot(task_root: Path, expected_generation_id: str,
                             source_kind: str) -> None:
    adapters._identifier(expected_generation_id, "/")
    if source_kind not in ("lark", "codex"):
        _fail("unsupported-source-kind")
    with TaskCoordinator(Path(task_root)) as coordinator:
        _require_ingestion_slot(coordinator, expected_generation_id, source_kind)
```

Call `_require_ingestion_slot` from the existing writer-lease block in `ingest_source`; never rely on the earlier preflight for atomicity.

- [x] **Step 4: Run shared-helper and regression suites green**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest \
  tests.test_runtime_support tests.test_local_runtime tests.test_ingestion -v
```

Expected: PASS with the existing Codex behavior and failure codes unchanged.

- [x] **Step 5: Commit the extraction**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/runtime_support.py \
  knowledge-distiller/scripts/knowledge_distiller/local_runtime.py \
  knowledge-distiller/scripts/knowledge_distiller/ingestion.py \
  tests/test_runtime_support.py tests/test_local_runtime.py tests/test_ingestion.py
git commit -m "refactor: share standalone runtime preflight"
```

### Task 2: Add the closed Lark request and canonical selector

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/lark_selector.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/lark_runtime.py`
- Create: `tests/test_lark_selector.py`
- Create: `tests/test_lark_runtime.py`

- [x] **Step 1: Write failing selector and request-decoder tests**

Use one synthetic 27-character token and prove bare-token/URL equivalence:

```python
TOKEN = "doxcn1234567890AbCdEfGhIjKl"

def test_token_and_url_normalize_to_one_private_selector(self):
    token = lark_selector.parse_document_selector(TOKEN)
    url = lark_selector.parse_document_selector(
        "https://tenant.larkoffice.com/docx/" + TOKEN)
    self.assertEqual(token, url)
    self.assertEqual(token.token, TOKEN)
    self.assertEqual(
        token.commitment,
        "sha256:" + hashlib.sha256(
            b"lark-docx-selector/v1\0" + TOKEN.encode("ascii")
        ).hexdigest(),
    )
    self.assertNotIn(TOKEN, repr(token))

def test_decoder_accepts_only_the_closed_private_request(self):
    request = lark_runtime.decode_local_lark_request(encoded_request())
    self.assertEqual(request.schema_version, lark_runtime.REQUEST_SCHEMA)
    self.assertEqual(request.document_selector, TOKEN)
    self.assertNotIn(TOKEN, repr(request))
```

Cover non-ASCII input, token lengths 26/28, `_`/`-`, uppercase scheme/host, empty or hyphen-edge labels, base domains without a tenant label, port `443`, userinfo, percent encoding, query, fragment, trailing slash, Wiki paths, duplicate JSON keys, unknown fields, booleans as timestamps, oversize input, and caller-provided identity/owner/revision/profile fields.

- [x] **Step 2: Run selector and decoder tests red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_lark_selector tests.test_lark_runtime.LarkRequestDecoderTest -v
```

Expected: FAIL because the modules do not exist.

- [x] **Step 3: Implement immutable selector parsing and strict decoding**

Create the selector record and parser:

```python
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]{27}\Z", re.ASCII)
HOST_PATTERN = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:larkoffice\.com|larksuite\.com|feishu\.cn)\Z",
    re.ASCII,
)

@dataclass(frozen=True, repr=False)
class ParsedDocumentSelector:
    token: str
    commitment: str

def selector_commitment(token: str) -> str:
    if type(token) is not str or TOKEN_PATTERN.fullmatch(token) is None:
        raise SelectorError("invalid-selector")
    return "sha256:" + hashlib.sha256(
        b"lark-docx-selector/v1\0" + token.encode("ascii")
    ).hexdigest()
```

`parse_document_selector` first accepts the exact token grammar. Otherwise it rejects `%` and non-ASCII input before `urlsplit`, then requires literal `https`, no username/password/port/query/fragment, an exact lowercase allowed host, and raw path `/docx/<token>`. It returns the same record for equivalent token and URL inputs and never performs I/O.

Create `LocalLarkRequest` with the five design fields, `REQUEST_SCHEMA`, `REQUEST_FIELDS`, and `MAX_LOCAL_REQUEST_BYTES = ingestion.MAX_REQUEST_BYTES`. Decode with `adapters.decode_event_graph_json`, exact object fields, built-in scalar checks, existing identifier validators, and `parse_document_selector`; store only the normalized token in the immutable request.

- [x] **Step 4: Run selector and decoder tests green**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest \
  tests.test_lark_selector tests.test_lark_runtime.LarkRequestDecoderTest -v
```

Expected: PASS with no network, subprocess, auth, filesystem enumeration, or source read.

- [x] **Step 5: Commit the request boundary**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/lark_selector.py \
  knowledge-distiller/scripts/knowledge_distiller/lark_runtime.py \
  tests/test_lark_selector.py tests/test_lark_runtime.py
git commit -m "feat: validate standalone Lark requests"
```

### Task 3: Pin the synthetic-only profile and control-response contracts

**Files:**

- Create: `knowledge-distiller/scripts/knowledge_distiller/lark_profile.py`
- Create: `knowledge-distiller/scripts/knowledge_distiller/lark_cli_transport.py`
- Create: `tests/test_lark_cli_transport.py`
- Create: `tests/fixtures/lark_runtime/1.0.86/auth-status.json`
- Create: `tests/fixtures/lark_runtime/1.0.86/document-info.json`
- Create: `tests/fixtures/lark_runtime/1.0.86/drive-metadata.json`
- Create: `tests/fixtures/lark_runtime/1.0.86/missing-scope.json`

- [x] **Step 1: Write failing profile and parser tests**

Require a closed synthetic profile and immutable parsed evidence:

```python
def test_checked_in_profile_is_synthetic_only_and_self_consistent(self):
    profile = lark_profile.load_pinned_profile()
    self.assertEqual(profile.status, "synthetic-only")
    self.assertEqual(profile.product_version, "1.0.86")
    self.assertEqual(profile.native_schema_digest,
                     native_adapters.LARK_NATIVE_SCHEMA_DIGEST)
    self.assertEqual(profile.timeout_seconds, 30)
    self.assertEqual(profile.raw_stdout_limit, adapters.MAX_GRAPH_BYTES)

def test_parses_verified_user_and_exact_scope_capabilities(self):
    identity = transport.parse_verified_identity(AUTH_FIXTURE)
    self.assertEqual(identity.open_id, "ou_SYNTHETIC_OWNER")
    self.assertTrue(transport.has_required_scopes(identity.scopes))

def test_observation_combines_exact_document_and_owner(self):
    observation = transport.parse_observation(
        DOCUMENT_FIXTURE, METADATA_FIXTURE, TOKEN)
    self.assertEqual(
        observation,
        transport.LarkObservation(TOKEN, "7", "ou_SYNTHETIC_OWNER"),
    )
```

Mutation tests must reject duplicate/unknown/missing fields, scalar subclasses, wrong success or identity literals, non-active auth/token literals, duplicate scopes, unknown broader scopes, revision zero/boolean/overflow, non-empty `failed_list`, multiple/no metadata rows, token/type mismatch, blank owner, malformed discarded title/timestamp/display fields, and free-form text that merely contains a scope name.

- [x] **Step 2: Run profile/parser tests red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_lark_cli_transport.LarkProfileTest \
  tests.test_lark_cli_transport.LarkControlParserTest -v
```

Expected: FAIL because the profile and transport parsers do not exist.

- [x] **Step 3: Implement the frozen profile and exact projections**

Define these immutable evidence records:

```python
@dataclass(frozen=True, repr=False)
class VerifiedUser:
    open_id: str
    scopes: Tuple[str, ...]

@dataclass(frozen=True, repr=False)
class LarkObservation:
    token: str
    revision: str
    owner_open_id: str
```

The profile pins:

```python
STATUS = "synthetic-only"
PRODUCT = "lark"
ADAPTER_VERSION = "1.0.0"
PRODUCT_VERSION = "1.0.86"
NATIVE_SCHEMA_VERSION = "docx-v1-raw-content-v1"
READ_SCOPE_ALTERNATIVES = frozenset({"docx:document:readonly", "docx:document"})
METADATA_SCOPE_ALTERNATIVES = frozenset({"drive:drive.metadata:readonly", "drive:drive"})
TIMEOUT_SECONDS = 30
CONTROL_STDOUT_LIMIT = 1024 * 1024
RAW_STDOUT_LIMIT = adapters.MAX_GRAPH_BYTES
STDERR_LIMIT = 64 * 1024
ACTIVE_STATUS = "active"
VALID_TOKEN_STATUS = "valid"
```

Represent each closed control schema as a canonical dictionary with explicit required fields, allowed optional discarded fields, scalar types, array bounds, `additional_properties=False`, and a versioned name. Compute and expose its canonical SHA-256 digest. Build one canonical profile record containing those digests, the existing raw schema digest, scope alternatives, limits, `endpoint_integrity.status="unverified"`, `consistency.mode="unaccepted"`, and `status="synthetic-only"`; expose and test one literal expected profile digest. Parsing uses the schema descriptors and never free-form error text.

`parse_verified_identity` accepts only the profile-pinned active and valid literals and produces the two retained fields. `has_required_scopes` requires one exact member from each alternative set. `parse_observation` independently parses both closed success envelopes and returns one `LarkObservation`. `parse_missing_scope` accepts only the closed typed error schema and returns a bounded tuple of missing scope names.

- [x] **Step 4: Run parser tests and fixture privacy checks green**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest \
  tests.test_lark_cli_transport.LarkProfileTest \
  tests.test_lark_cli_transport.LarkControlParserTest -v
rg -n "bytedance|VBEBwkbg|larkoffice.com/wiki|PRIVATE|real content" \
  tests/fixtures/lark_runtime knowledge-distiller/scripts/knowledge_distiller/lark_profile.py
```

Expected: all tests PASS and `rg` returns no matches.

- [x] **Step 5: Commit the schemas and synthetic fixtures**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/lark_profile.py \
  knowledge-distiller/scripts/knowledge_distiller/lark_cli_transport.py \
  tests/test_lark_cli_transport.py tests/fixtures/lark_runtime
git commit -m "feat: pin synthetic Lark control schemas"
```

### Task 4: Implement the bounded fixed-command Lark subprocess transport

**Files:**

- Modify: `knowledge-distiller/scripts/knowledge_distiller/lark_cli_transport.py`
- Modify: `tests/test_lark_cli_transport.py`

- [x] **Step 1: Write failing process-boundary tests**

Create an executable fixture in an owner-only temporary directory and assert the exact command records:

```python
EXPECTED_COMMANDS = (
    ("--version",),
    ("auth", "status", "--json", "--verify"),
    ("api", "GET", "/open-apis/docx/v1/documents/" + TOKEN, "--as", "user"),
    ("drive", "metas", "batch_query", "--user-id-type", "open_id",
     "--data", "-", "--as", "user", "--format", "json"),
    ("api", "GET", "/open-apis/docx/v1/documents/" + TOKEN + "/raw_content",
     "--as", "user"),
)

def test_transport_uses_only_fixed_commands_and_canonical_drive_stdin(self):
    client = self.transport()
    self.assertEqual(client.version(), "1.0.86")
    self.assertEqual(client.verify_user().open_id, OWNER)
    self.assertEqual(client.observe(TOKEN).revision, "7")
    self.assertEqual(client.raw_content(TOKEN), RAW_FIXTURE)
    self.assertEqual(tuple(self.command_log()), EXPECTED_COMMANDS)
    self.assertEqual(
        self.drive_stdin(),
        b'{"request_docs":[{"doc_token":"' + TOKEN.encode("ascii")
        + b'","doc_type":"docx"}],"with_url":false}',
    )
```

Add cases for missing/relative/dangling PATH entry, unrecognized wrapper layout, missing native binary, directory/world-writable native executable, version mismatch, fingerprint replacement before/after a call, inherited proxy/API-base/credential variables, shell invocation, timeout, stdout/stderr overflow, partial output, nonzero exit, cancellation, descendant survival, and descriptor/process leaks. Assert every diagnostic is an allowlisted code without fixture content, token, path, Open ID, title, argv, stdout, or stderr. Patch the wrapper fixture so its execution raises immediately, proving the transport never triggers its auto-download behavior.

- [x] **Step 2: Run transport tests red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_lark_cli_transport.LarkProcessTransportTest -v
```

Expected: FAIL because executable resolution and bounded execution are absent.

- [x] **Step 3: Implement fixed executable and streaming process control**

Create a frozen executable fingerprint containing canonical path, device, inode, UID, mode, size, and nanosecond modification time. Resolve `lark-cli` once with `shutil.which`. Accept a direct native executable, or recognize the packaged `scripts/run.js` PATH entry and derive its already-present sibling `bin/lark-cli`; never execute the Node wrapper or any install script. Reject dangling links, an unrecognized wrapper layout, a missing native target, and non-regular or group/other-writable native targets. Execute the canonical native path directly and restat it before and after every call.

Build the environment only from validated current `HOME`, `PATH`, `LANG`, `LC_ALL`, and `TMPDIR` values plus:

```python
{
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}
```

Never inherit names containing `TOKEN`, `SECRET`, `PROXY`, `BASE_URL`, or `ENDPOINT`. Start with `shell=False`, byte pipes, `close_fds=True`, and `start_new_session=True`. Use `selectors.DefaultSelector` and `time.monotonic()` to drain stdout/stderr in 64 KiB chunks while enforcing the per-stream profile limits. On timeout, overflow, or cancellation, signal the process group with `SIGTERM`, wait at most one second, escalate to `SIGKILL`, and reap the child in `finally`; discard partial bytes.

Expose only these methods:

```python
class LarkCliTransport:
    def version(self) -> str:
        raw = self._run(("--version",), stdout_limit=CONTROL_STDOUT_LIMIT)
        match = re.fullmatch(rb"lark-cli version (1\.0\.86)\n?", raw)
        if match is None:
            raise LarkTransportError("lark-cli-incompatible")
        return match.group(1).decode("ascii")

    def verify_user(self) -> VerifiedUser:
        raw = self._run(
            ("auth", "status", "--json", "--verify"),
            stdout_limit=CONTROL_STDOUT_LIMIT,
        )
        return parse_verified_identity(raw)

    def observe(self, selector: ParsedDocumentSelector) -> LarkObservation:
        document = self._run(
            ("api", "GET", "/open-apis/docx/v1/documents/" + selector.token,
             "--as", "user"),
            stdout_limit=CONTROL_STDOUT_LIMIT,
        )
        body = canonical_json({
            "request_docs": [{"doc_token": selector.token, "doc_type": "docx"}],
            "with_url": False,
        })
        metadata = self._run(
            ("drive", "metas", "batch_query", "--user-id-type", "open_id",
             "--data", "-", "--as", "user", "--format", "json"),
            stdin=body,
            stdout_limit=CONTROL_STDOUT_LIMIT,
        )
        return parse_observation(document, metadata, selector.token)

    def raw_content(self, selector: ParsedDocumentSelector) -> bytes:
        return self._run(
            ("api", "GET",
             "/open-apis/docx/v1/documents/" + selector.token + "/raw_content",
             "--as", "user"),
            stdout_limit=RAW_STDOUT_LIMIT,
        )
```

`observe` executes Docx basic information then Drive metadata once. `raw_content` returns bounded opaque bytes without JSON parsing. No retry exists.

- [x] **Step 4: Run process tests green, including cleanup**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest \
  tests.test_lark_cli_transport -v
```

Expected: PASS; timeout/overflow fixtures and their descendants are gone, all pipes are closed, and no real `lark-cli` or network operation runs.

- [x] **Step 5: Commit the transport**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/lark_cli_transport.py \
  tests/test_lark_cli_transport.py
git commit -m "feat: add bounded Lark CLI transport"
```

### Task 5: Bind the existing broker to the private Lark selector and ambient user

**Files:**

- Modify: `knowledge-distiller/scripts/knowledge_distiller/brokers.py`
- Modify: `knowledge-distiller/scripts/knowledge_distiller/ingestion.py`
- Modify: `tests/test_brokers.py`
- Modify: `tests/test_ingestion.py`

- [x] **Step 1: Write failing broker and ingestion binding tests**

Add committed-selector acceptance and substitution rejection while retaining the legacy literal-selector tests:

```python
def test_lark_selector_commitment_and_ambient_user_are_bound(self):
    committed = lark_selector.parse_document_selector(TOKEN)
    context = replace(self.context, selector=committed.commitment)
    request = brokers.LarkRequest(TOKEN, context.revision)
    self.runner.return_value = replace(
        self.response, selector_digest=committed.commitment)
    result = self.fetch_lark(
        request, context, records=self.records(context),
        required_auth_variables=())
    self.assertEqual(result.selector_digest, committed.commitment)
    self.resolver.assert_called_once_with(
        active_principal=context.active_principal,
        tenant_account=context.tenant_account,
        required_variables=(),
    )
```

Reject a different token, digest, owner, revision, user, non-empty ambient environment, boolean receipt flags, and any empty-variable mode paired with a non-user binding. Confirm the old explicit-secret/literal-selector broker path still passes unchanged. Add an ingestion test proving neither raw token nor URL appears in committed generation files.

- [x] **Step 2: Run focused broker tests red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_brokers.BrokerTest.test_lark_selector_commitment_and_ambient_user_are_bound \
  tests.test_ingestion.IngestionTest.test_lark_committed_selector_hides_token -v
```

Expected: FAIL because Lark currently requires a literal selector and at least one secret variable.

- [x] **Step 3: Implement backward-compatible commitment and ambient binding**

In `_lark_request`, normalize the request selector with `parse_document_selector`. If `context.selector` matches `sha256:[0-9a-f]{64}`, compare it in constant time to the normalized commitment; otherwise retain the exact legacy literal comparison. Keep revision and session-range checks unchanged.

Allow `required_variables` length `0..8`. For the zero-variable case, require a closed `CredentialBinding("user", expected_principal, expected_account, ())`; for nonzero variables, retain every existing name/value check. The trusted runner remains mandatory in both modes.

Change the standalone runner receipt and broker snapshot selector digest to the already validated context commitment, rather than hashing the commitment a second time. Update `_validated_snapshot` to independently repeat the same normalized-token/commitment comparison for Lark, while preserving legacy literal mode and all Codex behavior.

- [x] **Step 4: Run broker and ingestion regressions green**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest \
  tests.test_brokers tests.test_ingestion tests.test_lark_selector -v
```

Expected: PASS for committed standalone Lark, legacy injected Lark, and committed/literal Codex paths.

- [x] **Step 5: Commit the broker binding**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/brokers.py \
  knowledge-distiller/scripts/knowledge_distiller/ingestion.py \
  tests/test_brokers.py tests/test_ingestion.py
git commit -m "feat: bind standalone Lark broker evidence"
```

### Task 6: Materialize owner-only Lark authorization and stable acquisition

**Files:**

- Modify: `knowledge-distiller/scripts/knowledge_distiller/lark_runtime.py`
- Modify: `knowledge-distiller/scripts/knowledge_distiller/lark_cli_transport.py`
- Modify: `tests/test_lark_runtime.py`

- [x] **Step 1: Write failing authorization and orchestration tests**

Require opaque derivation and exact operation order:

```python
EXPECTED_FLOW = (
    "version", "verify-before", "observe-before",
    "verify-acquire", "raw-content", "observe-after",
)

def test_owner_only_materialization_and_stable_ingestion(self):
    result = self.ingest_synthetic()
    self.assertEqual(self.transport.calls, list(EXPECTED_FLOW))
    self.assertEqual(result.status, "snapshotted")
    self.assertEqual(result.source_kind, "document")
    persisted = self.generation_bytes(result.generation_id)
    for secret in (TOKEN, OWNER, TITLE, RAW_TEXT):
        self.assertNotIn(secret.encode("utf-8"), persisted)
```

Before-acquisition tests must reject stale generation, wrong phase, duplicate Lark source, missing consent, synthetic-only production profile, incompatible CLI, missing scopes, owner mismatch, and invalid deadline without calling raw content. Acquisition tests must reject changed Open ID, owner, token, type, revision, malformed post-observation, transport evidence unavailable, raw overflow, adapter mismatch, redaction failure, and commit crash without changing the authoritative generation.

- [x] **Step 2: Run runtime tests red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_lark_runtime.LarkMaterializationTest \
  tests.test_lark_runtime.LarkStableIngestionTest -v
```

Expected: FAIL because no Lark orchestration exists.

- [x] **Step 3: Implement short-lived authorization and trusted runner receipt**

Use the shared time/UID/key helpers and validate `derived_processing_until` with the same five-minute read window and 90-day maximum as Codex. Derive:

```python
principal = digest("lark-user-principal/v1", open_id.encode("utf-8"))
account = digest("lark-account-scope/v1", open_id.encode("utf-8"))
task_id = digest(
    "local-lark-task/v1",
    os.path.abspath(os.fspath(task_root)).encode("utf-8")
    + b"\0" + request.expected_generation_id.encode("utf-8"),
)
```

Build `AuthorizationContext`, `ContentGrant`, and `AuthorityAttestation` with selector commitment, observed revision, purpose `distill-knowledge`, issue/expiry/deadline, owner equal to principal, issuer equal to principal for the grant, and issuer `lark-owner-verifier-v1` for verified ownership. Record IDs and decision digests use canonical domain-separated SHA-256 exactly as the Codex runtime does.

The orchestration order is:

```python
request = decode_local_lark_request(raw_request)
parsed_selector = lark_selector.parse_document_selector(
    request.document_selector)
ingestion.preflight_ingestion_slot(
    Path(task_root), request.expected_generation_id, "lark")
uid = runtime_support.effective_uid()
key = runtime_support.read_redaction_key(redaction_key_file, uid)
now = runtime_support.runtime_time()
profile = _require_live_ingestion(allow_live_read)
transport = LarkCliTransport(profile)
if transport.version() != profile.product_version:
    raise LarkRuntimeError("lark-cli-incompatible")
identity = transport.verify_user()
if not lark_cli_transport.has_required_scopes(identity.scopes):
    raise LarkRuntimeError("lark-missing-scope")
before = transport.observe(parsed_selector)
require_owner(identity, before)
materialized = materialize_local_lark_request(
    task_root, request, parsed_selector, identity, before, now)
```

The acquisition callback reverifies the same Open ID, executes only the broker-provided raw argv through the transport, observes again, compares one typed `LarkObservation`, and returns the existing `LarkResponse` with opaque principal/account/owner, before/after revision, selector commitment, pinned tuple, and no-fallback/endpoint-integrity receipt. Use a zero-variable ambient credential resolver returning the exact expected `CredentialBinding`. Delegate to `ingestion.ingest_source` with Lark acquisition and a rejecting Codex branch.

- [x] **Step 4: Run Lark runtime and shared ingestion tests green**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest \
  tests.test_lark_runtime tests.test_lark_cli_transport \
  tests.test_brokers tests.test_ingestion -v
```

Expected: PASS with exact flow ordering, no real process/network use outside the fake executable, no private value in errors, and no partial commit.

- [x] **Step 5: Commit the Lark runtime**

```bash
git add knowledge-distiller/scripts/knowledge_distiller/lark_runtime.py \
  knowledge-distiller/scripts/knowledge_distiller/lark_cli_transport.py \
  tests/test_lark_runtime.py
git commit -m "feat: orchestrate owner-only Lark ingestion"
```

### Task 7: Expose the fail-closed CLI and synthetic vertical acceptance

**Files:**

- Modify: `knowledge-distiller/scripts/kd.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_lark_runtime.py`

- [x] **Step 1: Write failing CLI boundary and vertical tests**

Add parser/delegation tests for:

```text
ingest-lark-document TASK_PATH REQUEST.json \
  --redaction-key-file KEY --allow-live-read
```

Assert missing consent/key/request arguments fail before request read, an unsafe request file maps to exit 2, `LarkRuntimeError` maps to a bounded `invalid-input` reason, ingestion rejection maps to exit 3, and neither output stream contains token, URL, owner, title, key, request path, or upstream diagnostics.

Add one production-gate test:

```python
def test_lark_production_profile_rejects_before_executable_or_network(self):
    with mock.patch.object(shutil, "which") as resolve:
        result = self.run_cli(
            "ingest-lark-document", str(self.task), str(self.request),
            "--redaction-key-file", str(self.key), "--allow-live-read")
    self.assertEqual(result.returncode, 2)
    self.assertEqual(
        json.loads(result.stderr)["error"]["reason"],
        "lark-live-disabled",
    )
    resolve.assert_not_called()
```

The synthetic vertical test patches only `_require_live_ingestion` inside the test process to return a frozen synthetic-enabled profile, places the executable fixture first in temporary PATH, calls `kd_cli.main`, and inspects the real temporary task generation.

- [x] **Step 2: Run CLI tests red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_cli.CliTest.test_lark_production_profile_rejects_before_executable_or_network \
  tests.test_cli.CliTest.test_lark_synthetic_vertical_ingestion -v
```

Expected: FAIL because the command and error mapping do not exist.

- [x] **Step 3: Add the exact CLI command and bounded mapping**

Import `lark_runtime` and add the bounded reader:

```python
def _read_local_lark_request(path: str) -> bytes:
    try:
        return source_io.read_source(
            path, max_bytes=lark_runtime.MAX_LOCAL_REQUEST_BYTES)
    except source_io.SourceIOError as error:
        raise CliInputError(error.code) from None
```

Then add:

```python
ingest_lark = commands.add_parser("ingest-lark-document")
ingest_lark.add_argument("task_path")
ingest_lark.add_argument("request_path")
ingest_lark.add_argument("--redaction-key-file", required=True)
ingest_lark.add_argument("--allow-live-read", action="store_true", required=True)
```

Dispatch only after argparse succeeds:

```python
if options.command == "ingest-lark-document":
    raw = _read_local_lark_request(options.request_path)
    result = lark_runtime.ingest_lark_document(
        Path(options.task_path), raw, options.redaction_key_file,
        allow_live_read=options.allow_live_read,
    )
    return {"ok": True, "ingestion": asdict(result)}
```

Map allowlisted `LarkRuntimeError` codes to exit 2 with the existing canonical `invalid-input` envelope. Leave `ingestion.IngestionError` at exit 3. Do not add a probe command, transport/profile override, identity option, endpoint option, executable option, auto-login path, or raw error passthrough.

- [x] **Step 4: Run CLI and synthetic end-to-end suites green**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest \
  tests.test_cli tests.test_lark_runtime tests.test_local_runtime -v
```

Expected: PASS; the synthetic command handler reaches `source_kind=document`, while an unpatched production invocation stops at `lark-live-disabled` before `shutil.which` or any Lark subprocess.

- [x] **Step 5: Commit the CLI milestone**

```bash
git add knowledge-distiller/scripts/kd.py tests/test_cli.py tests/test_lark_runtime.py
git commit -m "feat: expose fail-closed Lark ingestion CLI"
```

### Task 8: Document, verify, and review the synthetic milestone

**Files:**

- Modify: `knowledge-distiller/SKILL.md`
- Modify: `knowledge-distiller/references/workflow.md`
- Modify: `knowledge-distiller/references/authorization.md`
- Modify: `knowledge-distiller/references/adapter-compatibility.md`
- Modify: `README.md`
- Modify: `tests/test_skill_contract.py`
- Modify: `docs/specs/2026-09-11-local-lark-runtime-design.md`
- Modify: `docs/status/2026-09-05-implementation-status.md`
- Modify: `docs/plans/2026-09-11-local-lark-runtime.md`

- [x] **Step 1: Write failing documentation-contract tests**

Require the skill and workflow references to state all of these exact facts:

```python
required = (
    "ingest-lark-document",
    "--allow-live-read",
    "synthetic-only",
    "owner_id",
    "openId",
    "observational",
    "lark-cli 1.0.86",
    "Wiki URLs are unsupported",
    "does not authorize a real Lark read",
)
```

Also require the status document to name the completed synthetic milestone and the three remaining live gates: endpoint-integrity evidence, accepted consistency mode, and one explicitly approved exact-document probe.

- [x] **Step 2: Run documentation tests red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_skill_contract -v
```

Expected: FAIL because the public skill still says the production Lark runtime is unavailable without describing the new synthetic-only command boundary.

- [x] **Step 3: Update user-facing contracts without overstating readiness**

Document the exact command, request schema, owner-only authority, selector restrictions, observational sandwich, synthetic-only profile, error behavior, and prohibition on real reads. Keep automatic discovery, Wiki resolution, shared-document authority, live probe, profile activation, evaluation, export, installation, publication, and purge unavailable. Mark the design `implemented (synthetic-only)` only after code gates pass, mark Tasks 1–8 in this plan complete as they land, and add exact test/review results to the status file.

- [ ] **Step 4: Run final verification and independent reviews**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest \
  tests.test_runtime_support tests.test_lark_selector \
  tests.test_lark_cli_transport tests.test_lark_runtime \
  tests.test_brokers tests.test_ingestion tests.test_cli \
  tests.test_skill_contract -v
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s tests
PYTHONPYCACHEPREFIX=/private/tmp/kd-lark-runtime-pycache \
  python3 -m compileall -q knowledge-distiller/scripts
git diff --check
find . -name __pycache__ -type d -prune
git status --short
```

Expected: focused and full suites PASS, compilation succeeds, diff check is empty, no repository `__pycache__` exists, and status shows only intended milestone files.

Request two independent reviews against the design: specification/security first, then quality/readability. Both must explicitly inspect for deletable helpers, duplicated validation, redundant fixtures/tests, speculative abstractions, dead compatibility branches, and documentation repetition. Fix every Critical/Important finding with a focused regression test and rerun both review gates until `SPEC PASS` and `READY`.

- [ ] **Step 5: Commit the verified milestone record**

```bash
git add knowledge-distiller/SKILL.md knowledge-distiller/references \
  README.md tests/test_skill_contract.py \
  docs/specs/2026-09-11-local-lark-runtime-design.md \
  docs/status/2026-09-05-implementation-status.md \
  docs/plans/2026-09-11-local-lark-runtime.md
git commit -m "docs: complete synthetic Lark runtime milestone"
```

Do not push, merge, run a real Lark command, inspect auth state, read the supplied Wiki URL, add the future probe command, activate the live profile, export, install, or publish as part of this plan.
