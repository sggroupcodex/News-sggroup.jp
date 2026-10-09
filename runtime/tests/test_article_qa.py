"""Adversarial structural tests; synthetic prose is never a news candidate."""
from copy import deepcopy
from pathlib import Path
import sys
import re
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from article_qa import (FragmentParser, article_root_id, audit_css, bundle_digest,
                        japanese_character_count, validate_bundle, validate_saved)


def synthetic_bundle():
    """Controlled artificial fixture for program tests, with no publication evidence."""
    slug = 'example-central-bank-official-policy-decision-global-market-channel-2026-10-09'
    root = article_root_id(slug)
    bundle = {}
    for lang in ('ja', 'en'):
        title = '制度の経路と時間差' if lang == 'ja' else 'Policy channels and time lags'
        css = f'''#{root}{{width:100vw;margin-inline:calc(50% - 50vw);box-sizing:border-box;padding-inline:24px;color:#10233b;background:#fffdf8;font:17px/1.9 system-ui,sans-serif;overflow-wrap:anywhere}}
#{root} *{{box-sizing:border-box}}#{root} .sgn-rail{{width:100%;margin-inline:auto;margin-block:20px}}
#{root} .sgn-shell{{max-width:1600px}}#{root} .sgn-reading{{max-width:1080px}}#{root} .sgn-visual{{max-width:1500px}}
#{root} h1{{font-size:52px;line-height:1.3}}#{root} h2{{font-size:26px;line-height:1.4}}#{root} p{{margin-block:0 18px}}#{root} figure{{margin-inline:auto;margin-block:20px}}#{root} svg{{display:block;width:100%;height:auto}}#{root} details{{margin-block:12px}}
#{root} a:focus-visible,#{root} summary:focus-visible,#{root} .sgn-table-scroll:focus-visible{{outline:3px solid #1f67d2;outline-offset:2px}}
#{root} .sgn-table-scroll{{overflow-x:auto}}
@media (max-width:1180px){{#{root}{{padding-inline:18px}}}}@media (max-width:720px){{#{root}{{padding-inline:12px;font-size:16px;line-height:1.85}}#{root} h1{{font-size:30px}}}}@media (max-width:380px){{#{root}{{padding-inline:10px}}#{root} h1{{font-size:28px}}}}
@media (prefers-reduced-motion:reduce){{#{root} *{{scroll-behavior:auto}}}}@media print{{#{root}{{width:100%;margin-inline:0;padding-inline:0}}}}'''
        sections, toc = [], []
        for index in range(12):
            heading = ('論点' if lang == 'ja' else 'Channel ') + str(index + 1)
            toc.append(f'<li><a href="#section-{index}">{heading}</a></li>')
            text = ('経路を比較し制度の条件と影響の時間差を説明する。' * 60 if lang == 'ja'
                    else 'This synthetic test sentence describes institutional channels and timing. ' * 60)
            links = f'<a data-sgn-link-role="context" href="https://sggroup.jp/{lang}/education/fixture-{index}/">制度の説明</a>' if index < 4 else ''
            body = f'<section data-sgn-section="channel-{index}"><div data-sgn-role="body-frame" data-sgn-rail="reading"><h2 id="section-{index}">{heading}</h2><p>{text}{links}</p></div>'
            if index < 6:
                kind = ('timeline', 'causal', 'stakeholder', 'comparison', 'matrix', 'scenario')[index]
                body += f'<figure data-sgn-figure="frame-{index}" data-sgn-figure-type="{kind}" data-sgn-rail="visual"><svg viewBox="0 0 400 100" role="img" aria-labelledby="figure-title-{index}"><title id="figure-title-{index}">Sequence</title><rect x="1" y="1" width="398" height="98" fill="#f5f8fb"/><text x="15" y="55" font-size="20">First → next</text></svg><figcaption><p data-sgn-role="figure-title">関係の順序</p><p data-sgn-role="figure-takeaway">時間差がある</p><p data-sgn-role="figure-condition">2026-10-09</p><p data-sgn-role="figure-source">Primary fixture source</p></figcaption></figure>'
            if index in (3, 8):
                role = 'guides' if index == 3 else 'tools'
                body += f'<aside data-sgn-role="{role}" data-sgn-rail="reading"><p>制度の背景</p></aside>'
            sections.append(body + '</section>')
        faq = ''.join(f'<details data-sgn-faq="question-{i}"><summary>Question {i}</summary><p>条件により経路は変わる。</p></details>' for i in range(6))
        html = f'''<style>{css}</style><article id="{root}" lang="{lang}"><header data-sgn-role="hero"><div data-sgn-role="hero-frame" data-sgn-rail="reading"><div data-sgn-role="hero-labels">NEWS &amp; CONTEXT</div><h1>{title}</h1><p data-sgn-role="deck">市場への経路を説明する。</p><p data-sgn-role="meta">2026-10-09</p></div></header><section data-sgn-role="summary" data-sgn-rail="reading"><p>五つの論点</p></section><nav data-sgn-role="toc" data-sgn-rail="reading"><ol>{''.join(toc)}</ol></nav><div data-sgn-role="main">{''.join(sections)}</div><section data-sgn-role="faq" data-sgn-rail="reading"><h2>FAQ</h2>{faq}</section><aside data-sgn-role="related" data-sgn-rail="reading"><p>関連記事</p></aside><section data-sgn-role="references" data-sgn-rail="reading"><h2>出典</h2><ol><li data-sgn-source="official-source-1">Official source 2026-10-09 <a href="https://example.org/official/">Official release</a></li></ol></section></article>'''
        html = re.sub(r'(<[a-z]+[^>]* data-sgn-rail="(shell|reading|visual)")', lambda m: m[1] + ' class="sgn-rail sgn-' + m[2] + '"', html)
        for index in range(12):
            html = html.replace('"section-' + str(index) + '"', '"' + root + '-section-' + str(index) + '"').replace('"#section-' + str(index) + '"', '"#' + root + '-section-' + str(index) + '"')
        for index in range(6):
            html = html.replace('"figure-title-' + str(index) + '"', '"' + root + '-figure-title-' + str(index) + '"')
        html = html.replace('<svg viewBox=', '<svg xmlns="http://www.w3.org/2000/svg" viewBox=')
        bundle[lang] = {'html': html, 'metadata': {'title': title, 'aioseo_title': title + ' l SG Group', 'aioseo_description': '制度の決定が市場に伝わる経路を考える。' if lang == 'ja' else 'How an institutional decision travels through market channels.', 'slug': slug}}
    return bundle


