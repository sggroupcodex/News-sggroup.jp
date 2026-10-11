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
from runtime.preflight_identity import (BOOTSTRAP_ORIGIN_SHA256, BOOTSTRAP_FIXED_SHA256,
                                        preflight_identities, validate_preflight_origin,
                                        validate_recovery_reconciliations)
from runtime.state import fingerprint
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
        self.pending_safe_keys = set()
        self.unresolved_keys = set()

    def request(self, method, endpoint, *, payload=None, params=None):
        if method == "GET" and endpoint.startswith(NAMESPACE + "/operations/"):
            self.calls.append((method, endpoint, None))
            key = endpoint.rsplit("/", 1)[1]
            if key in self.unresolved_keys:
                return {"key": key, "found": True, "state": "pending", "safe_to_resume": False}
            if key in self.pending_safe_keys:
                return {"key": key, "found": True, "state": "pending", "safe_to_resume": True}
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
            retained = self.records.get(payload["idempotency_key"])
            post = {"id": retained["id"] if retained else (60101 if payload["language"] == "ja" else 60102), "language": payload["language"],
                "kind": "stage", "status": "draft", "author": 5, "categories": [258],
                "is_accessible_for_free": True, "common_slug": payload["metadata"]["slug"],
                "title": payload["metadata"]["title"], "seo_title": payload["metadata"]["aioseo_title"],
                "meta_description": payload["metadata"]["aioseo_description"], "content_raw": payload["html"],
                "source_sha256": hashlib.sha256(payload["html"].encode()).hexdigest(), "public_url": None,
                "idempotency_key": payload["idempotency_key"], "seo_verified": True}
            self.posts[post["id"]] = post
            self.records[payload["idempotency_key"]] = deepcopy(post)
            self.pending_safe_keys.discard(payload["idempotency_key"])
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
            "source_sha256": source_hash, "runtime_binding": preflight_identities(json.loads(self.plan.read_text()))[1]["runtime_binding"],
            "saved_theme_document_path": str(snapshot), "saved_theme_document_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            "runs": [{"width": width, "height": height, "measurement": {"errors": []}} for width, height in [(w, 900) for w in WIDTHS] + list(LANDSCAPES)]}
        path = folder / f"preview-{post['id']}-{source_hash[:12]}.json"
        path.write_text(json.dumps(report)); os.chmod(path, 0o600)
        return report

    def run_preflight(self, revision="explicit-offline-binding", *, recover=False):
        output = io.StringIO()
        with patch("runtime.publish.WordPressTransport.from_environment", return_value=self.client), \
                patch("runtime.publish.LiveBrowserVerifier._run", side_effect=self.fake_browser), \
                patch("runtime.publish.StateStore") as state, redirect_stdout(output):
            code = main(["--route", "query", "--context", "offline-controlled-preflight", "--connection-revision", revision,
                "--expected-user-id", "5", "--approved-plugin", str(self.plugin), "preflight",
                "--bundle", str(self.bundle), "--plan", str(self.plan), "--browser-artifacts", str(self.root / "browser"),
                "--chromium", "/offline/unused-chromium"] + (["--recover-reviewed-bootstrap"] if recover else []))
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

    def seed_bootstrap_plan(self):
        """Actual blocked-plan shape; only local, synthetic article content."""
        php = Path(__file__).resolve().parents[2] / "wordpress/sggroup-news-publisher.php"
        fixed = php.read_bytes()
        self.assertEqual(hashlib.sha256(fixed).hexdigest(), BOOTSTRAP_FIXED_SHA256)
        self.plugin.write_bytes(fixed)
        self.client.status["implementation_sha256"] = BOOTSTRAP_FIXED_SHA256
        languages = json.loads(self.bundle.read_text())["languages"]
        binding = {"site_url": self.client.site_url, "execution_context": "offline-controlled-preflight",
                   "wordpress_identity_id": 5, "connection_revision": "explicit-offline-binding",
                   "publisher_policy_version": "sgnews-restricted-xml-css-1", "publisher_code_sha256": BOOTSTRAP_ORIGIN_SHA256}
        identity = {"schema_version": 1, "purpose": "controlled_private_bilingual_draft_preflight",
                    "runtime_binding": binding, "bundle_digest": fingerprint(languages),
                    "common_slug": languages["ja"]["metadata"]["slug"]}
        payloads = {language: {"language": language, "html": entry["html"], "metadata": entry["metadata"],
                              "categories": [258], "is_accessible_for_free": True, "status": "draft"}
                    for language, entry in languages.items()}
        keys = {language: fingerprint({**identity, "language": language, "operation": "draft", "payload": payload})
                for language, payload in payloads.items()}
        error = {"type": "WordPressError", "code": "wpvibe_tool_error", "http_status": 409, "ambiguous_write": False}
        plan = {"identity": identity, "payloads": payloads, "state": "blocked", "publication_performed": False,
                "production_ready": False, "previews": {}, "last_error": error,
                "structural_validation": {"passed": True, "errors": []},
                "operations": {"ja": {"key": keys["ja"], "status": "rejected", "post_id": None, "error": error},
                               "en": {"key": keys["en"], "status": "planned", "post_id": None}}}
        self.plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        os.chmod(self.plan, 0o600)
        self.client.pending_safe_keys.add(keys["ja"])
        return deepcopy(plan), self.plan.read_bytes(), keys

    def test_reviewed_bootstrap_recovery_keeps_exact_origin_and_same_pending_keys(self):
        original, raw, keys = self.seed_bootstrap_plan()
        code, result = self.run_preflight(recover=True)
        self.assertEqual(code, 0)
        plan = json.loads(self.plan.read_text())
        self.assertEqual(plan["identity"], original["identity"])
        self.assertEqual(plan["payloads"], original["payloads"])
        self.assertEqual(plan["recovery"]["operation_keys"], keys)
        snapshot = Path(plan["recovery"]["origin_plan_path"])
        self.assertEqual(snapshot.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
        self.assertEqual(plan["recovery"]["origin_plan_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(plan["recovery_reconciliations"]["ja"]["outcome"], "safe_to_resume")
        self.assertEqual(plan["recovery_reconciliations"]["en"]["outcome"], "definitive_absence")
        validate_preflight_origin(plan, original)
        validate_recovery_reconciliations(plan)
        native_mutations = [(i, payload) for i, (method, endpoint, payload) in enumerate(self.client.calls)
                            if method == "POST" and endpoint.endswith("/articles/draft")]
        reconciliations = [i for i, (method, endpoint, _) in enumerate(self.client.calls)
                           if method == "GET" and "/operations/" in endpoint]
        self.assertEqual(len(reconciliations), 2)
        self.assertLess(max(reconciliations), native_mutations[0][0])
        self.assertEqual([payload["idempotency_key"] for _, payload in native_mutations], [keys["ja"], keys["en"]])
        for language in ("ja", "en"):
            self.assertEqual(plan["operations"][language]["readback_runtime_binding"], result["runtime_binding"])
            self.assertEqual(plan["previews"][language]["runtime_binding"], result["runtime_binding"])
        self.assertEqual(result["runtime_binding"]["publisher_code_sha256"], BOOTSTRAP_FIXED_SHA256)
        self.assertEqual(self.run_preflight()[0], 0)  # Explicit transition, then normal bound resume.
        self.assertEqual(self.client.submissions, ["ja", "en"])

    def test_bootstrap_unknown_peer_blocks_all_same_key_mutations(self):
        _, raw, keys = self.seed_bootstrap_plan()
        self.client.unresolved_keys.add(keys["en"])
        code, result = self.run_preflight(recover=True)
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "preflight_operation_needs_reconciliation")
        self.assertEqual(self.client.submissions, [])
        self.assertFalse(any(method == "POST" for method, _, _ in self.client.calls))
        plan = json.loads(self.plan.read_text())
        self.assertEqual(Path(plan["recovery"]["origin_plan_path"]).read_bytes(), raw)
        self.assertEqual(set(plan["recovery_reconciliations"]), {"ja"})

    def test_pending_bootstrap_retry_reuses_original_server_stage_id(self):
        original, _, keys = self.seed_bootstrap_plan()
        payload = original["payloads"]["ja"]
        metadata = payload["metadata"]
        existing = {"id": 61234, "language": "ja", "kind": "stage", "status": "draft", "author": 5,
                    "categories": [258], "is_accessible_for_free": True, "public_url": None,
                    "common_slug": metadata["slug"], "title": metadata["title"], "seo_title": metadata["aioseo_title"],
                    "meta_description": metadata["aioseo_description"], "content_raw": payload["html"],
                    "source_sha256": hashlib.sha256(payload["html"].encode()).hexdigest(),
                    "idempotency_key": keys["ja"], "seo_verified": False}
        self.client.records[keys["ja"]] = deepcopy(existing)
        self.client.posts[61234] = existing
        code, result = self.run_preflight(recover=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["stages"]["ja"]["post_id"], 61234)
        self.assertEqual(self.client.posts[61234]["idempotency_key"], keys["ja"])
        self.assertTrue(self.client.posts[61234]["seo_verified"])
        self.assertNotIn(60101, self.client.posts)
        self.assertEqual(self.client.submissions, ["ja", "en"])
        self.assertEqual(self.run_preflight()[0], 0)
        self.assertEqual(self.client.submissions, ["ja", "en"])

    def test_bootstrap_upgrade_requires_explicit_flag(self):
        _, raw, _ = self.seed_bootstrap_plan()
        code, result = self.run_preflight()
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "preflight_plan_binding_mismatch")
        self.assertEqual(self.plan.read_bytes(), raw)
        self.assertEqual(self.client.submissions, [])

    def test_bootstrap_flag_cannot_create_a_fresh_plan(self):
        code, result = self.run_preflight(recover=True)
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "existing_bootstrap_recovery_plan_required")
        self.assertFalse(self.plan.exists())
        self.assertEqual(self.client.submissions, [])

    def test_bootstrap_origin_snapshot_tamper_blocks_resume_without_post(self):
        self.seed_bootstrap_plan()
        self.assertEqual(self.run_preflight(recover=True)[0], 0)
        plan = json.loads(self.plan.read_text())
        snapshot = Path(plan["recovery"]["origin_plan_path"])
        snapshot.write_bytes(snapshot.read_bytes() + b" ")
        self.client.calls.clear()
        code, result = self.run_preflight()
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "preflight_origin_snapshot_changed")
        self.assertFalse(any(method == "POST" for method, _, _ in self.client.calls))

    def test_bootstrap_different_connection_payload_or_key_cannot_rebind(self):
        self.seed_bootstrap_plan()
        original = self.plan.read_bytes()
        self.assertEqual(self.run_preflight("different-connection", recover=True)[0], 2)
        self.assertEqual(self.plan.read_bytes(), original)
        value = json.loads(self.bundle.read_text())
        value["languages"]["ja"]["metadata"]["title"] += " changed"
        self.bundle.write_text(json.dumps(value))
        self.assertEqual(self.run_preflight(recover=True)[0], 2)
        self.assertEqual(self.plan.read_bytes(), original)
        self.assertEqual(self.client.submissions, [])

    def test_bootstrap_unreviewed_code_or_identity_metadata_rejected(self):
        self.seed_bootstrap_plan()
        self.assertEqual(self.run_preflight(recover=True)[0], 0)
        valid = json.loads(self.plan.read_text())
        for field, value in (("publisher_code_sha256", "f" * 64), ("wordpress_identity_id", 6),
                             ("publisher_policy_version", "other-policy"), ("site_url", "https://other.example"),
                             ("connection_revision", "other-connection")):
            with self.subTest(field=field):
                changed = deepcopy(valid)
                changed["recovery"]["identity"]["runtime_binding"][field] = value
                with self.assertRaises(ValueError):
                    preflight_identities(changed)
        changed = deepcopy(valid)
        changed["operations"]["ja"]["key"] = "f" * 64
        with self.assertRaises(ValueError):
            preflight_identities(changed)
        changed = deepcopy(valid)
        changed["recovery"]["skip_reconciliation"] = True
        with self.assertRaises(ValueError):
            preflight_identities(changed)


if __name__ == "__main__":
    unittest.main()
