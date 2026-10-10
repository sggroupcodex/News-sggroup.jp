"""Private real-Chromium viewport, rail and actual-clipboard measurements.

No browser report alone certifies source accuracy, semantics or visual art
direction. Screenshots are private evidence for independent visual review.
"""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

try:
    from .article_qa import article_root_id, bundle_digest
    from .copy_viewer import clipboard_targets
except ImportError:
    from article_qa import article_root_id, bundle_digest
    from copy_viewer import clipboard_targets


WIDTHS = (280, 300, 320, 360, 375, 390, 412, 430, 568, 640, 667, 720, 736,
          768, 820, 844, 912, 1024, 1180, 1280, 1366, 1440, 1600, 1920, 2560)
LANDSCAPES = ((667, 375), (736, 414), (844, 390))

MEASURE_JS = r"""rootId => {
 const root = document.getElementById(rootId);
 if (!root) return {errors:['article root missing']};
 const errors=[], rects=[], vw=document.documentElement.clientWidth;
 const rectangle = e => {const r=e.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,top:r.top,height:r.height};};
 const visible = e => {const s=getComputedStyle(e);return s.display!=='none' && s.visibility!=='hidden' && e.getClientRects().length>0;};
 const rootRect=rectangle(root);
 if(Math.abs(rootRect.left)>2 || Math.abs(rootRect.right-vw)>2) errors.push('article does not span viewport in host container');
 if(document.documentElement.scrollWidth>vw+1) errors.push('page horizontal overflow');
 const limits={shell:1600,reading:1080,visual:1500};
 const horizontalPadding=vw<=380?10:vw<=720?12:vw<=1180?18:24;
 const hero=root.querySelector('[data-sgn-role="hero-frame"]');
 const hr=hero?rectangle(hero):null;
 if(!hero) errors.push('hero frame missing');
 let maxMarginDifference=0,maxReadingEdgeDifference=0;
 for(const e of root.querySelectorAll('[data-sgn-rail]')) {
   if(!visible(e)) {errors.push('hidden required rail '+e.dataset.sgnRail);continue;}
   const r=rectangle(e), rail=e.dataset.sgnRail;
   const marginDifference=Math.abs(r.left-(vw-r.right));
   maxMarginDifference=Math.max(maxMarginDifference,marginDifference);
   rects.push({role:e.dataset.sgnRole||null,section:e.dataset.sgnSection||null,rail,...r,marginDifference});
   if(marginDifference>2) errors.push('asymmetric '+rail+' rail '+(e.dataset.sgnRole||e.dataset.sgnSection||e.tagName));
   if(!limits[rail] || r.width>limits[rail]+2) errors.push('rail exceeds allowed maximum '+rail);
   if(rail==='reading' && r.width<Math.min(1080,vw-2*horizontalPadding)-2)errors.push('reading frame is narrower than its required common rail');
   if(r.left < -1 || r.right > vw+1) errors.push('rail outside viewport '+rail);
   if(rail==='reading' && hr) {
      const diff=Math.max(Math.abs(r.left-hr.left),Math.abs(r.right-hr.right));
      maxReadingEdgeDifference=Math.max(maxReadingEdgeDifference,diff);
      if(diff>2) errors.push('reading rail differs from Hero '+(e.dataset.sgnRole||e.dataset.sgnSection||e.tagName));
   }
   if(['guides','tools','related'].includes(e.dataset.sgnRole) && !['left','start'].includes(getComputedStyle(e).textAlign)) errors.push('link panel text is not left aligned');
 }
 if(!root.querySelector('[data-sgn-role="body-frame"]')) errors.push('body frame missing');
 for(const e of root.querySelectorAll('[data-sgn-role="body-frame"] h2,[data-sgn-role="body-frame"] h3')) {
   const r=rectangle(e);rects.push({role:'body-heading',id:e.id||null,...r});
   if(visible(e) && getComputedStyle(e).textAlign==='center')errors.push('body heading text is centered');
 }
 for(const e of root.querySelectorAll('h1,[data-sgn-role="hero-labels"],[data-sgn-role="deck"],[data-sgn-role="meta"],[data-sgn-role="hero-note"]')) {
    if(visible(e) && hr) {
      const r=rectangle(e), diff=Math.max(Math.abs(r.left-hr.left),Math.abs(r.right-hr.right));
      maxReadingEdgeDifference=Math.max(maxReadingEdgeDifference,diff);
      rects.push({role:e.dataset.sgnRole||'h1',...r,heroEdgeDifference:diff});
      if(diff>2) errors.push('Hero component has different horizontal edges '+(e.dataset.sgnRole||'h1'));
    }
 }
 const tableRegion=e=>e.closest('[data-sgn-role="table-scroll"]');
 for(const e of root.querySelectorAll('*')) {
   if(!visible(e) || ['STYLE','SCRIPT','path','defs','marker','title','desc'].includes(e.tagName)) continue;
   const r=rectangle(e), region=tableRegion(e), s=getComputedStyle(e);
   if((r.left < -1 || r.right > vw+1) && !region) errors.push('element outside viewport '+e.tagName+':'+(e.dataset.sgnRole||e.id||''));
   const htmlElement=e.namespaceURI==='http://www.w3.org/1999/xhtml';
   // SVG clientWidth/scrollWidth use incompatible coordinate spaces after
   // viewBox scaling. Its rendered rectangles, not HTML scrolling, are proof.
   if(htmlElement && e.scrollWidth>e.clientWidth+2 && e.clientWidth>0 && !region) errors.push('non-table horizontal overflow/clipping '+e.tagName+':'+(e.dataset.sgnRole||e.id||''));
   if(htmlElement && ['hidden','clip'].includes(s.overflowX) && !region && e.scrollWidth>e.clientWidth+2) errors.push('overflow hidden conceals content '+e.tagName);
   if(e.tagName==='text') {
      const svg=e.closest('svg'), sr=svg?rectangle(svg):null;
      if(sr && (r.left<sr.left-1 || r.right>sr.right+1 || r.top<sr.top-1 || r.top+r.height>sr.top+sr.height+1)) errors.push('SVG text clipped outside its viewport');
   }
 }
 for(const e of root.querySelectorAll('[data-sgn-role="table-scroll"]')) {
   const s=getComputedStyle(e);
   if(!['auto','scroll'].includes(s.overflowX) || e.getAttribute('role')!=='region' || e.getAttribute('tabindex')!=='0' || !e.getAttribute('aria-label')) errors.push('table scroll accessibility/operation');
 }
 const ids=Array.from(root.querySelectorAll('[id]'),e=>e.id);ids.push(root.id);
 if(new Set(ids).size!==ids.length) errors.push('duplicate IDs');
 if(root.querySelectorAll('h1').length!==1) errors.push('H1 count');
 for(const a of root.querySelectorAll('a[href^="#"]')) if(!document.getElementById(decodeURIComponent(a.getAttribute('href').slice(1)))) errors.push('missing anchor '+a.getAttribute('href'));
 const excludedRoles=new Set(['hero','summary','toc','references','notes','disclaimer','updates','related','guides','tools','caption','citation','footnote','quote','cta']);
 const excludedTags=new Set(['style','script','blockquote','q','cite','sup','figcaption','svg','figure','summary']);
 const prose=e=>{
   if(excludedTags.has(e.tagName.toLowerCase()) || excludedRoles.has(e.dataset.sgnRole) || !visible(e) || e.hasAttribute('hidden') || e.getAttribute('aria-hidden')==='true' || e.classList.contains('sgn-sr-only'))return '';
   return Array.from(e.childNodes,c=>c.nodeType===Node.TEXT_NODE?c.textContent:c.nodeType===Node.ELEMENT_NODE?prose(c):'').join('');
 };
 const main=root.querySelector('[data-sgn-role="main"]'), faq=root.querySelector('[data-sgn-role="faq"]');
 const bodyText=(main?prose(main):'')+(faq && (!main || !main.contains(faq))?prose(faq):'');
 const eligibleBodyCharacters=Array.from(bodyText.replace(/\s/gu,'')).length;
 if(root.lang==='ja' && eligibleBodyCharacters<10000)errors.push('rendered eligible Japanese body below 10000 characters');
 return {errors:Array.from(new Set(errors)),viewportWidth:vw,documentScrollWidth:document.documentElement.scrollWidth,root:rootRect,rects,maxMarginDifference,maxReadingEdgeDifference,eligibleBodyCharacters};
}"""

