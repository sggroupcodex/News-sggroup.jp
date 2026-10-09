"""Private, deterministic structural audit of the finished bilingual payload.

This deliberately cannot certify facts, originality, prose, semantic translation,
link relevance or CMS availability. Those require separately recorded reviews.
Contract attributes are structural selectors, never a place for production logs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

import tinycss2
from cssselect2 import parser as css_parser


METADATA_KEYS = ("title", "aioseo_title", "aioseo_description", "slug")
FIGURE_TYPES = frozenset(("timeline", "causal", "stakeholder", "comparison", "matrix",
                         "scenario", "checklist", "line", "step", "bar", "dot",
                         "stacked", "contribution"))
REQUIRED_ROLES = ("hero", "hero-frame", "hero-labels", "deck", "meta", "summary",
                  "toc", "main", "faq", "guides", "tools", "related", "references")
RAIL_MAXIMA = {"shell": 1600, "reading": 1080, "visual": 1500}
EXCLUDED_ROLES = frozenset(("hero", "summary", "toc", "references", "notes",
                            "disclaimer", "updates", "related", "guides", "tools",
                            "caption", "citation", "footnote", "quote", "cta"))
EXCLUDED_TAGS = frozenset(("style", "script", "blockquote", "q", "cite", "sup",
                           "figcaption", "svg", "figure", "summary"))
FORBIDDEN_TAGS = frozenset(("html", "head", "body", "script", "link", "iframe",
                           "object", "embed", "base", "meta", "form"))
PROCESS_PATTERNS = (
    r"入力見出しを修正", r"依頼文の前提", r"ユーザーの指定に従", r"文字数要件を達成",
    r"独立監査済み", r"監査でPASS", r"制作上の制約", r"本番環境では未検証",
    r"原文を取得したことには", r"本調査では取得でき", r"AIとして",
    r"日英の整合性を確保", r"管理者が公開後に確認", r"納品時点",
    r"as an ai\b", r"passed the independent audit", r"to comply with the user",
    r"the supplied headline has been corrected", r"we did not retrieve",
    r"this research could only access", r"we have not verified the live wordpress",
)


class Node:
    def __init__(self, tag: str, attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list[Node | str] = []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        return "".join(c.text() if isinstance(c, Node) else c for c in self.children)

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class FragmentParser(HTMLParser):
    VOID = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"))

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.errors: list[str] = []
        self.comments: list[str] = []
        self.feed(source)
        self.close()
        if len(self.stack) > 1:
            self.errors.append("unclosed tags: " + ",".join(n.tag for n in self.stack[1:]))

    def handle_starttag(self, tag, attrs):
        if len(attrs) != len(dict(attrs)):
            self.errors.append("duplicate attribute on " + tag)
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if len(self.stack) <= 1 or self.stack[-1].tag != tag:
            self.errors.append("mismatched closing tag: " + tag)
            # Do not silently repair markup as a browser would.
            return
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def handle_comment(self, data):
        self.comments.append(data)

    def handle_decl(self, decl):
        self.errors.append("document declaration in article fragment")


def article_root_id(slug: str) -> str:
    return "sg-news-" + hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12]


def bundle_digest(bundle: dict) -> str:
    """Bind private review evidence to these exact strings, not displayed HTML."""
    if isinstance(bundle, dict) and "languages" in bundle:
        bundle = bundle["languages"]
    return hashlib.sha256(json.dumps(bundle, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def role_nodes(node: Node, role: str):
    return [n for n in node.walk() if n.attrs.get("data-sgn-role") == role]


def _hidden(node: Node) -> bool:
    style = (node.attrs.get("style") or "").lower()
    classes = (node.attrs.get("class") or "").split()
    return ("hidden" in node.attrs or node.attrs.get("aria-hidden") == "true"
            or "sgn-sr-only" in classes
            or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style)))


def eligible_text(node: Node) -> str:
    """Conservative visible prose count: diagrams/labels never inflate the floor.

    Counts main headings/prose/scenarios/table explanation and FAQ answers. All
    quotes, footnotes, source names, diagram captions/content, links in card rails,
    CSS/JS, whitespace and explicitly hidden/accessibility-only text are excluded.
    Excluding entire figures is conservative even when a figure has long prose.
    """
    if (node.tag in EXCLUDED_TAGS or node.attrs.get("data-sgn-role") in EXCLUDED_ROLES
            or _hidden(node)):
        return ""
    return "".join(eligible_text(c) if isinstance(c, Node) else c for c in node.children)


def japanese_character_count(article: Node) -> int:
    mains, faqs = role_nodes(article, "main"), role_nodes(article, "faq")
    regions = mains[:1] + [n for n in faqs if not any(a in mains for a in n.ancestors())]
    return len(re.sub(r"\s", "", "".join(eligible_text(n) for n in regions)))


def _valid_scoped_selector(selector, root_id):
    tree = selector.parsed_tree
    steps = []
    while isinstance(tree, css_parser.CombinedSelector):
        steps.append(tree.combinator)
        tree = tree.left
    if not isinstance(tree, css_parser.CompoundSelector):
        return False
    root_ids = [s.ident for s in tree.simple_selectors if isinstance(s, css_parser.IDSelector)]
    # Explicit leftmost root. :is(#root,body), @scope and nesting are not accepted
    # as a substitute. The first step may not select the root's sibling.
    return root_id in root_ids and (not steps or steps[-1] in (" ", ">"))


def audit_css(css: str, root_id: str) -> list[str]:
    errors = []

    def inspect_tokens(tokens):
        for t in tokens:
            if t.type == "error":
                errors.append("css_parse: " + t.message)
            if t.type == "url" or (t.type == "function" and t.lower_name == "url"):
                errors.append("css_dependency: URL resource in article CSS")
            if hasattr(t, "arguments"):
                inspect_tokens(t.arguments)
            if hasattr(t, "content") and t.content:
                inspect_tokens(t.content)

    def rules(tokens):
        for rule in tinycss2.parse_rule_list(tokens, skip_comments=True, skip_whitespace=True):
            if rule.type == "error":
                errors.append("css_parse: " + rule.message)
            elif rule.type == "qualified-rule":
                selector_text = tinycss2.serialize(rule.prelude)
                try:
                    selectors = list(css_parser.parse(rule.prelude))
                    if not selectors or any(not _valid_scoped_selector(s, root_id) for s in selectors):
                        errors.append("css_scope: " + selector_text.strip())
                except (css_parser.SelectorError, ValueError) as exc:
                    errors.append("css_selector: " + str(exc))
                declarations = tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True)
                for d in declarations:
                    if d.type == "error":
                        errors.append("css_declaration: " + d.message)
                    elif d.type == "declaration":
                        inspect_tokens(d.value)
                        if d.lower_name == "margin":
                            errors.append("css_margin_shorthand: use margin-block/margin-inline explicitly")
            elif rule.type == "at-rule":
                if rule.lower_at_keyword not in ("media", "supports", "layer", "container") or rule.content is None:
                    errors.append("css_at_rule: unsupported/global @" + rule.lower_at_keyword)
                else:
                    rules(rule.content)

    rules(css)
    return list(dict.fromkeys(errors))


def _plain_metadata(value):
    if not isinstance(value, str) or not value or value.strip() != value or "\n" in value or "\r" in value:
        return False
    if unescape(value) != value or re.search(r"<[^>]+>|```|\[[^]]+\]\([^)]+\)", value):
        return False
    if value[0] in ('"', "'", "{", "[", "#", "*") or value[-1] in ('"', "'"):
        return False
    return not re.match(r"(?:記事タイトル|AIOSEO投稿タイトル|AIOSEOメタディスクリプション|共通スラッグ|title|slug)\s*[:：]", value, re.I)


def _slug_errors(slug):
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}-\d{2}", slug):
        return ["slug_format: common English slug must end exactly -YYYY-MM-DD"]
    try:
        date.fromisoformat(slug[-10:])
    except ValueError:
        return ["slug_date: nonexistent Gregorian date"]
    return []


def _keys(nodes, attr):
    return [n.attrs[attr] for n in nodes if attr in n.attrs]


def _audit_language(language: str, entry: dict):
    errors, metrics = [], {}
    if not isinstance(entry, dict) or set(entry) != {"html", "metadata"}:
        return ["entry_schema: requires only html and metadata"], metrics, None
    source, metadata = entry.get("html"), entry.get("metadata")
    if not isinstance(source, str) or not source:
        return ["html_type: nonempty full fragment string required"], metrics, None
    if not isinstance(metadata, dict) or set(metadata) != set(METADATA_KEYS):
        return ["metadata_schema: requires exactly four prescribed fields"], metrics, None
    for key in METADATA_KEYS:
        if not _plain_metadata(metadata[key]):
            errors.append("metadata_plain: " + key)
    slug = metadata.get("slug")
    errors.extend(_slug_errors(slug))
    if not isinstance(slug, str):
        return errors, metrics, None
    if not str(metadata["aioseo_title"]).endswith(" l SG Group"):
        errors.append("aioseo_suffix: exact suffix ' l SG Group' required")
    if re.search(r"by\s+SG\s+Group", str(metadata["title"]), re.I):
        errors.append("title_author: by SG Group in article title")
    parsed = FragmentParser(source)
    errors.extend("html_parse: " + e for e in parsed.errors)
    nodes = list(parsed.root.walk())[1:]
    if parsed.comments:
        errors.append("public_comments: production article comments are forbidden")
    for n in nodes:
        if n.tag in FORBIDDEN_TAGS:
            errors.append("forbidden_tag: " + n.tag)
        for name, value in n.attrs.items():
            if name.startswith("on") or (isinstance(value, str) and re.match(r"\s*(?:javascript|vbscript):", value, re.I)):
                errors.append("active_content: " + name)
        if n.tag in ("img", "audio", "video", "source") and n.attrs.get("src"):
            errors.append("external_media: article visuals must be self-contained SVG/HTML")
        if "style" in n.attrs:
            errors.append("inline_style: style rules must be audited in scoped style block")
        if any(not c.startswith("sgn-") for c in (n.attrs.get("class") or "").split()):
            errors.append("class_prefix: " + n.tag)
    top = [c for c in parsed.root.children if isinstance(c, Node)]
    if [n.tag for n in top] != ["style", "article"]:
        errors.append("fragment_structure: exactly style then article required")
    if any(isinstance(c, str) and c.strip() for c in parsed.root.children):
        errors.append("fragment_text: text outside article/style")
    articles = [n for n in nodes if n.tag == "article"]
    styles = [n for n in nodes if n.tag == "style"]
    if len(articles) != 1 or len(styles) != 1:
        return errors + ["fragment_count: exactly one article and one style required"], metrics, None
    article = articles[0]
    root_id = article_root_id(slug)
    if article.attrs.get("id") != root_id:
        errors.append("root_id: expected " + root_id)
    if article.attrs.get("lang") != language:
        errors.append("lang: article language must equal " + language)
    errors.extend(audit_css(styles[0].text(), root_id))
    css = styles[0].text().lower()
    if ":focus-visible" not in css:
        errors.append("focus_style: explicit focus-visible styles required")
    if "prefers-reduced-motion" not in css or not re.search(r"@media\s+print", css):
        errors.append("css_accessibility: reduced-motion and print provisions required")
    article_nodes = list(article.walk())
    ids = [n.attrs["id"] for n in nodes if "id" in n.attrs]
    if any(not i for i in ids) or len(ids) != len(set(ids)):
        errors.append("ids: empty or duplicate IDs")
    id_map = {n.attrs["id"]: n for n in nodes if "id" in n.attrs}
    for n in article_nodes:
        if n.tag != "a":
            for attr in ("href", "xlink:href"):
                value = n.attrs.get(attr)
                if value:
                    if not value.startswith("#"):
                        errors.append("svg_dependency: nonlocal resource reference")
                    elif unquote(value[1:]) not in id_map:
                        errors.append("svg_target: missing " + value)
        for attr in ("marker-start", "marker-end", "clip-path"):
            value = n.attrs.get(attr)
            if value:
                match = re.fullmatch(r"url\(#([a-zA-Z0-9_-]+)\)", value)
                if not match or match[1] not in id_map:
                    errors.append("svg_target: invalid/missing " + attr)
    h1s = [n for n in article_nodes if n.tag == "h1"]
    if len(h1s) != 1:
        errors.append("h1: exactly one H1 required")
    elif h1s[0].text().strip() != metadata["title"]:
        errors.append("h1_title: H1 and metadata.title mismatch")
    level = 0
    for n in article_nodes:
        if re.fullmatch(r"h[1-6]", n.tag):
            current = int(n.tag[-1])
            if current > level + 1 or current > 3:
                errors.append("heading_order: skipped/excess heading level " + n.tag)
            level = current
    for role in REQUIRED_ROLES:
        if len(role_nodes(article, role)) != 1:
            errors.append("role: exactly one " + role + " required")
    roles = {role: role_nodes(article, role) for role in REQUIRED_ROLES}
    for role in ("hero-frame", "guides", "tools", "related"):
        for n in roles[role]:
            permitted = ("reading",) if role == "hero-frame" else ("reading", "visual")
            if n.attrs.get("data-sgn-rail") not in permitted:
                errors.append("rail_contract: " + role)
    if roles["hero-frame"]:
        frame = roles["hero-frame"][0]
        for n in h1s + sum([roles[r] for r in ("hero-labels", "deck", "meta")], []):
            if frame not in n.ancestors():
                errors.append("hero_frame: content outside common reading frame")
    rails = [n for n in article_nodes if "data-sgn-rail" in n.attrs]
    for n in rails:
        if n.attrs["data-sgn-rail"] not in RAIL_MAXIMA:
            errors.append("rail_type: only shell/reading/visual permitted")
    body_frames = role_nodes(article, "body-frame")
    if not body_frames or any(n.attrs.get("data-sgn-rail") != "reading" for n in body_frames):
        errors.append("body_frame: at least one explicit reading frame required")
    main = roles["main"][0] if roles["main"] else None
    for role in ("guides", "tools"):
        if main and roles[role] and main not in roles[role][0].ancestors():
            errors.append("link_layer: " + role + " must be inside main")
    sections = [n for n in (main.walk() if main else []) if "data-sgn-section" in n.attrs]
    section_keys = _keys(sections, "data-sgn-section")
    if not sections or len(section_keys) != len(set(section_keys)) or any(not k for k in section_keys):
        errors.append("section_keys: unique nonempty ordered bilingual main keys required")
    h2_main = [n for n in (main.walk() if main else []) if n.tag == "h2"]
    if roles["toc"]:
        toc_links = [n for n in roles["toc"][0].walk() if n.tag == "a"]
        expected_toc = [n.attrs.get("id") for n in h2_main]
        actual_toc = [unquote((n.attrs.get("href") or "")[1:]) for n in toc_links]
        if actual_toc != expected_toc or any(not i for i in expected_toc):
            errors.append("toc: ordered TOC must match every main H2 anchor")
        for link in toc_links:
            target = id_map.get(unquote((link.attrs.get("href") or "")[1:]))
            if target and link.text().strip() != target.text().strip():
                errors.append("toc_text: TOC label must equal target H2")
    figures = [n for n in (main.walk() if main else []) if "data-sgn-figure" in n.attrs]
    types = [n.attrs.get("data-sgn-figure-type") for n in figures]
    figure_keys = _keys(figures, "data-sgn-figure")
    if len(figures) < 6 or len(set(types)) < 4:
        errors.append("visual_minimum: at least six original figures and four types required")
    if len(figure_keys) != len(set(figure_keys)) or any(not k for k in figure_keys):
        errors.append("figure_keys: unique nonempty bilingual keys required")
    for n, kind in zip(figures, types):
        if n.attrs.get("data-sgn-rail") not in ("reading", "visual"):
            errors.append("figure_rail: each major visual needs an explicit common rail")
        if n.tag != "figure" or kind not in FIGURE_TYPES or not any(c.tag in ("svg", "table", "ol", "ul") for c in n.walk() if c is not n):
            errors.append("figure_structure: real figure SVG/table/structured relation required")
        if any(a.attrs.get("data-sgn-role") in EXCLUDED_ROLES for a in n.ancestors() if a is not main):
            errors.append("figure_count: excluded card/summary content counted as figure")
        for role in ("figure-title", "figure-takeaway", "figure-condition", "figure-source"):
            if not any(c.text().strip() for c in role_nodes(n, role)):
                errors.append("figure_annotation: missing " + role + " for " + n.attrs.get("data-sgn-figure", ""))
    for svg in [n for n in article_nodes if n.tag == "svg"]:
        if not svg.attrs.get("viewbox") and not svg.attrs.get("viewBox"):
            errors.append("svg_viewbox: missing viewBox")
        if svg.attrs.get("aria-hidden") != "true":
            title = [n for n in svg.walk() if n.tag in ("title", "desc") and n.text().strip()]
            if not title and not svg.attrs.get("aria-label") and not svg.attrs.get("aria-labelledby"):
                errors.append("svg_accessibility: missing accessible explanation")
    faq_details = [n for n in (roles["faq"][0].walk() if roles["faq"] else []) if n.tag == "details"]
    faq_keys = _keys(faq_details, "data-sgn-faq")
    if not 6 <= len(faq_details) <= 10 or len(faq_keys) != len(faq_details) or len(faq_keys) != len(set(faq_keys)):
        errors.append("faq: six to ten keyed details required")
    for n in faq_details:
        children = [c for c in n.children if isinstance(c, Node)]
        if not children or children[0].tag != "summary" or len([c for c in children if c.tag == "summary"]) != 1:
            errors.append("faq_summary: first child must be a unique summary")
    references = [n for n in (roles["references"][0].walk() if roles["references"] else []) if "data-sgn-source" in n.attrs]
    source_keys = _keys(references, "data-sgn-source")
    if not source_keys or len(source_keys) != len(set(source_keys)):
        errors.append("source_keys: traceable source entries need unique bilingual keys")
    links, internal = [], []
    for n in article_nodes:
        if n.tag == "th" and n.attrs.get("scope") not in ("col", "row", "colgroup", "rowgroup"):
            errors.append("table_scope: header cells need correct scope")
        if n.tag == "table":
            region = next((a for a in n.ancestors() if a.attrs.get("data-sgn-role") == "table-scroll"), None)
            if region is None or region.attrs.get("role") != "region" or region.attrs.get("tabindex") != "0" or not region.attrs.get("aria-label"):
                errors.append("table_region: focusable labelled scroll wrapper required")
        for attr in ("aria-labelledby", "aria-describedby"):
            for target in (n.attrs.get(attr) or "").split():
                if target not in id_map:
                    errors.append("aria_target: missing " + target)
        if n.tag != "a":
            continue
        href = n.attrs.get("href") or ""
        if not href or not (n.text().strip() or n.attrs.get("aria-label")):
            errors.append("link_accessibility: missing href or meaningful name")
        if href.startswith("#"):
            if unquote(href[1:]) not in id_map:
                errors.append("anchor_target: missing " + href)
            continue
        url = urlsplit(urljoin("https://sggroup.jp/", unquote(href)))
        if url.scheme not in ("https", "http"):
            errors.append("link_scheme: unsupported " + href)
        is_internal = url.hostname in ("sggroup.jp", "www.sggroup.jp")
        if not is_internal and n.attrs.get("target") == "_blank" and not {"noopener", "noreferrer"} <= set((n.attrs.get("rel") or "").split()):
            errors.append("external_rel: blank target needs noopener noreferrer")
        record = {"href": href, "anchor": n.text().strip(), "internal": is_internal,
                  "role": n.attrs.get("data-sgn-link-role")}
        links.append(record)
        if is_internal:
            internal.append(record)
            if not url.path.startswith("/" + language + "/"):
                errors.append("internal_language: " + href)
            if url.query or not url.path.endswith("/"):
                errors.append("internal_canonical: clean trailing-slash URL required " + href)
            if "nofollow" in (n.attrs.get("rel") or "").split():
                errors.append("internal_follow: " + href)
            prefix = "/" + language + "/article/global-macro-analysis/"
            if url.path.startswith(prefix):
                index = url.path == prefix
                label = (("記事一覧 · 本文は全文有料" if index else "全文有料") if language == "ja"
                         else ("Index · full articles require paid access" if index else "Full article requires paid access"))
                # Only visibly adjacent content or this exact link's card counts.
                parent = n.parent
                adjacent = ""
                if parent:
                    position = parent.children.index(n)
                    for c in parent.children[position + 1:position + 3]:
                        adjacent += c.text() if isinstance(c, Node) and not _hidden(c) else (c if isinstance(c, str) else "")
                card = next((a for a in n.ancestors() if a.attrs.get("data-sgn-role") == "link-card"), None)
                card_text = "".join(c.text() for c in card.walk() if c.attrs.get("data-sgn-role") == "paid-label" and not _hidden(c)) if card else ""
                if label not in adjacent and label not in card_text:
                    errors.append("paid_label: visible exact access label missing for " + href)
    repetitions = Counter(r["href"] for r in internal)
    if any(count > 2 for count in repetitions.values()):
        errors.append("internal_repetition: URL occurs more than twice")
    contextual = [r for r in internal if r["role"] == "context"]
    if len(contextual) < 4:
        errors.append("internal_context: at least four contextual internal links required")
    for n in article_nodes:
        consecutive = 0
        for c in n.children:
            if isinstance(c, Node):
                consecutive = consecutive + 1 if c.tag == "p" else 0
                if consecutive == 5:
                    errors.append("paragraph_wall: more than four consecutive prose paragraphs")
    public = article.text() + " " + " ".join(str(v) for n in article_nodes for v in n.attrs.values()) + " " + " ".join(str(metadata[k]) for k in METADATA_KEYS[:-1])
    for pattern in PROCESS_PATTERNS:
        if re.search(pattern, public, re.I):
            errors.append("process_leak: matched " + pattern)
    count = japanese_character_count(article)
    if language == "ja" and count < 10000:
        errors.append("ja_length: eligible non-whitespace body below 10000")
    metrics.update({"eligible_body_characters": count, "ja_target_15000_met": count >= 15000 if language == "ja" else None,
                    "section_keys": section_keys, "figure_keys": figure_keys, "figure_types": types,
                    "faq_keys": faq_keys, "source_keys": source_keys,
                    "internal_unique_urls": len(repetitions), "links": links,
                    "internal_link_role_sequence": [r["role"] for r in internal],
                    "source_utf8_bytes": len(source.encode("utf-8"))})
    return list(dict.fromkeys(errors)), metrics, article


def validate_bundle(bundle: dict) -> dict:
    errors, metrics = [], {}
    # Workflow wrapper is private and may hold review evidence. It is never
    # copied into article/viewer. Audit only the exact two language entries.
    if isinstance(bundle, dict) and "languages" in bundle:
        bundle = bundle["languages"]
    if not isinstance(bundle, dict) or set(bundle) != {"ja", "en"}:
        return {"pass": False, "passed": False, "errors": ["bundle_schema: exactly ja and en required"], "metrics": {}}
    for language in ("ja", "en"):
        language_errors, language_metrics, _ = _audit_language(language, bundle[language])
        errors.extend(language + ":" + e for e in language_errors)
        metrics[language] = language_metrics
    try:
        if bundle["ja"]["metadata"]["slug"] != bundle["en"]["metadata"]["slug"]:
            errors.append("bilingual: common slug differs")
    except (KeyError, TypeError):
        pass
    for key in ("section_keys", "figure_keys", "figure_types", "faq_keys", "source_keys", "internal_link_role_sequence"):
        if key in metrics["ja"] and key in metrics["en"] and metrics["ja"][key] != metrics["en"][key]:
            errors.append("bilingual:" + key + " differs in count/order/identity")
    return {"pass": not errors, "passed": not errors, "errors": errors, "metrics": metrics,
            "scope": "deterministic structural audit only", "complete_editorial_qa": False,
            "bundle_digest": bundle_digest(bundle),
            "limitations": ["Structural correspondence is not proof of full semantic translation or factual accuracy.",
                            "Figure structure/type attributes do not prove meaningful original diagrams or correct data geometry.",
                            "Semantic process leaks/repetition, source verification, live link/canonical/redirect checks, shortage exceptions, slug identity/CMS collision and independent viewpoint review require separate evidence.",
                            "Visible count conservatively excludes entire figures; CSS-hidden text needs browser/independent inspection."]}


def validate_saved(language: str, expected: dict, post: dict) -> dict:
    content = post.get("content", {})
    raw = post.get("html", content.get("raw") if isinstance(content, dict) else content)
    errors = []
    if raw != expected["html"]:
        errors.append("saved_content: exact full fragment read-back differs or raw unavailable")
    if post.get("slug") != expected["metadata"]["slug"]:
        errors.append("saved_slug: unexpected CMS alteration/suffix")
    return {"pass": not errors, "passed": not errors, "errors": errors, "language": language}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    report = validate_bundle(json.loads(args.bundle.read_text(encoding="utf-8")))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
