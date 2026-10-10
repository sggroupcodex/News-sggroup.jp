"""Validate private article evidence without certifying unperformed work.

Machine reports are checked against their real output schema and exact payload.
Semantic reviews remain explicitly operator-attested. Actor/task receipts are
required for independent reviews; these local files are not cryptographic remote
attestation, and a caller must retain the actual tool/task receipts honestly.
No Boolean CLI flag can substitute for these records.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit


CHECKS = frozenset(("research_fact_check", "bilingual_semantic_parity", "independent_audit",
                    "viewpoint_reviews", "browser_qa", "clipboard_qa", "slug_cms_collision", "internal_links"))
WIDTHS = (280, 300, 320, 360, 375, 390, 412, 430, 568, 640, 667, 720, 736,
          768, 820, 844, 912, 1024, 1180, 1280, 1366, 1440, 1600, 1920, 2560)
LANDSCAPES = ((667, 375), (736, 414), (844, 390))
VIEWPOINTS = frozenset(("news_editor", "policy_economy_markets", "general_reader", "business_reader",
                       "seo_ai_search", "internal_links", "copyright_legal", "mobile_ux",
                       "editorial_art_direction", "reader_retention", "english_editor",
                       "bilingual_prose_editor", "slug_design", "data_visualization", "delivery_operation"))
STATIC_CHECKS = frozenset(("factual_claims", "copyright", "translation", "process_leaks",
                          "semantic_repetition", "visual_originality", "mobile_readability",
                          "source_data_geometry", "internal_link_relevance", "slug_identity"))
PARITY_DIMENSIONS = frozenset(("facts", "numbers_dates", "arguments", "qualifications", "sources",
                              "figures", "faq", "conclusions"))
MAX_AGE_SECONDS = 86400
MAX_RECEIPT_BYTES = 16 * 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _timestamp(value, now):
    return _number(value) and value > 0 and now - MAX_AGE_SECONDS <= value <= now + 5


def _read_json(path, digest):
    _require(_hash(digest) and _text(path), "receipt path and SHA-256 required")
    try:
        raw = Path(path).read_bytes()
        _require(len(raw) <= MAX_RECEIPT_BYTES, "receipt exceeds size limit")
        _require(hashlib.sha256(raw).hexdigest() == digest, "receipt bytes changed")
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("receipt unavailable or invalid") from exc
    _require(isinstance(value, dict), "receipt must contain an object")
    _require(value.get("test_only") is not True and value.get("execution_mode") not in ("offline", "simulated"),
             "simulated receipt cannot establish production article QA")
    return value


def _successful(report, digest):
    _require(report.get("passed") is True and report.get("errors") == [], "machine QA failed or was not performed")
    _require(report.get("bundle_digest") == digest, "machine QA covers another payload")


def _structural(report, digest):
    _successful(report, digest)
    metrics = report.get("metrics", {})
    for lang in ("ja", "en"):
        m = metrics.get(lang, {})
        for key in ("section_keys", "figure_keys", "figure_types", "faq_keys", "source_keys"):
            _require(isinstance(m.get(key), list) and bool(m[key]), "structural report lacks measured " + key)
        _require(len(m["figure_keys"]) >= 6 and len(set(m["figure_types"])) >= 4,
                 "required figure counts were not measured")
        _require(6 <= len(m["faq_keys"]) <= 10, "required FAQ count was not measured")
    ja_count = metrics["ja"].get("eligible_body_characters")
    _require(type(ja_count) is int and ja_count >= 10000, "measured Japanese article is below 10000")
    for key in ("section_keys", "figure_keys", "figure_types", "faq_keys", "source_keys"):
        _require(metrics["ja"][key] == metrics["en"][key], "bilingual structural correspondence differs")


def _browser(report, digest, viewer_sha256, receipt):
    _successful(report, digest)
    _require(_hash(viewer_sha256) and report.get("viewer_sha256") == viewer_sha256,
             "browser QA covers another viewer")
    _require(report.get("browser") == "actual headless Chromium via Playwright" and _text(report.get("chromium_path")),
             "actual browser provenance missing")
    _require(report.get("viewport_widths") == list(WIDTHS), "required viewport coverage missing")
    _require(report.get("landscapes") == [list(p) for p in LANDSCAPES], "required landscape coverage missing")
    measurements = report.get("measurements")
    _require(isinstance(measurements, list) and measurements, "actual browser measurements missing")
    _require(all(isinstance(m, dict) and m.get("errors") == [] for m in measurements), "browser measurements contain failures")
    for lang in ("ja", "en"):
        for width, height in [(w, 900) for w in WIDTHS] + list(LANDSCAPES):
            for constrained in (False, True):
                matching = [m for m in measurements if m.get("language") == lang and m.get("width") == width
                            and m.get("height") == height and m.get("constrained_host") is constrained]
                _require(bool(matching), "article browser viewport/host combination missing")
                for measured in matching:
                    _require(isinstance(measured.get("root"), dict) and isinstance(measured.get("rects"), list)
                             and measured["rects"] and all(_number(measured["root"].get(k)) for k in ("left", "right", "width"))
                             and _number(measured.get("viewportWidth")) and measured["viewportWidth"] > 0
                             and _number(measured.get("documentScrollWidth"))
                             and measured["documentScrollWidth"] <= measured["viewportWidth"] + 1,
                             "actual rendered article rectangle/scroll measurements missing")
                    count = measured.get("eligibleBodyCharacters")
                    _require(type(count) is int and count >= (10000 if lang == "ja" else 1),
                             "actual visible article body was not measured or below floor")
            _require(any(m.get("surface") == "viewer" and m.get("language") == lang and
                         m.get("width") == width and m.get("height") == height for m in measurements),
                     "viewer browser viewport combination missing")
    for metric in ("max_margin_difference_px", "max_reading_edge_difference_px"):
        _require(_number(report.get(metric)) and 0 <= report[metric] <= 2, "browser alignment measurement failed")
    screenshots, hashes = report.get("private_screenshots"), receipt.get("screenshot_sha256")
    _require(isinstance(screenshots, list) and screenshots and isinstance(hashes, dict) and set(hashes) == set(screenshots),
             "browser screenshot evidence missing")
    for path in screenshots:
        try:
            _require(_hash(hashes[path]) and hashlib.sha256(Path(path).read_bytes()).hexdigest() == hashes[path],
                     "browser screenshot changed")
        except OSError as exc:
            raise ValueError("browser screenshot unavailable") from exc


def _clipboard(report):
    clipboard = report.get("clipboard", {})
    _require(clipboard.get("passed") is True and clipboard.get("errors") == [], "actual clipboard QA failed")
    tests = clipboard.get("tests", [])
    _require(isinstance(tests, list) and tests, "actual clipboard observations missing")
    targets = {lang + ":" + key for lang in ("ja", "en") for key in ("html", "title", "aioseo_title", "aioseo_description", "slug")}
    for route in ("normal", "write-unavailable-fallback", "write-rejected-fallback", "both-denied"):
        for target in targets:
            matches = [t for t in tests if isinstance(t, dict) and t.get("route") == route and t.get("target") == target]
            _require(len(matches) == 1, "clipboard route/target observation missing or duplicated")
            t = matches[0]
            _require(t.get("pass") is True and type(t.get("clicks")) is int and t["clicks"] == 1
                     and t.get("focus_restored") is True and t.get("temporary_nodes_removed") is True,
                     "clipboard one-click/focus/cleanup test failed")
            _require((t.get("state") == "error" and t.get("exact_clipboard_match") is False) if route == "both-denied"
                     else (t.get("state") == "success" and t.get("exact_clipboard_match") is True),
                     "clipboard success/denial was misreported")
    for target in ("ja:title", "en:title", "ja:html"):
        _require(any(t.get("route") == "consecutive-tab-change" and t.get("target") == target and t.get("pass") is True
                     and t.get("exact_clipboard_match") is True and t.get("clicks") == 1 for t in tests),
                 "consecutive language-switch clipboard test missing")


def _review_checks(report, required):
    checks = report.get("checks", [])
    _require(isinstance(checks, list), "review observations required")
    mapping = {c.get("id"): c for c in checks if isinstance(c, dict)}
    _require(required <= set(mapping), "review coverage incomplete")
    for key in required:
        _require(mapping[key].get("result") == "passed" and _text(mapping[key].get("observations")),
                 "unresolved or assertion-only review: " + key)


def _validate_article_evidence(check, record, bundle_digest, prompt_sha256, runtime_binding,
                               test_mode, clock, *, viewer_sha256=None):
    """Fail closed; synthetic fixtures require an explicit test store and label.

    Receipts are JSON paths with SHA-256, kind, tool, action, actor_id, proof_id,
    and performed_at. Independent checks additionally require author_proof_ids
    and a task_identity receipt naming their separate completed reviewer task.
    Browser receipts carry screenshot_sha256 for every private screenshot.
    """
    _require(check in CHECKS and isinstance(record, dict), "unknown article evidence check")
    if test_mode and record.get("test_only") is True and record.get("execution_mode") == "simulated":
        _require(record.get("passed") is True and record.get("bundle_digest") == bundle_digest
                 and record.get("original_prompt_sha256") == prompt_sha256, "test evidence binding failed")
        return
    now = clock() if callable(clock) else clock
    _require(_number(now) and now > 0, "valid runtime clock required")
    _require(record.get("schema_version") == 1 and record.get("check") == check
             and record.get("test_only") is False and record.get("passed") is True,
             "executed production article evidence required")
    _require(_hash(bundle_digest) and record.get("bundle_digest") == bundle_digest and _hash(prompt_sha256)
             and record.get("original_prompt_sha256") == prompt_sha256, "article/prompt evidence binding failed")
    _require(isinstance(runtime_binding, dict) and runtime_binding and record.get("runtime_binding") == runtime_binding,
             "article evidence belongs to another runtime")
    _require(all(type(record.get(k)) is int and record[k] == 0 for k in ("unresolved_blockers", "unresolved_major")),
             "article evidence has unresolved findings")
    performed, until = record.get("performed_at"), record.get("valid_until")
    _require(_timestamp(performed, now) and _number(until) and now < until <= performed + MAX_AGE_SECONDS,
             "article evidence was not performed recently or expired")
    machine = check in ("browser_qa", "clipboard_qa")
    _require(record.get("evidence_kind") == ("machine_receipt" if machine else "operator_attested")
             and record.get("execution_mode") == ("actual_browser" if machine else "operator_attested"),
             "evidence origin/mode must accurately describe the performed work")
    author, producer = record.get("author_actor_id"), record.get("producer_actor_id")
    _require(_text(author) and _text(producer), "article author and evidence producer identities required")
    receipts = record.get("receipts")
    _require(isinstance(receipts, list) and receipts, "executed action receipts required")
    loaded, proof_ids = {}, set()
    for receipt in receipts:
        _require(isinstance(receipt, dict) and all(_text(receipt.get(k)) for k in ("kind", "tool", "action", "actor_id", "proof_id")),
                 "receipt action/provenance missing")
        _require(_timestamp(receipt.get("performed_at"), now), "receipt execution time invalid or stale")
        _require(receipt["proof_id"] not in proof_ids, "duplicate action proof identity")
        proof_ids.add(receipt["proof_id"])
        loaded.setdefault(receipt["kind"], []).append((receipt, _read_json(receipt.get("path"), receipt.get("sha256"))))
    independent = check in ("independent_audit", "viewpoint_reviews")
    if independent:
        author_proofs = record.get("author_proof_ids")
        _require(isinstance(author_proofs, list) and author_proofs and all(_text(p) for p in author_proofs)
                 and not (set(author_proofs) & proof_ids), "independent review reuses author proof or lacks authorship trace")
        _require(producer != author and record.get("reviewer_actor_id") == producer,
                 "article author cannot certify independent review")
        validator_ids = record.get("validation_actor_ids", [])
        _require(isinstance(validator_ids, list) and (check != "viewpoint_reviews" or validator_ids)
                 and producer not in validator_ids, "third-person reviewer must be separate from author/validation work")
        identities = loaded.get("task_identity", [])
        _require(any(r["actor_id"] == producer and p.get("actor_id") == producer and p.get("proof_id") == r["proof_id"]
                     and p.get("source") == "native_task_receipt" and p.get("task_status") == "completed" for r, p in identities),
                 "separate completed reviewer task provenance is not established")

    def one(kind):
        reports = loaded.get(kind, [])
        _require(len(reports) == 1, "exactly one " + kind + " action receipt required")
        if kind not in ("browser_qa", "structural_audit"):
            receipt, report = reports[0]
            _require(receipt["actor_id"] == producer and report.get("bundle_digest") == bundle_digest
                     and report.get("original_prompt_sha256") == prompt_sha256,
                     "semantic review receipt belongs to another actor/article/prompt")
        return reports[0]

    if check in ("browser_qa", "clipboard_qa"):
        receipt, report = one("browser_qa")
        _browser(report, bundle_digest, viewer_sha256, receipt)
        _clipboard(report)
    elif check == "independent_audit":
        _, report = one("structural_audit")
        _structural(report, bundle_digest)
        receipt, review = one("independent_review")
        _require(receipt["actor_id"] == producer, "independent review action belongs to another actor")
        _review_checks(review, STATIC_CHECKS)
    elif check == "viewpoint_reviews":
        receipt, report = one("viewpoint_reviews")
        _require(receipt["actor_id"] == producer, "viewpoint review action belongs to another actor")
        _review_checks(report, VIEWPOINTS)
    elif check == "bilingual_semantic_parity":
        _, report = one(check)
        _require(set(report.get("reviewed_dimensions", [])) >= PARITY_DIMENSIONS and report.get("omissions") == []
                 and report.get("unresolved_mismatches") == [], "full semantic parity review incomplete")
        units = report.get("mapped_units", [])
        _require(isinstance(units, list) and units and all(isinstance(u, dict) and all(_text(u.get(k)) for k in ("id", "ja", "en")) for u in units),
                 "actual Japanese/English correspondence observations missing")
    elif check == "research_fact_check":
        _, report = one(check)
        claims, sources = report.get("claims", []), report.get("sources", [])
        _require(isinstance(claims, list) and claims and isinstance(sources, list) and sources, "claim/source fact-check matrix missing")
        keys = set()
        for source in sources:
            _require(isinstance(source, dict) and _text(source.get("key")) and _text(source.get("url"))
                     and urlsplit(source["url"]).scheme in ("https", "http") and _timestamp(source.get("retrieved_at"), now)
                     and source.get("retrieval_status") == "retrieved" and type(source.get("primary")) is bool,
                     "source was not actually retrieved/classified")
            fetched = _read_json(source.get("receipt_path"), source.get("receipt_sha256"))
            _require(fetched.get("url") == source["url"] and fetched.get("retrieval_status") == "retrieved"
                     and _text(fetched.get("content")), "source receipt does not contain the retrieved source")
            keys.add(source["key"])
        for claim in claims:
            _require(isinstance(claim, dict) and _text(claim.get("claim_id")) and _text(claim.get("observation"))
                     and claim.get("classification") in ("fact", "attributed", "analysis", "inference", "forecast", "uncertain")
                     and isinstance(claim.get("source_keys"), list) and bool(claim["source_keys"])
                     and set(claim["source_keys"]) <= keys, "unsupported important factual claim")
    elif check == "slug_cms_collision":
        _, report = one(check)
        scopes = report.get("checked_scopes", [])
        mapping = {s.get("scope"): s for s in scopes if isinstance(s, dict)}
        _require({"published", "draft", "pending", "private", "sitemap", "site_search"} <= set(mapping)
                 and report.get("unresolved_collisions") == [] and _text(report.get("baseline_date_basis")),
                 "slug identity/collision checks missing")
        for scope in mapping.values():
            _require(scope.get("result") in ("complete", "unavailable") and _text(scope.get("details")), "slug coverage claim lacks actual outcome")
    elif check == "internal_links":
        _, report = one(check)
        links = report.get("links", [])
        _require(isinstance(links, list) and links and report.get("unresolved_errors") == [], "internal-link observations missing")
        _require({l.get("language") for l in links if isinstance(l, dict)} == {"ja", "en"}, "both language link audits required")
        for link in links:
            _require(isinstance(link, dict) and link.get("language") in ("ja", "en") and link.get("http_status") == 200
                     and _text(link.get("relevance_reason")) and link.get("relevant") is True and _timestamp(link.get("checked_at"), now),
                     "internal link was not actually checked")
            for key in ("url", "final_url", "canonical"):
                value = link.get(key)
                _require(_text(value), "internal link URL/canonical missing")
                parsed = urlsplit(value)
                _require(parsed.scheme == "https" and parsed.hostname in ("sggroup.jp", "www.sggroup.jp")
                         and parsed.path.startswith("/" + link["language"] + "/") and parsed.path.endswith("/")
                         and not parsed.query and not parsed.fragment and not parsed.username and not parsed.password,
                         "internal link URL/canonical is not a clean same-language site URL")
            fetched = _read_json(link.get("receipt_path"), link.get("receipt_sha256"))
            _require(all(fetched.get(k) == link[k] for k in ("url", "http_status", "final_url", "canonical", "language"))
                     and _hash(fetched.get("content_sha256")), "internal-link receipt does not establish the observed response")


def validate_article_evidence(check, record, bundle_digest, prompt_sha256, runtime_binding,
                              test_mode, clock, *, viewer_sha256=None):
    """Validate an evidence record; malformed/unestablished records raise ValueError."""
    try:
        return _validate_article_evidence(check, record, bundle_digest, prompt_sha256, runtime_binding,
                                          test_mode, clock, viewer_sha256=viewer_sha256)
    except (TypeError, KeyError, OverflowError, AttributeError) as exc:
        raise ValueError("malformed article evidence schema") from exc
