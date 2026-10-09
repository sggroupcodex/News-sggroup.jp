# SG Group News Publisher installation

This is a reviewable integration candidate, not an installed or activated plugin. The current WordPress connection is Author user 5. It can publish ordinary posts but cannot install plugins or preserve the template's mandatory style element through core KSES. This integration stores validated source in protected plugin metadata and renders it through a dedicated path. KSES remains active for ordinary posts and for the readable core fallback.

An administrator must perform the following setup through the site's normal administration path:

1. Review sggroup-news-publisher.php and the restricted grammar below. Back up the site and test the package in staging.
2. Upload sggroup-news-publisher-0.1.0.zip through WordPress **Plugins → Add New → Upload Plugin**, then activate it. PHP 8.1 or later with DOM/libxml is required.
3. Confirm the intended account is user **5**, Codex Account For Posting Articles. Keep its Author role. Grant only the dedicated sgnews_publish capability. With an administrator-authorized WP-CLI session:

   ~~~sh
   wp user add-cap 5 sgnews_publish
   ~~~

   The plugin grants no capabilities automatically. Do not grant unfiltered_html, change the Author role globally, disable KSES, or install executable snippets to bypass setup.
4. Confirm the existing sg-group-language-controller taxonomy retains Japanese term 261 and English term 262, and News category 258 remains news. The plugin fixes these IDs and the owner; callers cannot select another account, category or language taxonomy.
5. Configure automation authorization through its normal secret/account configuration. Do not copy credentials into articles, the repository, issue bodies or chat. A ready secret binding is insufficient: authenticated users/me must actually succeed as the intended account.
6. GET /wp-json/sgnews-publisher/v1/status must show owner/user 5, the dedicated capability, dependencies and AIOSEO abilities ready, the expected policy version, and an implementation SHA256 matching the reviewed PHP file. This verifies installed code, not article quality.
7. Exercise the complete unpublished flow: lease, canonicalize both fragments, save both stages, inspect source and actual AIOSEO read-back, then obtain each stage's authenticated native theme preview through GET /articles/{id}/preview. This endpoint returns JSON containing theme-rendered HTML and its source hash; it does not publish, impersonate a user or grant core editing access. Use its base_url when saving previews locally so relative theme assets resolve. Test saved previews and the copy viewer at all required widths.
8. Verify the language controller preserves the requested public shared slug. Different internal post names, including an English -2 suffix, are permitted. Revisions retain the existing internal identifier and public URL. Inspect actual canonical and hreflang output on the site.
9. Keep production paused until authenticated integration, real theme previews, actual AIOSEO metadata, canonical/hreflang/free-access schema, the original editorial audits and full browser checks pass. Offline tests establish neither live deployment nor atomic bilingual publication.

## Source and policy contract

POST /canonicalize accepts html, language and shared_slug. Use returned content_raw for final audits, the copy viewer and digests. Input is well-formed XML-compatible HTML: exactly one plain style followed by one article, explicit closing tags and self-closing void elements. Use Unicode or numeric entities instead of undefined named entities such as &nbsp;.

The root is sg-news- plus the first 12 lowercase hexadecimal characters of SHA256(shared slug), with the intended lang. Descendant IDs use that root prefix; classes use sgn-. Established data-sgn-* audit attributes are supported in HTML.

The restricted CSS parser supports scoped descendants/children, sgn- classes, rooted IDs, approved elements, simple focus/hover/structural pseudos, bounded listed declarations, --sgn- numeric/colour tokens, approved layout/colour/math functions, width queries, print and reduced motion. It rejects attribute selectors, escapes, comments, unknown at-rules, resources/url(), external fonts, !important, fixed/sticky positioning, transforms and unlisted declarations. Full-bleed viewport geometry is limited to the exact root's width:100vw and margin-inline:calc(50% - 50vw), or listed logical/physical margin equivalents. Use classes for CSS and retain audit attributes for inspection.

SVG supports bounded geometry/text/local markers, with namespace and viewBox. Scripts, handlers, foreignObject, image/use/link/animation elements, external references and unknown namespaces are rejected. Ordinary navigation/source links support HTTP(S). Unsupported input is rejected rather than silently deleted. Inline style attributes are unsupported.

Literal brackets and text line breaks are canonically entity-encoded without changing displayed text. This prevents shortcode/block/autoembed interpretation. Source renders after the normal content filters. Core post_content is a readable KSES fallback; the custom article endpoint's content_raw is authoritative.

## Leases and interrupted work

One fenced news lease uses add_option uniqueness and an internal prepared compare-and-swap of its own option row. There is no general SQL endpoint. A separate non-expiring mutation barrier serializes writes across lease expiry.

Resume a request using its same key and exact fingerprint only when GET /operations/{key} explicitly reports safe_to_resume:true. Do not infer absence from a generic post search. Stages remain immutable; publication creates or updates canonical posts. Languages publish separately, so retain and reconcile a partial pair.

A crashed barrier is not reclaimed automatically. An administrator must stop automation, confirm the request/worker has ended, inspect operation/source/SEO state and record the recovery decision. Only then may the administrator remove this single barrier:

~~~sh
wp option delete _sgnews_publisher_mutation_v1
~~~

Do not delete lease or operation mappings. Reconcile the persisted operation, acquire a new fenced lease and resume the same validated request. Never delete or publish automation ledger draft 4907.

## Verification boundaries

test-sanitizer.php executes the real PHP DOM/CSS validator. test-lifecycle.php executes plugin logic against WordPress API doubles, including idempotency, ownership, lease CAS interleavings and source/pair gates. They do not replace live database concurrency, active-theme preview, actual AIOSEO integration or frontend inspection. Compensation is best effort and reports failures; WordPress hooks and external effects are not an atomic transaction.
