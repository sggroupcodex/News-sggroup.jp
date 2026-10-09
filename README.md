# SG Group News publication repair

This implementation addresses duplicate publication, ambiguous write responses,
partial Japanese/English publication, preserved URLs during corrections, and
article inspection before publication. It supports a native hourly monitor that
researches internationally significant developments in financial markets,
economics, military affairs, geopolitics, politics, strategic chokepoints,
security, and shipping/sea lanes. Research, significance decisions, original
writing, complete translation, and semantic reviews remain agent tasks.

**Scheduling is paused.** The current posting account cannot install the required
WordPress integration. The configured direct worker connection has not
authenticated successfully. Local tests do not establish a working live route
or a published article.

## Acceptance order

1. Authenticate the actual worker as the intended posting identity and install
   the reviewed scoped WordPress route through an administrator.
2. Save an unpublished probe, read the exact canonical HTML back, and inspect
   the real saved page. Required scoped `style` must survive; SVG must survive
   when the article uses SVG. HTML/CSS figures are also permitted.
3. Verify execution capacity, API headroom, persistent storage, ownership,
   deduplication, retrieval gaps, and recovery.
4. Produce a complete real bilingual article against the private authoritative
   editorial prompt. Independently review and repair it, then repeat the affected
   inspections on the final exact artifact.
5. Publish that verified real article as a controlled first pair and inspect
   both public pages. Retain known post IDs during partial recovery.
6. Enable the hourly monitor last. Confirm an actual scheduled invocation;
   registration or enabled settings alone are not execution evidence.

The original prompt, credentials, generated articles, operation state, and QA
receipts belong in private storage and are deliberately excluded from this
repository. A news article's sole delivery artifact is its prescribed combined
copy viewer, with two complete HTML fragments and eight metadata targets.

## Components

| Component | Purpose |
| --- | --- |
| `runtime/state.py` | Durable event/revision queue, source watermarks, readiness gates, fenced ownership, operation intents |
| `runtime/workflow.py` | Both saved drafts before publication, exact read-back, ambiguous-write reconciliation, corrections and partial recovery |
| `runtime/wordpress_transport.py` | HTTPS transport preserving injected authorization, proxy and CA trust; no automatic write retries |
| `runtime/wordpress_publisher_adapter.py` | Reviewed scoped route, server ownership, exact source read-back and public verification |
| `runtime/publish.py` | Concrete diagnostic, canonicalization and execution CLI with actual saved/public browser inspection |
| `wordpress/sggroup-news-publisher.php` | Narrow editorial HTML validation/rendering without globally granting unfiltered HTML |
| `runtime/article_qa.py` | Deterministic structural inspections, eligible Japanese character counting, scoped CSS and bilingual structure |
| `runtime/browser_qa.py` | Actual Chromium geometry at 25 widths and landscape sizes, and real clipboard checks |
| `runtime/copy_viewer.py` | One bilingual copy viewer, exact text targets, functioning clipboard fallback and honest failure feedback |
| `runtime/evidence.py` | Payload/context-bound machine reports and distinct reviewer receipts |
| `readiness.py` | Diagnostic status; never enables scheduling |

All local workers must share one persistent SQLite database on storage that
supports SQLite locking. SQLite is not a cross-host lock or a backup. WordPress
server ownership and its operation journal provide a separate boundary.
Operator-attested receipts are not cryptographic remote execution attestation.
Static structural parity does not prove factual accuracy or complete translation.

## Local verification

Use Python 3.12+, PHP 8.3 with DOM, and Chromium. Preserve the execution
environment's proxy settings and CA trust.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s runtime/tests -v
python -m unittest runtime.test_wordpress_transport -v
php -l wordpress/sggroup-news-publisher.php
php wordpress/test-sanitizer.php
php wordpress/test-lifecycle.php
```

Chromium tests default to `/usr/bin/chromium`. See the browser module for its
supported executable override. Security fixtures and synthetic article fixtures
exercise behavior; they cannot pass production article or live deployment gates.

## Private runtime configuration

Supply the exact private `editorial-prompt-ja.txt`. Copy `goals.example.json` to
private `goals.json` and enter that file's SHA-256. Configure `WP_BASE_URL`,
`WP_NEWS_CATEGORY_ID`, and `WP_AUTHORIZATION` through the runtime's credential
configuration. The authorization value is the complete header or the managed
proxy placeholder: do not print, decode, re-encode, or normalize it.

The posting identity and connection revision must be explicitly bound to the
actual environment. A stable secret placeholder's hash does not identify a
credential rotation. Keep runtime bindings and executed gate/article receipts
private, and invalidate them whenever the relevant code, connection, or content
changes. Gates expire and are revalidated before external mutations.

```bash
python -m runtime.wordpress_transport diagnose
python -m runtime.workflow --database state/queue.sqlite3 --context ACTUAL_CONTEXT status
python -m runtime.workflow --database state/queue.sqlite3 --context ACTUAL_CONTEXT pause --reason 'live deployment checks pending'
python readiness.py --context ACTUAL_CONTEXT --runtime-binding runtime/runtime-binding.json
```

Use `python -m runtime.workflow --help` for source registration, atomic scan
ingestion, gate recording, and correction queue commands. Failed retrieval must
retain its coverage gap. Semantic event identity and material facts must come
from verified research; headline strings alone are not deduplication keys.

The WordPress core REST draft path is a diagnostic only. It must not replace the
scoped route for required styled articles, idempotent creates, staged updates,
or unknown write outcomes. A missing route, changed identity, failed QA, missing
receipt, unresolved mutation, or insufficient capacity keeps publication blocked.

Run `python -m runtime.publish --help` for the scoped diagnostic, canonicalization
and execution entry points. Bind the reviewed local plugin bytes to the live
deployment's reported hash and policy. Canonicalize source before building the
viewer or recording article evidence. Execution fetches the saved stage's actual
active-theme rendering through the authenticated scoped preview API, inspects
that output in Chromium, then performs anonymous checks at the real public URLs.
An unavailable saved preview blocks publication. Keep browser artifacts private.

Copy `deployment.example.json` to private `deployment.json` and supply the
verified current environment, connection revision and reviewed deployed PHP
hash. Then run `python -m runtime.publish --config deployment.json diagnose`.
The example contains placeholders and cannot make the worker ready. Authorization
must stay out of this configuration file.

See `wordpress/admin-install.md` for the scoped administrator deployment and
recovery procedure. The PHP lifecycle harness uses WordPress API doubles to
exercise ordering and injected failures; it does not establish live database,
theme, language-controller or AIOSEO compatibility.
