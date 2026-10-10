<?php
/**
 * Plugin Name: SG Group News Publisher
 * Description: Dedicated, fail-closed bilingual News publishing integration. Administrator installation and an explicit sgnews_publish grant are required.
 * Version: 0.1.0
 * Requires PHP: 8.1
 * License: GPL-2.0-or-later
 */
if (!defined('ABSPATH')) { exit; }

/** This is a restricted CSS parser, not support for arbitrary CSS. Unsupported syntax is rejected. */
final class SGNews_CSS {
    private string $css;
    private string $root;
    private int $at = 0;
    private int $rules = 0;
    private const PROPERTIES = 'color background background-color border border-color border-style border-width border-top border-bottom border-left border-right border-block border-inline border-block-start border-block-end border-inline-start border-inline-end border-collapse border-spacing border-radius box-sizing display visibility width min-width max-width height min-height max-height inline-size min-inline-size max-inline-size block-size min-block-size max-block-size margin margin-top margin-bottom margin-left margin-right margin-block margin-inline margin-block-start margin-block-end margin-inline-start margin-inline-end padding padding-top padding-bottom padding-left padding-right padding-block padding-inline padding-block-start padding-block-end padding-inline-start padding-inline-end font font-family font-size font-style font-weight font-variant font-variant-numeric line-height letter-spacing word-spacing text-align text-decoration text-decoration-color text-decoration-style text-decoration-thickness text-underline-offset text-transform text-indent text-wrap text-overflow white-space overflow-wrap word-break hyphens overflow overflow-x overflow-y overflow-inline overflow-block table-layout vertical-align list-style list-style-type list-style-position gap row-gap column-gap grid-template-columns grid-template-rows grid-column grid-row grid-auto-flow grid-auto-columns grid-auto-rows align-items align-content align-self justify-items justify-content justify-self flex flex-basis flex-grow flex-shrink flex-direction flex-wrap order position cursor opacity outline outline-color outline-style outline-width outline-offset scroll-behavior scroll-margin-top break-inside break-before break-after page-break-inside content fill stroke stroke-width stroke-dasharray stroke-linecap stroke-linejoin text-anchor dominant-baseline';
    public static function check(string $css, string $root): void {
        if (strlen($css) > 65536 || preg_match('/[\\\\<>&\\[\\]{}]/u', '') === false) { throw new InvalidArgumentException('CSS size or encoding.'); }
        if (preg_match('/[\\\\<>&\\[\\]\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]/u', $css) || str_contains($css, '/*') || str_contains($css, '*/')) {
            throw new InvalidArgumentException('CSS escapes, comments, brackets and markup are unsupported.');
        }
        $p = new self(); $p->css = $css; $p->root = $root; $p->sheet(false, 0);
        if (!$p->rules) { throw new InvalidArgumentException('At least one scoped CSS rule is required.'); }
    }
    private function whitespace(): void { while ($this->at < strlen($this->css) && ctype_space($this->css[$this->at])) { ++$this->at; } }
    private function sheet(bool $nested, int $depth): void {
        if ($depth > 3) { throw new InvalidArgumentException('CSS nesting exceeds policy.'); }
        while (true) {
            $this->whitespace();
            if ($this->at === strlen($this->css)) {
                if ($nested) { throw new InvalidArgumentException('Unclosed CSS block.'); }
                return;
            }
            if ($this->css[$this->at] === '}') {
                if (!$nested) { throw new InvalidArgumentException('Unexpected CSS closing brace.'); }
                ++$this->at; return;
            }
            $header = trim($this->readUntil('{'));
            if ($header === '') { throw new InvalidArgumentException('Empty CSS selector.'); }
            if ($header[0] === '@') {
                if (!preg_match('/^@media\s+(.+)$/s', $header, $m)) { throw new InvalidArgumentException('Only @media is permitted.'); }
                $this->media($m[1]); $this->sheet(true, $depth + 1); continue;
            }
            $selectors = $this->split($header, ',');
            foreach ($selectors as $selector) { $this->selector(trim($selector)); }
            $body = $this->readUntil('}');
            $this->declarations($body, count($selectors) === 1 && trim($selectors[0]) === '#' . $this->root);
            if (++$this->rules > 1500) { throw new InvalidArgumentException('Too many CSS rules.'); }
        }
    }
    private function readUntil(string $delimiter): string {
        $start = $this->at; $depth = 0; $quote = '';
        for (; $this->at < strlen($this->css); ++$this->at) {
            $c = $this->css[$this->at];
            if ($quote !== '') { if ($c === $quote) { $quote = ''; } continue; }
            if ($c === '"' || $c === "'") { $quote = $c; continue; }
            if ($c === '(') { if (++$depth > 12) { throw new InvalidArgumentException('CSS function depth.'); } continue; }
            if ($c === ')') { if (--$depth < 0) { throw new InvalidArgumentException('Unbalanced CSS function.'); } continue; }
            if ($c === $delimiter && !$depth) { $part = substr($this->css, $start, $this->at - $start); ++$this->at; return $part; }
            if (($c === '{' || $c === '}') && $c !== $delimiter) { throw new InvalidArgumentException('Unexpected CSS brace.'); }
        }
        throw new InvalidArgumentException('Unclosed CSS rule.');
    }
    private function split(string $text, string $delimiter): array {
        $result = []; $start = 0; $depth = 0; $quote = '';
        for ($i = 0, $n = strlen($text); $i < $n; ++$i) {
            $c = $text[$i];
            if ($quote !== '') { if ($c === $quote) { $quote = ''; } continue; }
            if ($c === '"' || $c === "'") { $quote = $c; continue; }
            if ($c === '(') { if (++$depth > 12) { throw new InvalidArgumentException('CSS function depth.'); } }
            elseif ($c === ')') { if (--$depth < 0) { throw new InvalidArgumentException('CSS function balance.'); } }
            elseif ($c === $delimiter && !$depth) { $result[] = substr($text, $start, $i - $start); $start = $i + 1; }
        }
        if ($quote !== '' || $depth) { throw new InvalidArgumentException('Unbalanced CSS value.'); }
        $result[] = substr($text, $start); return $result;
    }
    private function media(string $query): void {
        foreach ($this->split($query, ',') as $part) {
            $part = trim($part);
            if (preg_match('/^(?:screen|print|all)$/', $part)) { continue; }
            $part = preg_replace('/^(?:screen|print|all)\s+and\s+/', '', $part);
            foreach (preg_split('/\s+and\s+/', $part) as $condition) {
                if (!preg_match('/^\(\s*(?:(?:min|max)-width\s*:\s*(?:[1-9]\d{1,3})px|prefers-reduced-motion\s*:\s*(?:reduce|no-preference)|orientation\s*:\s*(?:portrait|landscape))\s*\)$/', $condition)) {
                    throw new InvalidArgumentException('Unsupported media query.');
                }
            }
        }
    }
    private function selector(string $selector): void {
        $root = '#' . $this->root;
        if (!str_starts_with($selector, $root) || preg_match('/[+~@\\[\\]\\\\]/', $selector)) { throw new InvalidArgumentException('Selector escapes the article root.'); }
        $tail = substr($selector, strlen($root));
        if ($tail !== '' && !preg_match('/^[\\s:.>]/', $tail)) { throw new InvalidArgumentException('Root ID must be an exact token.'); }
        $pattern = '/^(?:\s+|>|\.sgn-[a-z0-9_-]+|#' . preg_quote($this->root, '/') . '-[a-z0-9_-]+|::(?:before|after|marker)|:(?:hover|focus-visible|focus-within|focus|first-child|last-child|only-child|first-of-type|last-of-type|only-of-type)|:(?:nth-child|nth-of-type)\(\s*(?:odd|even|\d{1,4}|[+-]?\d{0,3}n(?:\s*[+-]\s*\d{1,3})?)\s*\)|\*|[a-z][a-z0-9-]*)/i';
        $previous = 'root';
        while ($tail !== '') {
            if (!preg_match($pattern, $tail, $m)) { throw new InvalidArgumentException('Unsupported selector token.'); }
            $token = $m[0]; $tail = substr($tail, strlen($token));
            if (ctype_space($token)) { $previous = 'space'; continue; }
            if ($token === '>') { if ($previous === '>') { throw new InvalidArgumentException('Invalid child selector.'); } $previous = '>'; continue; }
            if ($token[0] !== '.' && $token[0] !== '#' && $token[0] !== ':' && $previous !== 'space' && $previous !== '>') { throw new InvalidArgumentException('Invalid selector compound.'); }
            if ($token[0] !== '.' && $token[0] !== '#' && $token[0] !== ':' && $token !== '*' && !in_array(strtolower($token), SGNews_Fragment::selectorTags(), true)) { throw new InvalidArgumentException('Unsupported selector element.'); }
            $previous = 'compound';
        }
        if ($previous === '>') { throw new InvalidArgumentException('Incomplete selector.'); }
    }
    private function declarations(string $body, bool $rootOnly): void {
        $known = explode(' ', self::PROPERTIES);
        foreach ($this->split($body, ';') as $declaration) {
            if (trim($declaration) === '') { continue; }
            if (!preg_match('/^\s*([a-z-]+)\s*:\s*(.+?)\s*$/is', $declaration, $m)) { throw new InvalidArgumentException('Malformed declaration.'); }
            $property = strtolower($m[1]); $value = trim($m[2]);
            if (!in_array($property, $known, true) && !preg_match('/^--sgn-[a-z][a-z0-9-]*$/', $property)) { throw new InvalidArgumentException('Unsupported property: ' . $property); }
            if (strlen($value) > 2048 || preg_match('/[{};@\\\\<>&\\[\\]\\x00-\\x1f]/u', $value)) { throw new InvalidArgumentException('Unsupported declaration value.'); }
            if ($property === 'position' && !in_array($value, ['static', 'relative'], true)) { throw new InvalidArgumentException('Only static/relative positioning is supported.'); }
            if (str_contains($value, '!')) { throw new InvalidArgumentException('!important is unsupported.'); }
            foreach (preg_split('/(["\']).*?\1/s', $value) as $outside) {
                if (preg_match('/[^a-zA-Z0-9_\-+\s.,%#()\/:]/', $outside)) { throw new InvalidArgumentException('Unsupported CSS character.'); }
                $numeric = preg_replace('/#[a-fA-F0-9]{3,8}\b/', '', $outside);
                if (preg_match('/(?<![a-zA-Z0-9_-])\d(?:\.\d+)?[eE][+-]?\d/', $numeric)) { throw new InvalidArgumentException('Scientific CSS numbers are unsupported.'); }
                preg_match_all('/(?<![a-zA-Z0-9_-])[-+]?\d+(?:\.\d+)?/', $numeric, $numbers);
                foreach ($numbers[0] as $number) { if (!is_finite((float)$number) || abs((float)$number) > 100000) { throw new InvalidArgumentException('CSS number outside limits.'); } }
            }
            $this->functions($value);
            if (str_starts_with($property, '--') && preg_match('/["\']|\b(?:inherit|initial|unset|revert)\b/i', $value)) { throw new InvalidArgumentException('Custom tokens accept numeric/colour values only.'); }
            if (preg_match('/\b(?:vw|vh|vmin|vmax|svw|svh|dvw|dvh|lvw|lvh)\b/i', $value)) { throw new InvalidArgumentException('Unsupported viewport unit.'); }
            if (preg_match('/\d(?:\.\d+)?(?:vw|vh|vmin|vmax|svw|svh|dvw|dvh|lvw|lvh)\b/i', $value)) {
                $v = preg_replace('/\s+/', '', $value);
                if (!$rootOnly || !(($property === 'width' && $v === '100vw') || (in_array($property, ['margin-left','margin-right','margin-inline','margin-inline-start','margin-inline-end'], true) && $v === 'calc(50%-50vw)'))) {
                    throw new InvalidArgumentException('Viewport geometry is only allowed for the exact root full-bleed width/margin.');
                }
            }
        }
    }
    private function functions(string $value): void {
        $quote = ''; $stack = [];
        for ($i = 0, $n = strlen($value); $i < $n; ++$i) {
            $c = $value[$i];
            if ($quote !== '') { if ($c === $quote) { $quote = ''; } continue; }
            if ($c === '"' || $c === "'") { $quote = $c; continue; }
            if ($c === '(') {
                if (!preg_match('/([a-zA-Z][a-zA-Z0-9-]*)$/', substr($value, 0, $i), $m)) { throw new InvalidArgumentException('Bare CSS parenthesis.'); }
                $function = strtolower($m[1]);
                if (!in_array($function, ['var','calc','min','max','clamp','repeat','minmax','fit-content','rgb','rgba','hsl','hsla','linear-gradient','attr'], true)) { throw new InvalidArgumentException('Unsupported CSS function: ' . $function); }
                $stack[] = [$function, $i + 1]; if (count($stack) > 12) { throw new InvalidArgumentException('CSS function depth.'); }
            } elseif ($c === ')') {
                if (!$stack) { throw new InvalidArgumentException('Unbalanced CSS function.'); }
                [$function, $start] = array_pop($stack); $inside = trim(substr($value, $start, $i - $start));
                if ($function === 'var' && !preg_match('/^--sgn-[a-z][a-z0-9-]*(?:\s*,.+)?$/s', $inside)) { throw new InvalidArgumentException('Only --sgn- custom tokens may be referenced.'); }
                if ($function === 'attr' && $inside !== 'data-label') { throw new InvalidArgumentException('Only attr(data-label) is supported.'); }
            }
        }
        if ($quote !== '' || $stack) { throw new InvalidArgumentException('Unbalanced CSS value.'); }
    }
}

