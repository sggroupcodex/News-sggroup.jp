"""Offline private file RPC tests; no WordPress or native-tool calls."""

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from runtime.publish import main
from runtime.tool_transport import AUTHOR_ID, MAX_ENVELOPE_BYTES, SITE_URL, ToolWordPressTransport
from runtime.wordpress_transport import MAX_RESPONSE_BYTES, WordPressError


AUTHOR = {"id": AUTHOR_ID, "roles": ["author"],
          "capabilities": {"edit_posts": True, "publish_posts": True, "manage_options": False}}


class OfflineDriver:
    """A synthetic driver testing file boundaries, not live authentication proof."""

    def __init__(self, directory, replies):
        self.directory, self.replies = Path(directory), list(replies)
        self.requests, self.failure = [], None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(2)
        if self.failure:
            raise self.failure

    def run(self):
        seen = set()
        try:
            while not self.stop.is_set():
                for path in self.directory.glob("request-*.json"):
                    if path.name in seen:
                        continue
                    seen.add(path.name)
                    raw = path.read_bytes()
                    request = json.loads(raw)
                    self.requests.append(request)
                    if not self.replies:
                        continue
                    reply = self.replies.pop(0)
                    if reply is None:
                        continue
                    response = {"version": 1, "type": "response", "id": request["id"],
                                "request_sha256": hashlib.sha256(raw).hexdigest(),
                                "site_url": SITE_URL, "expected_user_id": AUTHOR_ID,
                                "method": request["method"], "endpoint": request["endpoint"],
                                "ok": True, "status": 200, "truncated": False, "data": {} if callable(reply) else reply}
                    if callable(reply):
                        response = reply(response, request)
                    target = self.directory / f"response-{request['id']}.json"
                    temporary = self.directory / ".offline-response"
                    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, "w") as handle:
                        json.dump(response, handle, ensure_ascii=False)
                    os.replace(temporary, target)
                self.stop.wait(0.005)
        except Exception as error:
            self.failure = error


