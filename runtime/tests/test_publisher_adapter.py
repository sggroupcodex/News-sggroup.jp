"""Scoped publisher contract tests against the shared, explicitly offline fixture."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import unittest

from runtime.wordpress_publisher_adapter import NAMESPACE, PublicPageVerifier, ScopedPublisherAdapter
from runtime.wordpress_transport import WordPressError, WordPressTransport

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures/sgnews-publisher-v1.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class FakeTransport:
    site_url = "https://example.org"
    news_category_id = 258
    _post_endpoint = staticmethod(WordPressTransport._post_endpoint)

    def __init__(self):
        self.user = deepcopy(FIXTURE["user"])
        self.status = deepcopy(FIXTURE["status"])
        self.posts = {post["id"]: deepcopy(post) for post in FIXTURE["posts"].values()}
        self.operation = deepcopy(FIXTURE["operation_absent"])
        self.calls = []
        self.unavailable = False

    def get_current_user(self):
        return deepcopy(self.user)

    def request(self, method, endpoint, *, payload=None, params=None):
        self.calls.append((method, endpoint, deepcopy(payload)))
        if endpoint == NAMESPACE + "/status":
            if self.unavailable:
                raise WordPressError("http_error", status=404)
            return deepcopy(self.status)
        if method == "GET" and endpoint.endswith("/preview"):
            post = self.posts[int(endpoint.split("/")[-2])]
            return {"html": f'<html><head><title>{post["seo_title"]}</title><meta name="description" content="{post["meta_description"]}"></head><body>{post["content_raw"]}</body></html>',
                    "stage_post_id": post["id"], "source_sha256": post["source_sha256"],
                    "preview_context": "authenticated_native_theme_template", "base_url": self.site_url + "/",
                    "seo_verified": True, "user_id": 5, "policy_version": self.status["policy_version"],
                    "implementation_sha256": self.status["implementation_sha256"]}
        if method == "GET" and endpoint.startswith(NAMESPACE + "/articles/"):
            return deepcopy(self.posts[int(endpoint.rsplit("/", 1)[1])])
        if method == "GET" and endpoint.startswith(NAMESPACE + "/operations/"):
            return deepcopy(self.operation)
        if endpoint == NAMESPACE + "/lease":
            return {"owner": payload["owner"], "fence": 17, "expires_at": 1700000120}
        if endpoint in (NAMESPACE + "/lease/renew", NAMESPACE + "/lease/release"):
            return {"released": True}
        if endpoint == NAMESPACE + "/articles/draft":
            return deepcopy(FIXTURE["posts"][payload["language"]])
        if endpoint == NAMESPACE + "/articles/publish":
            post = deepcopy(self.posts[payload["stage_post_id"]])
            post["id"] = payload["target_post_id"] or (70001 if post["language"] == "ja" else 70002)
            post["status"] = "publish"
            post["kind"] = "canonical"
            post["public_url"] = f"{self.site_url}/{post['language']}/article/news/{post['common_slug']}/"
            self.posts[post["id"]] = post
            return deepcopy(post)
        if endpoint == NAMESPACE + "/canonicalize":
            post = FIXTURE["posts"][payload["language"]]
            return {"content_raw": post["content_raw"], "source_sha256": post["source_sha256"],
                    "policy_version": FIXTURE["status"]["policy_version"],
                    "root_id": "sg-news-" + hashlib.sha256(payload["shared_slug"].encode()).hexdigest()[:12]}
        raise AssertionError("Unexpected contract endpoint")


def draft_payload(language="ja"):
    post = FIXTURE["posts"][language]
    return {"language": language, "html": post["content_raw"],
            "metadata": {"title": post["title"], "aioseo_title": post["seo_title"],
                         "aioseo_description": post["meta_description"], "slug": post["common_slug"]},
            "categories": [258], "is_accessible_for_free": True, "status": "draft"}


class PublisherContractTests(unittest.TestCase):
    def adapter(self):
        client = FakeTransport()
        return client, ScopedPublisherAdapter(client, expected_user_id=5,
                    expected_implementation_sha256=FIXTURE["status"]["implementation_sha256"], test_mode=True)

    def test_fixture_is_explicitly_simulated_not_a_live_readiness_claim(self):
        self.assertIs(FIXTURE["fixture_only"], True)
        self.assertFalse(FIXTURE["user"]["capabilities"]["unfiltered_html"])

    def test_missing_dedicated_capability_makes_no_remote_write(self):
        client, adapter = self.adapter()
        client.user["capabilities"]["sgnews_publish"] = False
        with self.assertRaises(WordPressError):
            adapter.stage_draft("ja", draft_payload(), None, FIXTURE["operation_key"])
        self.assertFalse(adapter.supports_idempotent_creates)
        self.assertFalse(any(method == "POST" for method, _, _ in client.calls))

    def test_missing_plugin_has_no_core_rest_fallback(self):
        client, adapter = self.adapter()
        client.unavailable = True
        with self.assertRaises(WordPressError):
            adapter.stage_draft("ja", draft_payload(), None, FIXTURE["operation_key"])
        self.assertFalse(adapter.supports_idempotent_creates)
        self.assertFalse(any(method == "POST" for method, _, _ in client.calls))
        self.assertFalse(any(endpoint.startswith("/wp/v2/posts") for _, endpoint, _ in client.calls))

    def test_incomplete_safety_features_reset_previously_verified_flags(self):
        client, adapter = self.adapter()
        adapter.verify_ready()
        self.assertTrue(adapter.supports_idempotent_creates)
        client.status["features"]["fenced_leases"] = False
        with self.assertRaises(WordPressError):
            adapter.verify_ready()
        self.assertFalse(adapter.supports_idempotent_creates)
        self.assertIsNone(adapter.status)

    def test_remote_code_change_invalidates_previously_verified_capabilities(self):
        client, adapter = self.adapter()
        adapter.verify_ready()
        client.status["implementation_sha256"] = "e" * 64
        with self.assertRaises(WordPressError):
            adapter.verify_ready()
        self.assertFalse(adapter.supports_idempotent_creates)

    def test_runtime_binding_requires_revision_and_binds_remote_reviewed_code(self):
        _, adapter = self.adapter()
        with self.assertRaises(WordPressError):
            adapter.runtime_binding("fixture-runtime")
        binding = adapter.runtime_binding("fixture-runtime", connection_revision="fixture-spec-10")
        self.assertEqual(binding["publisher_code_sha256"], FIXTURE["status"]["implementation_sha256"])
        self.assertEqual(binding["publisher_policy_version"], FIXTURE["status"]["policy_version"])

    def test_exact_scoped_source_uses_dedicated_route_and_fenced_lease(self):
        client, adapter = self.adapter()
        post = adapter.stage_draft("ja", draft_payload(), None, FIXTURE["operation_key"])
        self.assertEqual(post["content_raw"], FIXTURE["posts"]["ja"]["content_raw"])
        self.assertIsNone(post["public_url"])
        mutation = next(payload for method, endpoint, payload in client.calls
                        if endpoint == NAMESPACE + "/articles/draft")
        self.assertEqual(mutation["idempotency_key"], FIXTURE["operation_key"])
        self.assertEqual(mutation["lease_fence"], 17)
        self.assertTrue(mutation["lease_owner"].startswith("sgnews-"))
        self.assertIn(("POST", NAMESPACE + "/lease/release"), [(m, e) for m, e, _ in client.calls])

    def test_publish_without_bilingual_audit_binding_makes_no_write(self):
        client, adapter = self.adapter()
        with self.assertRaises(WordPressError):
            adapter.publish_post(60001, FIXTURE["operation_key"])
        self.assertFalse(any(method == "POST" for method, _, _ in client.calls))

    def test_unconfigured_live_public_verifier_blocks_production_publication(self):
        client = FakeTransport()
        adapter = ScopedPublisherAdapter(client, expected_user_id=5,
                    expected_implementation_sha256=FIXTURE["status"]["implementation_sha256"])
        adapter.authorize_publication(stage_ids={"ja": 60001, "en": 60002},
                                      audit_sha256=FIXTURE["audit_sha256"], bundle_digest=FIXTURE["bundle_digest"])
        with self.assertRaises(WordPressError) as failure:
            adapter.publish_post(60001, FIXTURE["operation_key"])
        self.assertEqual(failure.exception.code, "actual_live_publication_verifier_unavailable")
        self.assertFalse(any(method == "POST" for method, _, _ in client.calls))

    def test_publish_binds_actual_source_and_peer_and_actual_audit_digest(self):
        client, adapter = self.adapter()
        adapter.authorize_publication(stage_ids={"ja": 60001, "en": 60002},
                                      audit_sha256=FIXTURE["audit_sha256"],
                                      bundle_digest=FIXTURE["bundle_digest"])
        result = adapter.publish_post(60001, FIXTURE["operation_key"])
        self.assertEqual(result["status"], "publish")
        self.assertNotEqual(result["id"], 60001)
        self.assertEqual(client.posts[60001]["status"], "draft")
        payload = next(p for _, endpoint, p in client.calls if endpoint == NAMESPACE + "/articles/publish")
        self.assertEqual(payload["expected_source_sha256"], FIXTURE["posts"]["ja"]["source_sha256"])
        self.assertEqual(payload["peer_stage_post_id"], 60002)
        self.assertEqual(payload["peer_source_sha256"], FIXTURE["posts"]["en"]["source_sha256"])
        self.assertEqual(payload["audit_sha256"], FIXTURE["audit_sha256"])

    def test_stage_change_after_audit_blocks_publication_before_a_write(self):
        client, adapter = self.adapter()
        adapter.authorize_publication(stage_ids={"ja": 60001, "en": 60002},
                                      audit_sha256=FIXTURE["audit_sha256"],
                                      bundle_digest=FIXTURE["bundle_digest"])
        client.posts[60001]["content_raw"] += "<p>Changed</p>"
        client.posts[60001]["source_sha256"] = hashlib.sha256(client.posts[60001]["content_raw"].encode()).hexdigest()
        with self.assertRaises(WordPressError):
            adapter.publish_post(60001, FIXTURE["operation_key"])
        self.assertFalse(any(method == "POST" for method, _, _ in client.calls))

    def test_authoritative_operation_ledger_distinguishes_absent_pending_and_complete(self):
        client, adapter = self.adapter()
        args = ("ja", FIXTURE["posts"]["ja"]["common_slug"], FIXTURE["operation_key"], None, draft_payload())
        absent = adapter.reconcile(*args)
        self.assertTrue(absent["definitive_absence"])
        client.operation = deepcopy(FIXTURE["operation_pending"])
        pending = adapter.reconcile(*args)
        self.assertFalse(pending["complete"])
        self.assertFalse(pending["definitive_absence"])
        client.operation = deepcopy(FIXTURE["operation_complete"])
        complete = adapter.reconcile(*args)
        self.assertEqual(complete["posts"][0]["id"], 60001)
        self.assertTrue(complete["complete"])
        self.assertFalse(any(method == "POST" for method, _, _ in client.calls))

    def test_conflicting_operation_payload_is_never_treated_as_recovered_success(self):
        client, adapter = self.adapter()
        client.operation = deepcopy(FIXTURE["operation_complete"])
        client.operation["result"]["title"] = "Different operation"
        with self.assertRaises(WordPressError):
            adapter.reconcile("ja", FIXTURE["posts"]["ja"]["common_slug"],
                              FIXTURE["operation_key"], None, draft_payload())

    def test_persisted_pending_same_key_resumes_only_on_explicit_server_proof(self):
        client, adapter = self.adapter()
        client.operation = deepcopy(FIXTURE["operation_pending"])
        client.operation["safe_to_resume"] = True
        result = adapter.reconcile("ja", FIXTURE["posts"]["ja"]["common_slug"],
                                   FIXTURE["operation_key"], None, draft_payload())
        self.assertTrue(result["safe_to_resume"])
        self.assertFalse(result["definitive_absence"])

    def test_new_publication_reconciles_distinct_canonical_id(self):
        client, adapter = self.adapter()
        canonical = deepcopy(FIXTURE["posts"]["ja"])
        canonical.update({"id": 70001, "kind": "canonical", "status": "publish",
                          "public_url": f"{client.site_url}/ja/article/news/{canonical['common_slug']}/"})
        client.operation = {"key": FIXTURE["operation_key"], "found": True,
                            "state": "complete", "result": canonical}
        result = adapter.reconcile("ja", canonical["common_slug"], FIXTURE["operation_key"], 60001,
                                   {"status": "publish", "stage_post_id": 60001, "target_post_id": None})
        self.assertEqual(result["posts"][0]["id"], 70001)

    def test_canonicalization_uses_actual_supported_source_policy(self):
        _, adapter = self.adapter()
        result = adapter.canonicalize("ja", FIXTURE["posts"]["ja"]["common_slug"],
                                      FIXTURE["posts"]["ja"]["content_raw"])
        self.assertEqual(result["source_sha256"], FIXTURE["posts"]["ja"]["source_sha256"])

    def test_saved_theme_preview_is_authenticated_source_bound_get_only(self):
        client, adapter = self.adapter()
        document = adapter.get_saved_preview(FIXTURE["posts"]["ja"])
        self.assertEqual(document["stage_post_id"], 60001)
        self.assertEqual(document["preview_context"], "authenticated_native_theme_template")
        self.assertIn(FIXTURE["posts"]["ja"]["content_raw"], document["html"])
        self.assertFalse(any(method == "POST" for method, _, _ in client.calls))

    def test_wrong_identity_theme_preview_cannot_certify_a_saved_stage(self):
        client, adapter = self.adapter()
        original = client.request
        def request(method, endpoint, **kwargs):
            result = original(method, endpoint, **kwargs)
            if endpoint.endswith("/preview"):
                result["user_id"] = 1
            return result
        client.request = request
        with self.assertRaises(WordPressError):
            adapter.get_saved_preview(FIXTURE["posts"]["ja"])

    def test_unsupported_staging_never_downgrades_existing_public_post(self):
        client, adapter = self.adapter()
        client.status["features"]["staged_updates"] = False
        client.posts[60001]["status"] = "publish"
        client.posts[60001]["kind"] = "canonical"
        client.posts[60001]["public_url"] = f"{client.site_url}/ja/article/news/{client.posts[60001]['common_slug']}/"
        with self.assertRaises(WordPressError):
            adapter.stage_draft("ja", draft_payload(), 60001, FIXTURE["operation_key"])
        self.assertFalse(any(method == "POST" for method, _, _ in client.calls))


class PublicPageTests(unittest.TestCase):
    def page(self, post):
        slug = post["common_slug"]
        schema = json.dumps({"@graph": [
            {"@type": "NewsArticle", "isAccessibleForFree": True, "url": post["public_url"],
             "author": {"@id": "https://sggroup.jp/#organization"},
             "publisher": {"@id": "https://sggroup.jp/#organization"}},
            {"@id": "https://sggroup.jp/#organization", "@type": "Organization", "name": "SG Group", "url": "https://sggroup.jp/"}]})
        return (f'<html lang="ja"><head><title>{post["seo_title"]}</title>'
                f'<meta name="description" content="{post["meta_description"]}">'
                f'<link rel="canonical" href="{post["public_url"]}">'
                f'<link rel="alternate" hreflang="ja" href="https://example.org/ja/article/news/{slug}/">'
                f'<link rel="alternate" hreflang="en" href="https://example.org/en/article/news/{slug}/">'
                f'<link rel="alternate" hreflang="x-default" href="https://example.org/en/article/news/{slug}/">'
                f'<script type="application/ld+json">{schema}</script></head>'
                f'<body>{post["content_raw"]}</body></html>').encode()

    def verifier(self, browser=None):
        post = deepcopy(FIXTURE["posts"]["ja"])
        post["status"] = "publish"
        post["public_url"] = f"https://example.org/ja/article/news/{post['common_slug']}/"
        verifier = PublicPageVerifier("https://example.org", browser)
        requests = []
        body = self.page(post)
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                response = io.BytesIO(body)
                response.status = 200
                return response
        verifier._opener = Opener()
        return post, verifier, requests

    def test_structural_fetch_alone_cannot_replace_actual_browser_evidence(self):
        post, verifier, requests = self.verifier()
        report = verifier(post)
        self.assertFalse(report["passed"])
        self.assertTrue(report["details"]["seo_title"])  # SVG title must not corrupt head title.
        self.assertFalse(report["details"]["actual_live_browser"])
        self.assertIsNone(requests[0].get_header("Authorization"))

    def test_actual_public_browser_evidence_is_bound_to_url_and_exact_source(self):
        post, verifier, _ = self.verifier(lambda post: {"passed": True, "actual_live_browser": True,
                                    "url": post["public_url"], "source_sha256": post["source_sha256"]})
        self.assertTrue(verifier(post)["passed"])

    def test_draft_preview_evidence_cannot_approve_public_page(self):
        post, verifier, _ = self.verifier(lambda post: {"passed": True, "actual_live_browser": True,
                                    "url": "https://example.org/?p=60001&preview=true",
                                    "source_sha256": post["source_sha256"]})
        self.assertFalse(verifier(post)["passed"])

    def test_conflicting_duplicate_head_and_news_graph_cannot_be_hidden_by_last_value(self):
        post, verifier, _ = self.verifier(lambda post: {"passed": True, "actual_live_browser": True,
                              "url": post["public_url"], "source_sha256": post["source_sha256"]})
        body = self.page(post).replace(b'<head>', b'<head><link rel="canonical" href="https://unrelated.example/">')
        paid = json.dumps({"@type": "NewsArticle", "url": post["public_url"], "isAccessibleForFree": False})
        body = body.replace(b'</head>', f'<script type="application/ld+json">{paid}</script></head>'.encode())
        class Opener:
            def open(self, request, timeout):
                response = io.BytesIO(body)
                response.status = 200
                return response
        verifier._opener = Opener()
        result = verifier(post)
        self.assertFalse(result["passed"])
        self.assertFalse(result["details"]["canonical"])
        self.assertFalse(result["details"]["exactly_one_news_article"])

    def test_jsonld_data_chunks_are_joined_per_script(self):
        from runtime.wordpress_publisher_adapter import _Head
        parser = _Head()
        for chunk in ('<html><head><script type="application/ld+json">{"@type":',
                      '"NewsArticle",', '"isAccessibleForFree":true}</script>',
                      '<script type="application/ld+json">{"@type":"Organization"}</script></head></html>'):
            parser.feed(chunk)
        self.assertEqual(len(parser.jsonld), 2)
        self.assertEqual(json.loads(parser.jsonld[0])["@type"], "NewsArticle")
        self.assertEqual(json.loads(parser.jsonld[1])["@type"], "Organization")

    def test_missing_wrong_person_name_and_url_cannot_satisfy_author_contract(self):
        from runtime.wordpress_publisher_adapter import _organization
        correct = {"@type": "Organization", "name": "SG Group", "url": "https://sggroup.jp/"}
        for value in (None, {**correct, "@type": "Person"}, {**correct, "name": "Someone Else"},
                      {**correct, "url": "https://other.example/"}):
            with self.subTest(value=value):
                self.assertFalse(_organization(value, {}, set(), require_url=True))
        self.assertTrue(_organization(correct, {}, set(), require_url=True))

    def test_conflicting_graph_references_do_not_resolve_to_a_trusted_publisher(self):
        from runtime.wordpress_publisher_adapter import _graph_entities, _organization
        identifier = "https://sggroup.jp/#organization"
        entities, conflicts = {}, set()
        _graph_entities([{"@id": identifier, "@type": "Organization", "name": "SG Group", "url": "https://sggroup.jp/"},
                         {"@id": identifier, "@type": "Person", "name": "Other Person"}], entities, conflicts)
        self.assertFalse(_organization({"@id": identifier}, entities, conflicts, require_url=False))


if __name__ == "__main__":
    unittest.main()
