"""Produce the sole delivery HTML, with no private audits or external assets."""
from __future__ import annotations

import argparse
from html import escape
import json
import os
from pathlib import Path

try:
    from .article_qa import METADATA_KEYS, _plain_metadata, _slug_errors
except ImportError:
    from article_qa import METADATA_KEYS, _plain_metadata, _slug_errors


METADATA_LABELS = ("記事タイトル", "AIOSEO投稿タイトル", "AIOSEOメタディスクリプション", "共通スラッグ")


def clipboard_targets(bundle):
    """Exact original strings. Explicit ordering avoids object-order field mixups."""
    targets = {}
    for lang in ("ja", "en"):
        targets[lang + ":html"] = bundle[lang]["html"]
        for key in METADATA_KEYS:
            targets[lang + ":" + key] = bundle[lang]["metadata"][key]
    return targets


def _validate_payload(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {"ja", "en"}:
        raise ValueError("Viewer input requires exactly ja and en language entries")
    for lang in ("ja", "en"):
        entry = bundle[lang]
        if not isinstance(entry, dict) or set(entry) != {"html", "metadata"}:
            raise ValueError("Language entry requires only html and metadata")
        if not isinstance(entry["html"], str) or not entry["html"]:
            raise ValueError("Full original HTML string required")
        metadata = entry["metadata"]
        if not isinstance(metadata, dict) or set(metadata) != set(METADATA_KEYS):
            raise ValueError("Exactly four metadata values required")
        if any(not _plain_metadata(metadata[k]) for k in METADATA_KEYS):
            raise ValueError("Metadata must be unadorned single-line plain text")
    slug = bundle["ja"]["metadata"]["slug"]
    if slug != bundle["en"]["metadata"]["slug"] or _slug_errors(slug):
        raise ValueError("Both languages require the same real-date terminal slug")


def generate_viewer(bundle: dict) -> str:
    """Payload bytes survive HTML parsing, script termination and Unicode safely.

    The browser clipboard is Unicode text; UTF-8 encoding of each copied string
    is exactly the original UTF-8 payload, including non-BMP characters/newlines.
    """
    _validate_payload(bundle)
    payload = json.dumps(clipboard_targets(bundle), ensure_ascii=False, separators=(",", ":"))
    # JSON is data in a JavaScript string context; HTML parsing still sees script
    # terminators unless < is escaped. Do not Base64/embed any private records.
    payload = (payload.replace("&", "\\u0026").replace("<", "\\u003c")
               .replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    panels = []
    for lang, language_name in (("ja", "日本語"), ("en", "英語")):
        entry = bundle[lang]
        metadata = []
        for key, label in zip(METADATA_KEYS, METADATA_LABELS):
            metadata.append(
                '<div class="metadata-item"><h3 id="label-' + lang + '-' + key + '">' + label + '</h3>'
                '<p class="metadata-value" id="value-' + lang + '-' + key + '">' + escape(entry["metadata"][key]) + '</p>'
                '<button type="button" data-copy-target="' + lang + ':' + key + '" aria-label="' + language_name + '版の' + label + 'をコピー">コピー</button></div>')
        hidden = '' if lang == 'ja' else ' hidden'
        panels.append(
            '<section id="panel-' + lang + '" role="tabpanel" aria-labelledby="tab-' + lang + '" tabindex="0"' + hidden + '>'
            '<h2>' + language_name + '版</h2><button type="button" class="html-copy" data-copy-target="' + lang + ':html">' + language_name + 'HTMLをコピー</button>'
            '<div class="metadata" aria-label="' + language_name + '版のメタ情報">' + ''.join(metadata) + '</div>'
            '<details class="html-view"><summary>' + language_name + 'HTML全文を表示</summary>'
            '<pre lang="' + lang + '">' + escape(entry['html']) + '</pre></details></section>')
    return '''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SG Group News — 原稿コピー</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#fffdf8;color:#10233b;font:16px/1.7 system-ui,sans-serif;overflow-wrap:anywhere}
main{max-width:1080px;margin-inline:auto;padding:24px}h1{font-size:clamp(1.5rem,4vw,2.3rem);line-height:1.3}h2{margin-block:24px 12px}h3{font-size:1rem;margin-block:0 8px}
button,summary{cursor:pointer}button{min-height:44px;border:1px solid #5d6b7e;background:white;color:#10233b;padding:8px 14px;font:inherit;max-width:100%;overflow-wrap:anywhere}
button:focus-visible,summary:focus-visible,[tabindex]:focus-visible{outline:3px solid #1f67d2;outline-offset:3px}
[role=tablist]{display:flex;gap:10px;border-block-end:2px solid #10233b;padding-block-end:12px}button[aria-selected=true]{background:#10233b;color:white}
[hidden]{display:none!important}.metadata{margin-block:20px;display:grid;grid-template-columns:1fr 1fr;gap:20px}.metadata-item{min-width:0;padding-block:14px;border-block-start:1px solid #b9c6d4}
.metadata-value{white-space:pre-wrap;margin-block:0 12px;overflow-wrap:anywhere}.html-copy{font-weight:700}.html-view{border-block-start:1px solid #b9c6d4;padding-block:12px}
pre{font:13px/1.55 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%;tab-size:2}.status{min-height:2em;margin-block:12px}.status[data-copy-state=error]{color:#a53c45}
@media(max-width:720px){main{padding:12px}.metadata{grid-template-columns:1fr}}@media(max-width:380px){main{padding:10px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}@media print{button,[role=tablist],.status{display:none}main{padding:0}}
</style></head><body><main><h1>SG Group News 原稿コピー</h1>
<p>言語を切り替え、HTMLまたは各項目のボタンでコピーしてください。</p>
<div role="tablist" aria-label="原稿の言語"><button id="tab-ja" type="button" role="tab" aria-selected="true" aria-controls="panel-ja" tabindex="0" data-language="ja">日本語</button><button id="tab-en" type="button" role="tab" aria-selected="false" aria-controls="panel-en" tabindex="-1" data-language="en">English</button></div>
<p id="copy-status" class="status" role="status" aria-live="polite" aria-atomic="true"></p>
''' + ''.join(panels) + '''
</main><script>
"use strict";
const copyPayloads = ''' + payload + ''';
const statusNode = document.getElementById("copy-status");
const tabNodes = Array.from(document.querySelectorAll("[role=tab]"));
let copySequence = 0;
function selectLanguage(language, focusTab) {
  for (const tab of tabNodes) {
    const selected = tab.dataset.language === language;
    tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !selected;
    if (selected && focusTab) tab.focus({preventScroll:true});
  }
  statusNode.textContent = ""; statusNode.removeAttribute("data-copy-state");
}
for (const tab of tabNodes) {
  tab.addEventListener("click", () => selectLanguage(tab.dataset.language, false));
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const index = tabNodes.indexOf(tab);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabNodes.length-1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabNodes.length) % tabNodes.length;
    selectLanguage(tabNodes[next].dataset.language, true);
  });
}
function fallbackWrite(text) {
  const active = document.activeElement, x = window.scrollX, y = window.scrollY;
  const saved = []; const selection = window.getSelection();
  if (selection) for (let i=0;i<selection.rangeCount;i++) saved.push(selection.getRangeAt(i).cloneRange());
  const area = document.createElement("textarea");
  area.value = text; area.setAttribute("readonly", ""); area.setAttribute("aria-hidden", "true");
  area.style.cssText = "position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;pointer-events:none";
  document.body.appendChild(area);
  // textarea.value normalizes CRLF. A real copy event supplies the original
  // string as text/plain, preserving payload newline bytes and Unicode.
  let copyEventWritten = false;
  const onCopy = event => {
    if (!event.clipboardData) return;
    try { event.clipboardData.setData("text/plain", text); event.preventDefault(); copyEventWritten = true; }
    catch (_) { copyEventWritten = false; }
  };
  document.addEventListener("copy", onCopy, true);
  try { area.focus({preventScroll:true}); area.select(); return document.execCommand("copy") === true && copyEventWritten; }
  catch (_) { return false; }
  finally {
    document.removeEventListener("copy", onCopy, true);
    area.remove();
    if (selection) { selection.removeAllRanges(); for (const range of saved) selection.addRange(range); }
    if (active && active.isConnected && typeof active.focus === "function") active.focus({preventScroll:true});
    window.scrollTo(x,y);
  }
}
for (const button of document.querySelectorAll("[data-copy-target]")) {
  button.addEventListener("click", async () => {
    const sequence = ++copySequence;
    const text = copyPayloads[button.dataset.copyTarget];
    if (typeof text !== "string") { statusNode.dataset.copyState="error";statusNode.textContent="コピー対象を読み込めません。";return; }
    let written = false;
    statusNode.dataset.copyState = "pending"; statusNode.textContent = "";
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(text); written = true;
      }
    } catch (_) { written = false; }
    if (!written) written = fallbackWrite(text);
    if (sequence === copySequence) {
      statusNode.dataset.copyState = written ? "success" : "error";
      statusNode.textContent = written ? "コピーしました。" : "コピーできませんでした。ブラウザのクリップボード設定を確認して再試行してください。";
    }
  });
}
</script></body></html>
'''


def write_viewer(bundle: dict, directory: Path | str) -> Path:
    html = generate_viewer(bundle)
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    name = "SGGroup_News_" + bundle["ja"]["metadata"]["slug"] + "_All_Copy_Viewer.html"
    limit = os.pathconf(destination, "PC_NAME_MAX")
    if len(os.fsencode(name)) > limit:
        raise ValueError("The complete required viewer filename exceeds this filesystem's filename limit; use compatible storage without shortening the slug")
    path = destination / name
    path.write_text(html, encoding="utf-8", newline="")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--directory", required=True, type=Path)
    args = ap.parse_args()
    path = write_viewer(json.loads(args.bundle.read_text(encoding="utf-8")), args.directory)
    print(str(path))


if __name__ == "__main__":
    main()
