"""Draft-only preflight durability/recovery tests, entirely offline fixtures."""

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from runtime.publish import main
from runtime.browser_qa import WIDTHS, LANDSCAPES
from runtime.tests.test_article_qa import synthetic_bundle
from runtime.tests.test_publisher_adapter import FakeTransport
from runtime.wordpress_publisher_adapter import NAMESPACE
from runtime.wordpress_transport import WordPressError


class DraftClient(FakeTransport):
    site_url = "https://sggroup.jp"

    def __init__(self, plan):
        super().__init__()
        self.plan = plan
        self.records = {}
        self.mode = None
        self.submissions = []

    def request(self, method, endpoint, *, payload=None, params=None):
        if method == "GET" and endpoint.startswith(NAMESPACE + "/operations/"):
            self.calls.append((method, endpoint, None))
            key = endpoint.rsplit("/", 1)[1]
            if self.mode == "pending":
                return {"key": key, "found": True, "state": "pending", "safe_to_resume": False}
            if key in self.records:
                return {"key": key, "found": True, "state": "complete", "result": deepcopy(self.records[key])}
            return {"key": key, "found": False, "state": "not_found", "definitive_absence": True}
        if method == "POST" and endpoint == NAMESPACE + "/articles/draft":
            self.calls.append((method, endpoint, deepcopy(payload)))
            journal = json.loads(self.plan.read_text())
            operation = journal["operations"][payload["language"]]
            assert operation["status"] == "intent"
            assert operation["key"] == payload["idempotency_key"]
            assert journal["payloads"][payload["language"]]["html"] == payload["html"]
            assert stat.S_IMODE(self.plan.stat().st_mode) == 0o600
            post = {"id": 60101 if payload["language"] == "ja" else 60102, "language": payload["language"],
                "kind": "stage", "status": "draft", "author": 5, "categories": [258],
                "is_accessible_for_free": True, "common_slug": payload["metadata"]["slug"],
                "title": payload["metadata"]["title"], "seo_title": payload["metadata"]["aioseo_title"],
                "meta_description": payload["metadata"]["aioseo_description"], "content_raw": payload["html"],
                "source_sha256": hashlib.sha256(payload["html"].encode()).hexdigest(), "public_url": None,
                "idempotency_key": payload["idempotency_key"], "seo_verified": True}
            self.posts[post["id"]] = post
            self.records[payload["idempotency_key"]] = deepcopy(post)
            self.submissions.append(payload["language"])
            if self.mode in ("ambiguous", "pending"):
                raise WordPressError("offline_lost_response", ambiguous_write=True)
            return deepcopy(post)
        value = super().request(method, endpoint, payload=payload, params=params)
        if self.mode == "changed" and method == "GET" and endpoint.endswith("/60101"):
            value["content_raw"] += "CHANGED"
            value["source_sha256"] = hashlib.sha256(value["content_raw"].encode()).hexdigest()
        return value


