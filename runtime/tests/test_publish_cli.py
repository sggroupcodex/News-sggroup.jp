"""Operational CLI contracts; only offline transports and private temp artifacts."""

from contextlib import redirect_stdout
import hashlib
import io
import json
import shutil
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.publish import LiveBrowserVerifier, _PrivateThemeDocument, _Validator, main
from runtime.tests.test_article_qa import synthetic_bundle
from runtime.tests.test_publisher_adapter import FakeTransport


class PublishCLITest(unittest.TestCase):
    def test_workflow_language_mapping_reaches_actual_structural_validator(self):
        result = _Validator().validate_bundle(synthetic_bundle())
        self.assertTrue(result["passed"], result.get("errors"))

    def test_deployment_config_cannot_hold_credentials_or_forge_gate_records(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"WP_AUTHORIZATION": "DO-NOT-PRINT-CREDENTIAL"}))
            output = io.StringIO()
            with redirect_stdout(output), patch("runtime.publish.configured_adapter") as adapter:
                status = main(["--config", str(config), "diagnose"])
            self.assertEqual(status, 2)
            adapter.assert_not_called()
            self.assertNotIn("DO-NOT-PRINT-CREDENTIAL", output.getvalue())

    def test_execute_on_paused_store_cannot_stage_publish_unpause_or_open_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "approved.php"
            plugin.write_text("<?php /* Simulated contract artifact; not installed. */")
            plugin_hash = hashlib.sha256(plugin.read_bytes()).hexdigest()
            config = root / "config.json"
            config.write_text(json.dumps({"context": "offline-cli-fixture", "expected_user_id": 5,
                "connection_revision": "explicit-offline-spec", "publisher_code_sha256": plugin_hash,
                "publisher_policy_version": "sgnews-restricted-xml-css-1", "route": "query",
                "browser_output_dir": str(root / "private-browser")}))
            client = FakeTransport()
            client.site_url = "https://sggroup.jp"
            client.status["implementation_sha256"] = plugin_hash
            output = io.StringIO()
            with patch("runtime.publish.WordPressTransport.from_environment", return_value=client), \
                    patch("runtime.publish.LiveBrowserVerifier._run") as browser, redirect_stdout(output):
                status = main(["--config", str(config), "--approved-plugin", str(plugin), "execute",
                               "--state", str(root / "queue.sqlite3"), "--revision-id", "1"])
            self.assertEqual(status, 2)
            self.assertFalse(any(method == "POST" for method, _, _ in client.calls))
            browser.assert_not_called()
            self.assertFalse((root / "private-browser").exists())
            self.assertIn("GateBlocked", output.getvalue())

    def test_normalization_cannot_overwrite_an_existing_audited_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved.php"
            approved.write_text("<?php /* Simulated artifact. */")
            source = root / "source.html"
            source.write_text("<style>unused</style>")
            target = root / "already-audited.html"
            target.write_text("AUDITED-CONTENT-DO-NOT-CHANGE")
            client = FakeTransport()
            client.status["implementation_sha256"] = hashlib.sha256(approved.read_bytes()).hexdigest()
            output = io.StringIO()
            with patch("runtime.publish.WordPressTransport.from_environment", return_value=client), redirect_stdout(output):
                status = main(["--route", "query", "--context", "offline-fixture", "--connection-revision", "fixture-spec",
                    "--expected-user-id", "5", "--approved-plugin", str(approved), "canonicalize", "--language", "ja",
                    "--slug", "fixture-news-event-2026-10-09", "--input", str(source), "--output", str(target)])
            self.assertEqual(status, 2)
            self.assertEqual(target.read_text(), "AUDITED-CONTENT-DO-NOT-CHANGE")
            self.assertFalse(any(endpoint.endswith("/articles/draft") for _, endpoint, _ in client.calls))

    def test_missing_authenticated_theme_loader_cannot_certify_a_preview(self):
        verifier = LiveBrowserVerifier("https://sggroup.jp", 5, "/unused-private-path", preview_loader=None)
        with patch("playwright.sync_api.sync_playwright") as browser:
            report = verifier.saved_preview({"id": 60001, "content_raw": "unpublished source"})
        browser.assert_not_called()
        self.assertFalse(report["passed"])
        self.assertFalse(report["actual_authenticated_preview"])

    def test_private_theme_snapshot_uses_only_scoped_approved_asset_base(self):
        html = '<html><head><title>Saved theme</title></head><body><article>Actual template output</article></body></html>'
        document = _PrivateThemeDocument(html, "https://sggroup.jp/")
        self.assertIn('<base href="https://sggroup.jp/">', document.html)
        with document as url:
            import urllib.request
            # Loopback is our own ephemeral fixture server, never a remote policy bypass.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn(b'Actual template output', response.read())
        with self.assertRaises(Exception):
            _PrivateThemeDocument('<html><head><base href="https://other.example/"></head></html>', "https://sggroup.jp/")

    @unittest.skipUnless(shutil.which("chromium"), "Chromium is needed for the actual loopback smoke test")
    def test_actual_chromium_renders_synthetic_theme_snapshot_without_live_wordpress_claim(self):
        fragment = synthetic_bundle()["ja"]
        source, metadata = fragment["html"], fragment["metadata"]
        digest = hashlib.sha256(source.encode()).hexdigest()
        post = {"id": 60001, "status": "draft", "language": "ja", "common_slug": metadata["slug"],
                "seo_title": metadata["aioseo_title"], "meta_description": metadata["aioseo_description"],
                "content_raw": source, "public_url": None}
        document = {"html": f'<html lang="ja"><head><title>{metadata["aioseo_title"]}</title><meta name="description" content="{metadata["aioseo_description"]}"><style>html,body{{margin:0;padding:0}}</style></head><body>{source}</body></html>',
                    "stage_post_id": 60001, "source_sha256": digest, "user_id": 5,
                    "preview_context": "authenticated_native_theme_template", "base_url": "https://sggroup.jp/",
                    "seo_verified": True, "policy_version": "fixture-policy", "implementation_sha256": "d" * 64}
        with tempfile.TemporaryDirectory() as directory:
            verifier = LiveBrowserVerifier("https://sggroup.jp", 5, directory,
                                           preview_loader=lambda _: document, test_mode=True)
            verifier.runtime_binding = {"publisher_policy_version": "fixture-policy", "publisher_code_sha256": "d" * 64}
            report = verifier.saved_preview(post)
            self.assertTrue(report["passed"], report["errors"])
            self.assertTrue(report["browser_executed"])
            self.assertEqual(len(report["runs"]), 28)
            self.assertEqual(len(report["screenshots"]), 3)
            self.assertEqual(report["execution_mode"], "simulated")
            self.assertTrue(report["test_only"])
            self.assertFalse(report["actual_live_browser"])
            self.assertFalse(report["actual_authenticated_preview"])
            self.assertEqual(report["verification_context"], "synthetic_theme_fixture")
            for artifact in Path(directory).iterdir():
                self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)
            import urllib.request
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with self.assertRaises(Exception):
                opener.open(report["url"], timeout=1)  # The ephemeral document server has closed.


if __name__ == "__main__":
    unittest.main()