final class SGNews_Fragment {
    private const HTML = 'article section header footer nav aside div span p h1 h2 h3 h4 h5 h6 strong em b i u small s abbr mark sup sub time blockquote q cite code pre kbd samp ul ol li dl dt dd table caption colgroup col thead tbody tfoot tr th td a figure figcaption details summary br hr';
    private const SVG = 'svg g rect circle ellipse line polyline polygon path text tspan title desc defs marker linearGradient stop clipPath';
    private const SVG_NS = 'http://www.w3.org/2000/svg';
    private array $ids = [];
    private array $references = [];
    private int $nodes = 0;
    private string $root;
    public static function selectorTags(): array { return array_map('strtolower', explode(' ', self::HTML . ' ' . self::SVG)); }
    public static function canonical(string $input, string $root, string $language): string {
        if (!class_exists('DOMDocument')) { throw new RuntimeException('PHP DOM extension is required.'); }
        if (strlen($input) > 750000 || !preg_match('//u', $input) || preg_match('/<!|<\?/i', $input)) { throw new InvalidArgumentException('Unsupported markup declaration, encoding or size.'); }
        if (!preg_match('/^sg-news-[a-z0-9-]{3,120}$/', $root) || !in_array($language, ['ja','en'], true)) { throw new InvalidArgumentException('Invalid article root or language.'); }
        $old = libxml_use_internal_errors(true);
        try {
            $doc = new DOMDocument('1.0', 'UTF-8');
            if (!$doc->loadXML('<sgnews-wrapper>' . $input . '</sgnews-wrapper>', LIBXML_NONET | LIBXML_COMPACT)) { throw new InvalidArgumentException('Require well-formed XML-compatible HTML.'); }
            $elements = [];
            foreach ($doc->documentElement->childNodes as $child) {
                if ($child->nodeType === XML_TEXT_NODE && trim($child->textContent) === '') { continue; }
                if (!$child instanceof DOMElement) { throw new InvalidArgumentException('Only style and article may be top-level nodes.'); }
                $elements[] = $child;
            }
            if (count($elements) !== 2 || $elements[0]->tagName !== 'style' || $elements[1]->tagName !== 'article') { throw new InvalidArgumentException('Exactly one style followed by one article is required.'); }
            [$style, $article] = $elements;
            if ($style->attributes->length || $style->namespaceURI || $article->namespaceURI || $article->getAttribute('id') !== $root || $article->getAttribute('lang') !== $language) { throw new InvalidArgumentException('Article/style root mismatch.'); }
            foreach ($style->childNodes as $child) { if ($child->nodeType !== XML_TEXT_NODE) { throw new InvalidArgumentException('CSS must be plain text.'); } }
            SGNews_CSS::check($style->textContent, $root);
            $p = new self(); $p->root = $root;
            $html = $p->element($article, 0, false);
            if ($article->getElementsByTagName('h1')->length !== 1) { throw new InvalidArgumentException('Exactly one H1 is required.'); }
            foreach ($p->references as $id) { if (!isset($p->ids[$id])) { throw new InvalidArgumentException('Missing local ID reference.'); } }
            return '<style>' . $style->textContent . '</style>' . $html;
        } finally { libxml_clear_errors(); libxml_use_internal_errors($old); }
    }
    private static function escape(string $text): string {
        return str_replace(['[',']',"\r","\n"], ['&#91;','&#93;','&#13;','&#10;'], htmlspecialchars($text, ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML5, 'UTF-8'));
    }
    private function element(DOMElement $node, int $depth, bool $insideSVG): string {
        if (++$this->nodes > 10000 || $depth > 40) { throw new InvalidArgumentException('Markup complexity exceeds limits.'); }
        $svg = $insideSVG || $node->tagName === 'svg'; $tag = $node->tagName;
        if ($svg) {
            if ($node->namespaceURI !== self::SVG_NS || !in_array($tag, explode(' ', self::SVG), true)) { throw new InvalidArgumentException('Unsupported SVG element/namespace.'); }
            if ($tag === 'svg' && (!$node->hasAttribute('viewBox') || $insideSVG)) { throw new InvalidArgumentException('SVG requires a viewBox; nested SVG is unsupported.'); }
        } elseif ($node->namespaceURI || !in_array($tag, explode(' ', self::HTML), true) || ($tag === 'article' && $depth !== 0)) { throw new InvalidArgumentException('Unsupported HTML element/namespace.'); }
        $attrs = [];
        foreach ($node->attributes as $attribute) {
            $name = $attribute->nodeName; $value = $attribute->nodeValue;
            if ($attribute->namespaceURI || preg_match('/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/u', $value) || strlen($value) > 12000) { throw new InvalidArgumentException('Unsupported attribute namespace/value.'); }
            $common = ['id','class','lang','role','aria-label','aria-labelledby','aria-describedby','aria-hidden','tabindex','title','data-label','data-sgn-role','data-sgn-rail','data-sgn-section','data-sgn-figure','data-sgn-figure-type','data-sgn-faq','data-sgn-source','data-sgn-link-role'];
            $specific = [
                'a'=>['href','target','rel'], 'time'=>['datetime'], 'details'=>['open'], 'ol'=>['start','reversed'],
                'th'=>['scope','colspan','rowspan'], 'td'=>['colspan','rowspan'], 'col'=>['span'],
                'svg'=>['viewBox','width','height','preserveAspectRatio'], 'g'=>['transform','fill','stroke','stroke-width'],
                'rect'=>['x','y','width','height','rx','ry','fill','stroke','stroke-width'],
                'circle'=>['cx','cy','r','fill','stroke','stroke-width'], 'ellipse'=>['cx','cy','rx','ry','fill','stroke','stroke-width'],
                'line'=>['x1','y1','x2','y2','stroke','stroke-width','stroke-dasharray','marker-start','marker-end'],
                'polyline'=>['points','fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','marker-start','marker-end'],
                'polygon'=>['points','fill','stroke','stroke-width'], 'path'=>['d','fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','marker-start','marker-end'],
                'text'=>['x','y','dx','dy','fill','font-size','font-family','font-weight','text-anchor','dominant-baseline','transform'],
                'tspan'=>['x','y','dx','dy','fill','font-size','font-weight'],
                'marker'=>['viewBox','refX','refY','markerWidth','markerHeight','orient','markerUnits'],
                'linearGradient'=>['x1','y1','x2','y2','gradientUnits'], 'stop'=>['offset','stop-color','stop-opacity'], 'clipPath'=>['clipPathUnits']
            ];
            if (!in_array($name, array_merge($common, $specific[$tag] ?? []), true)) { throw new InvalidArgumentException('Unsupported attribute: ' . $name); }
            if ($name === 'id') {
                if (($value !== $this->root && !str_starts_with($value, $this->root . '-')) || !preg_match('/^[a-z][a-z0-9-]{2,160}$/', $value) || isset($this->ids[$value])) { throw new InvalidArgumentException('ID must be unique and rooted.'); }
                $this->ids[$value] = true;
            } elseif ($name === 'class' && !preg_match('/^sgn-[a-z0-9_-]+(?:\s+sgn-[a-z0-9_-]+)*$/', $value)) { throw new InvalidArgumentException('Only sgn- classes are supported.'); }
            elseif (in_array($name, ['aria-labelledby','aria-describedby'], true)) { foreach (preg_split('/\s+/', trim($value)) as $id) { $this->references[] = $id; } }
            elseif ($name === 'href') {
                if ($tag !== 'a' || preg_match('/[\x00-\x20\x7f\\\\]/', $value)) { throw new InvalidArgumentException('Unsafe link.'); }
                if (str_starts_with($value, '#')) { $this->references[] = substr($value, 1); }
                else { $url = parse_url($value); if (!$url || !in_array(strtolower($url['scheme'] ?? ''), ['http','https'], true) || empty($url['host']) || isset($url['user']) || isset($url['pass'])) { throw new InvalidArgumentException('Only HTTP(S) source/navigation links are supported.'); } }
            } elseif ($name === 'target' && !in_array($value, ['_blank','_self'], true)) { throw new InvalidArgumentException('Unsupported link target.'); }
            elseif ($name === 'rel' && !preg_match('/^(?:(?:noopener|noreferrer|nofollow|sponsored|ugc)\s*)*$/', $value)) { throw new InvalidArgumentException('Unsupported link rel.'); }
            elseif ($name === 'tabindex' && !in_array($value, ['0','-1'], true)) { throw new InvalidArgumentException('Unsupported tabindex.'); }
            elseif (in_array($name, ['aria-hidden'], true) && !in_array($value, ['true','false'], true)) { throw new InvalidArgumentException('Invalid ARIA boolean.'); }
            elseif ($svg && in_array($name, ['fill','stroke','stop-color'], true)) {
                if (!preg_match('/^(?:none|currentColor|transparent|#[a-fA-F0-9]{3,8}|[a-zA-Z]{1,20})$/', $value)) { throw new InvalidArgumentException('SVG paint resources are unsupported.'); }
            } elseif ($svg && in_array($name, ['marker-start','marker-end'], true)) {
                if (!preg_match('/^url\(#([a-z][a-z0-9-]+)\)$/', $value, $m)) { throw new InvalidArgumentException('Only local SVG markers are supported.'); }
                $this->references[] = $m[1];
            } elseif ($svg && $name === 'd') {
                if (strlen($value) > 10000 || !preg_match('/^[MmZzLlHhVvCcSsQqTtAa0-9eE.,+\-\s]+$/', $value)) { throw new InvalidArgumentException('Unsupported SVG path.'); } $this->numbers($value);
            } elseif ($svg && $name === 'transform') {
                if (!preg_match('/^(?:(?:translate|scale|rotate|matrix)\([-+0-9.,\s]+\)\s*)+$/', $value)) { throw new InvalidArgumentException('Unsupported SVG transform.'); } $this->numbers($value);
            } elseif ($svg && in_array($name, ['x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','dx','dy','width','height','viewBox','stroke-width','stroke-dasharray','points','font-size','refX','refY','markerWidth','markerHeight','offset','stop-opacity'], true)) {
                if (!preg_match('/^[-+0-9eE.,%\s]+$/', $value)) { throw new InvalidArgumentException('Unsupported SVG coordinate.'); } $this->numbers($value);
                if ($name === 'viewBox') { $parts = preg_split('/[\s,]+/', trim($value)); if (count($parts) !== 4 || (float)$parts[2] <= 0 || (float)$parts[3] <= 0) { throw new InvalidArgumentException('Invalid SVG viewBox.'); } }
            } elseif ($svg && $name === 'font-family' && !preg_match('/^[a-zA-Z ,\'"-]+$/', $value)) { throw new InvalidArgumentException('SVG font must be a system family.'); }
            $attrs[$name] = $value;
        }
        if ($tag === 'a' && ($attrs['target'] ?? '') === '_blank') { $attrs['rel'] = implode(' ', array_unique(preg_split('/\s+/', trim(($attrs['rel'] ?? '') . ' noopener noreferrer')))); }
        $output = '<' . $tag;
        if ($tag === 'svg') { $output .= ' xmlns="' . self::SVG_NS . '"'; }
        foreach ($attrs as $name => $value) { $output .= ' ' . $name . '="' . self::escape($value) . '"'; }
        $output .= in_array($tag, ['br','hr','col'], true) ? '/>' : '>'; 
        foreach ($node->childNodes as $child) {
            if ($child instanceof DOMElement) { $output .= $this->element($child, $depth + 1, $svg); }
            elseif ($child->nodeType === XML_TEXT_NODE) { $output .= self::escape($child->textContent); }
            else { throw new InvalidArgumentException('Comments, CDATA, processing instructions and directives are unsupported.'); }
        }
        return in_array($tag, ['br','hr','col'], true) ? $output : $output . '</' . $tag . '>';
    }
    private function numbers(string $value): void {
        preg_match_all('/[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?/', $value, $m);
        if (count($m[0]) > 2000) { throw new InvalidArgumentException('Too many SVG coordinates.'); }
        foreach ($m[0] as $number) { if (!is_finite((float)$number) || abs((float)$number) > 100000) { throw new InvalidArgumentException('SVG coordinate outside limits.'); } }
    }
}

