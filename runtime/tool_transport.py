"""Private, header-free RPC to the existing WPVibe Author connection.

The native tool driver owns authentication. This transport never reads
WP_AUTHORIZATION, creates credentials, or requests another WordPress account.
Each request gets one response; ambiguous mutations are never retried here.
Only the scoped publisher may mutate the site. The caller/driver must retain
the normal workflow gates; a connected Author alone is not publication proof.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import Any, Mapping
import uuid

from .wordpress_transport import MAX_RESPONSE_BYTES, WordPressError, WordPressTransport


SITE_URL = "https://sggroup.jp"
AUTHOR_ID = 5
NEWS_CATEGORY_ID = 258
MAX_ENVELOPE_BYTES = MAX_RESPONSE_BYTES + 16384
NAMESPACE = "/sgnews-publisher/v1"
_GET_ROUTE = re.compile(
    r"(?:/wp/v2/users/me|/wp/v2/posts/[1-9][0-9]*|"
    r"/sgnews-publisher/v1/(?:status|articles/[1-9][0-9]*(?:/preview)?|operations/[0-9a-f]{64}))\Z")
_POST_ROUTES = {NAMESPACE + path for path in (
    "/canonicalize", "/lease", "/lease/renew", "/lease/release", "/articles/draft", "/articles/publish")}


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise WordPressError("invalid_bridge_json") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("nonfinite_json_number")


def _atomic_private(path: Path, raw: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".rpc-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ToolWordPressTransport(WordPressTransport):
    """A live transport for the pinned Author; a private tool driver is required.

    request-<id>.json contains one bounded request. response-<id>.json must
    repeat its binding and SHA256 of the exact request bytes, including newline.
    A fresh, empty 0700 directory and exclusive .owner lock prevent stale reply
    reuse. Response files must be regular, singly linked, own-user 0600 files.
    The driver must stop when the Python process exits and must never replay a
    timed-out request. Server operation reconciliation owns any write recovery.
    """

    def __init__(self, bridge_dir: str | Path, news_category_id: int, *,
                 site_url: str = SITE_URL, expected_user_id: int = AUTHOR_ID,
                 route: str = "query", response_timeout: float = 45):
        if site_url.rstrip("/") != SITE_URL:
            raise WordPressError("bridge_site_binding_mismatch")
        if type(expected_user_id) is not int or expected_user_id != AUTHOR_ID:
            raise WordPressError("bridge_author_binding_mismatch")
        if type(news_category_id) is not int or news_category_id != NEWS_CATEGORY_ID:
            raise WordPressError("invalid_news_category_id")
        if route not in ("query", "pretty"):
            raise WordPressError("invalid_rest_route_mode")
        if isinstance(response_timeout, bool) or not isinstance(response_timeout, (int, float)) or not 0 < response_timeout <= 60:
            raise WordPressError("invalid_bridge_timeout")
        self.site_url, self.news_category_id, self.route = SITE_URL, news_category_id, route
        self.expected_user_id = AUTHOR_ID
        self.timeout = response_timeout
        self._identity_verified = False
        self._closed = False
        self._mutex = threading.Lock()
        self.bridge_dir = Path(bridge_dir).absolute()
        self._validate_directory()
        if any(self.bridge_dir.iterdir()):
            raise WordPressError("bridge_directory_not_empty")
        self._owner_path = self.bridge_dir / ".owner"
        try:
            fd = os.open(self._owner_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise WordPressError("bridge_directory_in_use") from None
        with os.fdopen(fd, "w") as handle:
            json.dump({"version": 1, "pid": os.getpid(), "site_url": SITE_URL,
                       "expected_user_id": AUTHOR_ID}, handle)

    @classmethod
    def from_environment(cls, *, bridge_dir: str | Path, expected_user_id: int = AUTHOR_ID,
                         route: str = "query", response_timeout: float = 45):
        missing = [key for key in ("WP_BASE_URL", "WP_NEWS_CATEGORY_ID") if not os.environ.get(key)]
        if missing:
            raise WordPressError("missing_runtime_configuration", details={"variables": missing})
        try:
            category = int(os.environ["WP_NEWS_CATEGORY_ID"])
        except ValueError:
            raise WordPressError("invalid_news_category_id") from None
        return cls(bridge_dir, category, site_url=os.environ["WP_BASE_URL"],
                   expected_user_id=expected_user_id, route=route, response_timeout=response_timeout)

    def _validate_directory(self):
        try:
            info = self.bridge_dir.lstat()
        except OSError:
            raise WordPressError("private_bridge_directory_required") from None
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise WordPressError("private_bridge_directory_required")

    def close(self):
        self._closed = True
        self._identity_verified = False
        try:
            self._owner_path.unlink()
        except FileNotFoundError:
            pass

    def _read_response(self, path: Path, *, write: bool) -> dict:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                    raise WordPressError("insecure_bridge_response", ambiguous_write=write)
                raw = handle.read(MAX_ENVELOPE_BYTES + 1)
        except WordPressError:
            raise
        except OSError:
            raise WordPressError("bridge_response_read_failed", ambiguous_write=write) from None
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise WordPressError("bridge_response_size_exceeded", ambiguous_write=write)
        try:
            value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise WordPressError("invalid_bridge_response_json", ambiguous_write=write) from None
        if not isinstance(value, dict):
            raise WordPressError("invalid_bridge_response_schema", ambiguous_write=write)
        return value

    def request(self, method: str, endpoint: str, *, params: Mapping[str, Any] | None = None,
                payload: Mapping[str, Any] | None = None) -> Any:
        if not isinstance(method, str):
            raise WordPressError("unsupported_http_method")
        method = method.upper()
        write = method == "POST"
        if method not in ("GET", "POST"):
            raise WordPressError("unsupported_http_method")
        if not isinstance(endpoint, str) or (method == "GET" and not _GET_ROUTE.fullmatch(endpoint)) or (write and endpoint not in _POST_ROUTES):
            raise WordPressError("unsupported_bridge_endpoint")
        if not write and payload is not None:
            raise WordPressError("get_payload_not_supported")
        if payload is not None and not isinstance(payload, Mapping):
            raise WordPressError("invalid_bridge_payload")
        if params is not None and (not isinstance(params, Mapping) or any(
                not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key)
                or key.lower() in {"authorization", "auth", "headers", "rest_route"}
                or type(value) not in (str, int, float, bool) for key, value in params.items())):
            raise WordPressError("invalid_bridge_parameters")
        if write and not self._identity_verified:
            raise WordPressError("bridge_verified_author_required")
        with self._mutex:
            if self._closed:
                raise WordPressError("bridge_transport_closed")
            self._validate_directory()
            identifier = uuid.uuid4().hex
            request = {"version": 1, "type": "request", "id": identifier,
                       "site_url": SITE_URL, "expected_user_id": AUTHOR_ID,
                       "method": method, "endpoint": endpoint, "params": dict(params or {}),
                       "payload": dict(payload) if payload is not None else None,
                       "max_response_bytes": MAX_RESPONSE_BYTES}
            raw = _json_bytes(request) + b"\n"
            if len(raw) > MAX_ENVELOPE_BYTES:
                raise WordPressError("bridge_request_size_exceeded")
            request_path = self.bridge_dir / f"request-{identifier}.json"
            response_path = self.bridge_dir / f"response-{identifier}.json"
            _atomic_private(request_path, raw)
            deadline = time.monotonic() + self.timeout
            while not response_path.exists() and not response_path.is_symlink():
                if time.monotonic() >= deadline:
                    raise WordPressError("bridge_response_timeout", retryable=True, ambiguous_write=write)
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            response = self._read_response(response_path, write=write)
            binding = {"version": 1, "type": "response", "id": identifier,
                       "request_sha256": hashlib.sha256(raw).hexdigest(), "site_url": SITE_URL,
                       "expected_user_id": AUTHOR_ID, "method": method, "endpoint": endpoint}
            if any(type(response.get(key)) is not type(value) or response.get(key) != value for key, value in binding.items()):
                raise WordPressError("bridge_response_binding_mismatch", ambiguous_write=write)
            if type(response.get("ok")) is not bool:
                raise WordPressError("invalid_bridge_response_schema", ambiguous_write=write)
            if response["ok"]:
                status = response.get("status")
                if (type(status) is not int or not 200 <= status < 300
                        or response.get("truncated") is not False or "data" not in response
                        or not isinstance(response["data"], (dict, list))):
                    raise WordPressError("invalid_bridge_success_response", ambiguous_write=write)
                try:
                    data_length = len(_json_bytes(response["data"]))
                except WordPressError:
                    raise WordPressError("invalid_bridge_response_json", ambiguous_write=write) from None
                if data_length > MAX_RESPONSE_BYTES:
                    raise WordPressError("bridge_response_size_exceeded", status=status, ambiguous_write=write)
                value = response["data"]
            else:
                error = response.get("error")
                if (not isinstance(error, dict) or not isinstance(error.get("code"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", error["code"])
                        or (error.get("status") is not None and (type(error["status"]) is not int or not 100 <= error["status"] <= 599))
                        or type(error.get("retryable")) is not bool or type(error.get("ambiguous_write")) is not bool):
                    raise WordPressError("invalid_bridge_error_response", ambiguous_write=write)
                request_path.unlink()
                response_path.unlink()
                # Tool failure cannot prove that a submitted mutation did not run.
                status = error.get("status")
                ambiguous = write and (error["ambiguous_write"] or status is None or status < 400 or status >= 500 or status in (408, 429))
                raise WordPressError(error["code"], status=status, retryable=error["retryable"], ambiguous_write=ambiguous)
            request_path.unlink()
            response_path.unlink()
            return value

    def get_current_user(self) -> dict[str, Any]:
        self._identity_verified = False
        value = self.request("GET", "/wp/v2/users/me", params={"context": "edit", "_fields": "id,roles,capabilities"})
        if not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] != AUTHOR_ID:
            raise WordPressError("bridge_author_identity_mismatch")
        roles, capabilities = value.get("roles"), value.get("capabilities")
        if (roles != ["author"]
                or not isinstance(capabilities, dict) or capabilities.get("manage_options") is True):
            raise WordPressError("bridge_author_role_required")
        self._identity_verified = True
        return value
