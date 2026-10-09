<?php
/** Offline helper; writes only the supplied output file, never contacts WordPress. */
define('ABSPATH',__DIR__.'/');
require __DIR__.'/sggroup-news-publisher.php';
if ($argc !== 3) { fwrite(STDERR,"Usage: canonicalize-bundle.php input.json output.json\n"); exit(2); }
$bundle=json_decode(file_get_contents($argv[1]),true,512,JSON_THROW_ON_ERROR);
$reports=[];
foreach($bundle as $language=>&$item) {
    if (!in_array($language,['ja','en'],true) || !is_array($item)) { throw new InvalidArgumentException('Expected ja/en objects.'); }
    $slug=$item['metadata']['slug']??$item['common_slug']??null;
    if (!$slug) { throw new InvalidArgumentException('Missing metadata.slug.'); }
    $root='sg-news-'.substr(hash('sha256',$slug),0,12);
    $canonical=SGNews_Fragment::canonical($item['html'],$root,$language);
    if (SGNews_Fragment::canonical($canonical,$root,$language)!==$canonical) { throw new RuntimeException('Canonicalization is not idempotent.'); }
    $item['html']=$canonical;
    $reports[$language]=['source_sha256'=>hash('sha256',$canonical),'bytes'=>strlen($canonical),'root_id'=>$root];
}
unset($item);
file_put_contents($argv[2],json_encode($bundle,JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT|JSON_THROW_ON_ERROR));
echo json_encode(['canonicalized'=>$reports,'policy_version'=>SGNews_Publisher::POLICY],JSON_PRETTY_PRINT)."\n";
