"""Pinned Lark response validation and a fixed, bounded CLI transport.

The transport remains a synthetic-test primitive until a separate runtime gate
and approved compatibility probe establish real CLI shapes and live readiness.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import time
from typing import Tuple

from . import adapters, lark_profile
from .journal import canonical_json
from .lark_selector import ParsedDocumentSelector, TOKEN, selector_commitment


_ERROR_CODES = frozenset(
    {
        "invalid-lark-control-response",
        "invalid-selector",
        "lark-cli-not-found",
        "lark-cli-unsafe",
        "lark-cli-incompatible",
        "lark-cli-fingerprint-changed",
        "lark-cli-timeout",
        "lark-cli-output-limit",
        "lark-cli-failed",
    }
)
_CHILD_ENVIRONMENT_NAMES = ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR")
_FIXED_CHILD_ENVIRONMENT = {
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}
_READ_CHUNK_BYTES = 64 * 1024
_TERMINATION_GRACE_SECONDS = 1.0


class LarkTransportError(ValueError):
    """A code-only failure that never carries response data."""

    def __init__(self, code="invalid-lark-control-response"):
        if type(code) is not str or code not in _ERROR_CODES:
            code = "lark-cli-failed"
        self.code = code
        super().__init__(self.code)


def _fail():
    raise LarkTransportError()


@dataclass(frozen=True, repr=False)
class VerifiedUser:
    open_id: str
    scopes: Tuple[str, ...]

    def __post_init__(self):
        if (
            type(self.open_id) is not str
            or not 1 <= len(self.open_id) <= 256
            or type(self.scopes) is not tuple
            or not 1 <= len(self.scopes) <= 256
            or any(type(scope) is not str or not 1 <= len(scope) <= 256 for scope in self.scopes)
            or len(set(self.scopes)) != len(self.scopes)
        ):
            _fail()


@dataclass(frozen=True, repr=False)
class LarkObservation:
    token: str
    revision: str
    owner_open_id: str

    def __post_init__(self):
        if (
            type(self.token) is not str
            or TOKEN.fullmatch(self.token) is None
            or type(self.revision) is not str
            or not self.revision.isascii()
            or not self.revision.isdecimal()
            or self.revision.startswith("0")
            or not 1 <= int(self.revision) <= 2**63 - 1
            or type(self.owner_open_id) is not str
            or not 1 <= len(self.owner_open_id) <= 256
        ):
            _fail()


def _validate(value, schema):
    kind = schema["type"]
    if kind == "object":
        if type(value) is not dict:
            _fail()
        keys = set(value)
        required = set(schema["required"])
        allowed = set(schema["properties"])
        if not required <= keys or not keys <= allowed:
            _fail()
        for key, item in value.items():
            if type(key) is not str:
                _fail()
            _validate(item, schema["properties"][key])
        return
    if kind == "array":
        if type(value) is not list:
            _fail()
        if not schema["min_items"] <= len(value) <= schema["max_items"]:
            _fail()
        for item in value:
            _validate(item, schema["items"])
        if schema["unique_items"]:
            try:
                if len(set(value)) != len(value):
                    _fail()
            except TypeError:
                _fail()
        return
    expected_type = {"string": str, "boolean": bool, "integer": int}.get(kind)
    if expected_type is None or type(value) is not expected_type:
        _fail()
    if "const" in schema and value != schema["const"]:
        _fail()
    if kind == "string" and not schema["min_length"] <= len(value) <= schema["max_length"]:
        _fail()
    if kind == "integer" and not schema["minimum"] <= value <= schema["maximum"]:
        _fail()


def _decode(raw, schema_name):
    if type(raw) is not bytes or not raw or len(raw) > lark_profile.CONTROL_STDOUT_LIMIT:
        _fail()
    try:
        value = adapters.decode_event_graph_json(raw)
        schema = lark_profile._load_control_schema(schema_name)
        _validate(value, schema)
        return value
    except LarkTransportError:
        raise
    except Exception:
        _fail()


def parse_verified_identity(raw):
    """Return the verified synthetic user projection from a closed response."""

    value = _decode(raw, "auth-status")
    user = value["identities"]["user"]
    return VerifiedUser(user["openId"], tuple(user["scope"]))


def has_required_scopes(scopes):
    """Check exact pinned Docx-read and Drive-metadata capabilities."""

    if (
        type(scopes) is not tuple
        or any(type(scope) is not str for scope in scopes)
        or len(set(scopes)) != len(scopes)
    ):
        return False
    available = set(scopes)
    return bool(available & lark_profile.READ_SCOPE_ALTERNATIVES) and bool(
        available & lark_profile.METADATA_SCOPE_ALTERNATIVES
    )


def parse_observation(document_raw, metadata_raw, expected_token):
    """Combine exact document revision and owner projections."""

    if type(expected_token) is not str or TOKEN.fullmatch(expected_token) is None:
        _fail()
    document = _decode(document_raw, "document-info")["data"]["document"]
    metadata = _decode(metadata_raw, "drive-metadata")["data"]["metas"][0]
    if document["document_id"] != expected_token or metadata["doc_token"] != expected_token:
        _fail()
    return LarkObservation(
        expected_token,
        str(document["revision_id"]),
        metadata["owner_id"],
    )


def parse_missing_scope(raw):
    """Return only scopes proven by the closed typed error response."""

    value = _decode(raw, "missing-scope")
    return tuple(value["error"]["missing_scopes"])


@dataclass(frozen=True, repr=False)
class _ExecutableFingerprint:
    canonical_path: str
    device: int
    inode: int
    uid: int
    mode: int
    size: int
    mtime_ns: int


def _native_fingerprint(path):
    try:
        metadata = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not stat.S_IMODE(metadata.st_mode) & 0o100
        ):
            raise LarkTransportError("lark-cli-unsafe")
        return _ExecutableFingerprint(
            str(path),
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            stat.S_IMODE(metadata.st_mode),
            metadata.st_size,
            metadata.st_mtime_ns,
        )
    except LarkTransportError:
        raise
    except FileNotFoundError:
        raise LarkTransportError("lark-cli-not-found") from None
    except Exception:
        raise LarkTransportError("lark-cli-unsafe") from None


def _resolve_native_executable():
    try:
        entry_text = shutil.which("lark-cli")
    except Exception:
        raise LarkTransportError("lark-cli-not-found") from None
    if entry_text is None:
        raise LarkTransportError("lark-cli-not-found")
    if type(entry_text) is not str or not Path(entry_text).is_absolute():
        raise LarkTransportError("lark-cli-unsafe")
    path_entry = Path(entry_text)
    linked_entry = path_entry.is_symlink()
    try:
        entry = path_entry.resolve(strict=True)
    except FileNotFoundError:
        raise LarkTransportError("lark-cli-not-found") from None
    except Exception:
        raise LarkTransportError("lark-cli-unsafe") from None

    if entry.name == "run.js" and entry.parent.name == "scripts":
        try:
            native = (entry.parent.parent / "bin" / "lark-cli").resolve(strict=True)
        except FileNotFoundError:
            raise LarkTransportError("lark-cli-not-found") from None
        except Exception:
            raise LarkTransportError("lark-cli-unsafe") from None
    elif linked_entry or entry.suffix == ".js":
        raise LarkTransportError("lark-cli-unsafe")
    else:
        native = entry
    return _native_fingerprint(native)


def _child_environment():
    child = dict(_FIXED_CHILD_ENVIRONMENT)
    for name in _CHILD_ENVIRONMENT_NAMES:
        value = os.environ.get(name)
        if value is None:
            continue
        if type(value) is not str or not value or "\0" in value:
            raise LarkTransportError("lark-cli-unsafe")
        if name in {"HOME", "TMPDIR"}:
            path = Path(value)
            if not path.is_absolute() or not path.is_dir():
                raise LarkTransportError("lark-cli-unsafe")
        if name == "PATH" and any(
            not entry or not Path(entry).is_absolute()
            for entry in value.split(os.pathsep)
        ):
            raise LarkTransportError("lark-cli-unsafe")
        child[name] = value
    return child


def _group_exists(process_group):
    try:
        os.killpg(process_group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_process_group(process):
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    while _group_exists(process_group) and time.monotonic() < deadline:
        try:
            process.wait(timeout=min(0.02, max(0.0, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.01)
    if _group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()


def _cleanup_process(process, selector, streams, terminate):
    """Close and reap every child resource, retaining only the first failure."""

    first_error = None

    def attempt(action):
        nonlocal first_error
        try:
            action()
        except BaseException as error:
            if first_error is None:
                first_error = error

    if process is not None and terminate:
        attempt(lambda: _terminate_process_group(process))
    if selector is not None:
        attempt(selector.close)
    if process is None:
        return first_error
    if process.stdin is not None and not process.stdin.closed:
        attempt(process.stdin.close)
    for stream in streams:
        if stream is not None and not stream.closed:
            attempt(stream.close)
    if terminate:
        attempt(process.kill)
    attempt(lambda: process.wait(timeout=_TERMINATION_GRACE_SECONDS))
    return first_error


class LarkCliTransport:
    """Fixed-command, bounded subprocess transport for synthetic Lark tests."""

    def __init__(self, profile):
        self._profile = profile
        self._fingerprint = _resolve_native_executable()
        self._environment = _child_environment()

    def _check_fingerprint(self):
        try:
            current = _native_fingerprint(Path(self._fingerprint.canonical_path))
        except Exception:
            raise LarkTransportError("lark-cli-fingerprint-changed") from None
        if current != self._fingerprint:
            raise LarkTransportError("lark-cli-fingerprint-changed")

    def _execute(self, arguments, stdin, stdout_limit):
        process = None
        selector = None
        streams = ()
        output_bytes = None
        primary_error = None
        try:
            process = subprocess.Popen(
                (self._fingerprint.canonical_path,) + arguments,
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                env=self._environment,
            )
            streams = (process.stdout, process.stderr)
            try:
                process.stdin.write(stdin)
                process.stdin.close()
            except BrokenPipeError:
                process.stdin.close()

            selector = selectors.DefaultSelector()
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            output = bytearray()
            stderr_size = 0
            deadline = time.monotonic() + self._profile.timeout_seconds
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LarkTransportError("lark-cli-timeout")
                events = selector.select(min(remaining, 0.1))
                if not events and process.poll() is not None:
                    events = tuple(
                        (key, selectors.EVENT_READ)
                        for key in selector.get_map().values()
                    )
                for key, _ in events:
                    try:
                        chunk = os.read(key.fd, _READ_CHUNK_BYTES)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
                        if len(output) > stdout_limit:
                            raise LarkTransportError("lark-cli-output-limit")
                    else:
                        stderr_size += len(chunk)
                        if stderr_size > self._profile.stderr_limit:
                            raise LarkTransportError("lark-cli-output-limit")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LarkTransportError("lark-cli-timeout")
            try:
                return_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise LarkTransportError("lark-cli-timeout") from None
            if return_code != 0:
                raise LarkTransportError("lark-cli-failed")
            if _group_exists(process.pid):
                raise LarkTransportError("lark-cli-failed")
            output_bytes = bytes(output)
        except BaseException as error:
            primary_error = error

        cleanup_error = _cleanup_process(
            process, selector, streams, terminate=primary_error is not None
        )
        fingerprint_error = None
        if process is not None:
            try:
                self._check_fingerprint()
            except BaseException as error:
                fingerprint_error = error
        if primary_error is not None:
            raise primary_error.with_traceback(primary_error.__traceback__)
        if cleanup_error is not None:
            raise cleanup_error.with_traceback(cleanup_error.__traceback__)
        if fingerprint_error is not None:
            raise fingerprint_error.with_traceback(fingerprint_error.__traceback__)
        return output_bytes

    def _run(self, arguments, *, stdin=b"", stdout_limit):
        self._check_fingerprint()
        try:
            return self._execute(arguments, stdin, stdout_limit)
        except (KeyboardInterrupt, SystemExit):
            raise
        except LarkTransportError:
            raise
        except Exception:
            raise LarkTransportError("lark-cli-failed") from None

    def version(self):
        raw = self._run(
            ("--version",), stdout_limit=self._profile.control_stdout_limit
        )
        match = re.fullmatch(rb"lark-cli version (1\.0\.86)\n?", raw)
        if match is None:
            raise LarkTransportError("lark-cli-incompatible")
        return match.group(1).decode("ascii")

    def verify_user(self):
        raw = self._run(
            ("auth", "status", "--json", "--verify"),
            stdout_limit=self._profile.control_stdout_limit,
        )
        return parse_verified_identity(raw)

    @staticmethod
    def _selector(selector):
        try:
            if (
                type(selector) is not ParsedDocumentSelector
                or TOKEN.fullmatch(selector.token) is None
                or selector.commitment != selector_commitment(selector.token)
            ):
                raise ValueError
            return selector
        except Exception:
            raise LarkTransportError("invalid-selector") from None

    def observe(self, selector):
        selector = self._selector(selector)
        document = self._run(
            (
                "api", "GET",
                "/open-apis/docx/v1/documents/" + selector.token,
                "--as", "user",
            ),
            stdout_limit=self._profile.control_stdout_limit,
        )
        body = canonical_json(
            {
                "request_docs": [
                    {"doc_token": selector.token, "doc_type": "docx"}
                ],
                "with_url": False,
            }
        )
        metadata = self._run(
            (
                "drive", "metas", "batch_query", "--user-id-type", "open_id",
                "--data", "-", "--as", "user", "--format", "json",
            ),
            stdin=body,
            stdout_limit=self._profile.control_stdout_limit,
        )
        return parse_observation(document, metadata, selector.token)

    def raw_content(self, selector):
        selector = self._selector(selector)
        return self._run(
            (
                "api", "GET",
                "/open-apis/docx/v1/documents/" + selector.token + "/raw_content",
                "--as", "user",
            ),
            stdout_limit=self._profile.raw_stdout_limit,
        )
