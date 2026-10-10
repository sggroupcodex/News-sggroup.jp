"""Offline schema/recovery fixtures; no test proves live article readiness."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from runtime.evidence import (LANDSCAPES, PARITY_DIMENSIONS, STATIC_CHECKS, VIEWPOINTS,
                              WIDTHS, validate_article_evidence)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1800000000.0
        self.digest, self.prompt, self.viewer = "a" * 64, "b" * 64, "c" * 64
        self.binding = {"site_url": "https://sggroup.jp", "wordpress_identity_id": 5,
                        "execution_context": "schema-fixture", "connection_revision": "schema-fixture"}

    def tearDown(self):
        self.tmp.cleanup()

    def receipt(self, kind, report, actor="reviewer"):
        if kind not in ("task_identity", "structural_audit", "browser_qa"):
            report = {**report, "bundle_digest": self.digest, "original_prompt_sha256": self.prompt}
        path = self.root / (kind + ".json")
        path.write_text(json.dumps(report), encoding="utf-8")
        return {"kind": kind, "tool": "offline schema fixture", "action": "test schema only",
                "actor_id": actor, "proof_id": kind + "-proof", "performed_at": self.now,
                "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def record(self, check, receipts):
        machine = check in ("browser_qa", "clipboard_qa")
        return {"schema_version": 1, "check": check, "test_only": False, "passed": True,
                "bundle_digest": self.digest, "original_prompt_sha256": self.prompt,
                "runtime_binding": self.binding, "unresolved_blockers": 0, "unresolved_major": 0,
                "performed_at": self.now, "valid_until": self.now + 1000,
                "evidence_kind": "machine_receipt" if machine else "operator_attested",
                "execution_mode": "actual_browser" if machine else "operator_attested",
                "author_actor_id": "author", "producer_actor_id": "reviewer",
                "reviewer_actor_id": "reviewer", "validation_actor_ids": ["validator"],
                "author_proof_ids": ["author-proof"], "receipts": receipts}

    def validate(self, check, record, test_mode=False):
        validate_article_evidence(check, record, self.digest, self.prompt, self.binding,
                                  test_mode, lambda: self.now, viewer_sha256=self.viewer)

    def parity(self):
        report = {"reviewed_dimensions": sorted(PARITY_DIMENSIONS), "omissions": [],
                  "unresolved_mismatches": [], "mapped_units": [{"id": "decision", "ja": "決定の説明", "en": "decision explanation"}]}
        return self.record("bilingual_semantic_parity", [self.receipt("bilingual_semantic_parity", report)])

    def identity(self, actor="reviewer"):
        return self.receipt("task_identity", {"source": "native_task_receipt", "actor_id": actor,
                                              "proof_id": "task_identity-proof", "task_status": "completed"}, actor)

    def independent(self):
        m = {"section_keys": ["decision"], "figure_keys": [str(i) for i in range(6)],
             "figure_types": ["timeline", "causal", "comparison", "matrix", "timeline", "causal"],
             "faq_keys": [str(i) for i in range(6)], "source_keys": ["official"], "eligible_body_characters": 10000}
        structural = {"passed": True, "errors": [], "bundle_digest": self.digest, "metrics": {"ja": m, "en": m}}
        review = {"checks": [{"id": key, "result": "passed", "observations": "Observed the corresponding exact fixture unit"} for key in STATIC_CHECKS]}
        return self.record("independent_audit", [self.identity(), self.receipt("structural_audit", structural, "validator"),
                                                 self.receipt("independent_review", review)])

    def browser(self, check="browser_qa"):
        measurements = []
        for lang in ("ja", "en"):
            for width, height in [(w, 900) for w in WIDTHS] + list(LANDSCAPES):
                for constrained in (False, True):
                    measurements.append({"language": lang, "width": width, "height": height,
                                         "constrained_host": constrained, "errors": [],
                                         "root": {"left": 0, "right": width, "width": width},
                                         "rects": [{"width": width}], "viewportWidth": width,
                                         "documentScrollWidth": width, "eligibleBodyCharacters": 10000})
                measurements.append({"surface": "viewer", "language": lang, "width": width, "height": height, "errors": []})
        tests = []
        for route in ("normal", "write-unavailable-fallback", "write-rejected-fallback", "both-denied"):
            for lang in ("ja", "en"):
                for key in ("html", "title", "aioseo_title", "aioseo_description", "slug"):
                    denied = route == "both-denied"
                    tests.append({"route": route, "target": lang + ":" + key, "pass": True, "clicks": 1,
                                  "focus_restored": True, "temporary_nodes_removed": True,
                                  "exact_clipboard_match": not denied, "state": "error" if denied else "success"})
        tests.extend({"route": "consecutive-tab-change", "target": target, "pass": True,
                      "exact_clipboard_match": True, "clicks": 1} for target in ("ja:title", "en:title", "ja:html"))
        screenshot = self.root / "screenshot.png"
        screenshot.write_bytes(b"offline schema fixture, not a rendered image")
        report = {"passed": True, "errors": [], "bundle_digest": self.digest, "viewer_sha256": self.viewer,
                  "browser": "actual headless Chromium via Playwright", "chromium_path": "/fixture/chromium",
                  "viewport_widths": list(WIDTHS), "landscapes": [list(p) for p in LANDSCAPES], "measurements": measurements,
                  "max_margin_difference_px": 0, "max_reading_edge_difference_px": 0,
                  "private_screenshots": [str(screenshot)], "clipboard": {"passed": True, "errors": [], "tests": tests}}
        receipt = self.receipt("browser_qa", report)
        receipt["screenshot_sha256"] = {str(screenshot): hashlib.sha256(screenshot.read_bytes()).hexdigest()}
        return self.record(check, [receipt])

    def alter_receipt(self, record, kind, modify):
        receipt = next(r for r in record["receipts"] if r["kind"] == kind)
        path = Path(receipt["path"])
        report = json.loads(path.read_text())
        modify(report)
        path.write_text(json.dumps(report), encoding="utf-8")
        receipt["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    def test_complete_operator_attestation_requires_correspondence(self):
        record = self.parity()
        self.validate("bilingual_semantic_parity", record)
        self.alter_receipt(record, "bilingual_semantic_parity", lambda p: p.update(mapped_units=[]))
        with self.assertRaises(ValueError): self.validate("bilingual_semantic_parity", record)

    def test_old_semantic_report_cannot_be_rewrapped_for_another_article(self):
        record = self.parity()
        self.alter_receipt(record, "bilingual_semantic_parity", lambda p: p.update(bundle_digest="d" * 64))
        with self.assertRaises(ValueError): self.validate("bilingual_semantic_parity", record)

    def test_minimal_simulation_only_accepted_by_explicit_test_store(self):
        record = {"test_only": True, "execution_mode": "simulated", "passed": True,
                  "bundle_digest": self.digest, "original_prompt_sha256": self.prompt}
        self.validate("research_fact_check", record, test_mode=True)
        with self.assertRaises(ValueError): self.validate("research_fact_check", record)

    def test_wrong_context_content_prompt_and_bool_only_record_fail(self):
        for field, value in (("runtime_binding", {}), ("bundle_digest", "d" * 64),
                             ("original_prompt_sha256", "d" * 64), ("test_only", True)):
            record = self.parity(); record[field] = value
            with self.assertRaises(ValueError): self.validate("bilingual_semantic_parity", record)
        with self.assertRaises(ValueError): self.validate("research_fact_check", {"passed": True})

    def test_stale_zero_future_and_nonfinite_times_fail(self):
        for value in (0, self.now - 86401, self.now + 6, float("nan"), float("inf"), True):
            record = self.parity(); record["performed_at"] = value
            with self.assertRaises(ValueError): self.validate("bilingual_semantic_parity", record)

    def test_malformed_nested_schema_and_fake_visible_count_fail_closed(self):
        record = self.parity()
        self.alter_receipt(record, "bilingual_semantic_parity", lambda p: p.update(reviewed_dimensions=None))
        with self.assertRaises(ValueError): self.validate("bilingual_semantic_parity", record)
        record = self.browser()
        self.alter_receipt(record, "browser_qa", lambda p: p["measurements"][0].update(eligibleBodyCharacters=9999))
        with self.assertRaises(ValueError): self.validate("browser_qa", record)

    def test_changed_missing_and_nonfinite_receipts_fail(self):
        record = self.parity()
        Path(record["receipts"][0]["path"]).write_text("changed")
        with self.assertRaises(ValueError): self.validate("bilingual_semantic_parity", record)
        record = self.parity()
        Path(record["receipts"][0]["path"]).unlink()
        with self.assertRaises(ValueError): self.validate("bilingual_semantic_parity", record)
        record = self.parity()
        self.alter_receipt(record, "bilingual_semantic_parity", lambda p: p.update(nan=float("nan")))
        with self.assertRaises(ValueError): self.validate("bilingual_semantic_parity", record)

    def test_independent_audit_validates_measured_report_and_separate_task(self):
        record = self.independent(); self.validate("independent_audit", record)
        self.alter_receipt(record, "structural_audit", lambda p: p["metrics"]["ja"].update(eligible_body_characters=9999))
        with self.assertRaises(ValueError): self.validate("independent_audit", record)

    def test_author_cannot_be_independent_reviewer_or_reuse_proof(self):
        for field, value in (("producer_actor_id", "author"), ("reviewer_actor_id", "author"),
                             ("author_proof_ids", ["independent_review-proof"]), ("validation_actor_ids", ["reviewer"])):
            record = self.independent(); record[field] = value
            with self.assertRaises(ValueError): self.validate("independent_audit", record)
        record = self.independent(); record["receipts"] = [r for r in record["receipts"] if r["kind"] != "task_identity"]
        with self.assertRaises(ValueError): self.validate("independent_audit", record)

    def test_all_prompt_viewpoints_require_observations(self):
        report = {"checks": [{"id": key, "result": "passed", "observations": "Concrete fixture observation"} for key in VIEWPOINTS]}
        record = self.record("viewpoint_reviews", [self.identity(), self.receipt("viewpoint_reviews", report)])
        self.validate("viewpoint_reviews", record)
        self.alter_receipt(record, "viewpoint_reviews", lambda p: p["checks"].pop())
        with self.assertRaises(ValueError): self.validate("viewpoint_reviews", record)

    def test_browser_and_clipboard_accept_complete_schema_matrix(self):
        for check in ("browser_qa", "clipboard_qa"):
            self.validate(check, self.browser(check))

    def test_missing_measurements_wrong_viewer_screenshot_change_fail(self):
        record = self.browser()
        self.alter_receipt(record, "browser_qa", lambda p: p["measurements"].pop())
        with self.assertRaises(ValueError): self.validate("browser_qa", record)
        record = self.browser()
        self.alter_receipt(record, "browser_qa", lambda p: p.update(viewer_sha256="d" * 64))
        with self.assertRaises(ValueError): self.validate("browser_qa", record)
        record = self.browser(); (self.root / "screenshot.png").write_bytes(b"changed")
        with self.assertRaises(ValueError): self.validate("browser_qa", record)

    def test_missing_clipboard_observation_or_false_success_fail(self):
        record = self.browser("clipboard_qa")
        self.alter_receipt(record, "browser_qa", lambda p: p["clipboard"]["tests"].pop(0))
        with self.assertRaises(ValueError): self.validate("clipboard_qa", record)
        record = self.browser("clipboard_qa")
        self.alter_receipt(record, "browser_qa", lambda p: p["clipboard"]["tests"][30].update(state="success"))
        with self.assertRaises(ValueError): self.validate("clipboard_qa", record)

    def test_fact_check_requires_retrieved_content_for_supported_claims(self):
        source_receipt = self.receipt("source", {"url": "https://example.test/announcement", "retrieval_status": "retrieved", "content": "Original source fixture"})
        report = {"sources": [{"key": "official", "url": "https://example.test/announcement", "retrieved_at": self.now,
                               "retrieval_status": "retrieved", "primary": True,
                               "receipt_path": source_receipt["path"], "receipt_sha256": source_receipt["sha256"]}],
                  "claims": [{"claim_id": "decision", "observation": "Specific source supports the decision", "classification": "fact", "source_keys": ["official"]}]}
        record = self.record("research_fact_check", [self.receipt("research_fact_check", report)])
        self.validate("research_fact_check", record)
        self.alter_receipt(record, "research_fact_check", lambda p: p["claims"][0].update(source_keys=["invented"]))
        with self.assertRaises(ValueError): self.validate("research_fact_check", record)

    def test_internal_link_audit_requires_observed_same_language_canonical(self):
        links = []
        for lang in ("ja", "en"):
            url = "https://sggroup.jp/" + lang + "/article/example/"
            observed = {"url": url, "http_status": 200, "final_url": url, "canonical": url, "language": lang, "content_sha256": "d" * 64}
            fetched = self.receipt("link-" + lang, observed)
            links.append({**observed, "checked_at": self.now, "relevant": True, "relevance_reason": "Specific fixture background",
                          "receipt_path": fetched["path"], "receipt_sha256": fetched["sha256"]})
        record = self.record("internal_links", [self.receipt("internal_links", {"links": links, "unresolved_errors": []})])
        self.validate("internal_links", record)
        self.alter_receipt(record, "internal_links", lambda p: p["links"][0].update(canonical=links[1]["canonical"]))
        with self.assertRaises(ValueError): self.validate("internal_links", record)

    def test_slug_checks_retain_permission_limits_and_fail_on_unresolved_collision(self):
        report = {"checked_scopes": [{"scope": scope, "result": "unavailable" if scope == "private" else "complete", "details": "Actual fixture result; unavailable permission is retained"}
                                     for scope in ("published", "draft", "pending", "private", "sitemap", "site_search")],
                  "unresolved_collisions": [], "baseline_date_basis": "Official announcement date in source"}
        record = self.record("slug_cms_collision", [self.receipt("slug_cms_collision", report)])
        self.validate("slug_cms_collision", record)
        self.alter_receipt(record, "slug_cms_collision", lambda p: p.update(unresolved_collisions=["existing article"]))
        with self.assertRaises(ValueError): self.validate("slug_cms_collision", record)


if __name__ == "__main__":
    unittest.main()
