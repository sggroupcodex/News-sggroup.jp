#!/usr/bin/env python3
"""Offline PHP fault regressions; no production WordPress or network access.

The maintained lifecycle double is loaded in memory. Only its two storage
functions receive one-shot fault hooks; the plugin and project mount stay
read-only. Requires Docker and the already-cached php:8.3-cli image with DOM.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


PROJECT = Path(__file__).resolve().parent.parent


def php_fault_probe() -> str:
    source = (PROJECT / "wordpress" / "test-lifecycle.php").read_text()
    source = source.replace("__DIR__", "'/app/wordpress'")
    footer = "echo json_encode(['tests'=>$tests"
    if source.count(footer) != 1:
        raise AssertionError("The maintained lifecycle footer changed; review the harness.")
    source = source[: source.index(footer)]
    term_function = "function wp_set_object_terms($id,$values,$tax,$append=false){"
    meta_function = (
        "function update_post_meta($id,$key,$value){"
        "$GLOBALS['meta'][$id][$key]=$value;return true;}"
    )
    if source.count(term_function) != 1 or source.count(meta_function) != 1:
        raise AssertionError("The maintained storage doubles changed; review the fault hooks.")
    source = source.replace(
        term_function,
        term_function
        + "if($GLOBALS['fault_terms_once']??false){"
        + "unset($GLOBALS['fault_terms_once']);"
        + "return new WP_Error('temporary','Injected one-time term assignment failure');}",
    )
    source = source.replace(
        meta_function,
        "function update_post_meta($id,$key,$value){"
        "$GLOBALS['meta'][$id][$key]=$value;"
        "if(($GLOBALS['fault_completion_once']??false)"
        "&&$key===($GLOBALS['fault_checkpoint_key']??'_sgnews_publish_complete')){"
        "unset($GLOBALS['fault_completion_once']);"
        "throw new RuntimeException('Injected hook exception after completion marker persisted');}"
        "return true;}",
    )
    ability_function = "function wp_get_ability($name){"
    rest_function = "function rest_do_request($request){"
    if source.count(ability_function) != 1 or source.count(rest_function) != 1:
        raise AssertionError("The maintained ability/REST doubles changed; review the disconnect hooks.")
    source = source.replace(
        ability_function,
        ability_function
        + "if($GLOBALS['frontend_disconnected']??false){"
        + "$GLOBALS['disconnected_ability_calls']++;"
        + "throw new RuntimeException('Connector/ability service is unavailable');}",
    )
    source = source.replace(
        rest_function,
        rest_function
        + "if($GLOBALS['frontend_disconnected']??false){"
        + "$GLOBALS['disconnected_rest_calls']++;"
        + "throw new RuntimeException('Connector/REST service is unavailable');}",
    )
    meta_insert = "foreach($fields['meta_input']??[] as $key=>$value)$GLOBALS['meta'][$id][$key]=$value;"
    if source.count(meta_insert) != 1:
        raise AssertionError("The maintained post/meta insertion double changed; review the crash hook.")
    source = source.replace(
        meta_insert,
        "foreach($fields['meta_input']??[] as $key=>$value){"
        "if(($GLOBALS['fault_partial_meta_once']??false)&&$key==='_sgnews_operation_key'){"
        "unset($GLOBALS['fault_partial_meta_once']);"
        "throw new RuntimeException('Injected exception after post insertion before operation-key meta');}"
        "$GLOBALS['meta'][$id][$key]=$value;}",
    )
    return source + r'''
function faultReset(string $name): void {
    global $owner,$lease,$slug,$root;
    $GLOBALS['options']=[];$GLOBALS['posts']=[];$GLOBALS['meta']=[];$GLOBALS['seo']=[];
    $GLOBALS['next_post']=100;$GLOBALS['current_post']=0;$GLOBALS['current_user']=5;
    unset($GLOBALS['fault_terms_once'],$GLOBALS['fault_completion_once'],$GLOBALS['fault_checkpoint_key'],$GLOBALS['fault_partial_meta_once'],$GLOBALS['frontend_disconnected']);
    $owner='review_nonce_aaaaaaaaaaaaaaaa';
    $lease=SGNews_Publisher::acquire(request(['scope'=>'news','owner'=>$owner,'ttl_seconds'=>300]));
    if(is_wp_error($lease))throw new RuntimeException('Probe lease could not be acquired.');
    $slug=$name.'-2026-10-11';$root='sg-news-'.substr(hash('sha256',$slug),0,12);
}
function faultDescribe($r): array {
    if(is_wp_error($r))return ['error'=>$r->get_error_message(),'status'=>$r->get_error_data()['status']??null];
    $out=[];
    foreach(['state','found','safe_to_resume','definitive_absence','id','status','source_sha256','idempotency_key'] as $k) {
        if(array_key_exists($k,$r))$out[$k]=$r[$k];
    }
    if(array_key_exists('result',$r))$out['result']=$r['result']===null?null:faultDescribe($r['result']);
    return $out;
}
function faultCount(string $kind): int {
    return count(array_filter($GLOBALS['meta'],fn($m)=>($m['_sgnews_kind']??null)===$kind));
}
function faultObserve(array $payload,$first,bool $publishing,string $kind): array {
    $lookup=SGNews_Publisher::operation(request([],['key'=>$payload['idempotency_key']]));
    $before=faultCount($kind);
    $statuses=[];foreach($GLOBALS['posts'] as $id=>$post)if(get_post_meta($id,'_sgnews_kind',true)===$kind)$statuses[]=$post->post_status;
    $retry=$publishing?SGNews_Publisher::publish(request($payload)):SGNews_Publisher::draft(request($payload));
    $after=faultCount($kind);
    $final=SGNews_Publisher::operation(request([],['key'=>$payload['idempotency_key']]));
    return ['first'=>faultDescribe($first),'lookup'=>faultDescribe($lookup),'retry'=>faultDescribe($retry),
        'final'=>faultDescribe($final),'count_before'=>$before,'count_after'=>$after,
        'statuses_before'=>$statuses,
        'barrier_present'=>isset($GLOBALS['options']['_sgnews_publisher_mutation_v1']),
        'fault_pending'=>isset($GLOBALS['fault_terms_once'])||isset($GLOBALS['fault_completion_once'])||isset($GLOBALS['fault_partial_meta_once']),
        'original_key'=>$payload['idempotency_key']];
}
$probe=['baseline_tests'=>$tests,'baseline_failures'=>$failures,'cases'=>[]];
faultReset('transient-stage-terms');
$payload=stagePayload('ja',str_repeat('t',64));$GLOBALS['fault_terms_once']=true;
$first=SGNews_Publisher::draft(request($payload));
$probe['cases']['stage_terms']=faultObserve($payload,$first,false,'stage');

faultReset('transient-canonical-terms');
$ja=SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('a',64))));
$en=SGNews_Publisher::draft(request(stagePayload('en',str_repeat('b',64))));
if(is_wp_error($ja)||is_wp_error($en))throw new RuntimeException('Healthy stage setup failed.');
$payload=pubPayload($ja,$en,str_repeat('p',64));$GLOBALS['fault_terms_once']=true;
$first=SGNews_Publisher::publish(request($payload));
$probe['cases']['canonical_terms']=faultObserve($payload,$first,true,'canonical');

faultReset('partial-completion-checkpoint');
$ja=SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('a',64))));
$en=SGNews_Publisher::draft(request(stagePayload('en',str_repeat('b',64))));
if(is_wp_error($ja)||is_wp_error($en))throw new RuntimeException('Healthy stage setup failed.');
$payload=pubPayload($ja,$en,str_repeat('p',64));$GLOBALS['fault_completion_once']=true;
$first=SGNews_Publisher::publish(request($payload));
$probe['cases']['completion_checkpoint']=faultObserve($payload,$first,true,'canonical');

faultReset('partial-published-id-checkpoint');
$ja=SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('a',64))));
$en=SGNews_Publisher::draft(request(stagePayload('en',str_repeat('b',64))));
if(is_wp_error($ja)||is_wp_error($en))throw new RuntimeException('Healthy stage setup failed.');
$payload=pubPayload($ja,$en,str_repeat('p',64));
$GLOBALS['fault_checkpoint_key']='_sgnews_published_id';$GLOBALS['fault_completion_once']=true;
$first=SGNews_Publisher::publish(request($payload));
$probe['cases']['published_id_checkpoint']=faultObserve($payload,$first,true,'canonical');

faultReset('legacy-partial-completion-checkpoint');
$ja=SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('a',64))));
$en=SGNews_Publisher::draft(request(stagePayload('en',str_repeat('b',64))));
if(is_wp_error($ja)||is_wp_error($en))throw new RuntimeException('Healthy legacy fixture setup failed.');
$payload=pubPayload($ja,$en,str_repeat('p',64));
$GLOBALS['fault_checkpoint_key']='_sgnews_published_id';$GLOBALS['fault_completion_once']=true;
$first=SGNews_Publisher::publish(request($payload));
// Reconstruct only the documented old-version marker-before-ID interruption;
// keep its original pending fingerprint and actual canonical/source/audit.
$GLOBALS['meta'][$ja['id']]['_sgnews_publish_complete']='yes';
unset($GLOBALS['meta'][$ja['id']]['_sgnews_published_id']);
$probe['cases']['legacy_completion_checkpoint']=faultObserve($payload,$first,true,'canonical');

faultReset('unproven-stage-first-write');
$payload=stagePayload('ja',str_repeat('t',64));$GLOBALS['fault_partial_meta_once']=true;
$first=SGNews_Publisher::draft(request($payload));
$probe['cases']['unproven_stage_creation']=faultObserve($payload,$first,false,'stage');

faultReset('unproven-canonical-first-write');
$ja=SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('a',64))));
$en=SGNews_Publisher::draft(request(stagePayload('en',str_repeat('b',64))));
if(is_wp_error($ja)||is_wp_error($en))throw new RuntimeException('Healthy unknown-creation fixture setup failed.');
$payload=pubPayload($ja,$en,str_repeat('p',64));$GLOBALS['fault_partial_meta_once']=true;
$first=SGNews_Publisher::publish(request($payload));
$probe['cases']['unproven_canonical_creation']=faultObserve($payload,$first,true,'canonical');

$probe['tamper']=[];
foreach(['payload','owner','source','operation_key','category','language'] as $tamper) {
    faultReset('partial-stage-tamper-'.str_replace('_','-',$tamper));
    $payload=stagePayload('ja',str_repeat('t',64));$GLOBALS['fault_terms_once']=true;
    $first=SGNews_Publisher::draft(request($payload));
    if(!is_wp_error($first)||faultCount('stage')!==1)throw new RuntimeException('Tamper fixture did not retain one partial stage.');
    $id=(int)array_key_first($GLOBALS['posts']);
    $before=count($GLOBALS['posts']);
    switch($tamper) {
        case 'payload': $payload['html']=str_replace('本文','Changed retry payload',$payload['html']);break;
        case 'owner': $GLOBALS['posts'][$id]->post_author=9;break;
        case 'source': $GLOBALS['meta'][$id]['_sgnews_source_html'].='<p>Changed stored source</p>';break;
        case 'operation_key': $GLOBALS['meta'][$id]['_sgnews_operation_key']=str_repeat('y',64);break;
        case 'category': $GLOBALS['meta'][$id]['categories']=[999];break;
        case 'language': $GLOBALS['meta'][$id]['terms']=[262];break;
    }
    $lookup=SGNews_Publisher::operation(request([],['key'=>$payload['idempotency_key']]));
    $retry=SGNews_Publisher::draft(request($payload));
    $probe['tamper'][$tamper]=['lookup'=>faultDescribe($lookup),'retry'=>faultDescribe($retry),
        'count_before'=>$before,'count_after'=>count($GLOBALS['posts']),
        'barrier_present'=>isset($GLOBALS['options']['_sgnews_publisher_mutation_v1'])];
}
faultReset('completed-stage-taxonomy-tamper');
$payload=stagePayload('ja',str_repeat('t',64));$saved=SGNews_Publisher::draft(request($payload));
if(is_wp_error($saved))throw new RuntimeException('Healthy completed stage setup failed.');
$GLOBALS['meta'][$saved['id']]['terms']=[];
$probe['completed_stage_tamper']=['retry'=>faultDescribe(SGNews_Publisher::draft(request($payload))),
    'posts'=>count($GLOBALS['posts'])];

faultReset('stored-frontend-disconnection');
$body='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" role="img" aria-label="Stored chart"><rect x="1" y="1" width="8" height="8" fill="#123456"></rect></svg>'
    .'<a href="https://sggroup.jp/en/article/news/linked-example-2026-10-11/">Stored internal link</a>';
$ja=SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('a',64),$body)));
$en=SGNews_Publisher::draft(request(stagePayload('en',str_repeat('b',64),$body)));
if(is_wp_error($ja)||is_wp_error($en))throw new RuntimeException('Frontend fixture staging failed.');
$publicJA=SGNews_Publisher::publish(request(pubPayload($ja,$en,str_repeat('c',64))));
$publicEN=SGNews_Publisher::publish(request(pubPayload($en,$ja,str_repeat('d',64))));
if(is_wp_error($publicJA)||is_wp_error($publicEN))throw new RuntimeException('Frontend fixture publication failed.');
$localSEO=$GLOBALS['seo'];
$GLOBALS['frontend_disconnected']=true;$GLOBALS['current_user']=0;$GLOBALS['capabilities']=[];
$GLOBALS['disconnected_ability_calls']=0;$GLOBALS['disconnected_rest_calls']=0;
$frontend=[];
foreach(['ja'=>$publicJA,'en'=>$publicEN] as $language=>$public) {
    $id=$public['id'];$GLOBALS['current_post']=$id;
    $fragment=SGNews_Publisher::render(get_post($id)->post_content);
    // AIOSEO stores its SEO row locally; this models its local head output,
    // not a deployed AIOSEO integration or a live theme certification.
    $localHead='<title>'.esc_attr($localSEO[$id]['title']).'</title>'
        .'<meta name="description" content="'.esc_attr($localSEO[$id]['description']).'" />'
        .'<link rel="canonical" href="https://example.invalid/wrong/" />';
    $head=newsHead($localHead);
    $articleGraph=[['@type'=>'BlogPosting','@id'=>$public['public_url'].'#article',
        'datePublished'=>'2026-10-11T00:00:00+00:00']];
    $schema=SGNews_Publisher::freeSchema($articleGraph);
    $dom=new DOMDocument();
    $dom->loadHTML('<!doctype html><html><head>'.$head.'</head><body>'.$fragment.'</body></html>',LIBXML_NONET|LIBXML_NOERROR|LIBXML_NOWARNING);
    $canonical=[];$alternates=[];$description=null;$links=[];
    foreach($dom->getElementsByTagName('link') as $link) {
        if($link->getAttribute('rel')==='canonical')$canonical[]=$link->getAttribute('href');
        if($link->getAttribute('rel')==='alternate')$alternates[$link->getAttribute('hreflang')][]=$link->getAttribute('href');
    }
    foreach($dom->getElementsByTagName('meta') as $metaNode) {
        if($metaNode->getAttribute('name')==='description')$description=$metaNode->getAttribute('content');
    }
    foreach($dom->getElementsByTagName('a') as $link)$links[]=$link->getAttribute('href');
    $frontend[$language]=['exact_source'=>$fragment===$public['content_raw'],
        'source_sha256'=>hash('sha256',$fragment),'expected_sha256'=>$public['source_sha256'],
        'style_count'=>$dom->getElementsByTagName('style')->length,
        'svg_count'=>$dom->getElementsByTagName('svg')->length,'links'=>$links,
        'seo_title'=>$dom->getElementsByTagName('title')->item(0)->textContent,
        'expected_seo_title'=>$localSEO[$id]['title'],'description'=>$description,
        'expected_description'=>$localSEO[$id]['description'],
        'canonical'=>$canonical,'public_url'=>$public['public_url'],'alternates'=>$alternates,
        'schema'=>$schema,'expected_title'=>$public['title']];
}
$probe['frontend']=['languages'=>$frontend,'ability_calls'=>$GLOBALS['disconnected_ability_calls'],
    'rest_calls'=>$GLOBALS['disconnected_rest_calls'],'anonymous_user'=>$GLOBALS['current_user']===0,
    'author_capabilities_absent'=>$GLOBALS['capabilities']===[],'local_seo_unchanged'=>$GLOBALS['seo']===$localSEO,
    'network'=>'Docker --network none; local WordPress/AIOSEO doubles, not live frontend evidence'];
echo json_encode($probe,JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT)."\n";
'''


class TransientStorageFaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            [
                "docker", "run", "--rm", "-i", "--network", "none",
                "-v", f"{PROJECT}:/app:ro", "-w", "/app", "php:8.3-cli", "php",
            ],
            input=php_fault_probe(), text=True, capture_output=True, timeout=90, check=False,
        )
        if result.returncode:
            raise AssertionError(f"Offline PHP harness failed: {result.stderr}\n{result.stdout}")
        cls.probe = json.loads(result.stdout)

    def test_maintained_lifecycle_still_passes(self) -> None:
        self.assertGreaterEqual(self.probe["baseline_tests"], 127)
        self.assertEqual(self.probe["baseline_failures"], [])

    def assert_recovers(self, name: str, published: bool) -> None:
        case = self.probe["cases"][name]
        self.assertIn("error", case["first"], "The intended one-shot fault must actually fire.")
        self.assertFalse(case["fault_pending"], "The later retry must run with healthy storage.")
        self.assertFalse(case["barrier_present"], "A handled fault must release its barrier.")
        self.assertEqual(case["count_before"], 1, "Keep the original partial post for reconciliation.")
        self.assertEqual(case["count_after"], 1, "A same-key retry must never create a duplicate.")
        self.assertNotIn("error", case["lookup"], case)
        self.assertEqual(case["lookup"]["state"], "pending", case)
        self.assertTrue(case["lookup"]["found"], case)
        self.assertTrue(case["lookup"]["safe_to_resume"], case)
        self.assertFalse(case["lookup"]["definitive_absence"], case)
        self.assertNotIn("error", case["retry"], case)
        self.assertEqual(case["retry"]["status"], "publish" if published else "draft", case)
        expected_post_key = ("canonical:" if published else "") + case["original_key"]
        self.assertEqual(case["retry"]["idempotency_key"], expected_post_key, case)
        self.assertEqual(case["final"]["state"], "complete", case)
        self.assertEqual(case["final"]["result"]["id"], case["retry"]["id"], case)
        self.assertEqual(case["final"]["result"]["source_sha256"], case["retry"]["source_sha256"], case)

    def test_stage_one_time_term_failure_recovers_same_post(self) -> None:
        self.assert_recovers("stage_terms", published=False)

    def test_canonical_one_time_term_failure_recovers_same_post(self) -> None:
        self.assertEqual(self.probe["cases"]["canonical_terms"]["statuses_before"], ["draft"])
        self.assert_recovers("canonical_terms", published=True)

    def test_partial_completion_checkpoint_recovers_same_publication(self) -> None:
        self.assertEqual(self.probe["cases"]["completion_checkpoint"]["statuses_before"], ["publish"])
        self.assert_recovers("completion_checkpoint", published=True)

    def test_partial_published_id_checkpoint_recovers_same_publication(self) -> None:
        self.assertEqual(self.probe["cases"]["published_id_checkpoint"]["statuses_before"], ["publish"])
        self.assert_recovers("published_id_checkpoint", published=True)

    def test_legacy_marker_before_published_id_recovers_proven_canonical(self) -> None:
        self.assert_recovers("legacy_completion_checkpoint", published=True)

    def test_unproven_first_write_stays_pending_and_never_duplicates(self) -> None:
        for name in ("unproven_stage_creation", "unproven_canonical_creation"):
            case = self.probe["cases"][name]
            with self.subTest(case=name):
                self.assertIn("error", case["first"], case)
                self.assertFalse(case["fault_pending"], case)
                self.assertFalse(case["barrier_present"], case)
                self.assertEqual(case["statuses_before"], ["draft"], case)
                self.assertEqual(case["count_before"], 1, case)
                self.assertEqual(case["count_after"], 1, case)
                self.assertIn("error", case["retry"], case)
                for receipt in (case["lookup"], case["final"]):
                    self.assertNotIn("error", receipt, case)
                    self.assertTrue(receipt["found"], case)
                    self.assertEqual(receipt["state"], "pending", case)
                    self.assertFalse(receipt["safe_to_resume"], case)
                    self.assertFalse(receipt["definitive_absence"], case)

    def test_changed_retry_payload_is_rejected_without_duplicate(self) -> None:
        case = self.probe["tamper"]["payload"]
        self.assertIn("error", case["retry"], case)
        self.assertEqual(case["count_before"], 1)
        self.assertEqual(case["count_after"], 1)
        self.assertFalse(case["barrier_present"])

    def test_corrupt_pending_stage_is_never_resumable_or_replaced(self) -> None:
        for tamper, case in self.probe["tamper"].items():
            if tamper == "payload":
                continue
            with self.subTest(tamper=tamper):
                self.assertIn("error", case["retry"], case)
                self.assertEqual(case["count_before"], 1)
                self.assertEqual(case["count_after"], 1)
                self.assertFalse(case["barrier_present"])
                if "error" not in case["lookup"]:
                    self.assertFalse(case["lookup"].get("safe_to_resume", False), case)
                    self.assertFalse(case["lookup"].get("definitive_absence", True), case)

    def test_completed_stage_taxonomy_is_not_repaired_as_initialization(self) -> None:
        case = self.probe["completed_stage_tamper"]
        self.assertIn("error", case["retry"], case)
        self.assertEqual(case["posts"], 1)

    def test_published_frontend_survives_absent_connector_and_author_capabilities(self) -> None:
        frontend = self.probe["frontend"]
        self.assertTrue(frontend["anonymous_user"])
        self.assertTrue(frontend["author_capabilities_absent"])
        self.assertTrue(frontend["local_seo_unchanged"])
        self.assertEqual(frontend["ability_calls"], 0)
        self.assertEqual(frontend["rest_calls"], 0)
        for language, result in frontend["languages"].items():
            with self.subTest(language=language):
                self.assertTrue(result["exact_source"])
                self.assertEqual(result["source_sha256"], result["expected_sha256"])
                self.assertEqual(result["style_count"], 1)
                self.assertEqual(result["svg_count"], 1)
                self.assertIn("https://sggroup.jp/en/article/news/linked-example-2026-10-11/", result["links"])
                self.assertEqual(result["seo_title"], result["expected_seo_title"])
                self.assertEqual(result["description"], result["expected_description"])
                self.assertEqual(result["canonical"], [result["public_url"]])
                self.assertEqual(
                    result["alternates"],
                    {
                        alternate: [result["public_url"].replace(f"/{language}/", f"/{target}/", 1)]
                        for alternate, target in (("ja", "ja"), ("en", "en"), ("x-default", "en"))
                    },
                )
                article = result["schema"][0]
                self.assertEqual(article["@type"], "NewsArticle")
                self.assertTrue(article["isAccessibleForFree"])
                self.assertEqual(article["url"], result["public_url"])
                self.assertEqual(article["inLanguage"], language)
                self.assertEqual(article["headline"], result["expected_title"])
                self.assertEqual(article["author"], article["publisher"])
                self.assertEqual(article["author"]["@type"], "Organization")
                self.assertEqual(article["author"]["name"], "SG Group")


if __name__ == "__main__":
    unittest.main()
