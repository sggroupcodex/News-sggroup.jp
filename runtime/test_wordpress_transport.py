"""Failure-path tests; use fake responses and never contact WordPress."""

import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

try:
    from .wordpress_transport import WordPressError, WordPressTransport, _NoRedirect, preflight
except ImportError:  # Also support discovery with runtime/ as the start directory.
    from wordpress_transport import WordPressError, WordPressTransport, _NoRedirect, preflight

TRANSPORT_MODULE = WordPressTransport.__module__


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class Opener:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def transport(mode="pretty"):
    return WordPressTransport("https://example.org/blog/wp-json/",
                              "__CODEX_SECRET_PLACEHOLDER_WP_AUTHORIZATION__", 258,
                              route=mode)


class TransportFailureTests(unittest.TestCase):
    def test_proxy_placeholder_passes_unchanged_in_both_documented_urls(self):
        for mode in ("pretty", "query"):
            with self.subTest(mode=mode):
                client = transport(mode)
                client._opener = Opener(Response(b'{"id":5}'))
                client.get_current_user()
                request = client._opener.calls[0]
                self.assertEqual(request.get_header("Authorization"),
                                 "__CODEX_SECRET_PLACEHOLDER_WP_AUTHORIZATION__")
                self.assertIn("example.org/blog/", request.full_url)
                self.assertNotIn("/wp-json/wp-json", request.full_url)
                self.assertEqual("rest_route=" in request.full_url, mode == "query")

    def test_ambiguous_write_has_no_automatic_retry(self):
        client = transport()
        client._opener = Opener(urllib.error.URLError("connection lost"))
        with self.assertRaises(WordPressError) as failure:
            client.create_draft({"status": "draft", "categories": [258], "content": "article"})
        self.assertTrue(failure.exception.ambiguous_write)
        self.assertTrue(failure.exception.retryable)
        self.assertEqual(len(client._opener.calls), 1)

    def test_server_body_and_authorization_are_not_logged(self):
        body = json.dumps({"code": "rest_cannot_create", "message": "SECRET-MUST-STAY-HIDDEN"}).encode()
        error = urllib.error.HTTPError("https://example.org", 403, "forbidden", {}, io.BytesIO(body))
        client = transport()
        client._opener = Opener(error)
        with self.assertRaises(WordPressError) as failure:
            client.request("POST", "/wp/v2/posts", payload={"status": "draft"})
        serialized = json.dumps(failure.exception.to_dict())
        self.assertNotIn("SECRET-MUST-STAY-HIDDEN", serialized)
        self.assertNotIn("PLACEHOLDER", serialized)
        self.assertFalse(failure.exception.ambiguous_write)
        self.assertEqual(failure.exception.details["wordpress_code"], "rest_cannot_create")

    def test_redirect_cannot_forward_credentials_to_a_second_host(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {},
                                                       "https://second-host.example/"))

    def test_known_published_article_is_not_downgraded_to_draft(self):
        client = transport()
        with patch.object(client, "get_post", return_value={"id": 100, "status": "publish"}), \
                patch.object(client, "request") as request:
            with self.assertRaises(WordPressError):
                client.update_draft(100, {"status": "draft", "categories": [258], "content": "changed"})
            request.assert_not_called()
        self.assertFalse(client.supports_staged_updates)
        self.assertFalse(client.supports_atomic_idempotency)

    def test_absolute_url_is_rejected_before_credentials_are_sent(self):
        client = transport()
        with patch.object(client._opener, "open") as network:
            with self.assertRaises(WordPressError):
                client.request("GET", "https://different-host.example/wp-json/wp/v2/users/me")
            network.assert_not_called()


class PreflightRecoveryTests(unittest.TestCase):
    def args(self, root):
        payload = root / "probe.json"
        payload.write_text(json.dumps({"title": "Unpublished integrity probe", "status": "draft",
                                       "categories": [258],
                                       "content": '<style>#probe{color:#123456}</style><article id="probe"><svg viewBox="0 0 1 1"></svg></article>'}),
                           encoding="utf-8")
        return SimpleNamespace(route="query", timeout=25, expected_user_id=5,
                               record=str(root / "record.json"), payload=str(payload))

    def test_missing_html_capability_blocks_create_without_a_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            client = transport()
            with patch.object(client, "get_current_user", return_value={"id": 5, "capabilities":
                              {"edit_posts": True, "publish_posts": True, "unfiltered_html": False}}), \
                    patch.object(client, "create_draft") as create, \
                    patch(f"{TRANSPORT_MODULE}.WordPressTransport.from_environment", return_value=client):
                with self.assertRaises(WordPressError) as failure:
                    preflight(args)
                self.assertEqual(failure.exception.code, "publication_capability_missing")
                create.assert_not_called()
                self.assertFalse(Path(args.record).exists())

    def test_created_draft_id_survives_readback_failure_and_prevents_second_create(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            client = transport()
            with patch.object(client, "get_current_user", return_value={"id": 5, "capabilities":
                              {"edit_posts": True, "publish_posts": True, "unfiltered_html": True}}), \
                    patch.object(client, "create_draft", return_value={"id": 12345}) as create, \
                    patch.object(client, "get_post", side_effect=WordPressError("http_error", status=500)), \
                    patch(f"{TRANSPORT_MODULE}.WordPressTransport.from_environment", return_value=client):
                with self.assertRaises(WordPressError):
                    preflight(args)
                record = json.loads(Path(args.record).read_text())
                self.assertEqual(record["draft_id"], 12345)
                self.assertFalse(record["published"])
                with self.assertRaises(WordPressError) as repeated:
                    preflight(args)
                self.assertEqual(repeated.exception.code, "existing_preflight_record_requires_review")
                self.assertEqual(create.call_count, 1)

    def test_unknown_create_result_blocks_blind_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            client = transport()
            client._opener = Opener(urllib.error.URLError("connection lost after request"))
            with patch.object(client, "get_current_user", return_value={"id": 5, "capabilities":
                              {"edit_posts": True, "publish_posts": True, "unfiltered_html": True}}), \
                    patch(f"{TRANSPORT_MODULE}.WordPressTransport.from_environment", return_value=client):
                with self.assertRaises(WordPressError):
                    preflight(args)
                record = json.loads(Path(args.record).read_text())
                self.assertTrue(record["error"]["ambiguous_write"])
                self.assertNotIn("draft_id", record)
                with self.assertRaises(WordPressError):
                    preflight(args)
                self.assertEqual(len(client._opener.calls), 1)


if __name__ == "__main__":
    unittest.main()
