#!/usr/bin/env python3
"""WordPress REST transport and an unpublished HTML integrity preflight.

WP_BASE_URL is the HTTPS site URL (or its /wp-json root), WP_AUTHORIZATION
is the complete Authorization header or an injected proxy placeholder, and
WP_NEWS_CATEGORY_ID is the existing News category. Authorization is never
decoded, base64-encoded, printed, or changed. urllib retains the inherited
HTTP(S) proxy and the runtime's default CA trust. TLS verification stays on.

Both documented REST URL forms are supported: /wp-json/wp/v2/... (pretty)
and /?rest_route=/wp/v2/... (query). Choose a working authenticated form from
diagnose; a failed POST is never retried using the other URL form. Core REST
does not provide an atomic idempotency key, so this module alone must not be
used to recover an ambiguous create by blindly creating another post.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
POST_FIELDS = (
    "id,status,author,categories,slug,title,content,link,date_gmt,modified_gmt,"
    "sg-group-language-controller"
)


class WordPressError(RuntimeError):
    """Safe structured failure; never includes headers or a response body."""

    def __init__(self, code: str, *, status: int | None = None,
                 retryable: bool = False, ambiguous_write: bool = False,
                 details: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.ambiguous_write = ambiguous_write
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "status": self.status,
                "retryable": self.retryable,
                "ambiguous_write": self.ambiguous_write,
                "details": self.details}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Prevent sending configured authorization to an unexpected location.
        return None


class WordPressTransport:
    supports_staged_updates = False
    supports_atomic_idempotency = False

    def __init__(self, base_url: str, authorization: str, news_category_id: int,
                 *, route: str = "pretty", timeout: float = 25):
        url = urllib.parse.urlsplit(base_url)
        if (url.scheme != "https" or not url.hostname or url.username or
                url.password or url.query or url.fragment):
            raise WordPressError("invalid_https_site_url")
        path = url.path.rstrip("/")
        if path.endswith("/wp-json"):
            path = path[:-len("/wp-json")]
        if any(part in (".", "..") for part in path.split("/")):
            raise WordPressError("invalid_site_path")
        if route not in ("pretty", "query"):
            raise WordPressError("invalid_rest_route_mode")
        if not authorization or "\r" in authorization or "\n" in authorization:
            raise WordPressError("invalid_authorization_configuration")
        if (isinstance(news_category_id, bool) or not isinstance(news_category_id, int)
                or news_category_id <= 0):
            raise WordPressError("invalid_news_category_id")
        self.site_url = urllib.parse.urlunsplit((url.scheme, url.netloc, path, "", ""))
        self.route = route
        self.news_category_id = news_category_id
        self.timeout = timeout
        self._authorization = authorization  # Preserve injected placeholders.
        self._opener = urllib.request.build_opener(
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )

    @classmethod
    def from_environment(cls, *, route: str = "pretty", timeout: float = 25):
        missing = [name for name in ("WP_BASE_URL", "WP_AUTHORIZATION", "WP_NEWS_CATEGORY_ID")
                   if not os.environ.get(name)]
        if missing:
            raise WordPressError("missing_runtime_configuration", details={"variables": missing})
        try:
            category = int(os.environ["WP_NEWS_CATEGORY_ID"])
        except ValueError:
            raise WordPressError("invalid_news_category_id") from None
        return cls(os.environ["WP_BASE_URL"], os.environ["WP_AUTHORIZATION"],
                   category, route=route, timeout=timeout)

    def _url(self, endpoint: str, params: Mapping[str, Any] | None = None) -> str:
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", endpoint):
            raise WordPressError("invalid_rest_endpoint")
        if "//" in endpoint or any(part in (".", "..") for part in endpoint.split("/")):
            raise WordPressError("invalid_rest_endpoint")
        query = dict(params or {})
        if "rest_route" in query:
            raise WordPressError("reserved_rest_route_parameter")
        if self.route == "query":
            query = {"rest_route": endpoint, **query}
            base = self.site_url + "/"
        else:
            base = self.site_url + "/wp-json" + endpoint
        return base + (("?" + urllib.parse.urlencode(query, doseq=True)) if query else "")

    def request(self, method: str, endpoint: str, *, params: Mapping[str, Any] | None = None,
                payload: Mapping[str, Any] | None = None) -> Any:
        method = method.upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise WordPressError("unsupported_http_method")
        write = method != "GET"
        if not write and payload is not None:
            raise WordPressError("get_payload_not_supported")
        headers = {"Accept": "application/json", "User-Agent": "SGGroupNewsPublisher/1.0",
                   "Authorization": self._authorization}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(self._url(endpoint, params), data=body,
                                     headers=headers, method=method)
        try:
            response = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as error:
            raise WordPressError("network_request_failed", retryable=True,
                                 ambiguous_write=write,
                                 details={"exception_type": type(error).__name__}) from None
        with response:
            status = response.status
            try:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                raise WordPressError("response_read_failed", status=status, retryable=True,
                                     ambiguous_write=write,
                                     details={"exception_type": type(error).__name__}) from None
            if len(raw) > MAX_RESPONSE_BYTES:
                raise WordPressError("response_size_exceeded", status=status,
                                     ambiguous_write=write)
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                value = None
            if not 200 <= status < 300:
                details: dict[str, Any] = {"json_response": value is not None}
                if isinstance(value, dict):
                    wp_code = value.get("code")
                    if isinstance(wp_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", wp_code):
                        details["wordpress_code"] = wp_code
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    details["retry_after_seconds"] = int(retry_after)
                raise WordPressError("redirect_rejected" if 300 <= status < 400 else "http_error",
                                     status=status, retryable=status == 429 or status >= 500,
                                     ambiguous_write=write and (300 <= status < 400 or status == 429 or status >= 500),
                                     details=details)
            if value is None:
                raise WordPressError("invalid_json_response", status=status,
                                     ambiguous_write=write)
            return value

    def get_current_user(self) -> dict[str, Any]:
        value = self.request("GET", "/wp/v2/users/me", params={"context": "edit",
                              "_fields": "id,roles,capabilities"})
        if (not isinstance(value, dict) or isinstance(value.get("id"), bool)
                or not isinstance(value.get("id"), int) or value["id"] <= 0):
            raise WordPressError("invalid_current_user_response")
        return value

    def get_post(self, post_id: int) -> dict[str, Any]:
        value = self.request("GET", self._post_endpoint(post_id),
                             params={"context": "edit", "_fields": POST_FIELDS})
        if not isinstance(value, dict) or value.get("id") != post_id:
            raise WordPressError("invalid_post_response")
        return value

    @staticmethod
    def _post_endpoint(post_id: int) -> str:
        if isinstance(post_id, bool) or not isinstance(post_id, int) or post_id <= 0:
            raise WordPressError("invalid_post_id")
        return f"/wp/v2/posts/{post_id}"

    def _validate_draft_payload(self, payload: Mapping[str, Any]) -> None:
        if payload.get("status") != "draft":
            raise WordPressError("draft_status_required")
        if self.news_category_id not in payload.get("categories", []):
            raise WordPressError("configured_news_category_required")
        if not isinstance(payload.get("content"), str) or not payload["content"]:
            raise WordPressError("nonempty_draft_content_required")

    def create_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_draft_payload(payload)
        value = self.request("POST", "/wp/v2/posts", payload=payload)
        if (not isinstance(value, dict) or isinstance(value.get("id"), bool)
                or not isinstance(value.get("id"), int) or value["id"] <= 0):
            raise WordPressError("invalid_create_response", ambiguous_write=True)
        return value

    def update_draft(self, post_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_draft_payload(payload)
        current = self.get_post(post_id)
        if current.get("status") != "draft":
            raise WordPressError("existing_post_is_not_draft")
        return self.request("POST", self._post_endpoint(post_id), payload=payload)

    def set_post_status(self, post_id: int, status: str) -> dict[str, Any]:
        # Editorial/QA gates belong to the workflow; preflight never uses this.
        if status not in ("draft", "publish"):
            raise WordPressError("unsupported_post_status")
        return self.request("POST", self._post_endpoint(post_id), payload={"status": status})

    def find_posts(self, slug: str, *, expected_author_id: int | None = None,
                   language_taxonomy: str | None = None,
                   language_term: int | None = None) -> list[dict[str, Any]]:
        """Evidence only: slug search cannot prove absence after an ambiguous POST."""
        params: dict[str, Any] = {"context": "edit", "slug": slug,
                                 "status": "any", "per_page": 100, "_fields": POST_FIELDS}
        if expected_author_id is not None:
            params["author"] = expected_author_id
        if language_taxonomy is not None and language_term is not None:
            params[language_taxonomy] = language_term
        value = self.request("GET", "/wp/v2/posts", params=params)
        if not isinstance(value, list):
            raise WordPressError("invalid_posts_response")
        return value


def _write_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _reserve_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise WordPressError("existing_preflight_record_requires_review") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def diagnose(timeout: float) -> dict[str, Any]:
    attempts = []
    for mode in ("pretty", "query"):
        transport = WordPressTransport.from_environment(route=mode, timeout=timeout)
        try:
            user = transport.get_current_user()
            capabilities = user.get("capabilities", {})
            attempts.append({"route": mode, "authenticated": True, "user_id": user["id"],
                             "roles": user.get("roles", []),
                             "capabilities": {name: capabilities.get(name) is True
                                              for name in ("edit_posts", "publish_posts", "unfiltered_html")}})
            return {"authenticated": True, "recommended_route": mode, "attempts": attempts}
        except WordPressError as error:
            attempts.append({"route": mode, "authenticated": False, "error": error.to_dict()})
    return {"authenticated": False, "recommended_route": None, "attempts": attempts}


def preflight(args) -> dict[str, Any]:
    record_path = Path(args.record).resolve()
    # Never create another probe while an earlier one has a saved ID or an
    # uncertain create result. Reconciliation must precede any retry.
    if record_path.exists():
        raise WordPressError("existing_preflight_record_requires_review")
    transport = WordPressTransport.from_environment(route=args.route, timeout=args.timeout)
    user = transport.get_current_user()
    if user["id"] != args.expected_user_id:
        raise WordPressError("intended_identity_mismatch")
    required = ("edit_posts", "publish_posts", "unfiltered_html")
    missing = [name for name in required if user.get("capabilities", {}).get(name) is not True]
    if missing:
        raise WordPressError("publication_capability_missing", details={"capabilities": missing})
    with open(args.payload, encoding="utf-8") as handle:
        document = json.load(handle)
    payload = document.get("draft_probe_payload", document)
    if not isinstance(payload, dict):
        raise WordPressError("invalid_preflight_payload")
    transport._validate_draft_payload(payload)
    if payload.get("author", args.expected_user_id) != args.expected_user_id:
        raise WordPressError("intended_author_mismatch")
    source = payload["content"]
    if not all(marker in source for marker in ("<style>", "<article ", "<svg ")):
        raise WordPressError("invalid_integrity_probe_source")
    record: dict[str, Any] = {"phase": "create_pending", "route": args.route,
                              "expected_user_id": args.expected_user_id,
                              "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                              "payload_path": str(Path(args.payload).resolve()),
                              "published": False, "browser_preview_verified": False}
    _reserve_record(record_path, record)
    try:
        created = transport.create_draft(payload)
        record.update({"phase": "draft_created", "draft_id": created["id"]})
        _write_record(record_path, record)  # Checkpoint before another network call.
        saved = transport.get_post(created["id"])
        raw = saved.get("content", {}).get("raw")
        rendered = saved.get("content", {}).get("rendered", "")
        checks = {"saved_as_draft": saved.get("status") == "draft",
                  "intended_author": saved.get("author") == args.expected_user_id,
                  "news_category": transport.news_category_id in saved.get("categories", []),
                  "title_preserved": saved.get("title", {}).get("raw") == payload.get("title"),
                  "source_exact": raw == source,
                  "style_retained": isinstance(raw, str) and "<style>" in raw,
                  "svg_retained": isinstance(raw, str) and "<svg " in raw,
                  "rendered_style_retained": "<style>" in rendered,
                  "rendered_svg_retained": "<svg " in rendered}
        for taxonomy in ("sg-group-language-controller",):
            if taxonomy in payload:
                checks["language_terms"] = saved.get(taxonomy) == payload[taxonomy]
        record.update({"phase": "roundtrip_checked", "checks": checks,
                       "roundtrip_passed": all(checks.values()),
                       "saved_source_sha256": hashlib.sha256((raw or "").encode()).hexdigest()})
        _write_record(record_path, record)
        return record
    except WordPressError as error:
        record.update({"phase": "failed", "error": error.to_dict()})
        _write_record(record_path, record)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=25)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("diagnose", help="Read-only authentication checks; outputs no credentials")
    probe = commands.add_parser("preflight", help="Create one capability-gated unpublished probe")
    probe.add_argument("--route", choices=("pretty", "query"), required=True)
    probe.add_argument("--expected-user-id", type=int, required=True)
    probe.add_argument("--payload", required=True, help="JSON payload or draft_probe_payload document")
    probe.add_argument("--record", required=True, help="New local checkpoint file; never overwritten")
    readback = commands.add_parser("readback", help="Read draft metadata and content hashes only")
    readback.add_argument("--route", choices=("pretty", "query"), required=True)
    readback.add_argument("--post-id", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.command == "diagnose":
            result = diagnose(args.timeout)
            successful = result["authenticated"]
        elif args.command == "preflight":
            result = preflight(args)
            successful = result["roundtrip_passed"]
        else:
            post = WordPressTransport.from_environment(route=args.route, timeout=args.timeout).get_post(args.post_id)
            raw = post.get("content", {}).get("raw", "")
            result = {key: post.get(key) for key in ("id", "status", "author", "categories", "slug")}
            result["content_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
            successful = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if successful else 2
    except WordPressError as error:
        print(json.dumps({"success": False, "error": error.to_dict()}, ensure_ascii=False, indent=2))
        return 2
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"success": False, "error": {"code": "local_configuration_error",
                                                   "exception_type": type(error).__name__}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