class ToolTransportTest(unittest.TestCase):
    def test_exact_file_binding_and_full_source_without_secret_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"WP_BASE_URL": SITE_URL, "WP_NEWS_CATEGORY_ID": "258"}, clear=True):
                client = ToolWordPressTransport.from_environment(bridge_dir=directory)
            source = "<style>preserved</style><svg>✓</svg>" * 2000
            with OfflineDriver(directory, [AUTHOR, {"id": 90001, "content_raw": source}]) as driver:
                self.assertEqual(client.get_current_user()["id"], 5)
                self.assertEqual(client.request("GET", "/sgnews-publisher/v1/articles/90001")["content_raw"], source)
            self.assertEqual(driver.requests[0]["params"], {"context": "edit", "_fields": "id,roles,capabilities"})
            self.assertEqual(driver.requests[0]["max_response_bytes"], MAX_RESPONSE_BYTES)
            self.assertFalse(hasattr(client, "_authorization"))
            self.assertEqual(list(Path(directory).glob("request-*.json")), [])
            client.close()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_private_fresh_directory_and_single_owner_required(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            with self.assertRaisesRegex(WordPressError, "private_bridge_directory_required"):
                ToolWordPressTransport(directory, 258)
            os.chmod(directory, 0o700)
            client = ToolWordPressTransport(directory, 258)
            self.assertEqual(Path(directory, ".owner").stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(WordPressError, "bridge_directory_not_empty"):
                ToolWordPressTransport(directory, 258)
            client.close()
            Path(directory, "stale.json").write_text("{}")
            with self.assertRaisesRegex(WordPressError, "bridge_directory_not_empty"):
                ToolWordPressTransport(directory, 258)

    def test_other_site_or_actor_never_creates_request(self):
        with tempfile.TemporaryDirectory() as directory:
            for values in ({"site_url": "https://other.example"}, {"expected_user_id": 1}, {"expected_user_id": True}):
                with self.subTest(values=values), self.assertRaises(WordPressError):
                    ToolWordPressTransport(directory, 258, **values)
            self.assertEqual(list(Path(directory).iterdir()), [])
            with self.assertRaisesRegex(WordPressError, "invalid_news_category_id"):
                ToolWordPressTransport(directory, 259)

    def test_role_and_actual_identity_are_verified_not_just_http_success(self):
        cases = [{**AUTHOR, "id": 1}, {**AUTHOR, "id": True}, {**AUTHOR, "roles": ["administrator"]},
                 {**AUTHOR, "roles": ["author", "administrator"]}, {**AUTHOR, "capabilities": {"manage_options": True}},
                 {**AUTHOR, "roles": ["author", "editor"]},
                 {**AUTHOR, "roles": ["editor"]}]
        for actor in cases:
            with self.subTest(actor=actor), tempfile.TemporaryDirectory() as directory:
                client = ToolWordPressTransport(directory, 258)
                with OfflineDriver(directory, [actor]), self.assertRaises(WordPressError):
                    client.get_current_user()
                with self.assertRaisesRegex(WordPressError, "bridge_verified_author_required"):
                    client.request("POST", "/sgnews-publisher/v1/lease", payload={"scope": "news"})
                client.close()

    def test_no_core_writes_or_delete_patch_admin_routes_and_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ToolWordPressTransport(directory, 258)
            for method, endpoint in (("POST", "/wp/v2/posts"), ("DELETE", "/wp/v2/posts/1"),
                                     ("PATCH", "/sgnews-publisher/v1/articles/draft"),
                                     ("GET", "/wp/v2/users/1"), ("POST", "/wp/v2/settings"),
                                     ("GET", "/sgnews-publisher/v1/status?context=edit")):
                with self.subTest(method=method, endpoint=endpoint), self.assertRaises(WordPressError):
                    client.request(method, endpoint)
            with self.assertRaisesRegex(WordPressError, "invalid_bridge_parameters"):
                client.request("GET", "/wp/v2/users/me", params={"Authorization": "DO-NOT-TRANSMIT"})
            self.assertEqual(list(Path(directory).glob("request-*.json")), [])
            client.close()

    def test_mismatched_response_and_truncation_fail_closed(self):
        changes = [{"id": "wrong"}, {"request_sha256": "0" * 64}, {"site_url": "https://other.example"},
                   {"expected_user_id": 1}, {"method": "POST"}, {"endpoint": "/wp/v2/posts/1"},
                   {"version": True}, {"truncated": True}, {"data": "<html>Tool error with embedded JSON</html>"}]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                client = ToolWordPressTransport(directory, 258)
                with OfflineDriver(directory, [lambda response, request: {**response, **change}]), self.assertRaises(WordPressError):
                    client.get_current_user()
                client.close()

    def test_structured_tool_failure_is_redacted_and_write_is_ambiguous(self):
        for status, expected in ((None, True), (500, True), (403, False)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                client = ToolWordPressTransport(directory, 258)
                failure = lambda response, request: {**response, "ok": False, "error": {
                    "code": "tool_request_failed", "status": status, "retryable": False, "ambiguous_write": False}}
                with OfflineDriver(directory, [AUTHOR, failure]) as driver:
                    client.get_current_user()
                    with self.assertRaises(WordPressError) as caught:
                        client.request("POST", "/sgnews-publisher/v1/articles/draft", payload={"html": "source"})
                self.assertEqual(caught.exception.ambiguous_write, expected)
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(len(driver.requests), 2)
                self.assertNotIn("source", str(caught.exception.to_dict()))
                client.close()

    def test_write_timeout_is_not_retried_and_retains_one_request(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ToolWordPressTransport(directory, 258, response_timeout=0.12)
            with OfflineDriver(directory, [AUTHOR, None]) as driver:
                client.get_current_user()
                with self.assertRaisesRegex(WordPressError, "bridge_response_timeout") as caught:
                    client.request("POST", "/sgnews-publisher/v1/articles/draft", payload={"idempotency_key": "a" * 64})
            self.assertTrue(caught.exception.ambiguous_write)
            self.assertEqual(sum(request["method"] == "POST" for request in driver.requests), 1)
            self.assertEqual(len(list(Path(directory).glob("request-*.json"))), 1)
            client.close()

    def test_oversize_request_response_and_insecure_reply_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ToolWordPressTransport(directory, 258)
            with self.assertRaisesRegex(WordPressError, "bridge_request_size_exceeded"):
                client.request("GET", "/wp/v2/users/me", params={"huge": "x" * MAX_ENVELOPE_BYTES})
            reply = Path(directory, "oversized.json")
            reply.write_bytes(b"x" * (MAX_ENVELOPE_BYTES + 1))
            os.chmod(reply, 0o600)
            with self.assertRaisesRegex(WordPressError, "bridge_response_size_exceeded"):
                client._read_response(reply, write=False)
            os.chmod(reply, 0o644)
            with self.assertRaisesRegex(WordPressError, "insecure_bridge_response"):
                client._read_response(reply, write=False)
            link = Path(directory, "linked.json")
            link.symlink_to(reply)
            with self.assertRaises(WordPressError):
                client._read_response(link, write=False)
            os.chmod(reply, 0o600)
            for invalid in (b'{"ok":true,"ok":false}', b'{"data":NaN}', b'<html>failure</html>'):
                reply.write_bytes(invalid)
                with self.assertRaisesRegex(WordPressError, "invalid_bridge_response_json") as caught:
                    client._read_response(reply, write=True)
                self.assertTrue(caught.exception.ambiguous_write)
            client.close()

    def test_cli_authenticate_uses_existing_author_without_plugin_or_auth_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with OfflineDriver(directory, [AUTHOR]) as driver, patch.dict(os.environ, {
                    "WP_BASE_URL": SITE_URL, "WP_NEWS_CATEGORY_ID": "258"}, clear=True), redirect_stdout(output):
                result = main(["--transport", "wpvibe", "--bridge-dir", directory, "--route", "query",
                    "--context", "offline-native-fixture", "--connection-revision", "fixture-only", "--expected-user-id", "5",
                    "--approved-plugin", "/never-read-missing-plugin.php", "authenticate"])
            self.assertEqual(result, 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["authenticated"])
            self.assertTrue(report["authentication_only"])
            self.assertFalse(report["production_ready"])
            self.assertEqual(report["user_id"], 5)
            self.assertEqual(len(driver.requests), 1)
            self.assertEqual(driver.requests[0]["endpoint"], "/wp/v2/users/me")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cli_authenticate_rejects_admin_wrong_id_and_missing_publish_capability(self):
        for actor in ({**AUTHOR, "id": 1}, {**AUTHOR, "roles": ["administrator"]},
                      {**AUTHOR, "roles": ["author", "editor"]},
                      {**AUTHOR, "capabilities": {"edit_posts": True, "publish_posts": False}}):
            with self.subTest(actor=actor), tempfile.TemporaryDirectory() as directory:
                output = io.StringIO()
                with OfflineDriver(directory, [actor]), patch.dict(os.environ, {
                        "WP_BASE_URL": SITE_URL, "WP_NEWS_CATEGORY_ID": "258"}, clear=True), redirect_stdout(output):
                    result = main(["--transport", "wpvibe", "--bridge-dir", directory, "--route", "query",
                        "--context", "offline-fixture", "--connection-revision", "fixture-only", "--expected-user-id", "5", "authenticate"])
                self.assertEqual(result, 2)
                self.assertFalse(json.loads(output.getvalue())["passed"])
                self.assertNotIn(".owner", [path.name for path in Path(directory).iterdir()])


if __name__ == "__main__":
    unittest.main()