VIEWER_MEASURE_JS = r"""() => {
 const vw=document.documentElement.clientWidth, errors=[];
 if(document.documentElement.scrollWidth>vw+1) errors.push('viewer page horizontal overflow');
 for(const e of document.querySelectorAll('main,button,.metadata,.metadata-item,.metadata-value,pre')) {
   if(!e.getClientRects().length)continue;
   const r=e.getBoundingClientRect();
   if(r.left < -1 || r.right > vw+1 || (e.clientWidth>0 && e.scrollWidth>e.clientWidth+2)) errors.push('viewer clipping/overflow '+e.tagName);
 }
 return {errors:Array.from(new Set(errors)),viewportWidth:vw,documentScrollWidth:document.documentElement.scrollWidth,language:document.querySelector('[role=tab][aria-selected=true]')?.dataset.language};
}"""


def _host_html(constrained):
    maximum = "760px" if constrained else "none"
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>'
            'html,body{margin:0;padding:0}body{font:16px/1.5 system-ui,sans-serif}'
            '.theme-container{max-width:' + maximum + ';margin-inline:auto}'
            '#theme-header,#theme-footer{padding:16px;background:rgb(245,248,251);color:rgb(16,35,59);font-size:18px}'
            '#theme-header table{border:1px solid rgb(185,198,212)}'
            '</style></head><body><header id="theme-header"><nav><a href="/">Theme navigation</a></nav><table><tr><td>Theme table</td></tr></table></header>'
            '<div class="theme-container" id="article-host"></div><footer id="theme-footer">Theme footer</footer></body></html>')


