# SG Group News publication repair

This implementation addresses duplicate publication, ambiguous write responses,
partial Japanese/English publication, preserved URLs during corrections, and
article inspection before publication. It supports a native hourly monitor that
researches internationally significant developments in financial markets,
economics, military affairs, geopolitics, politics, strategic chokepoints,
security, and shipping/sea lanes. Research, significance decisions, original
writing, complete translation, and semantic reviews remain agent tasks.

**Scheduling is paused.** On 2026-10-11, installed plugin 0.1.0 and the
Author user 5 dedicated capability/dependencies were verified through its real
status API. Reviewed plugin 0.1.1 adds managed-News schema and head corrections
and still requires an administrator's independent upload. The configured direct
HTTP connection returns 401; native WPVibe execution reuses the existing connector
without a second authorization secret. Actual saved-theme inspection and a
controlled first publication remain pending.

The automation must keep its existing Author user 5 connection. Do not connect
an administrator account or elevate its role. The site administrator performs
the one-time installation and narrow capability grant independently through
the site's normal administration path.

## Acceptance order

1. Verify the actual execution path authenticates as Author user 5. A site
   administrator independently installs the reviewed scoped WordPress route
   and grants only `sgnews_publish` to that account.
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
| `runtime/tool_transport.py` | Private request/response bridge using the existing Author connector without handling its credentials |
| `runtime/wpvibe_driver.js` | Trusted Codex tool orchestration for bridged REST requests; exact JSON and incomplete-response checks |
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

Use Python 3.12+, PHP 8.3 with DOM, Chromium, and Node.js 18+ for the driver tests. Preserve the execution
environment's proxy settings and CA trust.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s runtime/tests -v
python -m unittest runtime.test_wordpress_transport -v
node runtime/tests/test_wpvibe_driver.js
php -l wordpress/sggroup-news-publisher.php
php wordpress/test-sanitizer.php
php wordpress/test-lifecycle.php
php wordpress/test-admin-setup.php
```

Chromium tests default to `/usr/bin/chromium`. See the browser module for its
supported executable override. Security fixtures and synthetic article fixtures
exercise behavior; they cannot pass production article or live deployment gates.

## Private runtime configuration

Supply the exact private `editorial-prompt-ja.txt`. Copy `goals.example.json` to
private `goals.json` and enter that file's SHA-256. Native Codex execution uses
`--transport wpvibe --bridge-dir NEW_PRIVATE_DIRECTORY` and the existing connected
Author account. No new password or API secret needs to be entered in chat or
copied into the Python worker. The bridge carries requests and responses, not
connector credentials. This transport fixes the site to `https://sggroup.jp`,
News category 258 and the posting identity to user 5; callers cannot select an
administrator account or another destination.

Direct HTTP is an optional alternative. For that path only, configure
`WP_BASE_URL`, `WP_NEWS_CATEGORY_ID`, and `WP_AUTHORIZATION` through the normal
private runtime credential configuration. The authorization value is the
complete header or managed proxy placeholder: do not print, decode, re-encode,
or normalize it. Direct-path authentication failure does not invalidate a
separately verified native connector path.

The posting identity and connection revision must be explicitly bound to the
actual environment. A stable secret placeholder's hash does not identify a
credential rotation. Keep runtime bindings and executed gate/article receipts
private, and invalidate them whenever the relevant code, connection, or content
changes. Gates expire and are revalidated before external mutations.

```bash
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

For the user's explicitly authorized first JA/EN pair, `preflight` creates an
immutable private plan before any draft POST, records exact saved draft IDs,
and measures both authenticated native-theme previews at all 28 required
viewports. It does not publish or enable automation. Unknown writes require
the server's actual operation journal; an incomplete plan is resumed using its
same keys instead of blindly creating drafts again.

`first-publish` adopts only that exact verified pair and still requires all eight
original article audits, the four prepublication deployment gates, a current
receipt for the user's publication instruction, and fresh measured API quota
with recovery headroom. Its sole-revision permit binds current code, connection,
prompt, event and staged source. It preserves a previously published side on
partial resume and uses the normal fenced publication/reconciliation and public
verification logic. It leaves the hourly pause and all production gates intact.
Only actual successful public JA/EN checks can establish the remaining language
and complete-run capacity gates before the hourly automation is enabled.

The bridge requests complete fields and an 8 MiB response allowance. The real
English fragment contains 137,170 characters, already exceeding WPVibe's
default 50,000-character response limit. Truncated, malformed or error responses
must fail closed; saved HTML, metadata and theme previews must be complete and
match their expected hashes. An allowance is not evidence that the service
accepted or returned the complete payload. A lost write response retains its
operation key and requires server-journal reconciliation before retrying.

Verify API capacity before enabling production. The currently observed free
plan allows 100 calls per rolling 24 hours. Budget live canonicalization and
private preflight separately; controlled first publication reuses their saved
stages and requires at least 30 remaining calls plus 10 recovery calls immediately
before executing a fresh pair. The estimate drops for verified partial resumes.
These estimates are neither publishing limits nor capacity certification.
Budget actual remaining calls, queued work
and recovery headroom, and keep publication paused when capacity is insufficient.
An unchanged or non-actionable monitoring run should make no WordPress calls
and send no routine status notification.

For native execution, the trusted `runtime/wpvibe_driver.js` is loaded in
`functions.exec` and its `runWpvibe()` function drives the Python CLI and the
available `wpvibe_rest_api` tool. It creates a fresh private bridge directory and
forces `--transport wpvibe --expected-user-id 5`; invoking the bridged Python
command alone does not execute connector calls. Pass the context
`wpvibe:sggroup.jp:author-5` and a connection revision from fresh observed
connector identity evidence. A direct worker configuration revision is not
evidence for this connector.

Run this in `functions.exec`, loading only the trusted local driver source and
replacing the revision placeholder with current verified connector evidence:

```javascript
const source = await tools.exec_command({
  cmd: "cat runtime/wpvibe_driver.js",
  workdir: "/workspace/sggroup-news-automation", max_output_tokens: 20000
});
if (source.exit_code !== 0) throw new Error("Cannot load the trusted driver");
eval(source.output);
text(await globalThis.runWpvibe({args: ["--route", "query", "--context",
  "wpvibe:sggroup.jp:author-5", "--connection-revision",
  "ACTUAL_OBSERVED_CONNECTOR_REVISION", "authenticate"]}));
```

`authenticate` verifies the actual Author identity only and does not establish
publisher readiness. Run `diagnose` separately to require the dedicated
capability, deployed route, reviewed code hash and dependencies.

For the optional direct path, copy `deployment.example.json` to private
`deployment.json` and supply the verified current environment, connection
revision and reviewed deployed PHP hash. Run
`python -m runtime.publish --transport direct --config deployment.json diagnose`.
The example contains placeholders and cannot make the worker ready. Authorization
must stay out of this configuration file.

See `wordpress/plugin-review.md` for the Japanese installation review, and
`wordpress/admin-install.md` for the scoped administrator deployment and
recovery procedure. The PHP lifecycle harness uses WordPress API doubles to
exercise ordering and injected failures; it does not establish live database,
theme, language-controller or AIOSEO compatibility.

Plugin 0.1.1 additionally scopes NewsArticle/SG Group Organization/free-access
schema and own canonical/reciprocal JA-EN/x-default head corrections to intact
owned single managed News posts and their authenticated previews. It modifies
AIOSEO's existing single Article graph and adds no extra JSON-LD script.
Ordinary existing news and archives remain unchanged. Actual saved-preview and
published-page head checks are mandatory even after the offline regressions pass.
