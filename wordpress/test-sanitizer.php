<?php
define('ABSPATH', __DIR__ . '/');
require __DIR__ . '/sggroup-news-publisher.php';
$fixtures = json_decode(file_get_contents(__DIR__ . '/security-regression-inputs.json'), true, 512, JSON_THROW_ON_ERROR);
$failures = []; $count = 0;
foreach ($fixtures['cases'] as $case) {
    ++$count;
    try {
        $canonical = SGNews_Fragment::canonical($case['html'], $fixtures['root_id'], $fixtures['language']);
        if ($case['expected'] !== 'accept') { $failures[] = $case['id'] . ': incorrectly accepted'; }
        elseif (SGNews_Fragment::canonical($canonical, $fixtures['root_id'], $fixtures['language']) !== $canonical) { $failures[] = $case['id'] . ': canonicalization is not idempotent'; }
    } catch (Throwable $e) { if ($case['expected'] === 'accept') { $failures[] = $case['id'] . ': ' . $e->getMessage(); } }
}
$base = $fixtures['cases'][0]['html'];
$voids = str_replace('</article>', '<hr/><table><colgroup><col/></colgroup><tbody><tr><td>Cell<br/></td></tr></tbody></table></article>', $base);
try {
    ++$count;
    $out = SGNews_Fragment::canonical($voids, $fixtures['root_id'], $fixtures['language']);
    if (SGNews_Fragment::canonical($out, $fixtures['root_id'], $fixtures['language']) !== $out) { $failures[] = 'void element canonicalization is not idempotent'; }
} catch (Throwable $e) { $failures[] = 'void element canonicalization: ' . $e->getMessage(); }
$shortcode = str_replace('非公開の確認。', '[sgnews_shortcode_probe]&#10;https://example.invalid/embed&#10;', $base);
try {
    $out = SGNews_Fragment::canonical($shortcode, $fixtures['root_id'], $fixtures['language']);
    ++$count;
    if (str_contains($out, '[sgnews_shortcode_probe]') || str_contains($out, "\nhttps://") || !str_contains($out, '&#91;sgnews_shortcode_probe&#93;')) { $failures[] = 'render directives were not neutralized'; }
    if (SGNews_Fragment::canonical($out, $fixtures['root_id'], $fixtures['language']) !== $out) { $failures[] = 'directive encoding is not idempotent'; }
} catch (Throwable $e) { $failures[] = 'shortcode neutralization: ' . $e->getMessage(); }
foreach ([
    '#sg-news-security-probe-ja{color:#10233b;--sgn-paper:#fff;width:100vw;margin-inline:calc(50% - 50vw)}',
    '#sg-news-security-probe-ja .sgn-body{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));font-family:"Yu Mincho",Georgia,serif}',
    '#sg-news-security-probe-ja details:focus-visible{outline:2px solid #123456}',
    '#sg-news-security-probe-ja{color:#132e54;background-color:#1e14}'
] as $css) {
    ++$count;
    try { SGNews_CSS::check($css, $fixtures['root_id']); } catch (Throwable $e) { $failures[] = 'required CSS failed: ' . $css . ': ' . $e->getMessage(); }
}
foreach ([
    '#sg-news-security-probe-ja .sgn-body{width:100vw}',
    '#sg-news-security-probe-ja:has(header){color:red}',
    '#sg-news-security-probe-ja > body + header{color:red}',
    '#sg-news-security-probe-ja{font:var(--evil)}',
    '#sg-news-security-probe-ja{content:attr(href)}'
] as $css) {
    ++$count;
    try { SGNews_CSS::check($css, $fixtures['root_id']); $failures[] = 'unsafe CSS accepted: ' . $css; } catch (Throwable $e) {}
}
echo json_encode(['tests'=>$count,'failures'=>$failures,'scope'=>'actual PHP DOM/CSS parser; not a live WordPress deployment'], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . "\n";
exit($failures ? 1 : 0);
