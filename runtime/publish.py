"""Operational, fail-closed entry point for the reviewed publication workflow.

Examples (from the repository root; inputs are private deployment artifacts):

  python -m runtime.publish --route query --context ACTUAL_RUNTIME_CONTEXT \
    --connection-revision CURRENT_OBSERVED_DEPLOYMENT_REVISION \
    --expected-user-id 5 --approved-plugin wordpress/sggroup-news-publisher.php diagnose

  python -m runtime.publish --route query --context ACTUAL_RUNTIME_CONTEXT \
    --connection-revision CURRENT_OBSERVED_DEPLOYMENT_REVISION \
    --expected-user-id 5 --approved-plugin wordpress/sggroup-news-publisher.php \
    canonicalize --language ja --slug ACTUAL_COMMON_SLUG \
    --input private_qa/ja-source.html --output private_qa/ja-canonical.html

  python -m runtime.publish --route query --context ACTUAL_RUNTIME_CONTEXT \
    --connection-revision CURRENT_OBSERVED_DEPLOYMENT_REVISION \
    --expected-user-id 5 --approved-plugin wordpress/sggroup-news-publisher.php \
    execute --state state/queue.sqlite3 --revision-id ACTUAL_QUEUED_REVISION \
    --bundle private_qa/audited-bundle.json \
    --browser-artifacts private_qa/live-browser

The connection revision must come from the current observed managed environment
and approved deployment, not from hashing its opaque Authorization placeholder.
The approved PHP bytes/policy and actual runtime identity bind every gate and
article receipt. Canonicalize BEFORE independent and browser audits: changed
source needs fresh evidence. No command manufactures gate records or unpauses
the store/schedule. execute requires all existing production gates and complete
private, audited article evidence. It performs actual authenticated saved-preview
and anonymous live-page browser checks; a supplied "passed" file is never used.

Authentication repair: configure WP_AUTHORIZATION via the normal managed secret
workflow as a complete HTTP Authorization value (normally Basic plus the base64
encoding of the approved existing user's Application Password credential pair).
Do not paste credentials into chat, put them on the command line, print them, or
base64-encode the runtime's proxy placeholder. WP_BASE_URL remains the approved
HTTPS site and WP_NEWS_CATEGORY_ID the existing News category. Test users/me from
the actual runtime first. Installing this plugin and granting only sgnews_publish
to its approved owner are separate administrator deployment steps; this CLI does
neither. Saved-preview HTML comes from the authenticated scoped publisher API
and its actual active WordPress template. Chromium inspects this private theme
snapshot on loopback with the approved site's asset base. This is labelled an
authenticated saved-theme preview, never a live public page. Browser cookies or
ordinary edit_post permission are unnecessary. No new credentials are created.

The native tool driver can use --transport wpvibe --bridge-dir PRIVATE_RPC_DIR
to retain the existing Author5 connection without WP_AUTHORIZATION. The RPC
directory must be fresh, empty and mode0700. authenticate verifies this Author
with GET only; its success never establishes publisher readiness or opens gates.

preflight stages an already canonicalized JA/EN pair and inspects real saved-theme
previews without publication. Its private journal is separate from production
SQLite. first-publish requires the reviewed controlled-first-publication permit,
all article evidence, measured prepublication gates, and fresh authorization and
capacity receipts. It leaves ordinary queue readiness and scheduling unchanged.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import re
import stat
import tempfile
import threading
import uuid

from .article_qa import article_root_id, validate_bundle
from .browser_qa import LANDSCAPES, MEASURE_JS, WIDTHS
from .state import StateError, StateStore, fingerprint
from .workflow import Workflow, safe_error
from .wordpress_publisher_adapter import PublicPageVerifier, ScopedPublisherAdapter
from .wordpress_transport import WordPressError, WordPressTransport
from .tool_transport import ToolWordPressTransport


def _private_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class _PrivateThemeDocument:
    """Serve only an ephemeral authenticated theme snapshot on loopback."""

    def __init__(self, html: str, base_url: str):
        bases = re.findall(r'<base\b[^>]*href=["\']([^"\']+)["\'][^>]*>', html, re.I)
        if len(bases) > 1 or (bases and bases != [base_url]):
            raise WordPressError("saved_theme_asset_base_unverified")
        if not bases:
            html, count = re.subn(r'(<head\b[^>]*>)',
                lambda match: match[0] + '<base href="' + escape(base_url, quote=True) + '">',
                html, count=1, flags=re.I)
            if count != 1:
                raise WordPressError("saved_theme_document_head_missing")
        self.html = html

    def __enter__(self):
        route = "/private-theme-" + uuid.uuid4().hex
        payload = self.html.encode("utf-8")
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != route:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}{route}"

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class LiveBrowserVerifier:
    """Inspect real WordPress pages, not screenshots or claimed receipt files."""

    executes_live_browser = True

    def __init__(self, site_url: str, expected_user_id: int, output: str | Path,
                 *, preview_loader=None, chromium: str | None = None, test_mode: bool = False):
        self.site_url = site_url.rstrip("/")
        self.expected_user_id = expected_user_id
        self.output = Path(output).resolve()
        self.preview_loader = preview_loader
        self.test_mode = test_mode
        self.executes_live_browser = not test_mode
        self.chromium = chromium or shutil.which("chromium")
        self.runtime_binding = None

    def _run(self, post: dict, preview: bool) -> dict:
        source_hash = hashlib.sha256(post["content_raw"].encode()).hexdigest()
        target = None if preview else post["public_url"]
        result = {"passed": False, "errors": [], "url": target, "post_id": post["id"],
                  "source_sha256": source_hash, "actual_live_browser": not preview and not self.test_mode,
                  "actual_authenticated_preview": False, "wordpress_identity_id": None,
                  "runtime_binding": self.runtime_binding, "runs": [], "screenshots": [],
                  "execution_mode": "simulated" if self.test_mode else "live", "test_only": self.test_mode}
        result["verification_context"] = "authenticated_saved_theme_preview" if preview else "actual_public_page"
        if self.test_mode:
            result["verification_context"] = "synthetic_theme_fixture"
        if not self.chromium or (preview and not callable(self.preview_loader)):
            result["errors"].append("configured_browser_or_authenticated_theme_route_unavailable")
            return result
        self.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        report_path = self.output / f"{'preview' if preview else 'public'}-{post['id']}-{source_hash[:12]}.json"
        resources = ExitStack()
        try:
            if preview:
                document = self.preview_loader(post)
                binding = self.runtime_binding or {}
                if (document.get("stage_post_id") != post["id"]
                        or document.get("source_sha256") != source_hash
                        or document.get("user_id") != self.expected_user_id
                        or document.get("preview_context") != "authenticated_native_theme_template"
                        or document.get("base_url") != self.site_url + "/"
                        or document.get("seo_verified") is not True
                        or document.get("policy_version") != binding.get("publisher_policy_version")
                        or document.get("implementation_sha256") != binding.get("publisher_code_sha256")
                        or not isinstance(document.get("html"), str)
                        or post["content_raw"] not in document["html"]):
                    raise WordPressError("authenticated_theme_preview_document_unverified")
                private_html = self.output / f"saved-theme-{post['id']}-{source_hash[:12]}.html"
                with os.fdopen(os.open(private_html, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
                               "w", encoding="utf-8") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(document["html"])
                result["saved_theme_document_path"] = str(private_html)
                result["saved_theme_document_sha256"] = hashlib.sha256(document["html"].encode()).hexdigest()
                result["wordpress_identity_id"] = self.expected_user_id
                result["preview_context"] = document["preview_context"]
                target = resources.enter_context(_PrivateThemeDocument(document["html"], document["base_url"]))
                result["url"] = target
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                launch = {"executable_path": self.chromium, "headless": True,
                          "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
                if proxy:
                    # Playwright otherwise proxies loopback too. This exception
                    # reaches only our private local document; all remote theme
                    # assets and public pages retain the inherited sidecar path.
                    launch["proxy"] = {"server": proxy, "bypass": "127.0.0.1,localhost"}
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context(reduced_motion="reduce")  # TLS verification stays enabled.
                page = context.new_page()
                response = page.goto(target, wait_until="domcontentloaded", timeout=25000)
                if not response or response.status != 200:
                    raise WordPressError("actual_browser_page_not_http_200")
                if not preview and page.url != target:
                    raise WordPressError("actual_public_browser_url_mismatch")
                if preview and page.url != target:
                    raise WordPressError("saved_theme_preview_left_private_origin")
                if page.title() != post["seo_title"]:
                    raise WordPressError("actual_browser_seo_title_mismatch")
                description = page.locator('head meta[name="description"]')
                if description.count() != 1 or description.get_attribute("content") != post["meta_description"]:
                    raise WordPressError("actual_browser_meta_description_mismatch")
                page.evaluate("document.fonts.ready")
                root_id = article_root_id(post["common_slug"])
                root = page.locator("#" + root_id)
                if root.count() != 1 or not root.is_visible():
                    raise WordPressError("actual_browser_article_root_unverified")
                expected_style = post["content_raw"].split("<style>", 1)[1].split("</style>", 1)[0]
                styles = page.locator("style").all_text_contents()
                if styles.count(expected_style) != 1:
                    raise WordPressError("actual_browser_scoped_style_unverified")
                page.locator('[data-sgn-role="faq"] details').evaluate_all(
                    '(nodes)=>{for(const node of nodes)node.open=true}')
                for width, height in [(width, 900) for width in WIDTHS] + list(LANDSCAPES):
                    page.set_viewport_size({"width": width, "height": height})
                    measurement = page.evaluate(MEASURE_JS, root_id)
                    errors = measurement.get("errors", [])
                    result["runs"].append({"width": width, "height": height, "measurement": measurement})
                    result["errors"].extend(f"{width}x{height}: {error}" for error in errors)
                    if (width, height) in ((390, 900), (768, 900), (1920, 900)):
                        screenshot = self.output / f"{'preview' if preview else 'public'}-{post['id']}-{source_hash[:12]}-{width}.png"
                        page.screenshot(path=str(screenshot), full_page=True, animations="disabled")
                        os.chmod(screenshot, 0o600)
                        result["screenshots"].append(str(screenshot))
                faq = page.locator('[data-sgn-role="faq"] details').first
                if faq.count():
                    faq.evaluate('(node)=>{node.open=false}')
                    summary = faq.locator("summary")
                    summary.focus()
                    if summary.evaluate('node=>getComputedStyle(node).outlineStyle') == "none":
                        result["errors"].append("live_faq_keyboard_focus_not_visible")
                    page.keyboard.press("Enter")
                    if faq.get_attribute("open") is None:
                        result["errors"].append("live_faq_keyboard_expansion_failed")
                else:
                    result["errors"].append("live_faq_missing")
                result["actual_authenticated_preview"] = preview and not self.test_mode
                result["browser_executed"] = True
                result["passed"] = not result["errors"]
                context.close()
                browser.close()
        except Exception as error:
            # Browser/API errors can contain URLs or page text. Retain only a
            # safe type/code; never persist their original exception message.
            result["errors"].append(getattr(error, "code", "live_browser_execution_failed"))
            result["error_type"] = type(error).__name__
        finally:
            resources.close()
        _private_json(report_path, result)
        return result

    def saved_preview(self, post: dict) -> dict:
        return self._run(post, True)

    def public(self, post: dict) -> dict:
        return self._run(post, False)


class _Validator:
    @staticmethod
    def validate_bundle(bundle):
        # Deterministic QA accepts the language mapping. Workflow separately
        # validates independent, factual, browser and clipboard evidence.
        return validate_bundle(bundle)


def _preflight_time():
    return datetime.now(timezone.utc).isoformat()


def _atomic_preflight_json(path: Path, value: dict):
    """Commit a private intent before dispatch; survive interruption mid-write."""
    fd, temporary = tempfile.mkstemp(prefix=".preflight-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _preflight_bytes(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > 16 * 1024 * 1024):
            raise WordPressError("private_preflight_artifact_required")
        return handle.read()


def _preflight_json(path: Path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value
    return json.loads(_preflight_bytes(path), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))


@contextmanager
def _preflight_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise WordPressError("private_preflight_directory_required")
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise WordPressError("private_preflight_lock_required")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WordPressError("preflight_already_running") from None
        yield
    finally:
        os.close(fd)  # Never unlink a lock inode while another caller can open it.


def _preflight_saved(language: str, entry: dict, post: dict, slug: str, operation_key: str):
    errors = Workflow._saved_errors(language, entry, post, slug)
    if (type(post.get("id")) is not int or post["id"] <= 0 or post.get("author") != 5
            or post.get("kind") != "stage" or post.get("status") != "draft"
            or post.get("public_url") is not None):
        errors.append("owned_private_stage_required")
    if post.get("idempotency_key") != operation_key:
        errors.append("stage_operation_origin")
    if post.get("source_sha256") != hashlib.sha256(entry["html"].encode()).hexdigest():
        errors.append("stage_source_receipt")
    if post.get("seo_verified") is not True:
        errors.append("actual_aioseo_readback")
    if errors:
        raise WordPressError("preflight_saved_draft_mismatch", details={"language": language, "checks": errors})


def _preflight_preview_proof(browser, binding: dict, post: dict, report: dict):
    expected = {(width, 900) for width in WIDTHS} | set(LANDSCAPES)
    runs = report.get("runs", [])
    if (report.get("runtime_binding") != binding or not isinstance(runs, list) or len(runs) != len(expected)
            or {(item.get("width"), item.get("height")) for item in runs} != expected
            or any(not isinstance(item.get("measurement"), dict) or item["measurement"].get("errors") != [] for item in runs)):
        raise WordPressError("preflight_actual_preview_cases_unverified")
    source = hashlib.sha256(post["content_raw"].encode()).hexdigest()
    report_path = browser.output / f"preview-{post['id']}-{source[:12]}.json"
    snapshot_path = browser.output / f"saved-theme-{post['id']}-{source[:12]}.html"
    if (report.get("saved_theme_document_path") != str(snapshot_path)
            or _preflight_json(report_path) != report):
        raise WordPressError("preflight_actual_preview_artifacts_unverified")
    raw = _preflight_bytes(snapshot_path)
    if (hashlib.sha256(raw).hexdigest() != report.get("saved_theme_document_sha256")
            or post["content_raw"].encode() not in raw):
        raise WordPressError("preflight_actual_theme_snapshot_unverified")
    return {**report, "preview_report_path": str(report_path),
            "preview_report_sha256": hashlib.sha256(_preflight_bytes(report_path)).hexdigest()}


def preflight(adapter: ScopedPublisherAdapter, binding: dict, args):
    """Persist/reconcile controlled draft probes; never authorize or publish.

    This journal is separate from the production SQLite queue/checkpoints. Its
    results require later independent assessment; they open no readiness gates.
    The input must already be live-canonicalized before its content audits. Exact
    saved readback rejects any server normalization instead of rewriting input.
    """
    value = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    languages = value.get("languages", value) if isinstance(value, dict) else None
    if not isinstance(languages, dict) or set(languages) != {"ja", "en"}:
        raise WordPressError("complete_preflight_bilingual_input_required")
    structural = validate_bundle(languages)
    if structural.get("passed") is not True or structural.get("errors"):
        raise WordPressError("preflight_structural_validation_failed")
    browser = getattr(adapter.saved_preview_verifier, "__self__", None)
    if (not isinstance(browser, LiveBrowserVerifier) or browser.test_mode
            or browser.executes_live_browser is not True or not browser.chromium
            or browser.runtime_binding != binding):
        raise WordPressError("actual_preflight_browser_required")
    if adapter.supports_idempotent_creates is not True:
        raise WordPressError("preflight_server_idempotency_required")
    slug = languages["ja"]["metadata"]["slug"]
    identity = {"schema_version": 1, "purpose": "controlled_private_bilingual_draft_preflight",
                "runtime_binding": binding, "bundle_digest": fingerprint(languages), "common_slug": slug}
    payloads = {language: {"language": language, "html": languages[language]["html"],
        "metadata": languages[language]["metadata"], "categories": [258],
        "is_accessible_for_free": True, "status": "draft"} for language in ("ja", "en")}
    keys = {language: fingerprint({**identity, "language": language, "operation": "draft",
                                  "payload": payload}) for language, payload in payloads.items()}
    path = Path(args.plan).absolute()
    journal = None
    with _preflight_lock(path):
        if path.exists() or path.is_symlink():
            journal = _preflight_json(path)
            if (not isinstance(journal, dict) or journal.get("identity") != identity
                    or journal.get("payloads") != payloads or set(journal.get("operations", {})) != {"ja", "en"}
                    or any(journal["operations"][language].get("key") != keys[language] for language in ("ja", "en"))):
                raise WordPressError("preflight_plan_binding_mismatch")
        else:
            journal = {"identity": identity, "created_at": _preflight_time(), "payloads": payloads,
                "structural_validation": structural, "publication_performed": False, "production_ready": False,
                "operations": {language: {"key": keys[language], "status": "planned", "post_id": None}
                               for language in ("ja", "en")}, "previews": {}, "state": "planned"}
            _atomic_preflight_json(path, journal)
        journal.update(state="running", attempt_started_at=_preflight_time(), previews={})
        _atomic_preflight_json(path, journal)
        posts = {}
        try:
            for language in ("ja", "en"):
                operation = journal["operations"][language]
                payload = payloads[language]
                post = None
                if operation["status"] == "saved":
                    if type(operation.get("post_id")) is not int or operation["post_id"] <= 0:
                        raise WordPressError("preflight_saved_id_unverified")
                elif operation["status"] != "planned":
                    reconciliation = adapter.reconcile(language, slug, keys[language], None, payload)
                    matches = reconciliation.get("posts", [])
                    if reconciliation.get("complete") is not True or len(matches) > 1:
                        raise WordPressError("preflight_operation_needs_reconciliation", ambiguous_write=True)
                    if matches:
                        post = matches[0]
                    elif not (reconciliation.get("definitive_absence") is True or
                              reconciliation.get("safe_to_resume") is True and adapter.supports_idempotent_creates):
                        raise WordPressError("preflight_operation_needs_reconciliation", ambiguous_write=True)
                if operation["status"] != "saved" and post is None:
                    operation.update(status="intent", dispatched_at=_preflight_time())
                    _atomic_preflight_json(path, journal)  # Durable exact key/payload BEFORE a POST.
                    try:
                        post = adapter.stage_draft(language, payload, existing_id=None, idempotency_key=keys[language])
                    except Exception as error:
                        operation.update(status="ambiguous" if getattr(error, "ambiguous_write", True) else "rejected",
                                         error=safe_error(error))
                        _atomic_preflight_json(path, journal)
                        raise
                if post is not None:
                    operation.update(status="saved", post_id=post["id"], saved_at=_preflight_time(), receipt=post)
                    _atomic_preflight_json(path, journal)  # Persist known ID before any read/preview.
                    _preflight_saved(language, languages[language], post, slug, keys[language])
                saved = adapter.get_post(operation["post_id"])
                _preflight_saved(language, languages[language], saved, slug, keys[language])
                operation.update(saved_readback=saved, source_sha256=hashlib.sha256(saved["content_raw"].encode()).hexdigest(),
                                 metadata_sha256=fingerprint(languages[language]["metadata"]), readback_at=_preflight_time())
                _atomic_preflight_json(path, journal)
                posts[language] = saved
            if posts["ja"]["id"] == posts["en"]["id"]:
                raise WordPressError("distinct_preflight_bilingual_stages_required")
            for language in ("ja", "en"):
                report = adapter.verify_saved_preview(posts[language])
                journal["previews"][language] = report
                _atomic_preflight_json(path, journal)
                if (report.get("passed") is not True or report.get("errors") != []
                        or report.get("source_sha256") != journal["operations"][language]["source_sha256"]):
                    raise WordPressError("preflight_actual_saved_theme_preview_failed", details={"language": language})
                journal["previews"][language] = _preflight_preview_proof(browser, binding, posts[language], report)
                _atomic_preflight_json(path, journal)
            journal.update(state="private_drafts_verified", completed_at=_preflight_time(), last_error=None)
            _atomic_preflight_json(path, journal)
        except Exception as error:
            journal.update(state="blocked", last_error=safe_error(error), stopped_at=_preflight_time())
            _atomic_preflight_json(path, journal)
            raise
    return {"passed": True, "preflight_only": True, "publication_performed": False, "production_ready": False,
        "production_queue_or_gates_modified": False, "runtime_binding": binding,
        "bundle_digest": identity["bundle_digest"], "plan_path": str(path),
        "plan_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "stages": {language: {"post_id": posts[language]["id"],
            "source_sha256": journal["operations"][language]["source_sha256"],
            "metadata_sha256": journal["operations"][language]["metadata_sha256"],
            "saved_theme_preview_passed": True, "preview_cases": len(journal["previews"][language].get("runs", [])),
            "saved_theme_document_path": journal["previews"][language].get("saved_theme_document_path"),
            "saved_theme_document_sha256": journal["previews"][language].get("saved_theme_document_sha256"),
            "preview_report_path": journal["previews"][language]["preview_report_path"],
            "preview_report_sha256": journal["previews"][language]["preview_report_sha256"]}
            for language in ("ja", "en")}, "verification_context": "authenticated_saved_theme_preview",
        "public_canonical_hreflang_schema_validation_performed": False}


def configured_transport(args):
    if getattr(args, "transport", "direct") == "wpvibe":
        if not args.bridge_dir:
            raise WordPressError("private_bridge_directory_required")
        return ToolWordPressTransport.from_environment(bridge_dir=args.bridge_dir,
            expected_user_id=args.expected_user_id, route=args.route,
            response_timeout=args.bridge_timeout)
    if getattr(args, "bridge_dir", None):
        raise WordPressError("bridge_directory_requires_tool_transport")
    return WordPressTransport.from_environment(route=args.route)


def authenticate(transport, args):
    user = transport.get_current_user()
    roles, caps = user.get("roles"), user.get("capabilities")
    if type(user.get("id")) is not int or user["id"] != 5 or args.expected_user_id != 5:
        raise WordPressError("intended_author_identity_mismatch")
    if (roles != ["author"]
            or not isinstance(caps, dict) or caps.get("manage_options") is True
            or caps.get("edit_posts") is not True or caps.get("publish_posts") is not True):
        raise WordPressError("intended_author_capabilities_missing")
    return {"authenticated": True, "user_id": 5, "role": "author",
            "site_url": transport.site_url, "context": args.context,
            "connection_revision": args.connection_revision,
            "production_ready": False, "authentication_only": True}


def configured_adapter(args, transport=None):
    transport = transport or configured_transport(args)
    plugin_digest = hashlib.sha256(Path(args.approved_plugin).read_bytes()).hexdigest()
    if args.publisher_code_sha256 and args.publisher_code_sha256 != plugin_digest:
        raise WordPressError("approved_local_plugin_artifact_hash_mismatch")
    browser = None
    if args.command in ("execute", "preflight", "first-publish"):
        if not args.browser_artifacts:
            raise WordPressError("configured_private_browser_artifacts_required")
        browser = LiveBrowserVerifier(transport.site_url, args.expected_user_id,
            args.browser_artifacts, chromium=args.chromium)
    adapter = ScopedPublisherAdapter(transport, expected_user_id=args.expected_user_id,
        connection_revision=args.connection_revision, expected_implementation_sha256=plugin_digest,
        expected_policy_version=args.publisher_policy_version,
        public_verifier=PublicPageVerifier(transport.site_url, browser.public if browser else None),
        saved_preview_verifier=browser.saved_preview if browser else None)
    binding = adapter.runtime_binding(args.context)
    if browser:
        browser.runtime_binding = binding
        browser.preview_loader = adapter.get_saved_preview
    return adapter, binding


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="Nonsecret reviewed deployment JSON; never stores Authorization/cookies")
    parser.add_argument("--transport", choices=("direct", "wpvibe"), default="direct")
    parser.add_argument("--bridge-dir", help="Fresh empty private0700 tool RPC directory; no credentials")
    parser.add_argument("--bridge-timeout", type=float, default=45, help="Tool response deadline in seconds, at most60")
    parser.add_argument("--route", choices=("pretty", "query"))
    parser.add_argument("--context")
    parser.add_argument("--connection-revision")
    parser.add_argument("--expected-user-id", type=int)
    parser.add_argument("--approved-plugin", default=str(Path(__file__).resolve().parents[1] / "wordpress/sggroup-news-publisher.php"))
    parser.add_argument("--publisher-code-sha256")
    parser.add_argument("--publisher-policy-version", default="sgnews-restricted-xml-css-1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("authenticate", help="GET-only Author5 identity check; never establishes publisher readiness")
    sub.add_parser("diagnose", help="Verify deployed plugin hash/policy and actual identity with GET only")
    canonical = sub.add_parser("canonicalize", help="Normalize one private fragment before its audits; no saved post")
    canonical.add_argument("--language", choices=("ja", "en"), required=True)
    canonical.add_argument("--slug", required=True)
    canonical.add_argument("--input", required=True)
    canonical.add_argument("--output", required=True)
    probe = sub.add_parser("preflight", help="Persist/reconcile private draft stages and actual saved-theme previews; never publishes or opens gates")
    probe.add_argument("--bundle", required=True)
    probe.add_argument("--plan", required=True)
    probe.add_argument("--browser-artifacts", required=True)
    probe.add_argument("--chromium")
    first = sub.add_parser("first-publish", help="Use the reviewed, bound controlled-first-publication permit; leaves ordinary scheduling paused")
    first.add_argument("--state", default="state/queue.sqlite3")
    first.add_argument("--revision-id", type=int, required=True)
    first.add_argument("--bundle", required=True)
    first.add_argument("--preflight-plan", required=True)
    first.add_argument("--authorization-receipt", required=True)
    first.add_argument("--capacity-receipt", required=True)
    first.add_argument("--browser-artifacts", required=True)
    first.add_argument("--chromium")
    execute = sub.add_parser("execute", help="Resume one already queued revision; requires live readiness gates")
    execute.add_argument("--state", default="state/queue.sqlite3")
    execute.add_argument("--revision-id", type=int, required=True)
    execute.add_argument("--bundle")
    execute.add_argument("--browser-artifacts")
    execute.add_argument("--chromium")
    args = parser.parse_args(argv)
    transport = None
    try:
        if args.config:
            config = json.loads(Path(args.config).read_text(encoding="utf-8"))
            mapping = {"context": "context", "expected_user_id": "expected_user_id",
                "connection_revision": "connection_revision", "publisher_code_sha256": "publisher_code_sha256",
                "publisher_policy_version": "publisher_policy_version", "route": "route",
                "browser_output_dir": "browser_artifacts", "transport": "transport"}
            if not isinstance(config, dict) or set(config) - set(mapping):
                raise WordPressError("invalid_nonsecret_deployment_configuration")
            for key, attribute in mapping.items():
                if key in config:
                    setattr(args, attribute, config[key])
        if (args.transport not in ("direct", "wpvibe") or args.route not in ("pretty", "query") or not args.context or not args.connection_revision
                or type(args.expected_user_id) is not int or args.expected_user_id <= 0):
            raise WordPressError("explicit_current_runtime_binding_required")
        transport = configured_transport(args)
        if args.command == "authenticate":
            result = authenticate(transport, args)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        adapter, binding = configured_adapter(args, transport=transport)
        if args.command == "diagnose":
            result = {"passed": True, "runtime_binding": binding,
                      "supports_idempotent_creates": adapter.supports_idempotent_creates,
                      "supports_staged_updates": adapter.supports_staged_updates}
        elif args.command == "canonicalize":
            canonical = adapter.canonicalize(args.language, args.slug, Path(args.input).read_text(encoding="utf-8"))
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
                handle.write(canonical["content_raw"])
            result = {"passed": True, "output": str(output.resolve()),
                      "source_sha256": canonical["source_sha256"], "requires_fresh_audits": True}
        elif args.command == "preflight":
            result = preflight(adapter, binding, args)
        elif args.command == "first-publish":
            store = StateStore(args.state, runtime_binding=binding)
            bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
            workflow = Workflow(store, adapter, _Validator(), args.context, store.original_prompt)
            # Invalid or incomplete article evidence cannot even create a first
            # publication permit. The reviewed first path rechecks these facts.
            workflow._validate(bundle, store.revision(args.revision_id))
            store.record_first_publication_permit(revision_id=args.revision_id,
                bundle_digest=fingerprint(bundle["languages"]), preflight_plan_path=args.preflight_plan,
                authorization_receipt_path=args.authorization_receipt,
                capacity_receipt_path=args.capacity_receipt, context=args.context)
            result = {**workflow.execute_first_publication(args.revision_id,
                "sgnews-first-cli-" + uuid.uuid4().hex, bundle),
                "controlled_first_publication": True, "automation_changed": False, "production_ready": False}
        else:
            store = StateStore(args.state, runtime_binding=binding)
            bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8")) if args.bundle else None
            workflow = Workflow(store, adapter, _Validator(), args.context, store.original_prompt)
            result = workflow.execute(args.revision_id, "sgnews-cli-" + uuid.uuid4().hex, bundle)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (WordPressError, StateError) as error:
        print(json.dumps({"passed": False, "error": safe_error(error)}, ensure_ascii=False, indent=2))
        return 2
    except Exception as error:
        print(json.dumps({"passed": False, "error": {"type": type(error).__name__,
                                                    "code": "local_runtime_configuration_failed"}}))
        return 2
    finally:
        if transport is not None and isinstance(transport, ToolWordPressTransport):
            transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
