"""Workflow adapter for the separately reviewed, scoped SG News publisher plugin.

Importing or constructing this adapter never contacts or changes WordPress.
It refuses publication until the actual runtime identity and plugin status are
verified. Core WordPress REST is not a fallback for missing plugin operations.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from html.parser import HTMLParser
import json
import re
import ssl
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .wordpress_transport import MAX_RESPONSE_BYTES, WordPressError, WordPressTransport, _NoRedirect

NAMESPACE = "/sgnews-publisher/v1"
FEATURES = ("idempotent_creates", "fenced_leases", "exact_source_readback", "bilingual_publication_gate", "canonicalization")
POLICY_VERSION = "sgnews-restricted-xml-css-1"
POST_KEYS = ("id", "status", "language", "common_slug", "title", "seo_title",
             "meta_description", "categories", "content_raw", "is_accessible_for_free", "public_url")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise WordPressError("invalid_operation_digest")
    return value


class _Head(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.in_head = False
        self.in_title = False
        self.description = None
        self.canonical = None
        self.alternates = {}
        self.descriptions = []
        self.canonicals = []
        self.alternate_counts = {}
        self.title_count = 0
        self.language = None
        self.in_jsonld = False
        self.jsonld = []
        self._jsonld_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.language = attrs.get("lang", "").split("-")[0]
        if tag == "head":
            self.in_head = True
        if tag == "title" and self.in_head:
            self.in_title = True
            self.title_count += 1
        if self.in_head and tag == "meta" and attrs.get("name", "").lower() == "description":
            self.description = attrs.get("content")
            self.descriptions.append(self.description)
        if self.in_head and tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href")
            self.canonicals.append(self.canonical)
        if self.in_head and tag == "link" and attrs.get("rel") == "alternate" and attrs.get("hreflang"):
            self.alternates[attrs["hreflang"]] = attrs.get("href")
            language = attrs["hreflang"]
            self.alternate_counts[language] = self.alternate_counts.get(language, 0) + 1
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self.in_jsonld = True
            self._jsonld_parts = []

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "head":
            self.in_head = False
        if tag == "script":
            if self.in_jsonld:
                self.jsonld.append("".join(self._jsonld_parts))
            self.in_jsonld = False
            self._jsonld_parts = []

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_jsonld:
            self._jsonld_parts.append(data)


def _news_articles(value: Any) -> list[dict]:
    if isinstance(value, dict):
        article_type = value.get("@type", [])
        article_type = [article_type] if isinstance(article_type, str) else article_type
        if not isinstance(article_type, list):
            article_type = []
        found = [value] if "NewsArticle" in article_type else []
        for child in value.values():
            found.extend(_news_articles(child))
        return found
    if isinstance(value, list):
        return [article for child in value for article in _news_articles(child)]
    return []


def _graph_entities(value: Any, entities: dict, conflicts: set):
    if isinstance(value, dict):
        identifier = value.get("@id")
        if isinstance(identifier, str) and any(key != "@id" for key in value):
            if identifier in entities and entities[identifier] != value:
                conflicts.add(identifier)
            else:
                entities[identifier] = value
        for child in value.values():
            _graph_entities(child, entities, conflicts)
    elif isinstance(value, list):
        for child in value:
            _graph_entities(child, entities, conflicts)


def _organization(value: Any, entities: dict, conflicts: set, *, require_url: bool) -> bool:
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    if isinstance(value, str):
        value = {"@id": value}
    if not isinstance(value, dict):
        return False
    identifier = value.get("@id")
    if identifier is not None and not isinstance(identifier, str):
        return False
    if identifier in conflicts:
        return False
    if identifier in entities:
        resolved = entities[identifier]
        if any(key in resolved and resolved[key] != item for key, item in value.items()):
            return False
        value = {**resolved, **value}
    types = value.get("@type")
    types = [types] if isinstance(types, str) else types
    if not isinstance(types, list) or "Organization" not in types or "Person" in types:
        return False
    if value.get("name") != "SG Group":
        return False
    if require_url or "url" in value:
        return value.get("url") == "https://sggroup.jp/"
    return True


class PublicPageVerifier:
    """Anonymous public fetch plus required actual browser evidence callback.

    browser_verifier(post) must inspect the actual public URL and return passed,
    actual_live_browser, url and source_sha256. A source-only or draft-preview
    report cannot make this public verification pass.
    """

    def __init__(self, site_url: str, browser_verifier: Callable[[dict], dict] | None = None):
        self.site_url = site_url.rstrip("/")
        self.browser_verifier = browser_verifier
        self._opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def __call__(self, post: dict) -> dict:
        expected = f"{self.site_url}/{post['language']}/article/news/{post['common_slug']}/"
        if post.get("public_url") != expected or post.get("status") != "publish":
            return {"passed": False, "details": {"expected_published_url": False}}
        request = urllib.request.Request(expected, headers={"Accept": "text/html",
                                         "User-Agent": "SGGroupNewsPublicVerifier/1.0"})
        try:
            with self._opener.open(request, timeout=25) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if response.status != 200 or len(data) > MAX_RESPONSE_BYTES:
                    return {"passed": False, "details": {"anonymous_http_200": False}}
        except (urllib.error.URLError, OSError, TimeoutError):
            return {"passed": False, "details": {"anonymous_http_200": False}}
        try:
            page = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"passed": False, "details": {"utf8_page": False}}
        head = _Head()
        head.feed(page)
        news_articles = []
        schema_valid = True
        entities, conflicts = {}, set()
        for schema in head.jsonld:
            try:
                graph = json.loads(schema)
                news_articles.extend(_news_articles(graph))
                _graph_entities(graph, entities, conflicts)
            except ValueError:
                schema_valid = False
        article = news_articles[0] if len(news_articles) == 1 else {}
        article_identifiers = [article.get(name) for name in ("url", "mainEntityOfPage", "@id")
                               if article.get(name) is not None]
        article_identifiers = [value.get("@id") if isinstance(value, dict) else value for value in article_identifiers]
        article_identity = bool(article_identifiers) and all(
            isinstance(value, str) and urllib.parse.urldefrag(value)[0] == expected
            for value in article_identifiers)
        checks = {"anonymous_http_200": True,
                  "exact_source_visible": post["content_raw"] in page,
                  "seo_title": head.title_count == 1 and head.title == post["seo_title"],
                  "meta_description": head.descriptions == [post["meta_description"]],
                  "canonical": head.canonicals == [expected],
                  "language": head.language == post["language"],
                  "exactly_one_news_article": schema_valid and len(news_articles) == 1,
                  "news_article_identity": article_identity,
                  "full_free_access_schema": len(news_articles) == 1 and article.get("isAccessibleForFree") is True,
                  "author_organization": _organization(article.get("author"), entities, conflicts, require_url=True),
                  "publisher_organization": _organization(article.get("publisher"), entities, conflicts, require_url=False)}
        for language in ("ja", "en"):
            checks["hreflang_" + language] = head.alternate_counts.get(language) == 1 and head.alternates.get(language) == (
                f"{self.site_url}/{language}/article/news/{post['common_slug']}/")
        checks["hreflang_x_default"] = (head.alternate_counts.get("x-default") == 1
            and head.alternates.get("x-default") == f"{self.site_url}/en/article/news/{post['common_slug']}/")
        try:
            browser = self.browser_verifier(post) if self.browser_verifier else {}
        except Exception:
            browser = {}
        checks["actual_live_browser"] = (isinstance(browser, dict) and browser.get("passed") is True
            and browser.get("actual_live_browser") is True and browser.get("url") == expected
            and browser.get("source_sha256") == _sha(post["content_raw"]))
        return {"passed": all(checks.values()), "details": checks}


class ScopedPublisherAdapter:
    def __init__(self, transport: WordPressTransport, *, expected_user_id: int,
                 public_verifier: Callable[[dict], dict] | None = None,
                 connection_revision: str | None = None,
                 expected_implementation_sha256: str | None = None,
                 expected_policy_version: str = POLICY_VERSION,
                 saved_preview_verifier: Callable[[dict], dict] | None = None,
                 test_mode: bool = False):
        if test_mode and isinstance(transport, WordPressTransport):
            raise WordPressError("simulated_verifiers_cannot_use_live_transport")
        if isinstance(expected_user_id, bool) or not isinstance(expected_user_id, int) or expected_user_id <= 0:
            raise WordPressError("explicit_intended_user_id_required")
        self.transport = transport
        self.expected_user_id = expected_user_id
        self.connection_revision = connection_revision
        self.expected_implementation_sha256 = expected_implementation_sha256
        self.expected_policy_version = expected_policy_version
        self.public_verifier = public_verifier or PublicPageVerifier(transport.site_url)
        self.saved_preview_verifier = saved_preview_verifier
        self.test_mode = test_mode
        self.supports_idempotent_creates = False
        self.supports_staged_updates = False
        self.status = None
        self._publication = {}
        self.last_lease_release_error = None

    def verify_ready(self) -> dict:
        # Reset cached capability flags before checking the actual identity.
        self.supports_idempotent_creates = self.supports_staged_updates = False
        self.status = None
        expected_code = _key(self.expected_implementation_sha256)
        user = self.transport.get_current_user()
        if user["id"] != self.expected_user_id:
            raise WordPressError("intended_identity_mismatch")
        if user.get("capabilities", {}).get("sgnews_publish") is not True:
            raise WordPressError("scoped_publisher_capability_missing")
        status = self.transport.request("GET", NAMESPACE + "/status")
        if (not isinstance(status, dict) or isinstance(status.get("api_version"), bool)
                or status.get("api_version") != 1
                or status.get("user_id") != self.expected_user_id
                or status.get("can_publish") is not True
                or status.get("news_category_id") != self.transport.news_category_id):
            raise WordPressError("publisher_status_contract_unverified")
        if (status.get("implementation_sha256") != expected_code
                or status.get("policy_version") != self.expected_policy_version
                or status.get("namespace") != NAMESPACE.lstrip("/")
                or status.get("owner_user_id") != self.expected_user_id
                or status.get("dedicated_capability") != "sgnews_publish"
                or status.get("has_dedicated_capability") is not True
                or status.get("dependencies_ready") is not True
                or status.get("aioseo_abilities_available") is not True):
            raise WordPressError("deployed_publisher_implementation_unverified")
        features = status.get("features", {})
        if not isinstance(features, dict) or not all(features.get(name) is True for name in FEATURES):
            raise WordPressError("publisher_safety_features_unavailable")
        terms = status.get("language_terms", {})
        if (not isinstance(terms, dict) or set(terms) != {"ja", "en"}
                or any(isinstance(term, bool) or not isinstance(term, int) or term <= 0 for term in terms.values())):
            raise WordPressError("publisher_language_configuration_unverified")
        self.status = status
        self.supports_idempotent_creates = True
        self.supports_staged_updates = features.get("staged_updates") is True
        return status

    def runtime_binding(self, context: str, *, connection_revision: str | None = None) -> dict:
        revision = connection_revision or self.connection_revision
        if not isinstance(context, str) or not context or not isinstance(revision, str) or not revision:
            raise WordPressError("current_runtime_connection_revision_required")
        # A proxy placeholder is stable across secret rotations. Its hash must
        # never be mistaken for the managed runtime's observed config revision.
        self.verify_ready()
        return {"site_url": self.transport.site_url, "execution_context": context,
                "wordpress_identity_id": self.expected_user_id, "connection_revision": revision,
                "publisher_policy_version": self.expected_policy_version,
                "publisher_code_sha256": self.expected_implementation_sha256}

    def _require_ready(self):
        if self.status is None:
            self.verify_ready()

    @contextmanager
    def _lease(self):
        self.verify_ready()  # Refresh identity/capability before every mutation.
        owner = "sgnews-" + uuid.uuid4().hex
        lease = self.transport.request("POST", NAMESPACE + "/lease",
                                       payload={"scope": "news", "owner": owner, "ttl_seconds": 120})
        fence = lease.get("fence") if isinstance(lease, dict) else None
        if (not isinstance(lease, dict) or lease.get("owner") != owner
                or isinstance(fence, bool) or not isinstance(fence, int) or fence <= 0):
            raise WordPressError("invalid_publisher_lease", ambiguous_write=True)
        try:
            yield {"lease_owner": owner, "lease_fence": fence}
        finally:
            try:
                self.transport.request("POST", NAMESPACE + "/lease/release",
                                       payload={"scope": "news", "owner": owner, "fence": fence})
            except WordPressError as error:
                # Keep a completed operation result; the bounded server lease
                # expires. Releasing a lock never repeats the article mutation.
                self.last_lease_release_error = error.to_dict()

    def renew_lease(self, owner: str, fence: int) -> dict:
        self._require_ready()
        return self.transport.request("POST", NAMESPACE + "/lease/renew",
                                      payload={"scope": "news", "owner": owner,
                                               "fence": fence, "ttl_seconds": 120})

    def _post(self, value: Any) -> dict:
        if not isinstance(value, dict) or any(key not in value for key in POST_KEYS):
            raise WordPressError("invalid_publisher_post_response")
        if (isinstance(value["id"], bool) or not isinstance(value["id"], int) or value["id"] <= 0
                or value["language"] not in ("ja", "en") or value["status"] not in ("draft", "publish")
                or value["categories"] != [self.transport.news_category_id]
                or value["is_accessible_for_free"] is not True
                or any(not isinstance(value[name], str) or not value[name]
                       for name in ("common_slug", "title", "seo_title", "meta_description", "content_raw"))
                or (value["status"] == "draft" and value["public_url"] is not None)
                or (value["status"] == "publish" and (not isinstance(value["public_url"], str) or not value["public_url"]))):
            raise WordPressError("invalid_publisher_post_fields")
        if value.get("author") != self.expected_user_id:
            raise WordPressError("intended_author_mismatch")
        if value.get("source_sha256", _sha(value["content_raw"])) != _sha(value["content_raw"]):
            raise WordPressError("publisher_source_hash_mismatch")
        return value

    def _mutation_post(self, value: Any) -> dict:
        # A returned POST may already have saved/published even when its
        # normalized receipt is malformed. Only validation is wrapped here;
        # definitive HTTP failures raised by the transport retain their meaning.
        try:
            return self._post(value)
        except WordPressError as error:
            raise WordPressError(error.code, status=error.status,
                                 retryable=error.retryable, ambiguous_write=True,
                                 details=error.details) from None

    def canonicalize(self, language: str, common_slug: str, html: str) -> dict:
        """Normalize before editorial/browser audits, never after approved evidence."""
        self.verify_ready()
        if language not in ("ja", "en") or not isinstance(html, str) or not html:
            raise WordPressError("invalid_canonicalization_input")
        result = self.transport.request("POST", NAMESPACE + "/canonicalize", payload={
            "language": language, "shared_slug": common_slug, "html": html})
        root = "sg-news-" + _sha(common_slug)[:12]
        if (not isinstance(result, dict) or not isinstance(result.get("content_raw"), str)
                or result.get("source_sha256") != _sha(result["content_raw"])
                or result.get("policy_version") != self.expected_policy_version
                or result.get("root_id") != root):
            raise WordPressError("canonicalization_result_unverified")
        return result

    def get_post(self, post_id: int) -> dict:
        self._require_ready()
        self.transport._post_endpoint(post_id)
        post = self._post(self.transport.request("GET", NAMESPACE + f"/articles/{post_id}"))
        if post["id"] != post_id:
            raise WordPressError("publisher_post_identity_mismatch")
        return post

    def get_saved_preview(self, post: dict) -> dict:
        """Retrieve the owned stage's actual active-theme HTML through its scoped API."""
        self.verify_ready()
        saved = self.get_post(post["id"])
        if saved.get("kind") != "stage" or saved["status"] != "draft":
            raise WordPressError("saved_theme_preview_requires_owned_stage")
        if _sha(saved["content_raw"]) != _sha(post["content_raw"]):
            raise WordPressError("saved_preview_source_changed")
        document = self.transport.request("GET", NAMESPACE + f"/articles/{post['id']}/preview")
        if (not isinstance(document, dict) or document.get("stage_post_id") != saved["id"]
                or document.get("source_sha256") != _sha(saved["content_raw"])
                or document.get("preview_context") != "authenticated_native_theme_template"
                or document.get("base_url") != self.transport.site_url + "/"
                or document.get("seo_verified") is not True
                or document.get("user_id") != self.expected_user_id
                or document.get("policy_version") != self.expected_policy_version
                or document.get("implementation_sha256") != self.expected_implementation_sha256
                or not isinstance(document.get("html"), str)
                or saved["content_raw"] not in document["html"]):
            raise WordPressError("authenticated_saved_theme_document_unverified")
        return document

    def stage_draft(self, language: str, payload: dict, existing_id: int | None,
                    idempotency_key: str) -> dict:
        self._require_ready()
        if language not in ("ja", "en") or payload.get("language") != language:
            raise WordPressError("draft_language_mismatch")
        if (payload.get("status") != "draft" or payload.get("categories") != [self.transport.news_category_id]
                or payload.get("is_accessible_for_free") is not True):
            raise WordPressError("scoped_draft_requirements_missing")
        if existing_id is not None:
            current = self.get_post(existing_id)
            if current.get("kind") != "canonical":
                raise WordPressError("existing_canonical_target_required")
            if current["status"] == "publish" and not self.supports_staged_updates:
                raise WordPressError("supported_staged_update_required")
        key = _key(idempotency_key)
        with self._lease() as lease:
            if (existing_id is not None and current["status"] == "publish"
                    and not self.supports_staged_updates):
                raise WordPressError("supported_staged_update_required")
            receipt = self.transport.request("POST", NAMESPACE + "/articles/draft",
                payload={**payload, "existing_id": existing_id, "idempotency_key": key, **lease})
            post = self._mutation_post(receipt)
        if post["status"] != "draft" or post["language"] != language:
            raise WordPressError("publisher_draft_response_mismatch", ambiguous_write=True)
        return post

    def authorize_publication(self, *, stage_ids: dict, audit_sha256: str, bundle_digest: str):
        if set(stage_ids) != {"ja", "en"}:
            raise WordPressError("complete_bilingual_stage_pair_required")
        audit, bundle = _key(audit_sha256), _key(bundle_digest)
        posts = {language: self.get_post(post_id) for language, post_id in stage_ids.items()}
        if (any(post["language"] != language for language, post in posts.items())
                or any(post.get("kind") != "stage" or post["status"] != "draft" for post in posts.values())
                or posts["ja"]["common_slug"] != posts["en"]["common_slug"]):
            raise WordPressError("bilingual_stage_identity_mismatch")
        self._publication = {"posts": posts, "audit_sha256": audit, "bundle_digest": bundle}

    def publish_post(self, post_id: int, idempotency_key: str, existing_id: int | None = None) -> dict:
        self._require_ready()
        if not self.publication_verification_available:
            raise WordPressError("actual_live_publication_verifier_unavailable")
        if not self._publication:
            raise WordPressError("audited_bilingual_publication_authorization_required")
        if existing_id is not None and not self.supports_staged_updates:
            raise WordPressError("supported_staged_update_required")
        post = self.get_post(post_id)
        language = post["language"]
        intended = self._publication["posts"][language]
        peer = self._publication["posts"]["en" if language == "ja" else "ja"]
        if intended["id"] != post_id or intended["content_raw"] != post["content_raw"]:
            raise WordPressError("audited_stage_changed")
        key = _key(idempotency_key)
        with self._lease() as lease:
            receipt = self.transport.request("POST", NAMESPACE + "/articles/publish", payload={
                "stage_post_id": post_id, "target_post_id": existing_id, "idempotency_key": key,
                "expected_source_sha256": _sha(post["content_raw"]),
                "audit_sha256": self._publication["audit_sha256"],
                "peer_stage_post_id": peer["id"], "peer_source_sha256": _sha(peer["content_raw"]), **lease})
            result = self._mutation_post(receipt)
        if result["status"] != "publish" or result["language"] != language or (existing_id and result["id"] != existing_id):
            raise WordPressError("publisher_publication_response_mismatch", ambiguous_write=True)
        return result

    @property
    def publication_verification_available(self) -> bool:
        if self.test_mode:
            return True  # Only explicit offline fake transports can enter this mode.
        if not isinstance(self.public_verifier, PublicPageVerifier):
            return False
        browser = getattr(self.public_verifier.browser_verifier, "__self__", None)
        binding = getattr(browser, "runtime_binding", None)
        return (getattr(browser, "executes_live_browser", False) is True
                and getattr(browser, "test_mode", False) is False
                and bool(getattr(browser, "chromium", None))
                and isinstance(binding, dict)
                and binding.get("site_url") == self.transport.site_url
                and binding.get("wordpress_identity_id") == self.expected_user_id
                and binding.get("connection_revision") == self.connection_revision
                and binding.get("publisher_policy_version") == self.expected_policy_version
                and binding.get("publisher_code_sha256") == self.expected_implementation_sha256)

    def reconcile(self, language: str, common_slug: str, idempotency_key: str,
                  existing_id: int | None, expected_payload: dict) -> dict:
        self._require_ready()
        key = _key(idempotency_key)
        operation = self.transport.request("GET", NAMESPACE + "/operations/" + key)
        if not isinstance(operation, dict) or operation.get("key") != key:
            raise WordPressError("invalid_publisher_operation_response")
        if operation.get("found") is False and operation.get("state") == "not_found":
            return {"posts": [], "complete": True,
                    "definitive_absence": operation.get("definitive_absence") is True}
        if operation.get("found") is not True or operation.get("state") != "complete":
            safe_resume = (operation.get("found") is True and operation.get("state") == "pending"
                           and operation.get("safe_to_resume") is True
                           and self.supports_idempotent_creates)
            if safe_resume:
                return {"posts": [], "complete": True, "definitive_absence": False,
                        "safe_to_resume": True}
            return {"posts": [], "complete": False, "definitive_absence": False}
        post = self._post(operation.get("result"))
        if post["language"] != language or post["common_slug"] != common_slug:
            raise WordPressError("operation_result_identity_mismatch")
        if expected_payload.get("status") == "draft":
            metadata = expected_payload.get("metadata", {})
            expected = {"content_raw": expected_payload.get("html"), "title": metadata.get("title"),
                        "seo_title": metadata.get("aioseo_title"), "meta_description": metadata.get("aioseo_description")}
            if any(post[name] != value for name, value in expected.items()):
                raise WordPressError("operation_result_payload_mismatch")
        else:
            target = expected_payload.get("target_post_id")
            if post["status"] != "publish" or (target is not None and post["id"] != target):
                raise WordPressError("operation_result_publication_mismatch")
        return {"posts": [post], "complete": True, "definitive_absence": False}

    def verify_public(self, post: dict) -> dict:
        self._require_ready()
        return self.public_verifier(self._post(post))

    def verify_saved_preview(self, post: dict) -> dict:
        self._require_ready()
        post = self._post(post)
        if self.saved_preview_verifier is None:
            return {"passed": False, "errors": ["actual_authenticated_saved_preview_required"]}
        result = self.saved_preview_verifier(post)
        valid = (isinstance(result, dict) and result.get("passed") is True
                 and result.get("execution_mode") == "live" and result.get("test_only") is False
                 and result.get("actual_authenticated_preview") is True
                 and result.get("verification_context") == "authenticated_saved_theme_preview"
                 and result.get("actual_live_browser") is False
                 and result.get("wordpress_identity_id") == self.expected_user_id
                 and result.get("post_id") == post["id"]
                 and result.get("source_sha256") == _sha(post["content_raw"]))
        return result if valid else {"passed": False, "errors": ["saved_preview_evidence_unverified"]}