class PreflightCLITest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plugin = self.root / "approved.php"
        self.plugin.write_text("<?php /* Offline transport contract fixture; not installed. */")
        self.bundle = self.root / "canonical.json"
        self.bundle.write_text(json.dumps({"languages": synthetic_bundle()}))
        self.plan = self.root / "plan.json"
        self.client = DraftClient(self.plan)
        self.client.status["implementation_sha256"] = hashlib.sha256(self.plugin.read_bytes()).hexdigest()
        self.preview_failure = False
        self.previews = []

    def tearDown(self):
        self.temporary.cleanup()

    def fake_browser(self, post, preview):
        # This method is patched only in offline tests. No fake report is passed
        # as an input artifact or accepted by a production invocation.
        self.assertTrue(preview)
        self.assertEqual(set(self.client.submissions), {"ja", "en"})
        self.previews.append(post["language"])
        folder = self.root / "browser"
        folder.mkdir(mode=0o700, exist_ok=True)
        source_hash = hashlib.sha256(post["content_raw"].encode()).hexdigest()
        snapshot = folder / f"saved-theme-{post['id']}-{source_hash[:12]}.html"
        snapshot.write_text('<html><head></head><body>' + post["content_raw"] + '</body></html>')
        os.chmod(snapshot, 0o600)
        report = {"passed": not self.preview_failure, "errors": [] if not self.preview_failure else ["offline_preview_failure"],
            "execution_mode": "live", "test_only": False, "actual_authenticated_preview": True,
            "verification_context": "authenticated_saved_theme_preview", "actual_live_browser": False,
            "wordpress_identity_id": 5, "post_id": post["id"],
            "source_sha256": source_hash, "runtime_binding": json.loads(self.plan.read_text())["identity"]["runtime_binding"],
            "saved_theme_document_path": str(snapshot), "saved_theme_document_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            "runs": [{"width": width, "height": height, "measurement": {"errors": []}} for width, height in [(w, 900) for w in WIDTHS] + list(LANDSCAPES)]}
        path = folder / f"preview-{post['id']}-{source_hash[:12]}.json"
        path.write_text(json.dumps(report)); os.chmod(path, 0o600)
        return report

    def run_preflight(self, revision="explicit-offline-binding"):
        output = io.StringIO()
        with patch("runtime.publish.WordPressTransport.from_environment", return_value=self.client), \
                patch("runtime.publish.LiveBrowserVerifier._run", side_effect=self.fake_browser), \
                patch("runtime.publish.StateStore") as state, redirect_stdout(output):
            code = main(["--route", "query", "--context", "offline-controlled-preflight", "--connection-revision", revision,
                "--expected-user-id", "5", "--approved-plugin", str(self.plugin), "preflight",
                "--bundle", str(self.bundle), "--plan", str(self.plan), "--browser-artifacts", str(self.root / "browser"),
                "--chromium", "/offline/unused-chromium"])
        state.assert_not_called()
        self.assertFalse(any(endpoint.endswith("/articles/publish") for _, endpoint, _ in self.client.calls))
        self.assertNotIn("<style>", output.getvalue())
        return code, json.loads(output.getvalue())

    def test_success_persists_intent_pair_readback_and_preview_without_publication(self):
        code, result = self.run_preflight()
        self.assertEqual(code, 0)
        self.assertTrue(result["passed"])
        self.assertFalse(result["production_ready"])
        self.assertFalse(result["publication_performed"])
        self.assertEqual(self.client.submissions, ["ja", "en"])
        self.assertEqual(self.previews, ["ja", "en"])
        self.assertEqual(result["stages"]["ja"]["post_id"], 60101)
        journal = json.loads(self.plan.read_text())
        self.assertEqual(journal["state"], "private_drafts_verified")
        self.assertEqual(journal["operations"]["ja"]["saved_readback"]["content_raw"], synthetic_bundle()["ja"]["html"])
        self.assertEqual(sum(method == "POST" for method, _, _ in self.client.calls), 6)  # 2lease+2draft+2release.

    def test_ambiguous_save_recovers_same_known_stage_and_never_recreates(self):
        self.client.mode = "ambiguous"
        code, _ = self.run_preflight()
        self.assertEqual(code, 2)
        journal = json.loads(self.plan.read_text())
        key = journal["operations"]["ja"]["key"]
        self.assertEqual(journal["operations"]["ja"]["status"], "ambiguous")
        self.client.mode = None
        code, result = self.run_preflight()
        self.assertEqual(code, 0)
        self.assertEqual(self.client.submissions, ["ja", "en"])
        self.assertEqual(json.loads(self.plan.read_text())["operations"]["ja"]["key"], key)
        self.assertEqual(result["stages"]["ja"]["post_id"], 60101)

    def test_pending_server_barrier_cannot_replay_or_create_second_language(self):
        self.client.mode = "pending"
        self.assertEqual(self.run_preflight()[0], 2)
        code, result = self.run_preflight()
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "preflight_operation_needs_reconciliation")
        self.assertEqual(self.client.submissions, ["ja"])

    def test_readback_mismatch_keeps_known_id_and_does_not_repeat_post(self):
        self.client.mode = "changed"
        self.assertEqual(self.run_preflight()[0], 2)
        journal = json.loads(self.plan.read_text())
        self.assertEqual(journal["operations"]["ja"]["post_id"], 60101)
        self.assertEqual(journal["operations"]["ja"]["status"], "saved")
        self.assertEqual(self.run_preflight()[0], 2)
        self.assertEqual(self.client.submissions, ["ja"])

    def test_wrong_operation_origin_keeps_known_id_and_cannot_substitute_stage(self):
        self.assertEqual(self.run_preflight()[0], 0)
        self.client.posts[60101]["idempotency_key"] = "f" * 64
        code, result = self.run_preflight()
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "preflight_saved_draft_mismatch")
        self.assertEqual(json.loads(self.plan.read_text())["operations"]["ja"]["post_id"], 60101)
        self.assertEqual(self.client.submissions, ["ja", "en"])

    def test_preview_failure_keeps_both_stages_and_resume_only_rerenders(self):
        self.preview_failure = True
        self.assertEqual(self.run_preflight()[0], 2)
        journal = json.loads(self.plan.read_text())
        self.assertEqual(journal["operations"]["en"]["post_id"], 60102)
        self.preview_failure = False
        self.assertEqual(self.run_preflight()[0], 0)
        self.assertEqual(self.client.submissions, ["ja", "en"])

    def test_changed_binding_or_payload_cannot_reuse_an_old_plan(self):
        self.assertEqual(self.run_preflight()[0], 0)
        original = self.plan.read_bytes()
        code, result = self.run_preflight("different-connection")
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "preflight_plan_binding_mismatch")
        self.assertEqual(self.plan.read_bytes(), original)
        self.assertEqual(self.client.submissions, ["ja", "en"])

    def test_missing_dedicated_capability_cannot_create_plan_or_remote_draft(self):
        self.client.user["capabilities"]["sgnews_publish"] = False
        self.assertEqual(self.run_preflight()[0], 2)
        self.assertFalse(self.plan.exists())
        self.assertEqual(self.client.submissions, [])


if __name__ == "__main__":
    unittest.main()