SENTINEL_JS = r"""() => Array.from(document.querySelectorAll('#theme-header,#theme-header nav,#theme-header a,#theme-header table,#theme-footer'),e=>{const s=getComputedStyle(e);return {tag:e.tagName,font:s.font,display:s.display,padding:s.padding,margin:s.margin,width:s.width,color:s.color,background:s.background,border:s.border,position:s.position,transform:s.transform}})"""


class _ViewerServer:
    def __init__(self, viewer_text):
        self.viewer_text = viewer_text

    def __enter__(self):
        payload = self.viewer_text.encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/viewer":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return "http://127.0.0.1:" + str(self.server.server_port) + "/viewer"

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _screenshot(page, selector, path, screenshots, errors, required=True):
    loc = page.locator(selector).first
    if not loc.count():
        if required:
            errors.append("screenshot selector missing: " + selector)
        return
    loc.screenshot(path=str(path), animations="disabled", timeout=20000)
    screenshots.append(str(path))


def _copy_tests(context, url, expected):
    results, errors = [], []
    for route in ("normal", "write-unavailable-fallback", "write-rejected-fallback", "both-denied"):
        page = context.new_page()
        route_page_errors = []
        page.on("pageerror", lambda exc: route_page_errors.append(str(exc)))
        page.goto(url)
        # Test permission applies only to this local origin. Production viewer
        # never reads a user's clipboard. Compatibility writes remain real.
        page.evaluate("navigator.clipboard.writeText('SGGROUP_QA_SENTINEL')")
        if route != "normal":
            page.evaluate("""route => {
              const real=navigator.clipboard;
              const fake={readText:real.readText.bind(real)};
              if(route!=='write-unavailable-fallback') fake.writeText=()=>Promise.reject(new DOMException('Test denial','NotAllowedError'));
              Object.defineProperty(navigator,'clipboard',{configurable:true,value:fake});
              if(route==='both-denied') document.execCommand=()=>false;
            }""", route)
        for key, original in expected.items():
            lang = key.split(":", 1)[0]
            page.locator('[role="tab"][data-language="' + lang + '"]').click()
            button = page.locator('[data-copy-target="' + key + '"]')
            button.evaluate("e=>e.addEventListener('click',()=>{window.__qaCopyScroll=[window.scrollX,window.scrollY]},{once:true,capture:true})")
            button.click()  # Exactly one click on the real delivery control.
            page.wait_for_function("['success','error'].includes(document.getElementById('copy-status').dataset.copyState)")
            actual = page.evaluate("navigator.clipboard.readText()")
            state = page.locator("#copy-status").get_attribute("data-copy-state")
            matches = actual == original
            focus_ok = button.evaluate("e=>document.activeElement===e")
            scroll_restored = page.evaluate("JSON.stringify(window.__qaCopyScroll)===JSON.stringify([window.scrollX,window.scrollY])")
            no_temporary_nodes = page.locator("body > textarea").count() == 0
            passed = (matches and state == "success") if route != "both-denied" else (actual == "SGGROUP_QA_SENTINEL" and state == "error")
            passed = passed and focus_ok and scroll_restored and no_temporary_nodes
            results.append({"route": route, "target": key, "clicks": 1, "pass": passed,
                            "exact_clipboard_match": matches, "state": state,
                            "focus_restored": focus_ok, "temporary_nodes_removed": no_temporary_nodes,
                            "scroll_restored": scroll_restored,
                            "expected_utf8_bytes": len(original.encode("utf-8"))})
            if not passed:
                errors.append("clipboard " + route + " " + key)
        # Switch back after EN and check actual content, not a JS cache value.
        if route == "normal":
            page.locator('#tab-en').focus()
            page.keyboard.press("ArrowLeft")
            if page.locator('#tab-ja').get_attribute('aria-selected') != 'true' or page.locator('#panel-ja').is_hidden():
                errors.append("viewer keyboard tab switching")
            for key in ("ja:title", "en:title", "ja:html"):
                lang = key.split(":")[0]
                page.locator('#tab-' + lang).click()
                page.locator('[data-copy-target="' + key + '"]').click()
                page.wait_for_function("document.getElementById('copy-status').dataset.copyState==='success'")
                passed = page.evaluate("navigator.clipboard.readText()") == expected[key]
                results.append({"route": "consecutive-tab-change", "target": key, "clicks": 1, "pass": passed, "exact_clipboard_match": passed})
                if not passed:
                    errors.append("clipboard consecutive " + key)
        errors.extend("clipboard " + route + " JavaScript: " + e for e in route_page_errors)
        page.close()
    return {"pass": not errors, "passed": not errors, "errors": errors, "tests": results,
            "tested_routes": ["real Clipboard API", "real execCommand with unavailable/rejected write API", "controlled denial of both write paths"],
            "limitations": ["Only installed Chromium on loopback was tested; file-origin and other-browser permissions may differ."]}


