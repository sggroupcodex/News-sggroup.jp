"""Independent outage regressions using an explicitly synthetic private queue.

These checks exercise queue-wide failures and recovery across a StateStore
reopen. They do not call a connector, authorize publication, or attest live CMS
readiness. Fixtures are imported as a module to avoid collecting its test class.
"""
import unittest
import hashlib
import http.client
import urllib.error
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from runtime.state import StateStore
from runtime.workflow import Workflow, safe_error
from runtime.wordpress_transport import WordPressError
from runtime.tests import test_workflow as fixtures
from runtime.tests import test_first_publication as first_fixtures
from runtime import test_wordpress_transport as http_fixtures
from runtime.tests import test_publisher_adapter as publisher_fixtures
from runtime.wordpress_publisher_adapter import PublicPageVerifier


class _UnavailableTransport(fixtures.FakeTransport):
    def __init__(self, *, status=None):
        super().__init__()
        self.status = status
        self.probes = 0

    def verify_ready(self):
        self.probes += 1
        raise WordPressError("http_error" if self.status else "network_request_failed",
                             status=self.status, retryable=True,
                             details={"retry_after_seconds": 120} if self.status == 429 else {})


class _CommittedThenDisconnected(fixtures.FakeTransport):
    def __init__(self):
        super().__init__()
        self.disconnected = False

    def publish_post(self, post_id, idempotency_key, existing_id=None):
        result = super().publish_post(post_id, idempotency_key, existing_id)
        if result["language"] == "ja" and not self.disconnected:
            self.disconnected = True
            raise WordPressError("response_read_failed", status=200, retryable=True,
                                 ambiguous_write=True, details={"retry_after_seconds": 120})
        return result


class RuntimeFaultReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkflowTests(
            "test_server_known_pending_operation_can_resume_same_key_without_claiming_absence")
        self.fixture.setUp()
        self.fixture.ready()
        self.revision_ids = [self.fixture.revision_id]
        for number in (2, 3):
            revision_id, _ = self.fixture.store.enqueue(**dict(
                self.fixture.event, semantic_identity={"official_id": str(number)},
                common_slug=f"independent-outage-review-{number}-2026-10-09"))
            self.revision_ids.append(revision_id)

    def tearDown(self):
        self.fixture.tearDown()

    def test_global_429_stops_batch_and_cooldown_survives_store_reopen(self):
        fixture = self.fixture
        transport = _UnavailableTransport(status=429)
        fixture.workflow.transport = transport
        fixture.workflow.run_pending("outage-review", lambda *_: fixture.bundle())
        self.assertEqual(transport.probes, 1,
                         "A known global rate limit must stop the remaining queue requests")
        reopened = StateStore(fixture.store.path, clock=lambda: fixture.now[0], test_mode=True,
                              original_prompt=fixture.prompt, runtime_binding=fixture.binding)
        restarted = Workflow(reopened, transport, fixtures.FakeValidator(),
                             fixture.context, fixture.prompt)
        restarted.run_pending("after-restart", lambda *_: fixture.bundle())
        self.assertEqual(transport.probes, 1,
                         "The Retry-After deadline must survive a new worker/store instance")
        fixture.now[0] += 121
        restarted.run_pending("after-cooldown", lambda *_: fixture.bundle())
        self.assertEqual(transport.probes, 2,
                         "After the deadline, one fresh probe may test service recovery")
        self.assertEqual(transport.posts, {})
        self.assertEqual([row["id"] for row in reopened.status(fixture.context)["queue"]],
                         self.revision_ids, "An outage must retain every queued event")

    def test_global_network_outage_stops_remaining_queue_attempts(self):
        fixture = self.fixture
        transport = _UnavailableTransport()
        fixture.workflow.transport = transport
        fixture.workflow.run_pending("network-outage", lambda *_: fixture.bundle())
        self.assertEqual(transport.probes, 1,
                         "A retryable connection outage must not probe once per queued article")
        self.assertEqual(transport.posts, {})
        self.assertEqual(len(fixture.store.status(fixture.context)["queue"]), 3)

    def test_story_local_failure_still_visits_other_independent_events(self):
        visited = []
        def failed_producer(revision, _renew):
            visited.append(revision["id"])
            raise ValueError("synthetic independent article defect")
        result = self.fixture.workflow.run_pending("local-fault", failed_producer)
        self.assertEqual(visited, self.revision_ids)
        self.assertEqual(len(result["failures"]), 3)
        self.assertEqual(self.fixture.transport.calls, [])

    def test_sanitized_failure_retains_safe_retry_deadline(self):
        record = safe_error(WordPressError("http_error", status=429, retryable=True,
                                          details={"retry_after_seconds": 120,
                                                   "untrusted_body": "private remote detail"}))
        self.assertIs(record.get("retryable"), True)
        self.assertEqual(record.get("retry_after_seconds",
                                    record.get("details", {}).get("retry_after_seconds")), 120)
        self.assertNotIn("private remote detail", str(record))

    def test_direct_http_408_keeps_a_submitted_mutation_ambiguous(self):
        client = http_fixtures.transport()
        client._opener = http_fixtures.Opener(http_fixtures.Response(
            b'{"code":"request_timeout"}', status=408))
        with self.assertRaises(WordPressError) as failure:
            client.request("POST", "/sgnews-publisher/v1/articles/draft", payload={
                "idempotency_key": "a" * 64})
        self.assertTrue(failure.exception.ambiguous_write,
                        "A gateway timeout cannot prove that the submitted mutation did not commit")
        self.assertTrue(failure.exception.retryable)
        self.assertEqual(len(client._opener.calls), 1,
                         "The transport must leave reconciliation to the operation ledger")

    def test_timeout_reading_a_success_response_is_a_global_connection_failure(self):
        class TimedOutResponse(http_fixtures.Response):
            def read(self, _count):
                raise TimeoutError("injected body read timeout after successful headers")
        client = http_fixtures.transport()
        client._opener = http_fixtures.Opener(TimedOutResponse(b"", status=200))
        with self.assertRaises(WordPressError) as failure:
            client.request("POST", "/sgnews-publisher/v1/articles/publish", payload={
                "idempotency_key": "a" * 64})
        self.assertEqual(failure.exception.status, 200)
        self.assertTrue(failure.exception.ambiguous_write)
        from runtime.workflow import global_transport_failure
        self.assertTrue(global_transport_failure(failure.exception),
                        "Successful headers do not make a timed-out connection available")

    def test_http_date_retry_after_preserves_a_relative_wait(self):
        client = http_fixtures.transport()
        client._opener = http_fixtures.Opener(http_fixtures.Response(
            b'{"code":"quota_limited"}', status=429,
            headers={"Retry-After": "Wed, 01 Jan 2031 00:02:00 GMT"}))
        with patch("runtime.wordpress_transport.datetime") as remote_clock:
            remote_clock.now.return_value = datetime(2031, 1, 1, tzinfo=timezone.utc)
            with self.assertRaises(WordPressError) as failure:
                client.request("GET", "/sgnews-publisher/v1/status")
        self.assertEqual(failure.exception.details["retry_after_seconds"], 120)
        self.assertTrue(failure.exception.retryable)
        self.assertEqual(len(client._opener.calls), 1)

    def test_legacy_connector_execution_failure_stops_batch_despite_missing_retry_flag(self):
        class LegacyUnavailable(fixtures.FakeTransport):
            def __init__(self):
                super().__init__()
                self.probes = 0
            def verify_ready(self):
                self.probes += 1
                raise WordPressError("wpvibe_tool_execution_failed", retryable=False)
        fixture = self.fixture
        transport = LegacyUnavailable()
        fixture.workflow.transport = transport
        result = fixture.workflow.run_pending("legacy-disconnected-connector", lambda *_: fixture.bundle())
        self.assertEqual(transport.probes, 1)
        self.assertEqual(result["blocked"]["retry_not_before"], fixture.now[0] + 300)
        self.assertIs(result["blocked"]["error"]["retryable"], True)
        self.assertEqual(transport.posts, {})

    def test_unknown_connector_error_waits_without_relabeling_it_as_write_absence(self):
        class UnknownConnectorError(fixtures.FakeTransport):
            def __init__(self):
                super().__init__()
                self.probes = 0
            def verify_ready(self):
                self.probes += 1
                # The real driver's isError envelope with no parseable status
                # yields this known connector code, without a retryable claim.
                raise WordPressError("wpvibe_tool_error", retryable=False)
        fixture = self.fixture
        transport = UnknownConnectorError()
        fixture.workflow.transport = transport
        before = [fixture.store.revision(rid)["material_facts"] for rid in self.revision_ids]
        result = fixture.workflow.run_pending("unknown-connector-error", lambda *_: fixture.bundle())
        self.assertEqual(transport.probes, 1)
        self.assertEqual(result["blocked"]["retry_not_before"], fixture.now[0] + 300)
        self.assertIsNone(result["blocked"]["error"]["http_status"])
        reopened = StateStore(fixture.store.path, clock=lambda: fixture.now[0], test_mode=True,
                              original_prompt=fixture.prompt, runtime_binding=fixture.binding)
        restarted = Workflow(reopened, transport, fixtures.FakeValidator(), fixture.context, fixture.prompt)
        restarted.run_pending("unknown-error-new-worker", lambda *_: fixture.bundle())
        self.assertEqual(transport.probes, 1)
        self.assertEqual(transport.posts, {})
        self.assertEqual(reopened.status(fixture.context)["unresolved_operations"], [])
        self.assertEqual([reopened.revision(rid)["material_facts"] for rid in self.revision_ids], before)
        from runtime.workflow import global_transport_failure
        self.assertFalse(global_transport_failure(WordPressError("wpvibe_tool_error", status=409)),
                         "A known article conflict must keep its local classification")

    def test_cooldown_is_bound_to_execution_context_and_exact_connection(self):
        fixture = self.fixture
        fixture.store.record_transport_cooldown(fixture.context, safe_error(
            WordPressError("http_error", status=429, retryable=True,
                           details={"retry_after_seconds": 120})))
        active = fixture.store.transport_cooldown(fixture.context)
        self.assertIsNotNone(active)
        self.assertEqual(active["retry_not_before"], fixture.now[0] + 120)
        self.assertIsNone(fixture.store.transport_cooldown("a-different-execution-context"))
        changed = StateStore(fixture.store.path, clock=lambda: fixture.now[0], test_mode=True,
                             original_prompt=fixture.prompt,
                             runtime_binding=dict(fixture.binding, connection_revision="changed-connection"))
        self.assertIsNone(changed.transport_cooldown(fixture.context),
                          "An old connection's cooldown must not be relabeled as current evidence")
        self.assertIsNotNone(fixture.store.transport_cooldown(fixture.context))
        fixture.now[0] += 121
        self.assertIsNone(fixture.store.transport_cooldown(fixture.context))

    def test_invalid_retry_after_cannot_disable_outage_cooldown(self):
        fixture = self.fixture
        for invalid in (0, -1, True, float("nan"), float("inf"), "120"):
            with self.subTest(invalid=repr(invalid)):
                fixture.store.record_transport_cooldown(fixture.context, {
                    "code": "http_error", "http_status": 429, "retryable": True,
                    "ambiguous_write": False, "retry_after_seconds": invalid})
                active = fixture.store.transport_cooldown(fixture.context)
                self.assertIsNotNone(active)
                self.assertEqual(active["retry_not_before"], fixture.now[0] + 300,
                                 "Invalid hints must use a bounded safe fallback")

    def test_lost_committed_publication_resumes_original_key_after_cooldown(self):
        fixture = self.fixture
        transport = _CommittedThenDisconnected()
        fixture.workflow.transport = transport
        def producer(revision, _renew):
            if revision["id"] != fixture.revision_id:
                raise ValueError("independent article deliberately has no approved fixture")
            return fixture.bundle()
        first = fixture.workflow.run_pending("before-disconnection", producer)
        self.assertEqual(len(first["failures"]), 1)
        self.assertEqual([call for call in transport.calls if call == ("publish", "ja")],
                         [("publish", "ja")])
        uncertain = fixture.store.status(fixture.context)["unresolved_operations"]
        self.assertEqual(len(uncertain), 1)
        original_key = uncertain[0]["operation_key"]
        self.assertEqual(uncertain[0]["status"], "ambiguous")
        restarted_store = StateStore(fixture.store.path, clock=lambda: fixture.now[0], test_mode=True,
                                     original_prompt=fixture.prompt, runtime_binding=fixture.binding)
        restarted = Workflow(restarted_store, transport, fixtures.FakeValidator(),
                             fixture.context, fixture.prompt)
        restarted.run_pending("during-cooldown", producer)
        self.assertNotIn(("reconcile", "ja"), transport.calls)
        fixture.now[0] += 121
        result = restarted.run_pending("after-recovery", producer)
        self.assertEqual(len(result["completed"]), 1)
        self.assertIn(("reconcile", "ja"), transport.calls)
        self.assertEqual([call for call in transport.calls if call == ("publish", "ja")],
                         [("publish", "ja")], "The committed Japanese side must not publish twice")
        with restarted_store.connect() as db:
            operation = dict(db.execute("SELECT * FROM operations WHERE operation_key=?",
                                        (original_key,)).fetchone())
        self.assertEqual(operation["status"], "succeeded")
        self.assertEqual(restarted_store.revision(fixture.revision_id)["state"], "complete")

    def test_controlled_first_publication_cooldown_does_not_consume_capacity(self):
        first = first_fixtures.FirstPublicationTests(
            "test_complete_first_pair_reuses_stages_and_leaves_hourly_and_two_gates_blocked")
        first.setUp()
        try:
            first.prepare()
            first.record()
            first.store.record_transport_cooldown(first.context, safe_error(
                WordPressError("http_error", status=429, retryable=True,
                               details={"retry_after_seconds": 120})))
            from runtime.state import TransportCooldown
            with self.assertRaises(TransportCooldown):
                first.workflow.execute_first_publication(first.revision_id, "outage-first", first.article)
            self.assertFalse(any(call[0] == "publish" for call in first.transport.calls))
            with first.store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM first_publication_attempts").fetchone()[0], 0,
                                 "A cooldown must block before consuming the first-publication capacity receipt")
        finally:
            first.tearDown()

    def test_public_fetch_outages_are_typed_but_content_defects_stay_local(self):
        post = deepcopy(publisher_fixtures.FIXTURE["posts"]["ja"])
        post["status"] = "publish"
        post["public_url"] = "https://sggroup.jp/ja/article/news/" + post["common_slug"] + "/"
        class TimedOutBody(http_fixtures.Response):
            def read(self, _count):
                raise TimeoutError("injected anonymous body read timeout")
        failures = [
            http_fixtures.Response(b"temporarily unavailable", status=status)
            for status in (408, 429, 500, 503)
        ] + [urllib.error.URLError("injected anonymous connection loss"),
             urllib.error.HTTPError(post["public_url"], 503, "unavailable", {}, None),
             TimedOutBody(b"", status=200)]
        from runtime.workflow import global_transport_failure
        for result in failures:
            with self.subTest(result=type(result).__name__, status=getattr(result, "status", None)):
                verifier = PublicPageVerifier("https://sggroup.jp")
                verifier._opener = http_fixtures.Opener(result)
                with self.assertRaises(WordPressError) as failure:
                    verifier(post)
                self.assertTrue(failure.exception.retryable)
                self.assertFalse(failure.exception.ambiguous_write,
                                 "An anonymous GET must not invent uncertainty about an article mutation")
                self.assertTrue(global_transport_failure(failure.exception))
                self.assertEqual(len(verifier._opener.calls), 1)
                self.assertIsNone(verifier._opener.calls[0].get_header("Authorization"))
        for result in (http_fixtures.Response(b"<html><head></head><body>wrong article</body></html>"),
                       http_fixtures.Response(b"missing article", status=404),
                       urllib.error.HTTPError(post["public_url"], 404, "missing", {}, None)):
            with self.subTest(local_result=type(result).__name__, status=getattr(result, "status", None)):
                verifier = PublicPageVerifier("https://sggroup.jp")
                verifier._opener = http_fixtures.Opener(result)
                self.assertFalse(verifier(post)["passed"],
                                 "A real content defect or missing article remains an article-local QA failure")

    def test_truncated_chunked_body_is_retryable_in_both_direct_and_public_reads(self):
        class TruncatedChunkedBody(http_fixtures.Response):
            def read(self, _count):
                raise http.client.IncompleteRead(b"PRIVATE-PARTIAL-REMOTE-BODY", 128)
        post = deepcopy(publisher_fixtures.FIXTURE["posts"]["ja"])
        post["status"] = "publish"
        post["public_url"] = "https://sggroup.jp/ja/article/news/" + post["common_slug"] + "/"
        from runtime.workflow import global_transport_failure
        for public in (False, True):
            with self.subTest(public=public):
                client = PublicPageVerifier("https://sggroup.jp") if public else http_fixtures.transport()
                client._opener = http_fixtures.Opener(TruncatedChunkedBody(b"", status=200))
                with self.assertRaises(WordPressError) as failure:
                    if public:
                        client(post)
                    else:
                        client.request("POST", "/sgnews-publisher/v1/articles/publish", payload={
                            "idempotency_key": "a" * 64})
                self.assertEqual(failure.exception.code, "response_read_failed")
                self.assertTrue(failure.exception.retryable)
                self.assertEqual(failure.exception.ambiguous_write, not public)
                self.assertTrue(global_transport_failure(failure.exception))
                self.assertNotIn("PRIVATE-PARTIAL-REMOTE-BODY", str(safe_error(failure.exception)))
                self.assertEqual(len(client._opener.calls), 1)

    def test_public_site_outage_keeps_pair_and_rechecks_without_republication(self):
        fixture = self.fixture
        page_fixture = publisher_fixtures.PublicPageTests(
            "test_actual_public_browser_evidence_is_bound_to_url_and_exact_source")
        class PublicSite:
            unavailable = True
            requests = []
            def open(self, request, timeout):
                self.requests.append(request)
                if self.unavailable:
                    return http_fixtures.Response(b"injected public site outage", status=503)
                post = next(post for post in transport.posts.values()
                            if post["status"] == "publish" and post["public_url"] == request.full_url)
                page = page_fixture.page(post).replace(b"https://example.org/", b"https://sggroup.jp/")
                page = page.replace(b'<html lang="ja">', f'<html lang="{post["language"]}">'.encode())
                return http_fixtures.Response(page)
        service = PublicSite()
        verifier = PublicPageVerifier("https://sggroup.jp", lambda post: {
            "passed": True, "actual_live_browser": True, "url": post["public_url"],
            "source_sha256": hashlib.sha256(post["content_raw"].encode()).hexdigest()})
        verifier._opener = service
        class PublicTransport(fixtures.FakeTransport):
            def verify_public(self, post):
                self.calls.append(("verify", post["language"]))
                return verifier(post)
        transport = PublicTransport()
        fixture.workflow.transport = transport
        def producer(revision, _renew):
            if revision["id"] != fixture.revision_id:
                raise ValueError("independent story has no approved synthetic content")
            return fixture.bundle()
        first = fixture.workflow.run_pending("public-outage", producer)
        self.assertEqual(len(first["failures"]), 1)
        self.assertEqual(first["blocked"]["reason"], "transport_cooldown")
        self.assertEqual([call for call in transport.calls if call[0] == "publish"],
                         [("publish", "ja"), ("publish", "en")])
        known = {lang: fixture.workflow._checkpoint(fixture.revision_id, lang)["public_post_id"]
                 for lang in ("ja", "en")}
        self.assertTrue(all(known.values()))
        self.assertTrue(all(fixture.workflow._checkpoint(fixture.revision_id, lang)["published"]
                            for lang in known))
        reopened = StateStore(fixture.store.path, clock=lambda: fixture.now[0], test_mode=True,
                              original_prompt=fixture.prompt, runtime_binding=fixture.binding)
        restarted = Workflow(reopened, transport, fixtures.FakeValidator(), fixture.context, fixture.prompt)
        request_count = len(service.requests)
        restarted.run_pending("public-outage-new-worker", producer)
        self.assertEqual(len(service.requests), request_count)
        service.unavailable = False
        fixture.now[0] += 301
        resumed = restarted.run_pending("public-site-recovered", producer)
        self.assertEqual(len(resumed["completed"]), 1)
        self.assertEqual(reopened.revision(fixture.revision_id)["state"], "complete")
        self.assertEqual([call for call in transport.calls if call[0] == "publish"],
                         [("publish", "ja"), ("publish", "en")], "Recovery must recheck, never republish, the pair")
        self.assertEqual({lang: restarted._checkpoint(fixture.revision_id, lang)["public_post_id"] for lang in known}, known)
        self.assertGreater(len(service.requests), request_count)


if __name__ == "__main__":
    unittest.main()