final class SGNews_Publisher {
    public const VERSION = 1;
    public const POLICY = 'sgnews-restricted-xml-css-1';
    public const OWNER = 5;
    public const NEWS = 258;
    private const CAP = 'sgnews_publish';
    private const NS = 'sgnews-publisher/v1';
    private const LEASE = '_sgnews_publisher_lease_v1';
    private const BARRIER = '_sgnews_publisher_mutation_v1';
    private const SETUP_PAGE = 'sggroup-news-publisher';
    private const SETUP_ACTION = 'sgnews_grant_publisher';
    private static bool $internal = false;
    private static int $seoReadID = 0;
    public static function boot(): void {
        add_action('rest_api_init', [self::class, 'routes']);
        add_action('admin_menu', [self::class, 'setupMenu']);
        add_action('admin_post_' . self::SETUP_ACTION, [self::class, 'setupGrant']);
        add_filter('the_content', [self::class, 'render'], PHP_INT_MAX);
        add_filter('map_meta_cap', [self::class, 'protect'], 20, 4);
        add_filter('aioseo_schema_output', [self::class, 'freeSchema'], 20);
    }
    public static function setupMenu(): void {
        if (current_user_can('manage_options')) {
            add_options_page('SGGroup News Publisher', 'SGGroup News Publisher', 'manage_options', self::SETUP_PAGE, [self::class, 'setupPage']);
        }
    }
    private static function setupChecks(): array {
        $owner = get_userdata(self::OWNER);
        $site = rtrim(home_url('/'), '/');
        $category = get_term(self::NEWS, 'category');
        $taxonomy = taxonomy_exists('sg-group-language-controller');
        $ja = $taxonomy ? get_term(261, 'sg-group-language-controller') : false;
        $en = $taxonomy ? get_term(262, 'sg-group-language-controller') : false;
        return [
            'site'=>$site === 'https://sggroup.jp',
            'owner'=>$owner instanceof WP_User && (int)$owner->ID === self::OWNER && array_values($owner->roles) === ['author']
                && user_can($owner, 'edit_posts') && user_can($owner, 'publish_posts') && !user_can($owner, 'manage_options'),
            'category'=>$category && !is_wp_error($category) && (int)$category->term_id === self::NEWS
                && $category->taxonomy === 'category' && $category->slug === 'news',
            'languages'=>$ja && !is_wp_error($ja) && (int)$ja->term_id === 261 && $ja->taxonomy === 'sg-group-language-controller' && $ja->slug === 'ja'
                && $en && !is_wp_error($en) && (int)$en->term_id === 262 && $en->taxonomy === 'sg-group-language-controller' && $en->slug === 'en',
            'dom'=>class_exists('DOMDocument'), 'seo'=>self::seoAvailable(),
            'granted'=>$owner instanceof WP_User && user_can($owner, self::CAP)
        ];
    }
    public static function setupPage(): void {
        if (!current_user_can('manage_options')) {
            wp_die(esc_html('この設定はサイト管理者のみ操作できます。'), '', ['response'=>403]);
        }
        $checks = self::setupChecks();
        $ready = !in_array(false, array_intersect_key($checks, array_flip(['site','owner','category','languages','dom','seo'])), true);
        echo '<div class="wrap"><h1>' . esc_html('SGGroup News Publisher') . '</h1>';
        echo '<p>' . esc_html('投稿者ID 5に、ニュース公開専用の権限 sgnews_publish のみを付与します。投稿者の役割は変更しません。公開前の検証は別途必要です。') . '</p>';
        $notice = isset($_GET['sgnews_setup']) && is_string($_GET['sgnews_setup']) ? $_GET['sgnews_setup'] : '';
        if ($notice === 'granted') {
            echo '<div class="notice notice-success"><p>' . esc_html('設定を保存しました。実行側で投稿者本人と公開条件を再確認してください。') . '</p></div>';
        }
        $labels = ['site'=>'対象サイト: https://sggroup.jp', 'owner'=>'固定投稿者: ID 5 / 投稿者のみ',
            'category'=>'Newsカテゴリー: 258 / news', 'languages'=>'言語: 日本語 261 / ja、英語 262 / en',
            'dom'=>'PHP DOM拡張', 'seo'=>'AIOSEOのSEO読取・更新機能', 'granted'=>'専用権限 sgnews_publish'];
        echo '<table class="widefat striped"><tbody>';
        foreach ($labels as $key=>$label) {
            echo '<tr><th scope="row">' . esc_html($label) . '</th><td>' . esc_html($checks[$key] ? '確認済み' : '要確認') . '</td></tr>';
        }
        echo '</tbody></table>';
        if (!$ready) {
            echo '<p>' . esc_html('要確認の項目を整えてから、この画面を再読込してください。条件が揃うまで権限は付与できません。') . '</p>';
        } elseif (!$checks['granted']) {
            echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
            echo '<input type="hidden" name="action" value="' . esc_attr(self::SETUP_ACTION) . '" />';
            wp_nonce_field(self::SETUP_ACTION, '_wpnonce', false);
            echo '<p><button type="submit" class="button button-primary">' . esc_html('投稿者ID 5に専用権限のみを付与') . '</button></p></form>';
        }
        echo '</div>';
    }
    public static function setupGrant(): void {
        if (!current_user_can('manage_options')) {
            wp_die(esc_html('この設定はサイト管理者のみ操作できます。'), '', ['response'=>403]);
        }
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            wp_die(esc_html('設定画面から送信してください。'), '', ['response'=>405]);
        }
        if (array_diff(array_keys($_POST), ['action','_wpnonce']) || ($_POST['action'] ?? null) !== self::SETUP_ACTION
                || !isset($_POST['_wpnonce']) || !is_string($_POST['_wpnonce']) || $_POST['_wpnonce'] === ''
                || ($_REQUEST['_wpnonce'] ?? null) !== $_POST['_wpnonce']) {
            wp_die(esc_html('設定画面から送信してください。'), '', ['response'=>400]);
        }
        check_admin_referer(self::SETUP_ACTION, '_wpnonce');
        $checks = self::setupChecks();
        if (in_array(false, array_intersect_key($checks, array_flip(['site','owner','category','languages','dom','seo'])), true)) {
            wp_die(esc_html('設定条件を確認し、この画面を再読込してください。'), '', ['response'=>409]);
        }
        if (!$checks['granted']) {
            // The owner and capability are constants; submitted identities and
            // roles are never accepted, and no activation hook grants access.
            get_userdata(self::OWNER)->add_cap(self::CAP, true);
        }
        wp_safe_redirect(admin_url('options-general.php?page=' . self::SETUP_PAGE . '&sgnews_setup=granted'));
        exit;
    }
    public static function routes(): void {
        foreach ([
            '/status'=>['GET','status'], '/canonicalize'=>['POST','canonicalize'],
            '/lease'=>['POST','acquire'], '/lease/renew'=>['POST','renew'], '/lease/release'=>['POST','release'],
            '/articles/draft'=>['POST','draft'], '/articles/publish'=>['POST','publish'],
            '/articles/(?P<id>[1-9][0-9]*)'=>['GET','article'],
            '/articles/(?P<id>[1-9][0-9]*)/preview'=>['GET','preview'],
            '/operations/(?P<key>[a-zA-Z0-9:_-]{16,128})'=>['GET','operation']
        ] as $path => [$method, $handler]) {
            register_rest_route(self::NS, $path, [
                'methods'=>$method, 'callback'=>[self::class, $handler],
                'permission_callback'=>$handler === 'status' ? [self::class,'canInspect'] : [self::class,'permission']
            ]);
        }
    }
    public static function canInspect(): bool { return is_user_logged_in() && (get_current_user_id() === self::OWNER || current_user_can('manage_options')); }
    public static function permission() {
        if (get_current_user_id() !== self::OWNER || !current_user_can(self::CAP) || !current_user_can('publish_posts') || !current_user_can('edit_posts')) {
            return new WP_Error('sgnews_permission', 'The configured owner requires an explicit sgnews_publish grant; this plugin never grants it automatically.', ['status'=>403]);
        }
        return true;
    }
    public static function status(): array {
        $lease = self::fresh(self::LEASE); $barrier = self::fresh(self::BARRIER);
        $terms = taxonomy_exists('sg-group-language-controller') && ($ja = get_term(261, 'sg-group-language-controller')) && !is_wp_error($ja) && $ja->slug === 'ja'
            && ($en = get_term(262, 'sg-group-language-controller')) && !is_wp_error($en) && $en->slug === 'en';
        $category = get_term(self::NEWS, 'category');
        $dependencies = class_exists('DOMDocument') && $terms && $category && !is_wp_error($category) && $category->slug === 'news';
        return [
            'api_version'=>self::VERSION, 'namespace'=>self::NS, 'policy_version'=>self::POLICY,
            'implementation_sha256'=>hash_file('sha256', __FILE__), 'code_implementation_sha256'=>hash_file('sha256', __FILE__), 'owner_user_id'=>self::OWNER,
            'user_id'=>get_current_user_id(), 'dedicated_capability'=>self::CAP,
            'has_dedicated_capability'=>current_user_can(self::CAP),
            'can_publish'=>self::permission() === true && $dependencies && self::seoAvailable(),
            'dependencies_ready'=>(bool)$dependencies, 'aioseo_abilities_available'=>self::seoAvailable(),
            'news_category_id'=>self::NEWS, 'language_terms'=>['ja'=>261,'en'=>262],
            'features'=>['idempotent_creates'=>true,'staged_updates'=>true,'fenced_leases'=>true,'exact_source_readback'=>true,'bilingual_publication_gate'=>true,'canonicalization'=>true,'authenticated_theme_preview'=>true],
            'source_storage'=>'protected _sgnews_source_html; custom article.content_raw is authoritative; core post_content is a readable KSES fallback',
            'mutation_barrier'=>is_array($barrier) ? ['active'=>true,'since'=>$barrier['since'] ?? null,'fence'=>$barrier['fence'] ?? null] : ['active'=>false],
            'lease'=>is_array($lease) ? ['fence'=>$lease['fence'],'expires_at'=>$lease['expires_at'],'active'=>$lease['expires_at'] > time()] : null
        ];
    }
    private static function seoAvailable(): bool {
        return function_exists('wp_get_ability') && wp_get_ability('aioseo-posts/seo-data-get') && wp_get_ability('aioseo-posts/seo-data-update');
    }
    private static function body($request, array $allowed, array $required = []): array {
        $body = $request->get_json_params();
        if (!is_array($body) || array_is_list($body) || array_diff(array_keys($body), $allowed) || array_diff($required, array_keys($body))) { throw new InvalidArgumentException('Missing, unknown or non-object request fields.'); }
        return $body;
    }
    private static function fail(Throwable $e) { return new WP_Error('sgnews_rejected', $e->getMessage(), ['status'=>$e instanceof InvalidArgumentException ? 422 : 409]); }
    public static function canonicalize($request) {
        try {
            $b = self::body($request, ['html','language','shared_slug'], ['html','language','shared_slug']);
            self::slug($b['shared_slug']); self::language($b['language']);
            $html = SGNews_Fragment::canonical($b['html'], self::root($b['shared_slug']), $b['language']);
            return ['content_raw'=>$html,'source_sha256'=>hash('sha256',$html),'policy_version'=>self::POLICY,'root_id'=>self::root($b['shared_slug'])];
        } catch (Throwable $e) { return self::fail($e); }
    }
    private static function root(string $slug): string { return 'sg-news-' . substr(hash('sha256', $slug), 0, 12); }
    private static function language($language): void { if (!is_string($language) || !in_array($language,['ja','en'],true)) { throw new InvalidArgumentException('Language must be ja or en.'); } }
    private static function slug($slug): void {
        if (!is_string($slug) || strlen($slug) > 200 || !preg_match('/^[a-z0-9]+(?:-[a-z0-9]+)*-(\d{4})-(\d{2})-(\d{2})$/', $slug, $m) || !checkdate((int)$m[2],(int)$m[3],(int)$m[1])) { throw new InvalidArgumentException('Shared slug must be descriptive ASCII and end in a valid YYYY-MM-DD.'); }
    }
    private static function key($key): string {
        if (!is_string($key) || !preg_match('/^[a-zA-Z0-9:_-]{16,128}$/',$key)) { throw new InvalidArgumentException('Invalid idempotency key.'); } return $key;
    }
    private static function digest($digest): void { if (!is_string($digest) || !preg_match('/^[a-f0-9]{64}$/',$digest)) { throw new InvalidArgumentException('Expected a SHA256 digest.'); } }
    private static function owner($owner): void { if (!is_string($owner) || !preg_match('/^[a-zA-Z0-9_-]{16,120}$/',$owner)) { throw new InvalidArgumentException('Lease owner must be a unique worker nonce.'); } }
    private static function fresh(string $name) {
        wp_cache_delete($name,'options'); wp_cache_delete('notoptions','options');
        return get_option($name, null);
    }
    /** WP update_option has no CAS. This prepared, single-row internal CAS is the only direct SQL write. */
    private static function cas(string $name, $old, $new): bool {
        global $wpdb;
        $changed = $wpdb->query($wpdb->prepare("UPDATE {$wpdb->options} SET option_value = %s WHERE option_name = %s AND BINARY option_value = %s", maybe_serialize($new), $name, maybe_serialize($old)));
        wp_cache_delete($name,'options'); wp_cache_delete('notoptions','options');
        return $changed === 1;
    }
    public static function acquire($request) {
        try {
            $b = self::body($request,['scope','owner','ttl_seconds'],['scope','owner','ttl_seconds']);
            if ($b['scope'] !== 'news') { throw new InvalidArgumentException('Only the news scope exists.'); }
            self::owner($b['owner']); $ttl = self::ttl($b['ttl_seconds']);
            for ($i=0;$i<3;++$i) {
                $old = self::fresh(self::LEASE);
                if (is_array($old) && $old['expires_at'] > time()) {
                    if ($old['owner'] === $b['owner']) { return $old; }
                    throw new RuntimeException('Another worker holds the live lease.');
                }
                $new = ['owner'=>$b['owner'],'fence'=>(int)($old['fence'] ?? 0)+1,'expires_at'=>time()+$ttl,'user_id'=>self::OWNER];
                if ($old === null ? add_option(self::LEASE,$new,'',false) : self::cas(self::LEASE,$old,$new)) { return $new; }
            }
            throw new RuntimeException('Lease acquisition raced; retry.');
        } catch (Throwable $e) { return self::fail($e); }
    }
    private static function ttl($ttl): int { if (!is_int($ttl) || $ttl < 30 || $ttl > 900) { throw new InvalidArgumentException('Lease TTL must be an integer from 30 to 900 seconds.'); } return $ttl; }
    private static function lease(string $owner, int $fence): array {
        self::owner($owner); $lease = self::fresh(self::LEASE);
        if (!is_array($lease) || $lease['owner'] !== $owner || $lease['fence'] !== $fence || $lease['expires_at'] <= time()) { throw new RuntimeException('Lease is expired, replaced, or belongs to another worker.'); }
        return $lease;
    }
    public static function renew($request) {
        try {
            $b=self::body($request,['scope','owner','fence','ttl_seconds'],['scope','owner','fence','ttl_seconds']);
            if ($b['scope'] !== 'news' || !is_int($b['fence'])) { throw new InvalidArgumentException('Invalid lease scope/fence.'); }
            $old=self::lease($b['owner'],$b['fence']); $new=$old; $new['expires_at']=time()+self::ttl($b['ttl_seconds']);
            if ($new === $old || self::cas(self::LEASE,$old,$new)) { return $new; }
            throw new RuntimeException('Lease changed while renewing.');
        } catch (Throwable $e) { return self::fail($e); }
    }
    public static function release($request) {
        try {
            $b=self::body($request,['scope','owner','fence'],['scope','owner','fence']);
            if ($b['scope'] !== 'news' || !is_int($b['fence'])) { throw new InvalidArgumentException('Invalid lease scope/fence.'); }
            $old=self::lease($b['owner'],$b['fence']); $new=$old; $new['expires_at']=0;
            if (!self::cas(self::LEASE,$old,$new)) { throw new RuntimeException('Lease changed while releasing.'); }
            return ['released'=>true,'fence'=>$old['fence']];
        } catch (Throwable $e) { return self::fail($e); }
    }
    /** A non-expiring barrier prevents resumed old workers overlapping a successor after lease expiry. */
    private static function mutate(array $b, callable $callback) {
        if (!is_string($b['lease_owner'] ?? null) || !is_int($b['lease_fence'] ?? null)) { throw new InvalidArgumentException('A lease owner/fence is required.'); }
        self::lease($b['lease_owner'],$b['lease_fence']);
        $barrier=['nonce'=>bin2hex(random_bytes(32)),'since'=>time(),'fence'=>$b['lease_fence']];
        if (!add_option(self::BARRIER,$barrier,'',false)) { throw new RuntimeException('A mutation is active or a crashed barrier requires administrator inspection.'); }
        $prior=self::$internal;
        try { self::lease($b['lease_owner'],$b['lease_fence']); self::$internal=true; return $callback(); }
        finally {
            self::$internal=$prior;
            // No other worker can replace this non-expiring barrier through any public route.
            if (self::fresh(self::BARRIER) === $barrier) { delete_option(self::BARRIER); }
        }
    }
    private static function operationName(string $key): string { return '_sgnews_op_' . hash('sha256',$key); }
    private static function canonicalName(string $slug,string $lang): string { return '_sgnews_canonical_' . hash('sha256',$slug . ':' . $lang); }
    private static function metadata($metadata): array {
        if (!is_array($metadata) || array_diff(array_keys($metadata),['title','aioseo_title','aioseo_description','slug']) || count($metadata)!==4) { throw new InvalidArgumentException('Exactly the four established metadata fields are required.'); }
        foreach (['title'=>300,'aioseo_title'=>300,'aioseo_description'=>2000,'slug'=>200] as $field=>$max) {
            if (!is_string($metadata[$field] ?? null) || trim($metadata[$field])==='' || strlen($metadata[$field])>$max || preg_match('/[<>\x00-\x1f]/u',$metadata[$field])) { throw new InvalidArgumentException('Invalid metadata field: '.$field); }
        }
        self::slug($metadata['slug']); return $metadata;
    }
    private static function managed(int $id, ?string $kind=null): object {
        $post=get_post($id);
        if (!$post || $post->post_type!=='post' || (int)$post->post_author!==self::OWNER || get_post_meta($id,'_sgnews_manager',true)!==self::POLICY || ($kind && get_post_meta($id,'_sgnews_kind',true)!==$kind)) { throw new RuntimeException('Post is not a managed post of the configured owner.'); }
        $lang=get_post_meta($id,'_sgnews_language',true); self::language($lang);
        if (array_map('intval',wp_get_post_categories($id)) !== [self::NEWS] || array_map('intval',wp_get_object_terms($id,'sg-group-language-controller',['fields'=>'ids'])) !== [$lang==='ja'?261:262]) { throw new RuntimeException('Managed post category/language changed.'); }
        $source=get_post_meta($id,'_sgnews_source_html',true); $hash=get_post_meta($id,'_sgnews_source_sha256',true);
        if (!is_string($source) || !is_string($hash) || !hash_equals($hash,hash('sha256',$source))) { throw new RuntimeException('Managed source integrity mismatch.'); }
        $metadata=get_post_meta($id,'_sgnews_metadata',true); self::metadata($metadata);
        if ($post->post_title!==$metadata['title'] || get_post_meta($id,'_sg_sppp_mode',true)!=='none' || get_post_meta($id,'_sg_sppp_sale',true)!=='none') { throw new RuntimeException('Managed title or free-access state changed.'); }
        if (SGNews_Fragment::canonical($source,self::root($metadata['slug']),$lang)!==$source) { throw new RuntimeException('Stored source is not canonical under the current policy.'); }
        return $post;
    }
    private static function normalized(int $id): array {
        $post=self::managed($id); $meta=get_post_meta($id,'_sgnews_metadata',true);
        if (!self::seoAvailable()) { throw new RuntimeException('SEO read-back ability is unavailable.'); }
        $seo=self::readSEO($id);
        if (is_wp_error($seo) || !is_array($seo) || ($seo['title']??null)!==$meta['aioseo_title'] || ($seo['description']??null)!==$meta['aioseo_description']) { throw new RuntimeException('Actual AIOSEO metadata does not match the saved source metadata.'); }
        return ['id'=>$id,'status'=>$post->post_status,'author'=>(int)$post->post_author,
            'content_raw'=>get_post_meta($id,'_sgnews_source_html',true),'source_sha256'=>get_post_meta($id,'_sgnews_source_sha256',true),
            'common_slug'=>$meta['slug'],'title'=>$meta['title'],'seo_title'=>$seo['title'],'meta_description'=>$seo['description'],'seo_verified'=>true,
            'language'=>get_post_meta($id,'_sgnews_language',true),'categories'=>[self::NEWS],'is_accessible_for_free'=>true,
            'public_url'=>$post->post_status==='publish'?get_permalink($id):null,
            'kind'=>get_post_meta($id,'_sgnews_kind',true),'idempotency_key'=>get_post_meta($id,'_sgnews_operation_key',true)];
    }
    public static function article($request) { try { return self::normalized((int)$request['id']); } catch(Throwable $e) { return self::fail($e); } }
    /** Authenticated, read-only native-theme preview. No bearer URLs, impersonation or edit capability grants. */
    public static function preview($request) {
        $globals=[]; $prior=self::$internal; $bufferLevel=ob_get_level();
        try {
            $id=(int)$request['id']; $post=self::managed($id,'stage'); $saved=self::normalized($id);
            if ($post->post_status!=='draft' || !class_exists('WP_Query') || !function_exists('get_single_template')) { throw new RuntimeException('A native theme preview of this stage is unavailable.'); }
            foreach(['post','wp_query','wp_the_query','id','authordata','currentday','currentmonth','page','pages','multipage','more','numpages'] as $name) { $globals[$name]=['exists'=>array_key_exists($name,$GLOBALS),'value'=>$GLOBALS[$name]??null]; }
            $query=new WP_Query();
            $query->posts=[$post]; $query->post=$post; $query->post_count=1; $query->current_post=-1;
            $query->queried_object=$post; $query->queried_object_id=$id;
            $query->is_single=true; $query->is_singular=true; $query->is_preview=true; $query->is_home=false; $query->is_404=false;
            $query->query=['p'=>$id,'post_type'=>'post','post_status'=>'draft'];
            $query->query_vars=$query->query;
            $GLOBALS['post']=$post; $GLOBALS['wp_query']=$query; $GLOBALS['wp_the_query']=$query;
            setup_postdata($post);
            $template=get_single_template();
            if (!$template || !is_readable($template)) { throw new RuntimeException('The active theme single template could not be loaded.'); }
            nocache_headers();
            ob_start(); include $template;
            while (ob_get_level()>$bufferLevel+1) { ob_end_flush(); }
            $html=ob_get_clean();
            if (!is_string($html) || !str_contains($html,$saved['content_raw'])) { throw new RuntimeException('The saved source did not render intact in the active theme preview.'); }
            return ['html'=>$html,'stage_post_id'=>$id,'source_sha256'=>$saved['source_sha256'],'preview_context'=>'authenticated_native_theme_template','base_url'=>home_url('/'),'seo_verified'=>true,
                'user_id'=>get_current_user_id(),'owner_user_id'=>self::OWNER,'namespace'=>self::NS,'policy_version'=>self::POLICY,'implementation_sha256'=>hash_file('sha256',__FILE__)];
        } catch(Throwable $e) { return self::fail($e); }
        finally {
            while (ob_get_level()>$bufferLevel) { ob_end_clean(); }
            self::$internal=$prior;
            foreach($globals as $name=>$snapshot) { if($snapshot['exists']) { $GLOBALS[$name]=$snapshot['value']; } else { unset($GLOBALS[$name]); } }
        }
    }
    private static function recoverPost(string $key,string $kind): ?int {
        $ids=get_posts(['post_type'=>'post','author'=>self::OWNER,'post_status'=>['draft','publish','private','pending','future','trash'],'fields'=>'ids','numberposts'=>2,
            'meta_query'=>[['key'=>'_sgnews_operation_key','value'=>$key],['key'=>'_sgnews_kind','value'=>$kind]]]);
        if (count($ids)>1) { throw new RuntimeException('Duplicate stage mapping requires administrator inspection.'); }
        return $ids?(int)$ids[0]:null;
    }
    public static function operation($request) {
        try {
            $key=self::key($request['key']); $op=self::fresh(self::operationName($key));
            if (is_array($op)) {
                try { $result=self::normalized((int)$op['post_id']); }
                catch(Throwable $e) { if($op['state']!=='pending') { throw $e; } $result=null; }
                return ['key'=>$key,'found'=>true,'state'=>$op['state'],'kind'=>$op['kind'],'fingerprint'=>$op['fingerprint'],'result'=>$result,'definitive_absence'=>false,'safe_to_resume'=>$op['state']==='pending' && !is_array(self::fresh(self::BARRIER))];
            }
            $id=self::recoverPost($key,'stage');
            if ($id) {
                self::managed($id,'stage'); try { $result=self::normalized($id); } catch(Throwable $e) { $result=null; }
                return ['key'=>$key,'found'=>true,'state'=>'pending','kind'=>'draft','result'=>$result,'definitive_absence'=>false,'safe_to_resume'=>!is_array(self::fresh(self::BARRIER))];
            }
            $canonical=self::recoverPost('canonical:'.$key,'canonical');
            if ($canonical) {
                $busy=is_array(self::fresh(self::BARRIER));
                self::managed($canonical,'canonical'); try { $result=self::normalized($canonical); } catch(Throwable $e) { $result=null; }
                return ['key'=>$key,'found'=>true,'state'=>'pending','kind'=>'publish','result'=>$result,'definitive_absence'=>false,'safe_to_resume'=>!$busy];
            }
            $busy=is_array(self::fresh(self::BARRIER));
            return ['key'=>$key,'found'=>false,'state'=>$busy?'pending':'not_found','result'=>null,'definitive_absence'=>!$busy];
        } catch(Throwable $e) { return self::fail($e); }
    }
    private static function writeSource(?int $id,string $kind,array $b,string $source,array $metadata): int {
        // KSES remains active: only the readable fallback goes through core post_content.
        $fallback=preg_replace('/\A<style>.*?<\/style>/s','',$source);
        $fields=['post_type'=>'post','post_author'=>self::OWNER,'post_title'=>$metadata['title'],'post_content'=>wp_kses_post($fallback),'post_status'=>'draft',
            'post_name'=>$kind==='stage'?'sgnews-stage-'.substr(hash('sha256',$b['idempotency_key']),0,24):$metadata['slug'],
            'meta_input'=>['_sgnews_manager'=>self::POLICY,'_sgnews_kind'=>$kind,'_sgnews_source_html'=>$source,
                '_sgnews_source_sha256'=>hash('sha256',$source),'_sgnews_metadata'=>$metadata,'_sgnews_language'=>$b['language'],
                '_sgnews_operation_key'=>$b['idempotency_key'],'_sgnews_target_id'=>$b['existing_id']??0,
                '_sgnews_target_source_sha256'=>$kind==='stage' && !empty($b['existing_id']) ? get_post_meta($b['existing_id'],'_sgnews_source_sha256',true) : '',
                '_sg_sppp_mode'=>'none','_sg_sppp_sale'=>'none','_sg_sppp_lang'=>$b['language']]];
        if ($id) { $fields['ID']=$id; $existing=get_post($id); $fields['post_name']=$existing->post_name; if ($existing->post_status==='publish') { $fields['post_status']='publish'; } }
        $saved=wp_insert_post(wp_slash($fields),true);
        if (is_wp_error($saved)) { throw new RuntimeException('WordPress save failed: '.$saved->get_error_message()); }
        wp_set_post_categories($saved,[self::NEWS],false);
        $assigned=wp_set_object_terms($saved,[$b['language']==='ja'?261:262],'sg-group-language-controller',false);
        if (is_wp_error($assigned)) { throw new RuntimeException('Language assignment failed.'); }
        self::managed((int)$saved); return (int)$saved;
    }
    public static function draft($request) {
        try {
            $b=self::body($request,['language','html','metadata','categories','is_accessible_for_free','status','existing_id','idempotency_key','lease_owner','lease_fence'],
                ['language','html','metadata','categories','is_accessible_for_free','status','existing_id','idempotency_key','lease_owner','lease_fence']);
            self::language($b['language']); self::key($b['idempotency_key']); $metadata=self::metadata($b['metadata']);
            if ($b['categories']!==[self::NEWS] || $b['is_accessible_for_free']!==true || $b['status']!=='draft' || (!is_int($b['existing_id']) && $b['existing_id']!==null)) { throw new InvalidArgumentException('Only fixed-category free-access draft staging is allowed.'); }
            if (!is_string($b['html'])) { throw new InvalidArgumentException('HTML must be a string.'); }
            $source=SGNews_Fragment::canonical($b['html'],self::root($metadata['slug']),$b['language']);
            $fingerprint=hash('sha256',wp_json_encode([$source,$metadata,$b['language'],$b['existing_id']]));
            return self::mutate($b, function() use($b,$metadata,$source,$fingerprint) {
                $name=self::operationName($b['idempotency_key']); $op=self::fresh($name); $recovered=self::recoverPost($b['idempotency_key'],'stage');
                if (is_array($op)) {
                    if ($op['kind']!=='draft' || !hash_equals($op['fingerprint'],$fingerprint)) { throw new RuntimeException('Idempotency key was reused for a different operation/source.'); }
                    if($op['state']==='complete') { return self::normalized((int)$op['post_id']); }
                    $recovered=(int)$op['post_id'];
                }
                $canonical=self::fresh(self::canonicalName($metadata['slug'],$b['language']));
                $actualTarget=is_array($canonical)?(int)$canonical['post_id']:null;
                if ($actualTarget!==$b['existing_id']) { throw new RuntimeException('Canonical target changed; reconcile before saving a revision.'); }
                if ($actualTarget) { self::managed($actualTarget,'canonical'); }
                if ($recovered) {
                    self::managed($recovered,'stage');
                    if (get_post_meta($recovered,'_sgnews_source_sha256',true)!==hash('sha256',$source) || get_post_meta($recovered,'_sgnews_metadata',true)!==$metadata || (int)get_post_meta($recovered,'_sgnews_target_id',true)!==(int)($b['existing_id']??0)) { throw new RuntimeException('Recovered stage differs from retry payload.'); }
                    $id=$recovered;
                } else { self::lease($b['lease_owner'],$b['lease_fence']); $id=self::writeSource(null,'stage',$b,$source,$metadata); }
                update_option($name,['kind'=>'draft','state'=>'pending','fingerprint'=>$fingerprint,'post_id'=>$id],false);
                self::lease($b['lease_owner'],$b['lease_fence']);
                self::applySEO($id,$metadata,true);
                update_option($name,['kind'=>'draft','state'=>'complete','fingerprint'=>$fingerprint,'post_id'=>$id],false);
                return self::normalized($id);
            });
        } catch(Throwable $e) { return self::fail($e); }
    }
    private static function readSEO(int $id) {
        $prior=self::$seoReadID; self::$seoReadID=$id;
        try { return wp_get_ability('aioseo-posts/seo-data-get')->execute(['postId'=>$id]); }
        finally { self::$seoReadID=$prior; }
    }
    private static function applySEO(int $id,array $metadata,bool $new): void {
        if (!self::seoAvailable()) { throw new RuntimeException('Registered AIOSEO read/update abilities are required.'); }
        $read=self::readSEO($id);
        if (is_wp_error($read)) {
            $data=$read->get_error_data(); $status=is_array($data)?($data['status']??null):null;
            if (!$new || $status!==404) { throw new RuntimeException('AIOSEO SEO row is unavailable; no guessed bootstrap is attempted.'); }
            $request=new WP_REST_Request('POST','/aioseo/v1/post'); $request->set_body_params(['id'=>$id]);
            $bootstrap=rest_do_request($request);
            if ($bootstrap->is_error() || $bootstrap->get_status()>=300) { throw new RuntimeException('AIOSEO supported new-row bootstrap failed.'); }
        }
        $saved=wp_get_ability('aioseo-posts/seo-data-update')->execute(['postId'=>$id,'title'=>$metadata['aioseo_title'],'description'=>$metadata['aioseo_description']]);
        if (is_wp_error($saved)) { throw new RuntimeException('AIOSEO save failed: '.$saved->get_error_message()); }
        $verify=self::readSEO($id);
        if (is_wp_error($verify) || !is_array($verify) || ($verify['title']??null)!==$metadata['aioseo_title'] || ($verify['description']??null)!==$metadata['aioseo_description']) { throw new RuntimeException('AIOSEO read-back does not match the requested metadata.'); }
    }
    public static function publish($request) {
        try {
            $b=self::body($request,['stage_post_id','target_post_id','idempotency_key','lease_owner','lease_fence','expected_source_sha256','audit_sha256','peer_stage_post_id','peer_source_sha256'],
                ['stage_post_id','target_post_id','idempotency_key','lease_owner','lease_fence','expected_source_sha256','audit_sha256','peer_stage_post_id','peer_source_sha256']);
            foreach(['stage_post_id','peer_stage_post_id'] as $field) { if (!is_int($b[$field]) || $b[$field]<1) { throw new InvalidArgumentException('Invalid staging post ID.'); } }
            if ($b['target_post_id']!==null && (!is_int($b['target_post_id']) || $b['target_post_id']<1)) { throw new InvalidArgumentException('Invalid target ID.'); }
            self::key($b['idempotency_key']); foreach(['expected_source_sha256','peer_source_sha256','audit_sha256'] as $field) { self::digest($b[$field]); }
            return self::mutate($b,function() use($b) {
                $stage=self::managed($b['stage_post_id'],'stage'); $peer=self::managed($b['peer_stage_post_id'],'stage');
                $s=self::normalized($b['stage_post_id']); $p=self::normalized($b['peer_stage_post_id']);
                if ($s['language']===$p['language'] || $s['common_slug']!==$p['common_slug'] || !hash_equals($s['source_sha256'],$b['expected_source_sha256']) || !hash_equals($p['source_sha256'],$b['peer_source_sha256']) || $stage->post_status!=='draft' || $peer->post_status!=='draft') { throw new RuntimeException('Both intact audited language stages are required.'); }
                $metadata=get_post_meta($stage->ID,'_sgnews_metadata',true);
                $name=self::operationName($b['idempotency_key']); $op=self::fresh($name);
                $fingerprint=hash('sha256',wp_json_encode([$stage->ID,$peer->ID,$b['target_post_id'],$b['expected_source_sha256'],$b['peer_source_sha256'],$b['audit_sha256']]));
                if (is_array($op)) {
                    if ($op['kind']!=='publish' || !hash_equals($op['fingerprint'],$fingerprint)) { throw new RuntimeException('Publication key reused with different audited inputs.'); }
                    if ($op['state']==='complete') { return self::normalized((int)$op['post_id']); }
                }
                $canonicalName=self::canonicalName($s['common_slug'],$s['language']); $canonical=self::fresh($canonicalName);
                $id=is_array($canonical)?(int)$canonical['post_id']:null;
                if (get_post_meta($stage->ID,'_sgnews_publish_complete',true)==='yes') {
                    $done=(int)get_post_meta($stage->ID,'_sgnews_published_id',true); $out=self::normalized($done);
                    if ($out['source_sha256']!==$s['source_sha256']) { throw new RuntimeException('A newer canonical revision already exists; stale revision cannot replace it.'); }
                    add_option($name,['kind'=>'publish','state'=>'complete','fingerprint'=>$fingerprint,'post_id'=>$done],'',false); return $out;
                }
                $pending=is_array($op)?(int)$op['post_id']:null;
                $recovered=self::recoverPost('canonical:'.$b['idempotency_key'],'canonical');
                if (!$id && $recovered) {
                    $id=$recovered; $pending=$recovered;
                    add_option($canonicalName,['post_id'=>$id],'',false);
                } elseif ($id && get_post_meta($id,'_sgnews_operation_key',true)==='canonical:'.$b['idempotency_key']) { $pending=$id; }
                if ($pending && $id!==$pending) { throw new RuntimeException('Pending canonical operation requires reconciliation.'); }
                if (!$pending && $id!==$b['target_post_id']) { throw new RuntimeException('Target changed since this revision was staged.'); }
                if ((int)get_post_meta($stage->ID,'_sgnews_target_id',true)!==(int)($b['target_post_id']??0)) { throw new RuntimeException('The stage was prepared for a different canonical target.'); }
                if ($id) {
                    self::managed($id,'canonical');
                    $baseline=get_post_meta($stage->ID,'_sgnews_target_source_sha256',true);
                    $current=get_post_meta($id,'_sgnews_source_sha256',true);
                    if ($current!==$baseline && !($pending && $current===$s['source_sha256'])) { throw new RuntimeException('Canonical revision changed after staging; stale revisions are rejected.'); }
                }
                $new=$id===null;
                $save=['idempotency_key'=>'canonical:'.$b['idempotency_key'],'language'=>$s['language'],'existing_id'=>$b['target_post_id']];
                self::lease($b['lease_owner'],$b['lease_fence']);
                if ($new) {
                    $id=self::writeSource(null,'canonical',$save,$s['content_raw'],$metadata);
                    add_option($canonicalName,['post_id'=>$id],'',false);
                }
                $pendingRecord=['kind'=>'publish','state'=>'pending','fingerprint'=>$fingerprint,'post_id'=>$id,'new'=>$new || (bool)($op['new']??false) || ($recovered && get_post($id)->post_status==='draft' && !$b['target_post_id'])];
                update_option($name,$pendingRecord,false);
                $oldMetadata=get_post_meta($id,'_sgnews_metadata',true);
                $oldSource=get_post_meta($id,'_sgnews_source_html',true);
                $oldPost=clone get_post($id);
                $oldOperation=get_post_meta($id,'_sgnews_operation_key',true);
                $oldAudit=get_post_meta($id,'_sgnews_audit_sha256',true);
                $rollback=function() use($id,$new,$oldMetadata,$oldSource,$oldPost,$oldOperation,$oldAudit,$s) {
                    if ($new) { wp_update_post(['ID'=>$id,'post_status'=>'draft']); return; }
                    self::writeSource($id,'canonical',['idempotency_key'=>$oldOperation,'language'=>$s['language'],'existing_id'=>null],$oldSource,$oldMetadata);
                    $restored=wp_update_post(['ID'=>$id,'post_name'=>$oldPost->post_name,'post_status'=>$oldPost->post_status,'post_title'=>$oldPost->post_title],true);
                    if (is_wp_error($restored) || get_post($id)->post_status!==$oldPost->post_status || get_post($id)->post_name!==$oldPost->post_name) { throw new RuntimeException('Rollback requires administrator inspection; the previous status/identifier could not be verified.'); }
                    update_post_meta($id,'_sgnews_audit_sha256',$oldAudit);
                    self::applySEO($id,$oldMetadata,false);
                    self::managed($id,'canonical');
                };

                try {
                    self::applySEO($id,$metadata,$pendingRecord['new']);
                    self::lease($b['lease_owner'],$b['lease_fence']);
                    $id=self::writeSource($id,'canonical',$save,$s['content_raw'],$metadata);
                    update_post_meta($id,'_sgnews_audit_sha256',$b['audit_sha256']);
                    self::lease($b['lease_owner'],$b['lease_fence']);
                    $published=wp_update_post(['ID'=>$id,'post_status'=>'publish'],true);
                    if (is_wp_error($published)) { throw new RuntimeException('WordPress publication failed.'); }
                    $out=self::normalized($id);
                    $expected=home_url('/'.$s['language'].'/article/news/'.$metadata['slug'].'/');
                    if ($out['status']!=='publish' || $out['public_url']!==$expected) {
                        throw new RuntimeException('Published URL/status failed verification.');
                    }
                }
                catch (Throwable $e) {
                    // The existing source remains authoritative if an existing post save fails.
                    $rollback();
                    throw $e;
                }
                update_post_meta($stage->ID,'_sgnews_publish_complete','yes'); update_post_meta($stage->ID,'_sgnews_published_id',$id);
                update_option($name,['kind'=>'publish','state'=>'complete','fingerprint'=>$fingerprint,'post_id'=>$id],false);
                return $out;
            });
        } catch(Throwable $e) { return self::fail($e); }
    }
    public static function protect(array $caps,string $cap,int $user_id,array $args): array {
        if ($cap==='edit_post' && self::$seoReadID && isset($args[0]) && (int)$args[0]===self::$seoReadID && $user_id===self::OWNER && current_user_can(self::CAP)) { return ['edit_posts']; }
        if (!self::$internal && in_array($cap,['edit_post','delete_post','edit_post_meta','delete_post_meta','add_post_meta'],true) && isset($args[0]) && get_post_meta((int)$args[0],'_sgnews_manager',true)===self::POLICY && !user_can($user_id,'manage_options')) { return ['do_not_allow']; }
        return $caps;
    }
    public static function render(string $content): string {
        $id=get_the_ID();
        if (!$id || get_post_meta($id,'_sgnews_manager',true)!==self::POLICY) { return $content; }
        try { $post=self::managed($id); return $post->post_status==='publish'||current_user_can(self::CAP)||current_user_can('manage_options')?get_post_meta($id,'_sgnews_source_html',true):$content; }
        catch(Throwable $e) { return $content; }
    }
    public static function freeSchema($graphs) {
        $id=get_the_ID();
        if (!$id || get_post_meta($id,'_sgnews_kind',true)!=='canonical' || get_post_meta($id,'_sgnews_manager',true)!==self::POLICY || !is_array($graphs)) { return $graphs; }
        $walk=function(array $data) use(&$walk): array {
            $types=(array)($data['@type']??[]);
            if (array_intersect($types,['Article','BlogPosting','NewsArticle'])) { $data['isAccessibleForFree']=true; }
            foreach($data as $key=>$value) { if(is_array($value)) { $data[$key]=$walk($value); } }
            return $data;
        };
        return $walk($graphs);
    }
}
if (function_exists('add_action')) { SGNews_Publisher::boot(); }
