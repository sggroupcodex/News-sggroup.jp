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
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import re
import threading
import uuid

from .article_qa import article_root_id, validate_bundle
from .browser_qa import LANDSCAPES, MEASURE_JS, WIDTHS
from .state import StateError, StateStore
from .workflow import Workflow, safe_error
from .wordpress_publisher_adapter import PublicPageVerifier, ScopedPublisherAdapter
from .wordpress_transport import WordPressError, WordPressTransport


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


def configured_adapter(args):
    transport = WordPressTransport.from_environment(route=args.route)
    plugin_digest = hashlib.sha256(Path(args.approved_plugin).read_bytes()).hexdigest()
    if args.publisher_code_sha256 and args.publisher_code_sha256 != plugin_digest:
        raise WordPressError("approved_local_plugin_artifact_hash_mismatch")
    browser = None
    if args.command == "execute":
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
    parser.add_argument("--route", choices=("pretty", "query"))
    parser.add_argument("--context")
    parser.add_argument("--connection-revision")
    parser.add_argument("--expected-user-id", type=int)
    parser.add_argument("--approved-plugin", default=str(Path(__file__).resolve().parents[1] / "wordpress/sggroup-news-publisher.php"))
    parser.add_argument("--publisher-code-sha256")
    parser.add_argument("--publisher-policy-version", default="sgnews-restricted-xml-css-1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("diagnose", help="Verify deployed plugin hash/policy and actual identity with GET only")
    canonical = sub.add_parser("canonicalize", help="Normalize one private fragment before its audits; no saved post")
    canonical.add_argument("--language", choices=("ja", "en"), required=True)
    canonical.add_argument("--slug", required=True)
    canonical.add_argument("--input", required=True)
    canonical.add_argument("--output", required=True)
    execute = sub.add_parser("execute", help="Resume one already queued revision; requires live readiness gates")
    execute.add_argument("--state", default="state/queue.sqlite3")
    execute.add_argument("--revision-id", type=int, required=True)
    execute.add_argument("--bundle")
    execute.add_argument("--browser-artifacts")
    execute.add_argument("--chromium")
    args = parser.parse_args(argv)
    try:
        if args.config:
            config = json.loads(Path(args.config).read_text(encoding="utf-8"))
            mapping = {"context": "context", "expected_user_id": "expected_user_id",
                "connection_revision": "connection_revision", "publisher_code_sha256": "publisher_code_sha256",
                "publisher_policy_version": "publisher_policy_version", "route": "route",
                "browser_output_dir": "browser_artifacts"}
            if not isinstance(config, dict) or set(config) - set(mapping):
                raise WordPressError("invalid_nonsecret_deployment_configuration")
            for key, attribute in mapping.items():
                if key in config:
                    setattr(args, attribute, config[key])
        if (args.route not in ("pretty", "query") or not args.context or not args.connection_revision
                or type(args.expected_user_id) is not int or args.expected_user_id <= 0):
            raise WordPressError("explicit_current_runtime_binding_required")
        adapter, binding = configured_adapter(args)
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


if __name__ == "__main__":
    raise SystemExit(main())
