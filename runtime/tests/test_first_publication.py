"""Offline controlled-first recovery tests; these do not attest live publication."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import unittest

from runtime.evidence import WIDTHS, LANDSCAPES
from runtime.state import FIRST_PREPUBLICATION_GATES, GateBlocked, canonical, fingerprint
from runtime.workflow import NeedsCorrection, NeedsReconciliation, bundle_digest
from runtime.tests.test_workflow import FakeTransport, SimulatedWriteError
from runtime.tests import test_workflow as fixtures


class FirstTransport(FakeTransport):
    def stage_draft(self, *args, **kwargs):
        raise AssertionError("controlled first publication must never create another draft")

    def publish_post(self, post_id, idempotency_key, existing_id=None):
        stage = self.posts[post_id]
        language = stage["language"]
        self.calls.append(("publish", language))
        if idempotency_key in self.operations:
            return dict(self.operations[idempotency_key])
        if self.failure in (("publish_before_commit", language), ("publish_unknown", language)):
            ambiguous = self.failure[0] == "publish_unknown"
            self.failure = None
            raise SimulatedWriteError(ambiguous)
        post = dict(stage, id=self.next_id, status="publish", kind="canonical",
                    idempotency_key="canonical:" + idempotency_key,
                    public_url="https://sggroup.jp/" + language + "/article/news/" + stage["common_slug"] + "/")
        self.next_id += 1
        self.posts[post["id"]] = dict(post)
        self.operations[idempotency_key] = dict(post)
        if self.failure == ("publish_after_commit", language):
            self.failure = None
            raise SimulatedWriteError(True)
        return post

    def authorize_publication(self, **kwargs):
        self.calls.append(("authorize", tuple(sorted(kwargs["stage_ids"].values()))))


class FirstPublicationTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    gate_record = fixtures.WorkflowTests.gate_record
    bundle = fixtures.WorkflowTests.bundle

    def private(self, name, value, raw=False):
        path = self.root / name
        path.write_text(value if raw else canonical(value), encoding="utf-8")
        path.chmod(0o600)
        return path

    def prepare(self):
        self.article = self.bundle()
        self.transport = FirstTransport()
        self.workflow.transport = self.transport
        for gate in FIRST_PREPUBLICATION_GATES:
            path = self.private("gate-" + gate + ".json", self.gate_record(gate))
            self.store.record_gate(gate, path, self.context, 10000)
        identity = {"schema_version": 1, "purpose": "controlled_private_bilingual_draft_preflight",
                    "runtime_binding": self.binding, "bundle_digest": bundle_digest(self.article), "common_slug": self.slug}
        self.plan = {**identity, "payloads": {}, "operations": {}, "previews": {},
                     "state": "private_drafts_verified", "publication_performed": False, "production_ready": False,
                     "structural_validation": {"passed": True, "errors": []},
                     "completed_at": datetime.fromtimestamp(self.now[0], timezone.utc).isoformat()}
        for lang, entry in self.article["languages"].items():
            payload = {"language": lang, "html": entry["html"], "metadata": entry["metadata"],
                       "categories": [258], "is_accessible_for_free": True, "status": "draft"}
            key = fingerprint({**identity, "language": lang, "operation": "draft", "payload": payload})
            source = hashlib.sha256(entry["html"].encode()).hexdigest()
            post = FakeTransport.stage_draft(self.transport, lang, payload, None, key)
            post.update(author=5, kind="stage", public_url=None, idempotency_key=key, source_sha256=source, seo_verified=True)
            self.transport.posts[post["id"]] = post
            self.plan["payloads"][lang] = payload
            self.plan["operations"][lang] = {"key": key, "status": "saved", "post_id": post["id"],
                                             "receipt": post, "saved_readback": post, "source_sha256": source,
                                             "metadata_sha256": fingerprint(entry["metadata"])}
            snapshot = self.private("snapshot-" + lang + ".html", "<html>" + entry["html"] + "</html>", raw=True)
            report = {"passed": True, "errors": [], "execution_mode": "live", "test_only": False,
                      "actual_authenticated_preview": True, "actual_live_browser": False, "wordpress_identity_id": 5,
                      "verification_context": "authenticated_saved_theme_preview", "runtime_binding": self.binding,
                      "post_id": post["id"], "source_sha256": source,
                      "runs": [{"width": w, "height": h, "measurement": {"errors": []}}
                               for w, h in [(w, 900) for w in WIDTHS] + list(LANDSCAPES)],
                      "saved_theme_document_path": str(snapshot),
                      "saved_theme_document_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}
            report_path = self.private("preview-" + lang + ".json", report)
            self.plan["previews"][lang] = {**report, "preview_report_path": str(report_path),
                                           "preview_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()}
        self.transport.calls.clear()
        self.plan_path = self.private("preflight.json", self.plan)
        action = self.private("actual-user-action.json", {"source": "user_message", "instruction": "公開を開始すること"})
        quota = self.private("actual-quota.json", {"quota_available": 80, "offline_fixture_only": True})
        revision = self.store.revision(self.revision_id)
        common = {"schema_version": 1, "context": self.context, "runtime_binding": self.binding,
                  "implementation_sha256": self.store.implementation_digest(), "original_prompt_sha256": self.workflow.prompt_sha256,
                  "revision_id": self.revision_id, "event_key": revision["event_key"], "common_slug": self.slug,
                  "bundle_digest": bundle_digest(self.article), "checked_at": self.now[0], "valid_until": self.now[0] + 600,
                  "execution_mode": "live", "test_only": False}
        self.auth = {**common, "purpose": "user_authorized_controlled_first_publication", "source": "user_message",
                     "user_instruction": "公開を開始すること", "receipts": [{"tool": "user_message", "action": "authorize_first_pair",
                         "path": str(action), "sha256": hashlib.sha256(action.read_bytes()).hexdigest()}]}
        self.capacity = {**common, "purpose": "controlled_first_publication_capacity",
                         "measurements": {"quota_available": 80, "estimated_remaining_calls": 30, "recovery_headroom": 10},
                         "receipts": [{"tool": "offline-fixture", "action": "read_remaining_quota", "path": str(quota),
                                       "sha256": hashlib.sha256(quota.read_bytes()).hexdigest()}]}
        self.auth_path = self.private("authorization.json", self.auth)
        self.capacity_path = self.private("capacity.json", self.capacity)

    def record(self):
        return self.store.record_first_publication_permit(self.revision_id, bundle_digest(self.article),
                                                          self.plan_path, self.auth_path, self.capacity_path, self.context)

    def refreshed_capacity(self, estimate=30):
        self.now[0] += 1
        self.capacity.update(checked_at=self.now[0], valid_until=self.now[0] + 600)
        self.capacity["measurements"]["estimated_remaining_calls"] = estimate
        self.private("capacity.json", self.capacity)
        self.record()

    def test_complete_first_pair_reuses_stages_and_leaves_hourly_and_two_gates_blocked(self):
        self.prepare(); self.record()
        result = self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.assertEqual(result["state"], "complete")
        self.assertEqual([c for c in self.transport.calls if c[0] == "publish"], [("publish", "ja"), ("publish", "en")])
        self.assertFalse(any(c[0] == "draft" for c in self.transport.calls))
        self.assertEqual(len([c for c in self.transport.calls if c[0] == "verify"]), 2)
        status = self.store.status(self.context)
        self.assertTrue(status["paused"]); self.assertFalse(status["ready_for_workflow"])
        self.assertEqual({item.split(":")[0] for item in status["gate_blockers"]}, {"language_slug_metadata", "execution_capacity"})
        with self.assertRaises(GateBlocked): self.workflow.execute(self.revision_id, "normal", self.article)
        with self.assertRaises(GateBlocked): self.workflow.run_pending("normal", lambda *a: self.article)
        with self.assertRaises(GateBlocked): self.record()

    def test_partial_pair_resumes_only_missing_language_with_new_capacity(self):
        self.prepare(); self.record()
        self.transport.failure = ("publish_before_commit", "en")
        with self.assertRaises(SimulatedWriteError): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.assertTrue(self.workflow._checkpoint(self.revision_id, "ja")["published"])
        with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        with self.assertRaises(GateBlocked): self.record()
        self.refreshed_capacity(23)
        self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.assertEqual([c for c in self.transport.calls if c == ("publish", "ja")], [("publish", "ja")])
        self.assertEqual(self.store.revision(self.revision_id)["state"], "complete")

    def test_unknown_committed_publication_is_reconciled_without_second_post(self):
        self.prepare(); self.record(); self.transport.failure = ("publish_after_commit", "ja")
        with self.assertRaises(SimulatedWriteError): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.refreshed_capacity()
        self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.assertEqual([c for c in self.transport.calls if c == ("publish", "ja")], [("publish", "ja")])
        self.assertIn(("reconcile", "ja"), self.transport.calls)
        with self.store.connect() as db: self.assertEqual(db.execute("SELECT count(*) FROM posts").fetchone()[0], 2)

    def test_unknown_unproven_absence_cannot_replay(self):
        self.prepare(); self.record(); self.transport.failure = ("publish_unknown", "ja")
        with self.assertRaises(SimulatedWriteError): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.transport.definitive_absence = False; self.refreshed_capacity()
        with self.assertRaises(NeedsReconciliation): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.assertEqual([c for c in self.transport.calls if c[0] == "publish"], [("publish", "ja")])

    def test_live_public_qa_failure_keeps_both_public_and_mandatory_recheck(self):
        self.prepare(); self.record(); self.transport.verify_fail_language = "ja"
        with self.assertRaises(NeedsCorrection): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.refreshed_capacity(16); self.transport.verify_fail_language = None
        self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.assertEqual(len([c for c in self.transport.calls if c[0] == "publish"]), 2)
        self.assertEqual(self.store.revision(self.revision_id)["state"], "complete")

    def test_each_missing_full_editorial_check_blocks_without_mutation(self):
        self.prepare(); self.record()
        for name in tuple(self.article["evidence"]):
            with self.subTest(name=name):
                entry = self.article["evidence"].pop(name)
                with self.assertRaises(NeedsCorrection): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
                self.article["evidence"][name] = entry
        self.assertEqual(self.transport.calls, [])

    def test_changed_stage_readback_prevents_every_publish(self):
        self.prepare(); self.record()
        en = self.plan["operations"]["en"]["post_id"]
        self.transport.posts[en]["seo_title"] = "changed"
        with self.assertRaises(NeedsCorrection): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.assertEqual(self.transport.calls, [])
        with self.store.connect() as db: self.assertEqual(db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)

    def test_immutable_permit_cannot_switch_revision_digest_actor_or_connection(self):
        self.prepare(); self.record()
        changed = json.loads(json.dumps(self.article)); changed["languages"]["en"]["html"] += "changed"
        with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", changed)
        other = dict(self.event, material_facts={"decision": "different"})
        other_id, _ = self.store.enqueue(**other)
        with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(other_id, "worker", self.article)
        for key, value in (("wordpress_identity_id", 1), ("connection_revision", "other")):
            old = self.store.runtime_binding; self.store.runtime_binding = dict(old, **{key: value})
            with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
            self.store.runtime_binding = old
        self.assertEqual(self.transport.calls, [])

    def test_capacity_underestimate_or_missing_headroom_or_forged_scope_rejected(self):
        self.prepare()
        original = json.loads(json.dumps(self.capacity))
        for key, value in (("estimated_remaining_calls", 29), ("recovery_headroom", 9), ("quota_available", 39)):
            self.capacity = json.loads(json.dumps(original)); self.capacity["measurements"][key] = value
            self.private("capacity.json", self.capacity)
            with self.subTest(key=key), self.assertRaises(GateBlocked): self.record()
        self.capacity = original; self.capacity["bundle_digest"] = "0" * 64; self.private("capacity.json", self.capacity)
        with self.assertRaises(GateBlocked): self.record()
        self.assertEqual(self.transport.calls, [])

    def test_stale_changed_private_or_unproven_preview_receipts_rejected(self):
        self.prepare(); self.record()
        self.now[0] += 601
        with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.now[0] -= 601
        self.capacity_path.chmod(0o644)
        with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.capacity_path.chmod(0o600)
        self.private("capacity.json", {**self.capacity, "extra": "tamper"})
        with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.private("capacity.json", self.capacity)
        self.plan["previews"]["en"]["runs"].pop(); self.private("preflight.json", self.plan)
        with self.assertRaises(GateBlocked): self.record()
        self.assertEqual(self.transport.calls, [])

    def test_unrelated_existing_public_or_unknown_operation_blocks(self):
        self.prepare()
        with self.store.transaction() as db:
            db.execute("INSERT INTO posts VALUES(?,?,?,?)", (self.store.revision(self.revision_id)["event_key"], "ja", 99, "https://sggroup.jp/ja/other/"))
        with self.assertRaises(GateBlocked): self.record()
        with self.store.transaction() as db:
            db.execute("DELETE FROM posts")
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,'ambiguous',NULL,NULL,?)", ("unknown", self.revision_id, "ja", "publish", "bad", self.now[0]))
        with self.assertRaises(GateBlocked): self.record()
        self.assertEqual(self.transport.calls, [])

    def test_missing_prepublication_gate_and_wrong_user_instruction_rejected(self):
        self.prepare()
        with self.store.transaction() as db: db.execute("DELETE FROM gates WHERE name='recovery_tests'")
        with self.assertRaises(GateBlocked): self.record()
        path = self.private("gate-recovery_tests.json", self.gate_record("recovery_tests"))
        self.store.record_gate("recovery_tests", path, self.context, 10000)
        self.auth["user_instruction"] = "draft only"; self.private("authorization.json", self.auth)
        with self.assertRaises(GateBlocked): self.record()
        self.assertEqual(self.transport.calls, [])

    def test_unrelated_unresolved_revision_and_changed_code_block(self):
        self.prepare()
        other_id, _ = self.store.enqueue(**dict(self.event, material_facts={"decision": "unrelated unresolved"}))
        with self.store.transaction() as db:
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,'intent',NULL,NULL,?)",
                       ("unrelated", other_id, "en", "draft", "unknown", self.now[0]))
        with self.assertRaises(GateBlocked): self.record()
        with self.store.transaction() as db: db.execute("DELETE FROM operations")
        self.record()
        original = self.store.implementation_digest
        self.store.implementation_digest = lambda: "0" * 64
        with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        self.store.implementation_digest = original
        self.assertEqual(self.transport.calls, [])

    def test_each_prepublication_gate_is_still_required_and_active_lease_blocks_refresh(self):
        self.prepare(); self.record()
        for name in FIRST_PREPUBLICATION_GATES:
            with self.subTest(gate=name):
                with self.store.transaction() as db:
                    row = dict(db.execute("SELECT * FROM gates WHERE name=?", (name,)).fetchone())
                    db.execute("DELETE FROM gates WHERE name=?", (name,))
                with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
                self.store.record_gate(name, row["evidence"], self.context, 10000)
        lease = self.store.acquire(self.revision_id, "other-worker")
        try:
            from runtime.state import LeaseLost
            with self.assertRaises(LeaseLost): self.record()
            with self.assertRaises(LeaseLost): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
        finally: self.store.release(lease)
        self.assertEqual(self.transport.calls, [])

    def test_stale_preflight_snapshot_tamper_and_wrong_operation_origin_rejected(self):
        self.prepare()
        original = self.plan["completed_at"]
        self.plan["completed_at"] = datetime.fromtimestamp(self.now[0] - 3601, timezone.utc).isoformat()
        self.private("preflight.json", self.plan)
        with self.assertRaises(GateBlocked): self.record()
        self.plan["completed_at"] = original
        self.plan["operations"]["ja"]["key"] = "0" * 64
        self.private("preflight.json", self.plan)
        with self.assertRaises(GateBlocked): self.record()
        self.plan["operations"]["ja"]["key"] = self.plan["operations"]["ja"]["saved_readback"]["idempotency_key"]
        self.private("preflight.json", self.plan)
        self.private("snapshot-ja.html", "<html>unrelated saved source</html>", raw=True)
        with self.assertRaises(GateBlocked): self.record()
        self.assertEqual(self.transport.calls, [])

    def test_known_public_side_is_revalidated_before_publishing_missing_peer(self):
        for field, value in (("content_raw", "externally changed source"), ("status", "draft"),
                             ("author", 1), ("kind", "stage"), ("source_sha256", "0" * 64)):
            with self.subTest(field=field):
                # Each separate database represents a clean offline first-pair attempt.
                if hasattr(self, "article"):
                    self.tearDown(); self.setUp()
                self.prepare(); self.record(); self.transport.failure = ("publish_before_commit", "en")
                with self.assertRaises(SimulatedWriteError): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
                canonical = self.workflow._checkpoint(self.revision_id, "ja")["public_post_id"]
                self.transport.posts[canonical][field] = value
                self.refreshed_capacity(23)
                before = len([call for call in self.transport.calls if call[0] == "publish"])
                with self.assertRaises(NeedsCorrection): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
                self.assertEqual(len([call for call in self.transport.calls if call[0] == "publish"]), before)
                self.assertFalse(self.workflow._checkpoint(self.revision_id, "en")["published"])

    def test_permit_rejects_same_revision_row_identity_or_baseline_changes(self):
        self.prepare(); self.record()
        for column, replacement in (("revision_key", "0" * 64), ("baseline_date", "2026-10-10")):
            with self.subTest(column=column):
                with self.store.transaction() as db:
                    if column == "revision_key":
                        old = db.execute("SELECT revision_key FROM revisions WHERE id=?", (self.revision_id,)).fetchone()[0]
                        db.execute("UPDATE revisions SET revision_key=? WHERE id=?", (replacement, self.revision_id))
                    else:
                        event = self.store.revision(self.revision_id, db)["event_key"]
                        old = db.execute("SELECT baseline_date FROM events WHERE event_key=?", (event,)).fetchone()[0]
                        db.execute("UPDATE events SET baseline_date=? WHERE event_key=?", (replacement, event))
                with self.assertRaises(GateBlocked): self.workflow.execute_first_publication(self.revision_id, "worker", self.article)
                with self.store.transaction() as db:
                    if column == "revision_key": db.execute("UPDATE revisions SET revision_key=? WHERE id=?", (old, self.revision_id))
                    else: db.execute("UPDATE events SET baseline_date=? WHERE event_key=?", (old, event))
        self.assertEqual(self.transport.calls, [])

    def test_duplicate_json_and_symlink_evidence_rejected(self):
        self.prepare()
        self.private("capacity.json", '{"schema_version":1,"schema_version":1}', raw=True)
        with self.assertRaises(GateBlocked): self.record()
        self.capacity_path.unlink(); self.capacity_path.symlink_to(self.auth_path)
        with self.assertRaises(GateBlocked): self.record()


if __name__ == "__main__": unittest.main()