def run_browser_qa(bundle: dict, viewer_path: Path | str, output_dir: Path | str,
                   chromium_path="/usr/bin/chromium") -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    screenshot_dir = output / "screenshots"
    screenshot_dir.mkdir(exist_ok=True)
    errors, runs, screenshots, page_errors = [], [], [], []
    viewports = [(w, 900) for w in WIDTHS] + list(LANDSCAPES)
    viewer_text = Path(viewer_path).read_text(encoding="utf-8")
    with _ViewerServer(viewer_text) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium_path, headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"], reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        for language in ("ja", "en"):
            fragment = bundle[language]["html"]
            root_id = article_root_id(bundle[language]["metadata"]["slug"])
            for constrained in (False, True):
                for width, height in viewports:
                    page.set_viewport_size({"width": width, "height": height})
                    page.set_content(_host_html(constrained))
                    before = page.evaluate(SENTINEL_JS)
                    page.locator('#article-host').evaluate("(e,text)=>{e.innerHTML=text;}", fragment)
                    page.wait_for_function("document.fonts.status==='loaded'")
                    page.locator('[data-sgn-role="faq"] details').evaluate_all('(nodes)=>{for(const n of nodes)n.open=true}')
                    measurement = page.evaluate(MEASURE_JS, root_id)
                    if page.evaluate(SENTINEL_JS) != before:
                        measurement["errors"].append("article CSS changed theme sentinel styles")
                    measurement.update({"language": language, "width": width, "height": height, "constrained_host": constrained})
                    runs.append(measurement)
                    errors.extend(language + " " + str(width) + "x" + str(height) + (" constrained" if constrained else "") + ": " + e for e in measurement["errors"])
                    if not constrained and (width, height) in ((1920, 900), (390, 900), (820, 900)):
                        prefix = language + "-" + str(width)
                        _screenshot(page, '[data-sgn-role="hero"]', screenshot_dir / (prefix + '-hero.png'), screenshots, errors)
                        if width in (1920, 390):
                            _screenshot(page, '[data-sgn-role="body-frame"]', screenshot_dir / (prefix + '-body-start-same-scale.png'), screenshots, errors)
                            for role in ('guides', 'tools', 'related'):
                                _screenshot(page, '[data-sgn-role="' + role + '"]', screenshot_dir / (prefix + '-' + role + '.png'), screenshots, errors)
                            _screenshot(page, '[data-sgn-figure]', screenshot_dir / (prefix + '-principal-figure.png'), screenshots, errors)
                            quantitative = page.locator('[data-sgn-figure-type="line"],[data-sgn-figure-type="step"],[data-sgn-figure-type="bar"],[data-sgn-figure-type="dot"],[data-sgn-figure-type="stacked"],[data-sgn-figure-type="contribution"]')
                            for index in range(quantitative.count()):
                                p = screenshot_dir / (prefix + '-quantitative-' + str(index + 1) + '.png')
                                quantitative.nth(index).screenshot(path=str(p), animations="disabled")
                                screenshots.append(str(p))
                        if width == 390:
                            _screenshot(page, '[data-sgn-role="table-scroll"]', screenshot_dir / (prefix + '-wide-table.png'), screenshots, errors, required=False)
                            # Viewport captures preserve Hero/body same scale for review.
                            page.evaluate("window.scrollTo(0,0)")
                            p = screenshot_dir / (prefix + '-hero-to-body.png')
                            page.screenshot(path=str(p), full_page=False, animations="disabled")
                            screenshots.append(str(p))
                        if width == 820:
                            p = screenshot_dir / (prefix + '-tablet.png')
                            page.screenshot(path=str(p), animations="disabled")
                            screenshots.append(str(p))
                        if width == 1920:
                            page.evaluate("window.scrollTo(0,0)")
                            p = screenshot_dir / (prefix + '-whole-page.png')
                            page.screenshot(path=str(p), full_page=True, animations="disabled")
                            screenshots.append(str(p))
            # A half-width viewport is the layout consequence of 200% desktop
            # zoom. It is a surrogate, explicitly not native browser zoom proof.
            page.set_viewport_size({"width": 640, "height": 450})
            page.set_content(_host_html(False))
            page.locator('#article-host').evaluate("(e,text)=>{e.innerHTML=text;}", fragment)
            page.locator('[data-sgn-role="faq"] details').evaluate_all('(nodes)=>{for(const n of nodes)n.open=true}')
            zoom = page.evaluate(MEASURE_JS, root_id)
            zoom.update({"language": language, "width": 640, "height": 450, "zoom": "200% layout surrogate of 1280x900"})
            runs.append(zoom)
            errors.extend(language + " zoom surrogate: " + e for e in zoom["errors"])
            # Functional FAQ keyboard expansion, independent of static markup.
            faq = page.locator('[data-sgn-role="faq"] details').first
            if faq.count():
                faq.evaluate('(e)=>{e.open=false}')
                summary = faq.locator('summary')
                summary.focus()
                if summary.evaluate("e=>getComputedStyle(e).outlineStyle") == 'none':
                    errors.append(language + " FAQ keyboard focus not visible")
                page.keyboard.press('Enter')
                if faq.get_attribute('open') is None:
                    errors.append(language + " FAQ keyboard expansion failed")
            else:
                errors.append(language + " FAQ absent in browser")
        for width, height in viewports:
            page.set_viewport_size({"width": width, "height": height})
            page.goto(url)
            for lang in ("ja", "en"):
                page.locator('#tab-' + lang).click()
                result = page.evaluate(VIEWER_MEASURE_JS)
                result.update({"surface": "viewer", "width": width, "height": height, "language": lang})
                runs.append(result)
                errors.extend("viewer " + lang + " " + str(width) + ": " + e for e in result["errors"])
                if (width, height) in ((1920, 900), (390, 900)):
                    p = screenshot_dir / ('viewer-' + lang + '-' + str(width) + '.png')
                    page.screenshot(path=str(p), full_page=True, animations="disabled")
                    screenshots.append(str(p))
                    # Expand full source display and check it is also responsive.
                    page.locator('.html-view:visible summary').click()
                    expanded = page.evaluate(VIEWER_MEASURE_JS)
                    expanded.update({"surface": "viewer-expanded", "width": width, "height": height, "language": lang})
                    runs.append(expanded)
                    errors.extend("viewer expanded " + lang + " " + str(width) + ": " + e for e in expanded["errors"])
        clipboard = _copy_tests(context, url, clipboard_targets(bundle))
        errors.extend(clipboard["errors"])
        errors.extend("browser JavaScript: " + e for e in page_errors)
        context.close()
        browser.close()
    report = {"pass": not errors, "passed": not errors, "errors": errors,
              "bundle_digest": bundle_digest(bundle), "viewer_sha256": hashlib.sha256(viewer_text.encode('utf-8')).hexdigest(),
              "browser": "actual headless Chromium via Playwright", "chromium_path": chromium_path,
              "viewport_widths": list(WIDTHS), "landscapes": list(LANDSCAPES), "measurements": runs,
              "max_margin_difference_px": max((r.get("maxMarginDifference", 0) for r in runs), default=0),
              "max_reading_edge_difference_px": max((r.get("maxReadingEdgeDifference", 0) for r in runs), default=0),
              "clipboard": clipboard, "private_screenshots": screenshots,
              "limitations": ["Native 200% zoom was not executed; an equivalent narrow layout was tested.",
                              "Screenshots and DOM measurements require independent visual/semantic review for meaningful diagrams, label overlap, contrast, prose and news quality.",
                              "Live WordPress theme/plugin output and actual HTTP link/canonical checks need separate saved/live checks."]}
    (output / 'browser-qa.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle', required=True, type=Path)
    ap.add_argument('--viewer', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--chromium', default='/usr/bin/chromium')
    args = ap.parse_args()
    report = run_browser_qa(json.loads(args.bundle.read_text(encoding='utf-8')), args.viewer, args.output, args.chromium)
    print(json.dumps({"pass": report['pass'], "errors": report['errors'], "report": str(args.output / 'browser-qa.json'), "clipboard_tests": len(report['clipboard']['tests'])}, ensure_ascii=False))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
