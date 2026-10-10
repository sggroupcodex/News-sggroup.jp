<?php
/** Offline WordPress admin API doubles; no deployed login, grant or publication. */
define('ABSPATH', __DIR__ . '/');
$GLOBALS['actions']=[]; $GLOBALS['menus']=[]; $GLOBALS['grants']=[]; $GLOBALS['lookups']=[];
class SetupDenied extends RuntimeException { public function __construct(public int $status) { parent::__construct('Synthetic admin request denied.'); } }
class SetupRedirect extends RuntimeException {}
class WP_Error {}
class WP_User {
    public function __construct(public int $ID, public array $roles, public array $caps) {}
    public function add_cap($cap, $grant=true) { $GLOBALS['grants'][]=[$this->ID,$cap,$grant]; $this->caps[$cap]=$grant; }
}
function add_action($hook,$callback) { $GLOBALS['actions'][$hook]=$callback; }
function add_filter(...$args) {}
function add_options_page(...$args) { $GLOBALS['menus'][]=$args; }
function current_user_can($cap) { return $cap==='manage_options' && $GLOBALS['admin']; }
function get_userdata($id) { $GLOBALS['lookups'][]=$id; return $GLOBALS['users'][$id] ?? false; }
function user_can($user,$cap) { if (is_int($user)) $user=$GLOBALS['users'][$user]??false; return $user instanceof WP_User && ($user->caps[$cap]??false); }
function home_url($path='') { return $GLOBALS['site'] . $path; }
function admin_url($path='') { return 'https://sggroup.jp/wp-admin/' . $path; }
function taxonomy_exists($name) { return $name==='sg-group-language-controller' && $GLOBALS['taxonomy']; }
function get_term($id,$taxonomy) { return $GLOBALS['terms'][$taxonomy][$id]??false; }
function is_wp_error($value) { return $value instanceof WP_Error; }
function wp_get_ability($name) { return in_array($name,$GLOBALS['abilities'],true) ? (object)['name'=>$name] : null; }
function esc_html($text) { return htmlspecialchars((string)$text,ENT_QUOTES|ENT_SUBSTITUTE,'UTF-8'); }
function esc_attr($text) { return esc_html($text); }
function esc_url($text) { return esc_html($text); }
function wp_die($message,$title='',$args=[]) { $GLOBALS['denial_message']=$message; throw new SetupDenied($args['response']??500); }
function wp_nonce_field($action,$field='_wpnonce',$referer=true) {
    $GLOBALS['nonce_fields'][]=[$action,$field,$referer];
    echo '<input type="hidden" name="'.esc_attr($field).'" value="offline-valid-nonce" />';
}
function check_admin_referer($action,$field='_wpnonce') {
    $GLOBALS['nonce_checks'][]=[$action,$field];
    if ($action!=='sgnews_grant_publisher' || ($_REQUEST[$field]??null)!=='offline-valid-nonce') wp_die('Synthetic invalid nonce.','',['response'=>403]);
}
function wp_safe_redirect($url) { $GLOBALS['redirect']=$url; throw new SetupRedirect('Synthetic redirect; actual handler exits immediately after redirect.'); }
function resetSetup(): void {
    $GLOBALS['admin']=true; $GLOBALS['site']='https://sggroup.jp'; $GLOBALS['taxonomy']=true;
    $GLOBALS['abilities']=['aioseo-posts/seo-data-get','aioseo-posts/seo-data-update'];
    $GLOBALS['users']=[5=>new WP_User(5,['author'],['edit_posts'=>true,'publish_posts'=>true,'read'=>true])];
    $GLOBALS['terms']=['category'=>[258=>(object)['term_id'=>258,'taxonomy'=>'category','slug'=>'news']],
        'sg-group-language-controller'=>[261=>(object)['term_id'=>261,'taxonomy'=>'sg-group-language-controller','slug'=>'ja'],262=>(object)['term_id'=>262,'taxonomy'=>'sg-group-language-controller','slug'=>'en']]];
    $GLOBALS['grants']=[]; $GLOBALS['lookups']=[]; $GLOBALS['menus']=[]; $GLOBALS['nonce_fields']=[]; $GLOBALS['nonce_checks']=[];
    unset($GLOBALS['redirect'],$GLOBALS['denial_message']);
    $_SERVER['REQUEST_METHOD']='POST'; $_GET=[];
    $_POST=['action'=>'sgnews_grant_publisher','_wpnonce'=>'offline-valid-nonce']; $_REQUEST=$_POST;
}
resetSetup();
require __DIR__ . '/sggroup-news-publisher.php';
$tests=0; $failures=[];
function checkSetup($condition,$label) { global $tests,$failures; ++$tests; if (!$condition) $failures[]=$label; }
function denySetup($status,$label,$page=false) {
    try { $page ? SGNews_Publisher::setupPage() : SGNews_Publisher::setupGrant(); checkSetup(false,$label . ': accepted'); }
    catch (SetupDenied $error) { checkSetup($error->status===$status,$label); }
    catch (Throwable $error) { checkSetup(false,$label . ': unexpected failure'); }
    checkSetup($GLOBALS['grants']===[],$label . ': no capability grant');
}
checkSetup($GLOBALS['grants']===[] && !isset($GLOBALS['users'][5]->caps['sgnews_publish']),'boot never grants capability');
checkSetup(isset($GLOBALS['actions']['admin_menu'],$GLOBALS['actions']['admin_post_sgnews_grant_publisher']),'normal admin hooks registered');
checkSetup(!isset($GLOBALS['actions']['admin_post_nopriv_sgnews_grant_publisher']),'no unauthenticated action registered');
$GLOBALS['admin']=false;
SGNews_Publisher::setupMenu(); checkSetup($GLOBALS['menus']===[],'Author sees no setup menu');
denySetup(403,'Author cannot invoke grant handler directly'); denySetup(403,'Author cannot invoke setup page directly',true);
$GLOBALS['admin']=true; SGNews_Publisher::setupMenu();
checkSetup(count($GLOBALS['menus'])===1 && $GLOBALS['menus'][0][2]==='manage_options','Settings page requires manage_options');
foreach (['GET','PUT',''] as $method) { resetSetup(); $_SERVER['REQUEST_METHOD']=$method; denySetup(405,'non-POST denied: '.$method); }
resetSetup(); unset($_POST['_wpnonce']); $_REQUEST=$_POST; denySetup(400,'missing nonce denied');
resetSetup(); $_POST['_wpnonce']='invalid'; $_REQUEST=$_POST; denySetup(403,'invalid nonce denied');
resetSetup(); $_REQUEST['_wpnonce']='different'; denySetup(400,'query/request nonce cannot replace POST nonce');
resetSetup(); $_POST['_wpnonce']=[]; $_REQUEST=$_POST; denySetup(400,'array nonce denied');
foreach (['user_id'=>99,'role'=>'administrator','capability'=>'unfiltered_html','redirect_to'=>'https://evil.example/','submit'=>['invalid']] as $key=>$value) {
    resetSetup(); $_POST[$key]=$value; $_REQUEST=$_POST; denySetup(400,'unexpected posted field denied: '.$key);
}
resetSetup(); $_POST['action']='another_action'; $_REQUEST=$_POST; denySetup(400,'different action denied');
resetSetup(); unset($GLOBALS['users'][5]); denySetup(409,'missing fixed owner denied');
resetSetup(); $GLOBALS['users'][5]->ID=99; denySetup(409,'mismatched owner object denied');
foreach ([[],['editor'],['administrator'],['author','editor'],['author','administrator']] as $roles) {
    resetSetup(); $GLOBALS['users'][5]->roles=$roles; denySetup(409,'only soleAuthor accepted: '.json_encode($roles));
}
foreach (['edit_posts','publish_posts'] as $cap) { resetSetup(); $GLOBALS['users'][5]->caps[$cap]=false; denySetup(409,'missing core capability denied: '.$cap); }
resetSetup(); $GLOBALS['users'][5]->caps['manage_options']=true; denySetup(409,'privileged owner denied despite Author role');
foreach (['https://other.example','http://sggroup.jp','https://sggroup.jp/subsite'] as $badSite) { resetSetup(); $GLOBALS['site']=$badSite; denySetup(409,'other site denied: '.$badSite); }
resetSetup(); $GLOBALS['terms']['category'][258]=new WP_Error(); denySetup(409,'invalid News term denied');
resetSetup(); $GLOBALS['terms']['category'][258]->slug='other'; denySetup(409,'wrong News slug denied');
resetSetup(); $GLOBALS['terms']['category'][258]->term_id=259; denySetup(409,'wrong News term identity denied');
resetSetup(); $GLOBALS['taxonomy']=false; denySetup(409,'missing language taxonomy denied');
foreach ([261,262] as $id) {
    resetSetup(); unset($GLOBALS['terms']['sg-group-language-controller'][$id]); denySetup(409,'missing language term denied: '.$id);
    resetSetup(); $GLOBALS['terms']['sg-group-language-controller'][$id]->slug='other'; denySetup(409,'wrong language slug denied: '.$id);
    resetSetup(); $GLOBALS['terms']['sg-group-language-controller'][$id]->term_id=999; denySetup(409,'wrong language identity denied: '.$id);
}
foreach (['aioseo-posts/seo-data-get','aioseo-posts/seo-data-update'] as $ability) {
    resetSetup(); $GLOBALS['abilities']=array_values(array_diff($GLOBALS['abilities'],[$ability])); denySetup(409,'missing SEO dependency denied: '.$ability);
}
resetSetup(); ob_start(); SGNews_Publisher::setupPage(); $html=ob_get_clean();
checkSetup(str_contains($html,'method="post"') && str_contains($html,'https://sggroup.jp/wp-admin/admin-post.php'),'normal local POST form');
checkSetup($GLOBALS['nonce_fields']===[['sgnews_grant_publisher','_wpnonce',false]],'normal WordPress nonce is required');
checkSetup(!str_contains($html,'name="user_id"') && !str_contains($html,'name="role"') && !str_contains($html,'name="capability"'),'form cannot select another user role or capability');
checkSetup(str_contains($html,'ID 5') && str_contains($html,'258') && str_contains($html,'261') && str_contains($html,'262'),'fixed setup identities displayed');
checkSetup($GLOBALS['grants']===[],'reading admin UI never grants capability');
resetSetup(); $GLOBALS['site']='<script>evil()</script>'; $_GET['sgnews_setup']='<img src=x onerror=evil()>'; $GLOBALS['users'][5]->roles=['author','editor'];
ob_start(); SGNews_Publisher::setupPage(); $html=ob_get_clean();
checkSetup(!str_contains($html,'<script>') && !str_contains($html,'<img') && !str_contains($html,'<form'),'unsafe values are not echoed; setup stays blocked');
resetSetup();
foreach ([1,2] as $attempt) {
    try { SGNews_Publisher::setupGrant(); checkSetup(false,'valid admin submission should redirect'); }
    catch (SetupRedirect $error) { checkSetup($GLOBALS['redirect']==='https://sggroup.jp/wp-admin/options-general.php?page=sggroup-news-publisher&sgnews_setup=granted','fixed local safe redirect'); }
}
checkSetup($GLOBALS['grants']===[[5,'sgnews_publish',true]],'repeat submit grants only fixed dedicated capability once');
checkSetup($GLOBALS['users'][5]->roles===['author'] && !isset($GLOBALS['users'][5]->caps['unfiltered_html']) && !isset($GLOBALS['users'][5]->caps['manage_options']),'role and broad capabilities unchanged');
checkSetup(array_unique($GLOBALS['lookups'])===[5],'only fixed user5 looked up');
checkSetup(count($GLOBALS['nonce_checks'])===2,'every valid submission verifies nonce');
ob_start(); SGNews_Publisher::setupPage(); $html=ob_get_clean(); checkSetup(!str_contains($html,'<form'),'already configured owner needs no second grant');
$source=file_get_contents(__DIR__.'/sggroup-news-publisher.php');
checkSetup(!str_contains($source,'register_activation_hook') && preg_match('/wp_safe_redirect\(admin_url\(\x27options-general\.php\?page=\x27.*?\);\s*exit;/s',$source)===1,'no activation grant and real redirect immediately exits');
echo json_encode(['tests'=>$tests,'failures'=>$failures,'scope'=>'offline WordPress administrator GUI API doubles; not live installation, login, privilege grant or publication'],JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT)."\n";
exit($failures?1:0);
