from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from copy_viewer import clipboard_targets, generate_viewer, write_viewer
from browser_qa import _ViewerServer, _copy_tests, VIEWER_MEASURE_JS, MEASURE_JS, _host_html, SENTINEL_JS
from article_qa import article_root_id
from playwright.sync_api import sync_playwright
from test_article_qa import synthetic_bundle


class Tags(HTMLParser):
    def __init__(self, source):
        super().__init__(); self.tags=[];self.feed(source)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class CopyViewerTest(unittest.TestCase):
    def test_only_ten_exact_targets_in_prescribed_order_and_no_private_record(self):
        bundle = synthetic_bundle()
        source = generate_viewer(bundle)
        buttons = [attrs['data-copy-target'] for tag,attrs in Tags(source).tags if tag=='button' and 'data-copy-target' in attrs]
        self.assertEqual(list(clipboard_targets(bundle)), buttons)
        self.assertEqual(10, len(set(buttons)))
        self.assertNotIn('Independent_Static_Audit', source)
        self.assertNotIn('SGGROUP_QA_SENTINEL', source)
        self.assertNotIn('Base64', source)
        self.assertEqual(1, sum(tag=='script' for tag,_ in Tags(source).tags))

    def test_untrusted_source_is_text_not_executable_script_and_roundtrips_unicode(self):
        bundle = synthetic_bundle()
        raw = '<style>#x{}</style><article>\r\n日本語 & <b>😀</b>\u2028\u2029</article></script><script>window.injected=true</script>'
        bundle['ja']['html'] = raw
        source = generate_viewer(bundle)
        self.assertEqual(1, sum(tag=='script' for tag,_ in Tags(source).tags))
        payload = source.split('const copyPayloads = ',1)[1].split(';\nconst statusNode',1)[0]
        parsed = json.loads(payload)
        self.assertEqual(raw.encode('utf-8'), parsed['ja:html'].encode('utf-8'))
        self.assertNotIn('</script>', payload)

    def test_bad_metadata_rejected_before_viewer_creation(self):
        bundle = synthetic_bundle()
        for change in ({'slug':'event-2026-02-30'}, {'title':' value'}, {'aioseo_description':'A &amp; B'}):
            b=deepcopy(bundle);b['ja']['metadata'].update(change)
            with self.assertRaises(ValueError):generate_viewer(b)

    def test_no_silent_filename_shortening(self):
        bundle=synthetic_bundle()
        with tempfile.TemporaryDirectory() as d:
            p=write_viewer(bundle,d)
            self.assertEqual('SGGroup_News_'+bundle['ja']['metadata']['slug']+'_All_Copy_Viewer.html',p.name)
            longslug='explanatory-'*30+'2026-10-09'
            for lang in ('ja','en'):bundle[lang]['metadata']['slug']=longslug
            with self.assertRaises(ValueError):write_viewer(bundle,d)

    def test_real_chromium_all_buttons_clipboard_fallback_denial_and_mobile(self):
        bundle=synthetic_bundle()
        # Escaping concerns are exercised through actual parsing and copying,
        # not just by asserting the generator's variable contains its input.
        bundle['ja']['html'] += '\r\n<script>window.injected=true</script>😀 & </script>\u2028'
        bundle['en']['metadata']['title']='Policy & global markets 😀'
        source=generate_viewer(bundle)
        with _ViewerServer(source) as url, sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            context=browser.new_context(permissions=['clipboard-read','clipboard-write'])
            report=_copy_tests(context,url,clipboard_targets(bundle))
            self.assertTrue(report['pass'],report['errors'])
            self.assertEqual(43,len(report['tests']))
            self.assertTrue(all(t['pass'] for t in report['tests']))
            page=context.new_page();page.set_viewport_size({'width':280,'height':700});page.goto(url)
            self.assertIsNone(page.evaluate('window.injected'))
            self.assertEqual([],page.evaluate(VIEWER_MEASURE_JS)['errors'])
            page.locator('.html-view:visible summary').click()
            self.assertEqual([],page.evaluate(VIEWER_MEASURE_JS)['errors'])
            # Detector regression: a centered declaration does not prove its
            # cascade survived, and overflow:hidden must not conceal bad text.
            page.set_viewport_size({'width':390,'height':900})
            page.set_content(_host_html(True))
            before=page.evaluate(SENTINEL_JS)
            clean=synthetic_bundle()['ja']
            page.locator('#article-host').evaluate('(e,t)=>{e.innerHTML=t}',clean['html'])
            rootid=article_root_id(clean['metadata']['slug'])
            self.assertEqual([],page.evaluate(MEASURE_JS,rootid)['errors'])
            self.assertEqual(before,page.evaluate(SENTINEL_JS))
            # add_style_tag inserts into HEAD, before the fragment's STYLE in
            # BODY. Append the defect to the actual later article stylesheet.
            page.locator('#article-host style').evaluate('(e,css)=>{e.textContent+=css}',f'#{rootid} [data-sgn-role="guides"]{{margin-inline-start:20px;margin-inline-end:0}}')
            drift=page.evaluate(MEASURE_JS,rootid)
            self.assertGreater(drift['maxMarginDifference'],2)
            self.assertTrue(any('asymmetric' in e for e in drift['errors']))
            page.locator('#article-host style').evaluate('(e,css)=>{e.textContent+=css}',f'#{rootid} [data-sgn-role="deck"]{{width:100px;overflow:hidden;white-space:nowrap}}')
            clipped=page.evaluate(MEASURE_JS,rootid)
            self.assertTrue(any('overflow hidden conceals' in e for e in clipped['errors']))
            page.add_style_tag(content='header{color:red!important}')
            self.assertNotEqual(before,page.evaluate(SENTINEL_JS))
            context.close();browser.close()


if __name__=='__main__':unittest.main()
