"""Synthetic offline recovery tests. None of these results are live CMS readiness."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from runtime.state import CORE_GATES, GateBlocked, LeaseLost, StateError, StateStore, canonical
from runtime.workflow import EDITORIAL_EVIDENCE, NeedsCorrection, NeedsReconciliation, Workflow, bundle_digest


class SimulatedWriteError(RuntimeError):
    def __init__(self, ambiguous):
        self.ambiguous_write = ambiguous
        self.code = "synthetic_failure"
        self.status = 503


class FakeValidator:
    def validate_bundle(self, bundle):
        return {"passed": True, "errors": []}


class FakeTransport:
    supports_idempotent_creates = True
    supports_staged_updates = True

    def __init__(self):
        self.posts, self.operations, self.calls = {}, {}, []
        self.next_id = 1
        self.failure = None
        self.definitive_absence = True
        self.corrupt_language = None
        self.verify_fail_language = None
        self.malformed_publication_response = None
        self.safe_pending_operations = set()

    def stage_draft(self, language, payload, existing_id, idempotency_key):
        self.calls.append(("draft", language))
        if existing_id is not None and self.posts[existing_id]["status"] != "publish":
            raise ValueError("immutable-stage plugin existing_id requires canonical public target")
        if idempotency_key in self.operations:
            return dict(self.operations[idempotency_key])
        metadata = payload["metadata"]
        post_id = self.next_id
        self.next_id += 1
        post = {"id": post_id, "status": "draft", "language": language,
                "common_slug": metadata["slug"], "title": metadata["title"],
                "seo_title": metadata["aioseo_title"], "meta_description": metadata["aioseo_description"],
                "categories": [258], "is_accessible_for_free": True, "content_raw": payload["html"],
                "public_url": "https://sggroup.jp/" + language + "/article/news/" + metadata["slug"] + "/"}
        if self.corrupt_language == language:
            post["content_raw"] = post["content_raw"].replace("<style>", "")
        self.posts[post_id] = dict(post)
        self.operations[idempotency_key] = dict(post)
        if self.failure == ("draft_after_commit", language):
            self.failure = None
            raise SimulatedWriteError(True)
        return dict(post)

    def get_post(self, post_id):
        return dict(self.posts[post_id])

    def reconcile(self, language, common_slug, idempotency_key, existing_id, expected_payload):
        self.calls.append(("reconcile", language))
        return {"posts": [dict(self.operations[idempotency_key])] if idempotency_key in self.operations else [],
                "complete": True, "definitive_absence": self.definitive_absence,
                "safe_to_resume": idempotency_key in self.safe_pending_operations}

    def publish_post(self, post_id, idempotency_key, existing_id=None):
        language = self.posts[post_id]["language"]
        self.calls.append(("publish", language))
        if self.failure == ("publish_before_commit", language):
            self.failure = None
            raise SimulatedWriteError(False)
        if self.failure == ("publish_unknown", language):
            self.failure = None
            raise SimulatedWriteError(True)
        if self.failure == ("publish_pending", language):
            self.failure = None
            self.safe_pending_operations.add(idempotency_key)
            raise SimulatedWriteError(True)
        post = dict(self.posts[post_id])
        post["status"] = "publish"
        if existing_id:
            post["id"] = existing_id
        self.posts[post["id"]] = dict(post)
        self.operations[idempotency_key] = dict(post)
        if self.failure == ("publish_after_commit", language):
            self.failure = None
            raise SimulatedWriteError(True)
        if self.malformed_publication_response == language:
            self.malformed_publication_response = None
            return dict(post, public_url=None)
        return dict(post)

    def verify_public(self, post):
        self.calls.append(("verify", post["language"]))
        return {"passed": post["language"] != self.verify_fail_language,
                "details": "synthetic canonical/hreflang/schema/content checks"}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = [1000.0]
        self.context = "offline-test-runtime-only"
        self.prompt = self.root / "original-prompt.txt"
        self.prompt.write_text("original synthetic test contract", encoding="utf-8")
        self.binding = {"site_url": "https://sggroup.jp", "execution_context": self.context,
                        "wordpress_identity_id": 5, "connection_revision": "offline-fixture"}
        self.store = StateStore(self.root / "state.sqlite", clock=lambda: self.now[0], test_mode=True,
                                original_prompt=self.prompt, runtime_binding=self.binding)
        self.evidence = self.root / "gate-evidence.json"
        self.evidence.write_text('{"test_only":true}', encoding="utf-8")
        self.slug = "example-authority-adopts-international-policy-first-phase-2026-10-09"
        self.event = {"semantic_identity": {"actor": "example-authority", "action": "policy-first-phase", "official_id": "123"},
                      "material_facts": {"decision": "adopted", "effective_date": "2026-11-01"},
                      "headline": "Authority adopts policy", "sources": [{"url": "https://example.test/official/123"}],
                      "common_slug": self.slug, "baseline_date": "2026-10-09"}
        self.revision_id, _ = self.store.enqueue(**self.event)
        self.transport = FakeTransport()
        self.workflow = Workflow(self.store, self.transport, FakeValidator(), self.context, self.prompt)

    def tearDown(self):
        self.tmp.cleanup()

    def ready(self):
        for gate in CORE_GATES:
            path = self.root / ("gate-" + gate + ".json")
            path.write_text(canonical(self.gate_record(gate)), encoding="utf-8")
            self.store.record_gate(gate, path, self.context, 10000)
        self.store.unpause(self.context)

    def gate_record(self, name):
        m = {"authenticated": True, "can_create_draft": True, "can_publish": True, "verified_from_runtime": True,
             "http_status": 200, "identity_id": 5, "style_preserved": True, "article_preserved": True,
             "exact_content_match": True, "probe_post_id": 123, "svg_used": False,
             "browser_rendered": True, "css_effect_verified": True, "no_theme_breakage": True,
             "existing_terms_verified": True, "shared_public_slug_verified": True, "metadata_roundtrip": True,
             "single_news_article_schema": True, "free_access_verified": True, "news_category_id": 258,
             "ja_term_id": 261, "en_term_id": 262, "successful_complete_bilingual_run": True,
             "rate_limit_headroom_verified": True, "quota_required": 20, "quota_available": 100,
             "execution_timeout_seconds": 3600, "observed_total_seconds": 1800,
             "cases": {key: "passed" for key in ("dedup_cosmetic", "dedup_syndicated", "material_update", "retrieval_gap", "ambiguous_write", "ja_success_en_failure", "concurrency", "lease_expiration")},
             "unresolved_failures": 0}
        return {"schema_version": 1, "gate": name, "context": self.context, "passed": True,
                "implementation_sha256": self.store.implementation_digest(),
                "original_prompt_sha256": self.workflow.prompt_sha256, "checked_at": self.now[0],
                "valid_until": self.now[0] + 10000, "execution_mode": "simulated", "test_only": True,
                "runtime_binding": self.binding,
                "receipts": [{"tool": "unit-test", "action": "synthetic-test", "path": str(self.evidence),
                              "sha256": hashlib.sha256(self.evidence.read_bytes()).hexdigest()}], "measurements": m}

    def bundle(self, body="synthetic test content", languages_override=None):
        languages = languages_override or {}
        if languages_override is None:
            for language in ("ja", "en"):
                languages[language] = {"html": '<style>#root p{color:black}</style><article lang="' + language + '">' + body + '</article>',
                                       "metadata": {"title": "Test " + language, "aioseo_title": "Test " + language + " l SG Group",
                                                    "aioseo_description": "Test description " + language, "slug": self.slug}}
        bundle = {"languages": languages, "evidence": {}}
        digest = bundle_digest(bundle)
        for check in EDITORIAL_EVIDENCE:
            path = self.root / (check + ".json")
            record = {"passed": True, "unresolved_blockers": 0, "unresolved_major": 0,
                      "test_only": True, "execution_mode": "simulated",
                      "bundle_digest": digest, "original_prompt_sha256": self.workflow.prompt_sha256,
                      "performed_at": "2026-10-09T00:00:00Z", "execution_details": {"offline_fixture_only": True}}
            path.write_text(canonical(record), encoding="utf-8")
            bundle["evidence"][check] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        viewer = self.root / ("SGGroup_News_" + self.slug + "_All_Copy_Viewer.html")
        viewer.write_text("synthetic viewer fixture", encoding="utf-8")
        bundle["viewer"] = {"path": str(viewer), "sha256": hashlib.sha256(viewer.read_bytes()).hexdigest()}
        return bundle

    def test_initial_pause_and_missing_gates_prevent_every_mutation(self):
        with self.assertRaises(GateBlocked):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.assertEqual(self.transport.calls, [])
        with self.assertRaises(GateBlocked):
            self.store.unpause(self.context)

    def test_production_rejects_boolean_fixture_and_simulated_gate_evidence(self):
        production = StateStore(self.root / "production.sqlite", clock=lambda: self.now[0], original_prompt=self.prompt, runtime_binding=self.binding)
        with self.assertRaises(GateBlocked):
            production.record_gate("runtime_authentication", self.evidence, self.context, 1000)
        path = self.root / "simulated-gate.json"
        path.write_text(canonical(self.gate_record("runtime_authentication")), encoding="utf-8")
        with self.assertRaises(GateBlocked):
            production.record_gate("runtime_authentication", path, self.context, 1000)

    def test_gate_identity_and_nonfinite_capacity_cannot_pass(self):
        record = self.gate_record("runtime_authentication")
        record["runtime_binding"] = dict(self.binding, wordpress_identity_id=1)
        path = self.root / "mismatch-gate.json"
        path.write_text(canonical(record), encoding="utf-8")
        with self.assertRaises(GateBlocked):
            self.store.record_gate("runtime_authentication", path, self.context, 1000)
        for key in ("quota_available", "observed_total_seconds"):
            record = self.gate_record("execution_capacity")
            record["measurements"][key] = float("nan")
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaises(GateBlocked):
                self.store.record_gate("execution_capacity", path, self.context, 1000)
        record = self.gate_record("runtime_authentication")
        record.update(execution_mode="live", test_only=False)
        record["measurements"]["http_status"] = 401
        path.write_text(canonical(record), encoding="utf-8")
        with self.assertRaises(GateBlocked):
            self.store.record_gate("runtime_authentication", path, self.context, 1000)

    def test_cosmetic_and_syndicated_headlines_deduplicate_material_facts(self):
        duplicate = dict(self.event)
        duplicate["headline"] = "A completely different syndicated headline"
        duplicate["sources"] = [{"url": "https://example.test/syndicated"}]
        self.assertEqual(self.store.enqueue(**duplicate), (self.revision_id, False))
        development = dict(self.event, material_facts={"decision": "adopted", "effective_date": "2026-12-01"})
        new_id, new = self.store.enqueue(**development)
        self.assertTrue(new)
        self.assertNotEqual(new_id, self.revision_id)
        self.assertEqual(self.store.revision(new_id)["common_slug"], self.slug)

    def test_material_update_cannot_change_slug_or_baseline(self):
        update = dict(self.event, common_slug="other-policy-2026-10-10", baseline_date="2026-10-10")
        with self.assertRaises(StateError):
            self.store.enqueue(**update)

    def test_queue_has_no_arbitrary_event_cap(self):
        for index in range(151):
            event = dict(self.event, semantic_identity={"official_id": str(index)},
                         common_slug="example-authority-policy-" + str(index) + "-2026-10-09")
            self.store.enqueue(**event)
        self.assertEqual(len(self.store.status(self.context)["queue"]), 152)

    def test_retrieval_failure_keeps_gap_until_backfill(self):
        self.store.register_source("official", 100)
        self.store.record_scan("official", 100, 200, False, "timeout")
        self.store.record_scan("official", 200, 300, True)
        source = self.store.status(self.context)["sources"][0]
        self.assertEqual(source["watermark"], 100)
        self.assertEqual(source["retrieval_gap"], 1)
        self.store.record_scan("official", 100, 200, True)
        self.assertEqual(self.store.status(self.context)["sources"][0]["watermark"], 300)

    def test_ingest_and_watermark_commit_atomically(self):
        self.store.register_source("official", 100)
        good = dict(self.event, semantic_identity={"official_id": "new"}, common_slug="new-international-policy-2026-10-09")
        conflicting = dict(self.event, common_slug="different-slug-2026-10-09")
        with self.assertRaises(StateError):
            self.store.ingest_scan("official", 100, 200, [good, conflicting], True)
        status = self.store.status(self.context)
        self.assertEqual(len(status["queue"]), 1)
        self.assertEqual(status["sources"][0]["watermark"], 100)
        result = self.store.ingest_scan("official", 100, 200, [good], True)
        self.assertTrue(result[0][1])
        self.assertEqual(self.store.status(self.context)["sources"][0]["watermark"], 200)

    def test_partial_retrieval_keeps_verified_discoveries_and_gap(self):
        self.store.register_source("official", 100)
        good = dict(self.event, semantic_identity={"official_id": "new"}, common_slug="new-international-policy-2026-10-09")
        result = self.store.ingest_scan("official", 100, 200, [good], False, "page two unavailable")
        self.assertTrue(result[0][1])
        source = self.store.status(self.context)["sources"][0]
        self.assertEqual(source["watermark"], 100)
        self.assertTrue(source["retrieval_gap"])

    def test_expires_and_stale_release_cannot_unlock_new_owner(self):
        old = self.store.acquire(self.revision_id, "old", ttl_seconds=10)
        self.now[0] += 11
        new = self.store.acquire(self.revision_id, "new", ttl_seconds=10)
        self.assertGreater(new["fence"], old["fence"])
        self.store.release(old)
        with self.assertRaises(LeaseLost):
            with self.store.fenced(old):
                pass
        with self.assertRaises(LeaseLost):
            self.store.acquire(self.revision_id, "third")
        with self.store.fenced(new):
            pass

    def test_concurrent_claims_have_exactly_one_owner(self):
        barrier = threading.Barrier(8)
        def claim(index):
            barrier.wait()
            try:
                return self.store.acquire(self.revision_id, str(index))
            except LeaseLost:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_external_mutation_transaction_blocks_expired_takeover(self):
        old = self.store.acquire(self.revision_id, "old", 10)
        started, finished = threading.Event(), threading.Event()
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            with self.store.fenced(old, renew_seconds=10):
                self.now[0] += 11
                def takeover():
                    started.set()
                    result = self.store.acquire(self.revision_id, "new")
                    finished.set()
                    return result
                future = pool.submit(takeover)
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.1))
            new = future.result(timeout=2)
            self.assertEqual(new["owner"], "new")
        finally:
            pool.shutdown(wait=True)

    def test_both_drafts_are_validated_before_first_publication(self):
        self.ready()
        self.transport.corrupt_language = "en"
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.assertFalse(any(stage == "publish" for stage, _ in self.transport.calls))
        self.assertEqual(self.store.revision(self.revision_id)["retry_stage"], "draft_en")

    def test_success_and_completed_event_dedup(self):
        self.ready()
        result = self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(self.transport.posts), 2)
        self.assertEqual(self.store.enqueue(**self.event), (self.revision_id, False))
        with self.assertRaises(StateError):
            self.workflow.execute(self.revision_id, "worker")
        self.assertEqual(sum(stage == "publish" for stage, _ in self.transport.calls), 2)

    def test_ambiguous_draft_response_adopts_existing_post(self):
        self.ready()
        self.transport.failure = ("draft_after_commit", "ja")
        with self.assertRaises(SimulatedWriteError):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.workflow.execute(self.revision_id, "recovery")
        self.assertEqual(len(self.transport.posts), 2)
        self.assertEqual(self.transport.calls.count(("draft", "ja")), 1)
        self.assertIn(("reconcile", "ja"), self.transport.calls)

    def test_ja_success_en_failure_retries_only_missing_publication(self):
        self.ready()
        self.transport.failure = ("publish_before_commit", "en")
        with self.assertRaises(SimulatedWriteError):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.assertTrue(self.workflow._checkpoint(self.revision_id, "ja")["published"])
        self.assertFalse(self.workflow._checkpoint(self.revision_id, "en")["published"])
        self.workflow.execute(self.revision_id, "recovery")
        self.assertEqual(self.transport.calls.count(("publish", "ja")), 1)
        self.assertEqual(self.transport.calls.count(("publish", "en")), 2)
        self.assertEqual(len(self.transport.posts), 2)

    def test_ambiguous_publish_reconciles_without_republishing(self):
        self.ready()
        self.transport.failure = ("publish_after_commit", "ja")
        with self.assertRaises(SimulatedWriteError):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.workflow.execute(self.revision_id, "recovery")
        self.assertEqual(self.transport.calls.count(("publish", "ja")), 1)

    def test_incomplete_publication_response_is_reconciled_instead_of_cached_as_success(self):
        self.ready()
        self.transport.malformed_publication_response = "ja"
        with self.assertRaises(ValueError):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.assertFalse(self.workflow._checkpoint(self.revision_id, "ja")["published"])
        self.workflow.execute(self.revision_id, "recovery")
        self.assertEqual(self.transport.calls.count(("publish", "ja")), 1)
        self.assertEqual(self.store.revision(self.revision_id)["state"], "complete")

    def test_unproven_absence_blocks_blind_retry(self):
        self.ready()
        self.transport.failure = ("publish_unknown", "ja")
        self.transport.definitive_absence = False
        with self.assertRaises(SimulatedWriteError):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        with self.assertRaises(NeedsReconciliation):
            self.workflow.execute(self.revision_id, "recovery")
        self.assertEqual(self.transport.calls.count(("publish", "ja")), 1)

    def test_server_known_pending_operation_can_resume_same_key_without_claiming_absence(self):
        self.ready()
        self.transport.failure = ("publish_pending", "ja")
        self.transport.definitive_absence = False
        with self.assertRaises(SimulatedWriteError):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.workflow.execute(self.revision_id, "recovery")
        self.assertEqual(self.transport.calls.count(("publish", "ja")), 2)
        self.assertEqual(self.store.revision(self.revision_id)["state"], "complete")
        self.assertEqual(len(self.transport.posts), 2)

    def test_updated_article_keeps_ids_urls_and_requires_staging(self):
        self.ready()
        self.workflow.execute(self.revision_id, "worker", self.bundle())
        first = {lang: self.workflow._target(self.store.revision(self.revision_id)["event_key"], lang) for lang in ("ja", "en")}
        update = dict(self.event, material_facts={"decision": "adopted", "effective_date": "2026-12-01"})
        revision_id, _ = self.store.enqueue(**update)
        self.transport.supports_staged_updates = False
        with self.assertRaises(GateBlocked):
            self.workflow.execute(revision_id, "worker", self.bundle("new substantive facts"))
        self.transport.supports_staged_updates = True
        self.workflow.execute(revision_id, "worker")
        for lang in ("ja", "en"):
            current = self.workflow._target(self.store.revision(revision_id)["event_key"], lang)
            self.assertEqual(current, first[lang])
            self.assertIn("new substantive facts", self.transport.get_post(current["post_id"])["content_raw"])

    def test_pause_expired_or_tampered_gate_stops_resume(self):
        self.ready()
        self.store.pause("maintenance")
        with self.assertRaises(GateBlocked):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.store.unpause(self.context)
        self.evidence.write_text("changed", encoding="utf-8")
        with self.assertRaises(GateBlocked):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.ready()
        self.now[0] += 10001
        with self.assertRaises(GateBlocked):
            self.workflow.execute(self.revision_id, "worker", self.bundle())

    def test_qa_content_binding_and_missing_evidence_fail_closed(self):
        self.ready()
        bundle = self.bundle()
        bundle["languages"]["en"]["html"] += "changed after QA"
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "worker", bundle)
        self.assertEqual(self.transport.calls, [])

    def test_actual_structural_validator_receives_language_map_and_rejects_short_fixture(self):
        from runtime import article_qa
        self.ready()
        bundle = self.bundle()
        self.assertEqual(bundle_digest(bundle), article_qa.bundle_digest(bundle["languages"]))
        actual = Workflow(self.store, self.transport, article_qa, self.context, self.prompt)
        with self.assertRaises(NeedsCorrection):
            actual.execute(self.revision_id, "worker", bundle)
        self.assertEqual(self.transport.calls, [])

    def test_actual_structural_validator_and_workflow_integrate_on_artificial_valid_fixture(self):
        from runtime import article_qa
        from runtime.tests.test_article_qa import synthetic_bundle
        self.ready()
        languages = synthetic_bundle()
        self.slug = languages["ja"]["metadata"]["slug"]
        event = dict(self.event, semantic_identity={"official_id": "integration-fixture-only"}, common_slug=self.slug)
        revision_id, _ = self.store.enqueue(**event)
        actual = Workflow(self.store, self.transport, article_qa, self.context, self.prompt)
        result = actual.execute(revision_id, "integration-test-only", self.bundle(languages_override=languages))
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(self.transport.posts), 2)

    def test_unpublished_editorial_repair_revalidates_both_drafts(self):
        self.ready()
        self.transport.corrupt_language = "en"
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.transport.corrupt_language = None
        result = self.workflow.execute(self.revision_id, "repair", self.bundle("new repaired content with full audits repeated"))
        self.assertEqual(result["state"], "complete")
        first_publish = next(index for index, (stage, _) in enumerate(self.transport.calls) if stage == "publish")
        self.assertEqual(sum(stage == "draft" for stage, _ in self.transport.calls[:first_publish]), 4)
        for language in ("ja", "en"):
            post = self.transport.get_post(result["languages"][language]["post_id"])
            self.assertIn("new repaired content", post["content_raw"])

    def test_queue_native_producer_repairs_unpublished_failure_under_owned_lease(self):
        self.ready()
        self.transport.corrupt_language = "en"
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.transport.corrupt_language = None
        supplied = []
        def native_producer(revision, renew_lease):
            self.assertEqual(revision["state"], "needs_correction")
            self.assertEqual(revision["retry_stage"], "draft_en")
            renew_lease()
            supplied.append(revision["id"])
            return self.bundle("native repair with all evidence rebuilt")
        result = self.workflow.run_pending("native-repair-worker", native_producer)
        self.assertEqual(supplied, [self.revision_id])
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(result["failures"], [])
        self.assertEqual(self.store.revision(self.revision_id)["state"], "complete")

    def test_live_failure_does_not_mark_pair_complete_or_republish_ja(self):
        self.ready()
        self.transport.verify_fail_language = "ja"
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        self.assertNotEqual(self.store.revision(self.revision_id)["state"], "complete")
        self.transport.verify_fail_language = None
        self.workflow.execute(self.revision_id, "worker")
        self.assertEqual(self.transport.calls.count(("publish", "ja")), 1)

    def test_previously_verified_public_side_is_rechecked_after_peer_live_failure(self):
        self.ready()
        self.transport.verify_fail_language = "en"
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        checkpoint = self.workflow._checkpoint(self.revision_id, "ja")
        self.assertTrue(checkpoint["public_verified"])
        self.transport.posts[checkpoint["public_post_id"]]["content_raw"] = "changed after prior live verification"
        self.transport.verify_fail_language = None
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "recovery")
        self.assertNotEqual(self.store.revision(self.revision_id)["state"], "complete")
        self.assertFalse(self.workflow._checkpoint(self.revision_id, "ja")["public_verified"])
        self.assertEqual(self.transport.calls.count(("publish", "ja")), 1)

    def test_deleted_previously_verified_side_cannot_complete_on_peer_retry(self):
        self.ready()
        self.transport.verify_fail_language = "en"
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        checkpoint = self.workflow._checkpoint(self.revision_id, "ja")
        self.assertTrue(checkpoint["public_verified"])
        del self.transport.posts[checkpoint["public_post_id"]]
        self.transport.verify_fail_language = None
        with self.assertRaises(KeyError):
            self.workflow.execute(self.revision_id, "recovery")
        self.assertNotEqual(self.store.revision(self.revision_id)["state"], "complete")
        self.assertFalse(self.workflow._checkpoint(self.revision_id, "ja")["public_verified"])
        self.assertEqual(self.transport.calls.count(("publish", "ja")), 1)

    def test_public_content_defect_can_be_corrected_without_falsely_completing_old_revision(self):
        self.ready()
        self.transport.verify_fail_language = "ja"
        with self.assertRaises(NeedsCorrection):
            self.workflow.execute(self.revision_id, "worker", self.bundle())
        event_key = self.store.revision(self.revision_id)["event_key"]
        old_public_id = self.workflow._target(event_key, "ja")["post_id"]
        correction_id = self.store.enqueue_correction(self.revision_id, self.event["material_facts"],
                                                       self.event["headline"], self.event["sources"],
                                                       "restore source-supported figure and canonical rendering")
        self.assertEqual(self.store.revision(self.revision_id)["state"], "superseded_by_correction")
        self.assertTrue(self.workflow._checkpoint(self.revision_id, "ja")["published"])
        self.transport.verify_fail_language = None
        result = self.workflow.execute(correction_id, "repair", self.bundle("corrected public artifact"))
        self.assertEqual(result["state"], "complete")
        self.assertEqual(self.workflow._target(event_key, "ja")["post_id"], old_public_id)
        self.assertEqual(self.store.revision(correction_id)["supersedes_revision_id"], self.revision_id)
        with self.assertRaises(StateError):
            self.workflow.execute(self.revision_id, "stale-worker")


if __name__ == "__main__":
    unittest.main()
