<?php
/** Local WordPress API double: executes plugin logic, not a deployed WordPress/MySQL concurrency test. */
define('ABSPATH', __DIR__.'/');
$GLOBALS['options']=[]; $GLOBALS['posts']=[]; $GLOBALS['meta']=[]; $GLOBALS['seo']=[];
$GLOBALS['current_user']=5; $GLOBALS['capabilities']=['read'=>true,'edit_posts'=>true,'publish_posts'=>true];
$GLOBALS['next_post']=100; $GLOBALS['current_post']=0;
class WP_Error {
    public function __construct(public string $code,public string $message,public array $data=[]) {}
    public function get_error_message(){return $this->message;} public function get_error_data(){return $this->data;}
}
function is_wp_error($v){return $v instanceof WP_Error;}
function add_action(...$a){} function add_filter(...$a){}
function is_user_logged_in(){return $GLOBALS['current_user']>0;}
function get_current_user_id(){return $GLOBALS['current_user'];}
function current_user_can($cap){return $GLOBALS['capabilities'][$cap]??false;}
function user_can($id,$cap){return $id===99 && $cap==='manage_options';}
function wp_cache_delete(...$a){}
function get_option($name,$default=null){return $GLOBALS['options'][$name]??$default;}
function add_option($name,$value,...$a){if(array_key_exists($name,$GLOBALS['options']))return false;$GLOBALS['options'][$name]=$value;return true;}
function update_option($name,$value,...$a){$GLOBALS['options'][$name]=$value;return true;}
function delete_option($name){unset($GLOBALS['options'][$name]);return true;}
function maybe_serialize($v){return is_array($v)?serialize($v):(string)$v;}
class FakeDB {
    public string $options='wp_options'; public $beforeCAS=null;
    public function prepare($query,...$args){return [$query,$args];}
    public function query($prepared){
        [$query,[$new,$name,$old]]=$prepared;
        if(!str_contains($query,'AND BINARY option_value = %s'))throw new RuntimeException('Lease CAS lost its exact-value guard.');
        if($this->beforeCAS){$f=$this->beforeCAS;$this->beforeCAS=null;$f();}
        if(!array_key_exists($name,$GLOBALS['options']) || maybe_serialize($GLOBALS['options'][$name])!==$old)return 0;
        $GLOBALS['options'][$name]=unserialize($new,['allowed_classes'=>false]);return 1;
    }
}
$GLOBALS['wpdb']=new FakeDB();
function taxonomy_exists($name){return $name==='sg-group-language-controller';}
function get_term($id,$tax){return (object)['slug'=>[258=>'news',261=>'ja',262=>'en'][$id]??'unknown'];}
function wp_json_encode($v){return json_encode($v,JSON_UNESCAPED_UNICODE);}
function wp_slash($v){return is_array($v)?array_map('wp_slash',$v):(is_string($v)?addslashes($v):$v);}
function wp_unslash($v){return is_array($v)?array_map('wp_unslash',$v):(is_string($v)?stripslashes($v):$v);}
function wp_kses_post($v){return strip_tags($v,'<article><h1><h2><p><section><div><a>');}
function wp_insert_post($fields,$error=false){
    $fields=wp_unslash($fields);$id=$fields['ID']??$GLOBALS['next_post']++;
    if(!isset($fields['ID']) && ($fields['meta_input']['_sgnews_kind']??null)==='canonical' && ($fields['meta_input']['_sgnews_language']??null)==='en')$fields['post_name'].='-2';
    $old=$GLOBALS['posts'][$id]??(object)['ID'=>$id];
    foreach($fields as $key=>$value){if($key!=='meta_input' && $key!=='ID')$old->$key=$value;}
    $GLOBALS['posts'][$id]=$old;
    foreach($fields['meta_input']??[] as $key=>$value)$GLOBALS['meta'][$id][$key]=$value;
    return $id;
}
function wp_update_post($fields,$error=false){
    $id=wp_insert_post($fields,$error);
    if(($fields['post_status']??null)==='publish' && isset($GLOBALS['publish_hook'])){$hook=$GLOBALS['publish_hook'];unset($GLOBALS['publish_hook']);$hook($id);}
    return $id;
}
function get_post($id){return $GLOBALS['posts'][$id]??null;}
function get_post_meta($id,$key,$single=true){return $GLOBALS['meta'][$id][$key]??'';}
function update_post_meta($id,$key,$value){$GLOBALS['meta'][$id][$key]=$value;return true;}
function wp_set_post_categories($id,$values,$append=false){$GLOBALS['meta'][$id]['categories']=$values;}
function wp_get_post_categories($id){return $GLOBALS['meta'][$id]['categories']??[];}
function wp_set_object_terms($id,$values,$tax,$append=false){$GLOBALS['meta'][$id]['terms']=$values;return $values;}
function wp_get_object_terms($id,$tax,$args){return $GLOBALS['meta'][$id]['terms']??[];}
function get_posts($args){
    $ids=[];
    foreach($GLOBALS['posts'] as $id=>$p){
        if($p->post_type!==$args['post_type']||(int)$p->post_author!==$args['author'])continue;
        $okay=true;foreach($args['meta_query'] as $m){if(get_post_meta($id,$m['key'],true)!==$m['value'])$okay=false;}
        if($okay)$ids[]=$id;
    }
    return array_slice($ids,0,$args['numberposts']);
}
function get_the_ID(){return $GLOBALS['post']->ID??$GLOBALS['current_post'];}
#[AllowDynamicProperties]
class WP_Query {}
function setup_postdata($post){
    $GLOBALS['id']=$post->ID;$GLOBALS['authordata']=(object)['ID'=>$post->post_author];
    foreach(['currentday','currentmonth','page','pages','multipage','more','numpages'] as $name)$GLOBALS[$name]='preview-'.$name;
}
function get_single_template(){return $GLOBALS['template_path']??'';}
function nocache_headers(){}
function home_url($path){return 'https://sggroup.jp'.$path;}
function get_permalink($id){
    $metadata=get_post_meta($id,'_sgnews_metadata',true);
    return home_url('/'.get_post_meta($id,'_sgnews_language',true).'/article/news/'.($metadata['slug']??get_post($id)->post_name).'/');
}
function wp_unique_post_slug($slug,...$args){return $slug;}
class WP_REST_Request implements ArrayAccess {
    public array $body=[];public array $params=[];
    public function __construct($method='POST',$route=''){}
    public function get_json_params(){return $this->body;} public function set_body_params($body){$this->body=$body;}
    public function offsetExists($o):bool{return isset($this->params[$o]);}
    public function offsetGet($o):mixed{return $this->params[$o]??null;}
    public function offsetSet($o,$v):void{$this->params[$o]=$v;}
    public function offsetUnset($o):void{unset($this->params[$o]);}
}
class FakeRestResponse {public function is_error(){return false;}public function get_status(){return 200;}}
function rest_do_request($request){$GLOBALS['seo'][$request->body['id']]=[];return new FakeRestResponse();}
class FakeAbility {
    public function __construct(private string $name){}
    public function execute($input){
        $id=$input['postId'];
        if($this->name==='aioseo-posts/seo-data-get'){
            if(isset($GLOBALS['assert_seo_cap']))check(SGNews_Publisher::protect(['edit_posts'],'edit_post',5,[$id])===['edit_posts'],'owning read ability has exact scoped read permission');
            return $GLOBALS['seo'][$id]??new WP_Error('missing','Post not found',['status'=>404]);
        }
        if($GLOBALS['fail_next_seo']??false){unset($GLOBALS['fail_next_seo']);return new WP_Error('injected','Injected SEO write failure',['status'=>503]);}
        $GLOBALS['seo'][$id]=['title'=>$input['title'],'description'=>$input['description']];return ['updated'=>true,'post'=>$GLOBALS['seo'][$id]];
    }
}
function wp_get_ability($name){return new FakeAbility($name);}
require __DIR__.'/sggroup-news-publisher.php';
$tests=0;$failures=[];
function check($condition,$label){global $tests,$failures;++$tests;if(!$condition)$failures[]=$label;}
function request(array $body=[],array $params=[]){$r=new WP_REST_Request();$r->body=$body;$r->params=$params;return $r;}
function okay($value,$label){check(!is_wp_error($value),$label.(is_wp_error($value)?': '.$value->message:''));return $value;}
function rejected($value,$label){check(is_wp_error($value),$label);}
rejected(SGNews_Publisher::permission(),'dedicated capability is required');
$GLOBALS['capabilities']['sgnews_publish']=true;
check(SGNews_Publisher::permission()===true,'configured owner and minimal dedicated capability accepted');
$GLOBALS['current_user']=9;rejected(SGNews_Publisher::permission(),'other account denied');$GLOBALS['current_user']=5;
$status=SGNews_Publisher::status();
check($status['user_id']===5 && $status['owner_user_id']===5 && $status['can_publish'] && !($GLOBALS['capabilities']['unfiltered_html']??false),'status separates narrow publication from global HTML capability');
check($status['implementation_sha256']===hash_file('sha256',__DIR__.'/sggroup-news-publisher.php'),'installed source proof matches actual file');
$owner='worker_nonce_aaaaaaaaaaaaaaaa';
$lease=okay(SGNews_Publisher::acquire(request(['scope'=>'news','owner'=>$owner,'ttl_seconds'=>300])),'lease acquired');
check($lease['fence']===1,'initial lease fence');
rejected(SGNews_Publisher::acquire(request(['scope'=>'news','owner'=>'other_nonce_bbbbbbbbbbbbb','ttl_seconds'=>300])),'live lease cannot be stolen');
$slug='integration-news-event-2026-10-09';$root='sg-news-'.substr(hash('sha256',$slug),0,12);
function stagePayload($lang,$key,$body='本文',$target=null){
    global $lease,$owner,$slug,$root;
    return ['language'=>$lang,'html'=>'<style>#'.$root.'{color:#10233b}#'.$root.' .sgn-body{max-width:1080px;margin-inline:auto}</style><article id="'.$root.'" lang="'.$lang.'" data-sgn-role="article"><h1>Title</h1><p class="sgn-body">'.$body.'</p></article>',
        'metadata'=>['title'=>'Title','aioseo_title'=>'SEO title l SG Group','aioseo_description'=>'Description','slug'=>$slug],
        'categories'=>[258],'is_accessible_for_free'=>true,'status'=>'draft','existing_id'=>$target,'idempotency_key'=>$key,'lease_owner'=>$owner,'lease_fence'=>$lease['fence']];
}
$jaPayload=stagePayload('ja',str_repeat('a',64));$enPayload=stagePayload('en',str_repeat('b',64));
$ja=okay(SGNews_Publisher::draft(request($jaPayload)),'JA stage saved');
$en=okay(SGNews_Publisher::draft(request($enPayload)),'EN stage saved');
check($ja['status']==='draft' && $ja['public_url']===null && str_contains($ja['content_raw'],'<style>'),'unpublished source retains mandatory style');
check(!str_contains(get_post($ja['id'])->post_content,'<style>'),'core fallback does not expose stylesheet text');
$repeat=okay(SGNews_Publisher::draft(request($jaPayload)),'same draft key/source no-op');
check($repeat['id']===$ja['id'] && count($GLOBALS['posts'])===2,'draft retry creates no duplicate');
$changed=$jaPayload;$changed['html']=str_replace('本文','違う内容',$changed['html']);
rejected(SGNews_Publisher::draft(request($changed)),'same key with different content rejected');
$unknown=$jaPayload;$unknown['author']=99;rejected(SGNews_Publisher::draft(request($unknown)),'caller cannot set author');
check(SGNews_Publisher::protect(['edit_posts'],'edit_post',5,[$ja['id']])===['do_not_allow'],'core editing of managed source blocked');
check(SGNews_Publisher::protect(['delete_posts'],'delete_post',5,[$ja['id']])===['do_not_allow'],'core deletion of managed source blocked');
check(SGNews_Publisher::protect(['edit_posts'],'edit_post',99,[$ja['id']])===['edit_posts'],'administrator review remains possible');
$complete=okay(SGNews_Publisher::operation(request([],['key'=>$jaPayload['idempotency_key']])),'durable stage operation lookup');
check($complete['found'] && $complete['state']==='complete' && $complete['result']['id']===$ja['id'],'operation returns source readback');
$GLOBALS['assert_seo_cap']=true;
okay(SGNews_Publisher::article(request([],['id'=>$ja['id']])),'actual SEO read ability is scoped on article read');
unset($GLOBALS['assert_seo_cap']);
check(SGNews_Publisher::protect(['edit_posts'],'edit_post',5,[$ja['id']])===['do_not_allow'],'read permission is restored after ability execution');
$oldSEO=$GLOBALS['seo'][$ja['id']];$GLOBALS['seo'][$ja['id']]['title']='External stale title';
rejected(SGNews_Publisher::article(request([],['id'=>$ja['id']])),'actual external SEO drift rejects source readback');
$GLOBALS['seo'][$ja['id']]=$oldSEO;
$seoPayload=stagePayload('ja',str_repeat('s',64),'SEO retry stage');$beforeSEO=count($GLOBALS['posts']);
$GLOBALS['fail_next_seo']=true;
rejected(SGNews_Publisher::draft(request($seoPayload)),'stage SEO write failure keeps pending operation');
$pendingSEO=okay(SGNews_Publisher::operation(request([],['key'=>$seoPayload['idempotency_key']])),'pending stage operation after SEO failure');
check($pendingSEO['state']==='pending' && $pendingSEO['safe_to_resume']===true && $pendingSEO['result']===null,'unverified SEO stage remains pending and may resume same key');
$retrySEO=okay(SGNews_Publisher::draft(request($seoPayload)),'same-key stage resumes failed actual SEO write');
check(count($GLOBALS['posts'])===$beforeSEO+1 && $retrySEO['seo_verified']===true,'SEO retry keeps one stage and verifies persisted metadata');
$template=sys_get_temp_dir().'/sgnews-native-preview-'.getmypid().'.php';
file_put_contents($template,'<?php $GLOBALS["preview_write_allowed"]=SGNews_Publisher::protect(["edit_posts"],"edit_post",5,[$GLOBALS["post"]->ID])!==["do_not_allow"]; echo "<!doctype html><html><head></head><body>".SGNews_Publisher::render("fallback")."</body></html>";');
$GLOBALS['template_path']=$template;$GLOBALS['post']=(object)['ID'=>999];$GLOBALS['wp_query']=(object)['original'=>true];$GLOBALS['pages']=['original'];$GLOBALS['more']=false;
$beforeGlobals=[];foreach(['post','wp_query','wp_the_query','id','authordata','currentday','currentmonth','page','pages','multipage','more','numpages'] as $name)$beforeGlobals[$name]=['exists'=>array_key_exists($name,$GLOBALS),'value'=>$GLOBALS[$name]??null];
$preview=okay(SGNews_Publisher::preview(request([],['id'=>$ja['id']])),'owned native theme saved preview');
check(str_contains($preview['html'],$ja['content_raw']) && $preview['source_sha256']===$ja['source_sha256'] && $preview['user_id']===5 && $preview['implementation_sha256']===hash_file('sha256',__DIR__.'/sggroup-news-publisher.php'),'preview binds exact saved source and trusted producer identity');
check($GLOBALS['preview_write_allowed']===false,'native template retains managed core write protections');
$restored=true;foreach($beforeGlobals as $name=>$snapshot)if(array_key_exists($name,$GLOBALS)!==$snapshot['exists']||($GLOBALS[$name]??null)!==$snapshot['value'])$restored=false;
check($restored,'native preview restores all query and postdata globals exactly');
file_put_contents($template,'<?php ob_start(); echo "secret nested output"; throw new RuntimeException("Injected template failure");');
$level=ob_get_level();rejected(SGNews_Publisher::preview(request([],['id'=>$ja['id']])),'template failure rejects saved preview');
check(ob_get_level()===$level,'template failure cleans nested output buffers');
unlink($template);unset($GLOBALS['template_path']);
foreach(['post','wp_query','wp_the_query','id','authordata','currentday','currentmonth','page','pages','multipage','more','numpages'] as $name)unset($GLOBALS[$name]);
function pubPayload($stage,$peer,$key,$target=null){
    global $lease,$owner;
    return ['stage_post_id'=>$stage['id'],'target_post_id'=>$target,'idempotency_key'=>$key,'lease_owner'=>$owner,'lease_fence'=>$lease['fence'],
        'expected_source_sha256'=>$stage['source_sha256'],'peer_stage_post_id'=>$peer['id'],'peer_source_sha256'=>$peer['source_sha256'],'audit_sha256'=>str_repeat('f',64)];
}
rejected(SGNews_Publisher::publish(request(pubPayload($ja,$ja,str_repeat('c',64)))),'same-language peer rejected');
$wrongHash=pubPayload($ja,$en,str_repeat('c',64));$wrongHash['expected_source_sha256']=str_repeat('0',64);
rejected(SGNews_Publisher::publish(request($wrongHash)),'wrong audited source hash rejected');
$publishedJa=okay(SGNews_Publisher::publish(request(pubPayload($ja,$en,str_repeat('c',64)))),'JA canonical publication');
check($publishedJa['status']==='publish' && $publishedJa['id']!==$ja['id'] && $publishedJa['public_url']==='https://sggroup.jp/ja/article/news/'.$slug.'/','canonical publication has stable supported URL');
$publishedEn=okay(SGNews_Publisher::publish(request(pubPayload($en,$ja,str_repeat('d',64)))),'EN publication after JA success');
check($publishedEn['status']==='publish' && get_post($ja['id'])->post_status==='draft','immutable peer stage remains available for partial recovery');
$pubRetry=okay(SGNews_Publisher::publish(request(pubPayload($ja,$en,str_repeat('c',64)))),'publication retry no-op');
check($pubRetry['id']===$publishedJa['id'] && count($GLOBALS['posts'])===5,'publication retry creates no canonical duplicate');
check(get_post($publishedEn['id'])->post_name===$slug.'-2' && $publishedEn['public_url']==='https://sggroup.jp/en/article/news/'.$slug.'/','controller supports shared public slug with distinct EN internal identifier');
$GLOBALS['current_post']=$publishedJa['id'];
check(SGNews_Publisher::render('fallback')===$publishedJa['content_raw'],'managed renderer returns exact validated source');
$schema=SGNews_Publisher::freeSchema([['@type'=>'NewsArticle']]);
check($schema[0]['isAccessibleForFree']===true,'free-access schema uses owning plugin output filter');
$nextJa=okay(SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('e',64),'次の本文',$publishedJa['id']))),'revision stage saved');
$nextEn=okay(SGNews_Publisher::draft(request(stagePayload('en',str_repeat('f',64),'Next body',$publishedEn['id']))),'revision peer saved');
$staleJa=okay(SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('g',64),'古い競合原稿',$publishedJa['id']))),'competing revision staged');
$baselineSource=$publishedJa['content_raw'];$baselineTitle=get_post($publishedJa['id'])->post_title;$baselineSEO=$GLOBALS['seo'][$publishedJa['id']];
$GLOBALS['publish_hook']=function($id){$GLOBALS['posts'][$id]->post_title='Injected external title drift';};
rejected(SGNews_Publisher::publish(request(pubPayload($nextJa,$nextEn,str_repeat('h',64),$publishedJa['id']))),'final readback failure rejects publication update');
check(get_post_meta($publishedJa['id'],'_sgnews_source_html',true)===$baselineSource && get_post($publishedJa['id'])->post_title===$baselineTitle && get_post($publishedJa['id'])->post_status==='publish' && $GLOBALS['seo'][$publishedJa['id']]===$baselineSEO,'failed update restores prior public source/title/status/SEO');
$newJa=okay(SGNews_Publisher::publish(request(pubPayload($nextJa,$nextEn,str_repeat('h',64),$publishedJa['id']))),'revision updates canonical');
check($newJa['id']===$publishedJa['id'] && $newJa['public_url']===$publishedJa['public_url'],'material update retains canonical ID and URL');
rejected(SGNews_Publisher::publish(request(pubPayload($staleJa,$nextEn,str_repeat('i',64),$publishedJa['id']))),'stale canonical baseline rejected');
$newEn=okay(SGNews_Publisher::publish(request(pubPayload($nextEn,$nextJa,str_repeat('m',64),$publishedEn['id']))),'EN revision with internal suffix updates canonical');
check(get_post($newEn['id'])->post_name===$slug.'-2' && $newEn['public_url']===$publishedEn['public_url'],'EN revision preserves internal identifier and shared public URL');
$pubKey=str_repeat('m',64);unset($GLOBALS['options']['_sgnews_op_'.hash('sha256',$pubKey)]);
$recovered=okay(SGNews_Publisher::operation(request([],['key'=>$pubKey])),'canonical mutation can be recovered without operation option');
check($recovered['found'] && $recovered['state']==='pending' && !$recovered['definitive_absence'] && $recovered['safe_to_resume']===true,'recovered canonical is pending rather than falsely absent');
$oldSEO=$GLOBALS['seo'][$newEn['id']];unset($GLOBALS['seo'][$newEn['id']]);
$partial=okay(SGNews_Publisher::operation(request([],['key'=>$pubKey])),'canonical source created before SEO/operation record is recoverable');
check($partial['state']==='pending' && $partial['result']===null && $partial['safe_to_resume']===true && !$partial['definitive_absence'],'partial canonical SEO state is explicit pending rather than readback fault');
$GLOBALS['seo'][$newEn['id']]=$oldSEO;
$original=$GLOBALS['meta'][$en['id']]['_sgnews_source_html'];$GLOBALS['meta'][$en['id']]['_sgnews_source_html'].='<script/>';
rejected(SGNews_Publisher::article(request([],['id'=>$en['id']])),'tampered protected source rejected');
$GLOBALS['meta'][$en['id']]['_sgnews_source_html']=$original;
$GLOBALS['options']['_sgnews_publisher_mutation_v1']=['nonce'=>'stuck','since'=>time()-9999,'fence'=>1];
rejected(SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('j',64),'危険な再開',$newJa['id']))),'crashed mutation barrier never auto-reclaimed');
$absent=SGNews_Publisher::operation(request([],['key'=>str_repeat('z',64)]));
check(!$absent['definitive_absence'] && $absent['state']==='pending','uncertain active mutation never asserts absence');
unset($GLOBALS['options']['_sgnews_publisher_mutation_v1']);
$GLOBALS['options']['_sgnews_publisher_lease_v1']['expires_at']=time()-1;
rejected(SGNews_Publisher::draft(request(stagePayload('ja',str_repeat('k',64),'期限切れ',$newJa['id']))),'expired lease cannot mutate');
$successor=['owner'=>'successor_nonce_bbbbbbbbbbbb','fence'=>2,'expires_at'=>time()+300,'user_id'=>5];
$GLOBALS['wpdb']->beforeCAS=function()use($successor){$GLOBALS['options']['_sgnews_publisher_lease_v1']=$successor;};
rejected(SGNews_Publisher::acquire(request(['scope'=>'news','owner'=>$owner,'ttl_seconds'=>300])),'CAS loser does not overwrite successor');
check($GLOBALS['options']['_sgnews_publisher_lease_v1']===$successor,'fenced successor record unchanged');
rejected(SGNews_Publisher::renew(request(['scope'=>'news','owner'=>$owner,'fence'=>1,'ttl_seconds'=>300])),'old fencing token cannot renew');
check(!array_key_exists('_sgnews_publisher_mutation_v1',$GLOBALS['options']),'mutation context/barrier restored after failures');
$fixture=json_decode(file_get_contents(__DIR__.'/../runtime/fixtures/sgnews-publisher-v1.json'),true,512,JSON_THROW_ON_ERROR);
check($fixture['fixture_only']===true && $status['api_version']===$fixture['status']['api_version'],'shared contract fixture is explicitly simulated');
echo json_encode(['tests'=>$tests,'failures'=>$failures,'scope'=>'PHP plugin logic with WordPress API doubles; live WordPress/MySQL/rendering validation is still required'],JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT)."\n";
exit($failures?1:0);