class ArticleQATest(unittest.TestCase):
    def setUp(self):
        self.bundle = synthetic_bundle()

    def assert_error(self, bundle, code):
        result = validate_bundle(bundle)
        self.assertFalse(result['pass'])
        self.assertTrue(any(code in e for e in result['errors']), result['errors'])

    def test_structural_fixture_and_private_workflow_wrapper(self):
        result = validate_bundle(self.bundle)
        self.assertTrue(result['pass'], result['errors'])
        wrapped = {'languages': self.bundle, 'evidence': {'private': 'must never be public'}, 'viewer': {'path': 'private'}}
        self.assertEqual(result['bundle_digest'], validate_bundle(wrapped)['bundle_digest'])
        self.assertFalse(result['complete_editorial_qa'])
        self.assertEqual('deterministic structural audit only', result['scope'])

    def test_count_excludes_hero_cards_quote_css_figure_caption_sources_and_faq_question(self):
        source = '''<article><header data-sgn-role="hero">不算入</header><div data-sgn-role="main"><h2>見出し</h2><p> 本 文\nです <sup>999</sup><q>引用</q><cite>参照</cite></p><blockquote>引用文</blockquote><figure><p>長い図文</p><figcaption>caption</figcaption></figure><aside data-sgn-role="guides">カード</aside><span hidden>隠す</span><span class="sgn-sr-only">読む</span><p data-sgn-role="footnote">脚注</p><p data-sgn-role="references">資料URL</p><style>body{x:y}</style></div><section data-sgn-role="faq"><details><summary>質問</summary><p>答え</p></details></section></article>'''
        article = next(n for n in FragmentParser(source).root.walk() if n.tag == 'article')
        self.assertEqual(japanese_character_count(article), len('見出し本文です答え'))

    def test_floor_one_character_below_10000_and_exact_floor(self):
        # All other requirements irrelevant here: change only eligible prose.
        original = self.bundle['ja']['html']
        start = original.index('<div data-sgn-role="main">')
        end = original.index('<section data-sgn-role="faq"')
        for count in (9999, 10000):
            b = deepcopy(self.bundle)
            b['ja']['html'] = original[:start] + '<div data-sgn-role="main"><p>' + '字' * count + '</p></div>' + original[end:]
            article = next(n for n in FragmentParser(b['ja']['html']).root.walk() if n.tag == 'article')
            extra = japanese_character_count(article) - count
            b['ja']['html'] = b['ja']['html'].replace('字' * count, '字' * (count - extra))
            result = validate_bundle(b)
            self.assertEqual(any('ja_length:' in e for e in result['errors']), count < 10000)

    def test_selector_parser_catches_comma_nested_and_root_sibling_escape(self):
        root = 'sg-news-abc'
        for css in (f'#{root} a, body{{color:red}}', f'@media(min-width:1px){{a{{color:red}}}}', f'#{root} + body{{color:red}}', f':is(#{root},body) a{{color:red}}'):
            self.assertTrue(any(e.startswith('css_scope') for e in audit_css(css, root)), css)
        self.assertEqual([], audit_css(f'#{root} .sgn-row + .sgn-row{{color:red}}', root))

    def test_css_dependencies_and_margin_cascade(self):
        root = 'sg-news-abc'
        self.assertTrue(any('css_dependency' in e for e in audit_css(f'#{root}{{background:url(https://example.org/a)}}', root)))
        self.assertTrue(any('css_at_rule' in e for e in audit_css('@import "https://example.org/a";', root)))
        self.assertTrue(any('css_margin_shorthand' in e for e in audit_css(f'#{root} .sgn-rail{{margin-inline:auto;margin:18px 0 44px}}', root)))

    def test_fragments_scripts_comments_and_global_css(self):
        for bad in ('<script>alert(1)</script>', '<!--独立監査済み-->', '<iframe src="https://example.org/"></iframe>'):
            b = deepcopy(self.bundle); b['ja']['html'] += bad
            self.assert_error(b, 'forbidden_tag' if 'script' in bad or 'iframe' in bad else 'public_comments')
        b = deepcopy(self.bundle); b['ja']['html'] = b['ja']['html'].replace('</style>', 'body{color:red}</style>')
        self.assert_error(b, 'css_scope')

    def test_actual_gregorian_date_suffix_and_metadata_exact_suffix(self):
        for slug in ('event-2026-02-30', 'event-2026-10-09-2', 'event-2026-10-09-en', 'event-2026-1-9'):
            b = deepcopy(self.bundle); b['ja']['metadata']['slug'] = slug
            self.assert_error(b, 'slug_')
        b = deepcopy(self.bundle); b['en']['metadata']['aioseo_title'] = 'Policy | SG Group'
        self.assert_error(b, 'aioseo_suffix')

    def test_plain_metadata_and_no_additional_fields(self):
        for value in ('Title\nsecond', ' Title', 'Title &amp; policy', '**Title**', '記事タイトル：値'):
            b = deepcopy(self.bundle); b['ja']['metadata']['title'] = value
            self.assert_error(b, 'metadata_plain')
        b = deepcopy(self.bundle); b['ja']['metadata']['canonical'] = 'https://sggroup.jp/'
        self.assert_error(b, 'metadata_schema')

    def test_ids_toc_and_aria_references(self):
        root = article_root_id(self.bundle['en']['metadata']['slug'])
        for old, new, code in ((f'id="{root}-section-1"', f'id="{root}-section-0"', 'ids:'), (f'href="#{root}-section-0"', 'href="#missing"', 'anchor_target'), (f'aria-labelledby="{root}-figure-title-0"', 'aria-labelledby="unknown"', 'aria_target')):
            b = deepcopy(self.bundle); b['en']['html'] = b['en']['html'].replace(old, new)
            self.assert_error(b, code)

    def test_structural_parity_cannot_be_faked_by_equal_counts(self):
        b = deepcopy(self.bundle); b['en']['html'] = b['en']['html'].replace('data-sgn-section="channel-1"', 'data-sgn-section="other-fact"')
        self.assert_error(b, 'bilingual:section_keys')
        b = deepcopy(self.bundle); b['en']['html'] = b['en']['html'].replace('data-sgn-figure-type="causal"', 'data-sgn-figure-type="timeline"')
        self.assert_error(b, 'bilingual:figure_types')

    def test_process_leaks_inside_hidden_text_and_accessibility_attributes(self):
        for extra in ('<span hidden>監査でPASSした</span>', '<span aria-label="Passed the independent audit">別文</span>'):
            b = deepcopy(self.bundle); b['ja']['html'] = b['ja']['html'].replace('</article>', extra + '</article>')
            self.assert_error(b, 'process_leak')

    def test_link_language_safety_paid_labels_and_overcount(self):
        b = deepcopy(self.bundle); b['en']['html'] = b['en']['html'].replace('/en/education/fixture-0/', '/ja/education/fixture-0/')
        self.assert_error(b, 'internal_language')
        b = deepcopy(self.bundle); b['ja']['html'] = b['ja']['html'].replace('/ja/education/fixture-0/', '/ja/article/global-macro-analysis/actual-article/')
        self.assert_error(b, 'paid_label')
        b['ja']['html'] = b['ja']['html'].replace('制度の説明</a>', '制度の説明</a><span>全文有料</span>', 1)
        self.assertFalse(any('paid_label' in e for e in validate_bundle(b)['errors']))
        b = deepcopy(self.bundle); b['ja']['html'] = b['ja']['html'].replace('href="https://example.org/official/"', 'target="_blank" href="https://example.org/official/"')
        self.assert_error(b, 'external_rel')

    def test_tables_require_accessible_local_scroll_region(self):
        b = deepcopy(self.bundle); b['ja']['html'] = b['ja']['html'].replace('</article>', '<table><tr><th>Label</th><td>1</td></tr></table></article>')
        self.assert_error(b, 'table_region')
        self.assert_error(b, 'table_scope')

    def test_svg_cannot_fetch_external_resource_or_reference_missing_marker(self):
        b = deepcopy(self.bundle)
        b['ja']['html'] = b['ja']['html'].replace('</svg>', '<use href="https://example.org/external.svg#picture"></use></svg>', 1)
        self.assert_error(b, 'svg_dependency')
        b = deepcopy(self.bundle)
        b['ja']['html'] = b['ja']['html'].replace('</svg>', '<path d="M 1 1 L 20 20" marker-end="url(#missing)"></path></svg>', 1)
        self.assert_error(b, 'svg_target')

    def test_saved_readback_sanitization_and_wp_slug_suffix(self):
        entry = self.bundle['ja']
        good = {'content': {'raw': entry['html']}, 'slug': entry['metadata']['slug']}
        self.assertTrue(validate_saved('ja', entry, good)['pass'])
        bad = deepcopy(good); bad['content']['raw'] = bad['content']['raw'].replace('<style>', '')
        self.assertFalse(validate_saved('ja', entry, bad)['pass'])
        bad = deepcopy(good); bad['slug'] += '-2'
        self.assertFalse(validate_saved('ja', entry, bad)['pass'])

    def test_digest_binds_actual_article_and_metadata(self):
        digest = bundle_digest(self.bundle)
        changed = deepcopy(self.bundle); changed['en']['html'] += ' '
        self.assertNotEqual(digest, bundle_digest(changed))
        changed = deepcopy(self.bundle); changed['en']['metadata']['aioseo_description'] += '.'
        self.assertNotEqual(digest, bundle_digest(changed))


if __name__ == '__main__':
    unittest.main()
