"""Resumable bilingual publication. Native agents supply complete verified bundles.

This module is a queue/gate/publisher, not a substitute for the original prompt,
research, real browser testing or independent editorial review. It intentionally
contains no assumed model credentials, scheduled service or fabricated articles.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Protocol

from .state import GateBlocked, LeaseLost, StateError, StateStore, canonical, fingerprint
from .evidence import validate_article_evidence

LANGUAGES = ("ja", "en")
META_KEYS = ("title", "aioseo_title", "aioseo_description", "slug")
EDITORIAL_EVIDENCE = (
    "research_fact_check", "bilingual_semantic_parity", "independent_audit",
    "viewpoint_reviews", "browser_qa", "clipboard_qa", "slug_cms_collision", "internal_links",
)


class NeedsReconciliation(StateError):
    pass


class NeedsCorrection(StateError):
    pass


class PublicationTransport(Protocol):
    """Return normalized post dicts, never assume core REST supports idempotency.

    reconcile must return ONLY verified same-operation matches. Absence after a
    timeout is not proof of no write: definitive_absence requires the server's
    supported operation ledger/idempotent editorial route, not a slug search.
    A known server operation may return safe_to_resume=True only after matching
    its fingerprint and establishing that no active mutation barrier remains;
    this authorizes resuming the SAME key and is not proof of write absence.
    stage_draft for an existing published ID must leave its public version intact.
    publish_post finalizes such a stage into the same original ID and public URL.
    """
    supports_idempotent_creates: bool
    supports_staged_updates: bool

    def stage_draft(self, language: str, payload: dict, existing_id: int | None,
                    idempotency_key: str) -> dict: ...

    def get_post(self, post_id: int) -> dict: ...

    def reconcile(self, language: str, common_slug: str, idempotency_key: str,
                  existing_id: int | None, expected_payload: dict) -> dict: ...

    def publish_post(self, post_id: int, idempotency_key: str,
                     existing_id: int | None = None) -> dict: ...

    def verify_public(self, post: dict) -> dict: ...


class ContentValidator(Protocol):
    def validate_bundle(self, bundle: dict) -> dict: ...


def bundle_digest(bundle: dict) -> str:
    # Evidence does not get to alter the payload it attests to.
    return fingerprint(bundle["languages"])


def safe_error(exc: Exception) -> dict:
    # No request payload, authentication header or server error text in durable status.
    result = {"type": type(exc).__name__, "code": getattr(exc, "code", None),
              "http_status": getattr(exc, "status", None),
              "ambiguous_write": getattr(exc, "ambiguous_write", None)}
    if isinstance(exc, StateError):
        result["reason"] = str(exc)[:1000]  # Our local messages name the failed stage/check, never server text.
    return result


class Workflow:
    def __init__(self, store: StateStore, transport: PublicationTransport,
                 validator: ContentValidator, context: str, original_prompt: str | Path):
        if not context:
            raise ValueError("actual runtime identity/context required")
        self.store, self.transport, self.validator, self.context = store, transport, validator, context
        self.original_prompt = Path(original_prompt).resolve()
        self.prompt_sha256 = hashlib.sha256(self.original_prompt.read_bytes()).hexdigest()

    def _validate(self, bundle: dict, revision: dict):
        languages = bundle.get("languages", {})
        if set(languages) != set(LANGUAGES):
            raise NeedsCorrection("complete Japanese and English fragments required")
        for language in LANGUAGES:
            entry = languages[language]
            if set(entry) != {"html", "metadata"} or not entry["html"]:
                raise NeedsCorrection("each language requires full HTML and exactly four metadata values")
            metadata = entry["metadata"]
            if set(metadata) != set(META_KEYS) or any(not isinstance(metadata[k], str) or not metadata[k] for k in META_KEYS):
                raise NeedsCorrection("exactly four nonempty plain metadata strings required")
            if metadata["slug"] != revision["common_slug"] or not metadata["aioseo_title"].endswith(" l SG Group"):
                raise NeedsCorrection("reserved common slug or exact AIOSEO title suffix changed")
            if any(value != value.strip() or "\n" in value or "\r" in value for value in metadata.values()):
                raise NeedsCorrection("metadata must contain plain values without surrounding space or manual newline")
        slug = revision["common_slug"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}-\d{2}", slug):
            raise NeedsCorrection("common slug must be meaningful English ending in YYYY-MM-DD")
        try:
            date.fromisoformat(slug[-10:])
        except ValueError as exc:
            raise NeedsCorrection("invalid Gregorian slug date") from exc
        if slug[-10:] != revision["baseline_date"]:
            raise NeedsCorrection("event baseline date changed")
        report = self.validator.validate_bundle(bundle["languages"])
        if report.get("passed") is not True or report.get("errors"):
            raise NeedsCorrection("structural validator reports unresolved defects")
        digest = bundle_digest(bundle)
        evidence = bundle.get("evidence", {})
        if set(evidence) != set(EDITORIAL_EVIDENCE):
            raise NeedsCorrection("all original per-article editorial and actual QA evidence required")
        for check in EDITORIAL_EVIDENCE:
            item = evidence[check]
            try:
                path = Path(item["path"]).resolve()
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise NeedsCorrection("article evidence changed: " + check)
                record = json.loads(raw)
            except (KeyError, ValueError, OSError) as exc:
                raise NeedsCorrection("article evidence unavailable: " + check) from exc
            if (record.get("passed") is not True or record.get("unresolved_blockers") != 0 or
                    record.get("unresolved_major") != 0 or record.get("bundle_digest") != digest or
                    record.get("original_prompt_sha256") != self.prompt_sha256 or
                    not record.get("performed_at") or not record.get("execution_details")):
                raise NeedsCorrection("article evidence missing executed, content-bound pass: " + check)
            try:
                validate_article_evidence(check, record, digest, self.prompt_sha256,
                    self.store.runtime_binding, self.store.test_mode, self.store.clock,
                    viewer_sha256=bundle.get("viewer", {}).get("sha256"))
            except ValueError as exc:
                raise NeedsCorrection("article evidence does not establish required execution: " + check) from exc
        viewer = bundle.get("viewer", {})
        viewer_path = Path(viewer.get("path", ""))
        expected_filename = "SGGroup_News_" + slug + "_All_Copy_Viewer.html"
        try:
            if viewer_path.name != expected_filename or hashlib.sha256(viewer_path.read_bytes()).hexdigest() != viewer["sha256"]:
                raise NeedsCorrection("single verified viewer is missing or changed")
        except (KeyError, OSError) as exc:
            raise NeedsCorrection("single verified viewer unavailable") from exc

    @staticmethod
    def _saved_errors(language: str, entry: dict, post: dict, slug: str) -> list[str]:
        metadata = entry["metadata"]
        checks = {
            "content": post.get("content_raw") == entry["html"],
            "language": post.get("language") == language,
            "public_common_slug": post.get("common_slug") == slug,
            "title": post.get("title") == metadata["title"],
            "aioseo_title": post.get("seo_title") == metadata["aioseo_title"],
            "aioseo_description": post.get("meta_description") == metadata["aioseo_description"],
            "news_only": post.get("categories") == [258],
            "full_free_access": post.get("is_accessible_for_free") is True,
        }
        return [name for name, passed in checks.items() if not passed]

    def _checkpoint(self, revision_id: int, language: str, db=None) -> dict:
        if db is None:
            with self.store.connect() as connection:
                return self._checkpoint(revision_id, language, connection)
        row = db.execute("SELECT * FROM checkpoints WHERE revision_id=? AND language=?",
                         (revision_id, language)).fetchone()
        return dict(row) if row else {"stage_post_id": None, "public_post_id": None,
                                     "draft_validated": 0, "published": 0, "public_verified": 0}

    def _target(self, event_key: str, language: str, db=None) -> dict | None:
        if db is None:
            with self.store.connect() as connection:
                return self._target(event_key, language, connection)
        row = db.execute("SELECT * FROM posts WHERE event_key=? AND language=?", (event_key, language)).fetchone()
        return dict(row) if row else None

    def _operation(self, lease: dict, language: str, stage: str, payload: dict,
                   existing_id: int | None, call) -> dict:
        revision = self.store.revision(lease["revision_id"])
        operation_key = fingerprint({"event": lease["event_key"], "revision": revision["revision_key"],
                                     "language": language, "stage": stage, "payload": payload})
        with self.store.fenced(lease) as db:
            self.store.require_ready(self.context, db)
            operation = db.execute("SELECT * FROM operations WHERE operation_key=?", (operation_key,)).fetchone()
            fresh = operation is None
            if operation and operation["status"] == "succeeded":
                return json.loads(operation["result"])
            if fresh:
                db.execute("INSERT INTO operations VALUES(?,?,?,?,?,'intent',NULL,NULL,?)",
                           (operation_key, lease["revision_id"], language, stage, fingerprint(payload), self.store.clock()))
        failure = None
        result = None
        # The intent above survives a process crash inside the following transaction.
        with self.store.fenced(lease) as db:
            self.store.require_ready(self.context, db)
            operation = db.execute("SELECT * FROM operations WHERE operation_key=?", (operation_key,)).fetchone()
            if operation["status"] == "succeeded":
                return json.loads(operation["result"])
            if not fresh and operation["status"] in ("intent", "ambiguous"):
                reconciliation = self.transport.reconcile(language, revision["common_slug"], operation_key,
                                                           existing_id, payload)
                matches = reconciliation.get("posts", [])
                if reconciliation.get("complete") is not True or len(matches) > 1:
                    raise NeedsReconciliation("incomplete or conflicting CMS operation reconciliation")
                if matches:
                    candidate = matches[0]
                    # A draft recovered during publish is not proof of publication.
                    if stage != "publish" or candidate.get("status") == "publish":
                        result = candidate
                safe_resume = (reconciliation.get("safe_to_resume") is True and
                               getattr(self.transport, "supports_idempotent_creates", False) is True)
                if result is None and reconciliation.get("definitive_absence") is not True and not safe_resume:
                    raise NeedsReconciliation("ambiguous write retained; CMS absence has not been established")
            if result is None:
                try:
                    result = call(operation_key)
                    if not isinstance(result, dict) or type(result.get("id")) is not int or result["id"] <= 0:
                        raise ValueError("transport returned no valid operation result")
                    if stage == "publish":
                        expected_url = "https://sggroup.jp/" + language + "/article/news/" + revision["common_slug"] + "/"
                        if (result.get("status") != "publish" or result.get("language") != language or
                                result.get("common_slug") != revision["common_slug"] or result.get("public_url") != expected_url or
                                (payload.get("target_post_id") and result["id"] != payload["target_post_id"])):
                            raise ValueError("publication response is incomplete or identifies a different public target")
                except Exception as exc:
                    failure = exc
                    status = "failed" if getattr(exc, "ambiguous_write", True) is False else "ambiguous"
                    db.execute("UPDATE operations SET status=?,error=?,updated_at=? WHERE operation_key=?",
                               (status, canonical(safe_error(exc)), self.store.clock(), operation_key))
            if failure is None:
                db.execute("UPDATE operations SET status='succeeded',result=?,error=NULL,updated_at=? WHERE operation_key=?",
                           (canonical(result), self.store.clock(), operation_key))
        if failure:
            raise failure
        return result

    def submit_bundle(self, lease: dict, bundle: dict):
        """Correct and resubmit before publication; affected draft checks are rerun.

        After either language is public, a different payload is a NEW material
        revision/correction and may not silently invalidate the completed side.
        """
        revision = self.store.revision(lease["revision_id"])
        self._validate(bundle, revision)
        digest = bundle_digest(bundle)
        with self.store.fenced(lease) as db:
            old = self.store.revision(lease["revision_id"], db)
            if old["bundle_digest"] and old["bundle_digest"] != digest:
                public = db.execute("SELECT 1 FROM checkpoints WHERE revision_id=? AND published=1 LIMIT 1",
                                    (lease["revision_id"],)).fetchone()
                uncertain = db.execute("SELECT 1 FROM operations WHERE revision_id=? AND status IN ('intent','ambiguous') LIMIT 1",
                                       (lease["revision_id"],)).fetchone()
                if public or uncertain:
                    raise NeedsReconciliation("reconcile partial/uncertain writes before replacing an article; public corrections need a new revision")
                db.execute("UPDATE checkpoints SET draft_validated=0,public_verified=0 WHERE revision_id=?",
                           (lease["revision_id"],))
            db.execute("UPDATE revisions SET bundle_json=?,bundle_digest=?,state='validated',retry_stage=NULL,last_error=NULL,updated_at=? "
                       "WHERE id=?", (canonical(bundle), digest, self.store.clock(), lease["revision_id"]))

    def execute(self, revision_id: int, owner: str, bundle: dict | None = None, *, owned_lease: dict | None = None) -> dict:
        self.store.require_ready(self.context)
        if hasattr(self.transport, "verify_ready"):
            self.transport.verify_ready()  # Refresh actual identity/route capabilities before inspecting supported flags.
        if hasattr(self.transport, "runtime_binding"):
            binding = self.transport.runtime_binding(self.context)
            if binding != self.store.runtime_binding:
                raise GateBlocked("actual current transport identity/connection differs from recorded readiness")
        lease = owned_lease or self.store.acquire(revision_id, owner)
        if (lease["revision_id"] != revision_id or lease["owner"] != owner or
                lease["event_key"] != self.store.revision(revision_id)["event_key"]):
            raise LeaseLost("provided lease does not own this revision and worker")
        with self.store.fenced(lease):
            pass
        stage = "editorial_validation"
        try:
            if bundle is not None:
                self.submit_bundle(lease, bundle)
            revision = self.store.revision(revision_id)
            if not revision["bundle_json"]:
                raise NeedsCorrection("native production must submit full audited bilingual article bundle")
            bundle = json.loads(revision["bundle_json"])
            self._validate(bundle, revision)  # Includes evidence/artifact changes since a previous run.
            for language in LANGUAGES:
                checkpoint = self._checkpoint(revision_id, language)
                if checkpoint["published"]:
                    continue
                stage = "draft_" + language
                target = self._target(lease["event_key"], language)
                target_id = target["post_id"] if target else None
                if target and not getattr(self.transport, "supports_staged_updates", False):
                    raise GateBlocked("supported staged update route required to preserve current public article")
                if not target and not getattr(self.transport, "supports_idempotent_creates", False):
                    raise GateBlocked("supported server idempotency route required before creating new drafts")
                entry = bundle["languages"][language]
                payload = {"language": language, "html": entry["html"], "metadata": entry["metadata"],
                           "categories": [258], "is_accessible_for_free": True, "status": "draft"}
                if not checkpoint["draft_validated"]:
                    # Publisher stages are immutable. A corrected payload has a new
                    # operation key/stage; only an existing canonical public ID is a target.
                    saved = self._operation(lease, language, "draft", payload, target_id,
                                            lambda key: self.transport.stage_draft(language, payload,
                                                existing_id=target_id, idempotency_key=key))
                    post = self.transport.get_post(saved["id"])
                    errors = self._saved_errors(language, entry, post, revision["common_slug"])
                    if post.get("status") != "draft":
                        errors.append("stage_not_draft")
                    if not errors and not self.store.test_mode:
                        preview_verifier = getattr(self.transport, "verify_saved_preview", None)
                        if not callable(preview_verifier):
                            raise GateBlocked("actual full saved-draft preview browser verification is required before publication")
                        preview = preview_verifier(post)
                        if (preview.get("passed") is not True or preview.get("errors") != [] or
                                preview.get("source_sha256") != hashlib.sha256(entry["html"].encode("utf-8")).hexdigest()):
                            errors.append("actual_saved_preview_browser_check")
                    with self.store.fenced(lease) as db:
                        db.execute("INSERT INTO checkpoints(revision_id,language,stage_post_id,draft_validated,result) VALUES(?,?,?,?,?) "
                                   "ON CONFLICT(revision_id,language) DO UPDATE SET stage_post_id=excluded.stage_post_id,"
                                   "draft_validated=excluded.draft_validated,result=excluded.result",
                                   (revision_id, language, post["id"], int(not errors), canonical(post)))
                    if errors:
                        raise NeedsCorrection("saved draft differs from verified artifact: " + language + ": " + ",".join(errors))
            # Both drafts passed read-back before either side can become public.
            if hasattr(self.transport, "authorize_publication"):
                self.transport.authorize_publication(
                    stage_ids={lang: self._checkpoint(revision_id, lang)["stage_post_id"] for lang in LANGUAGES},
                    audit_sha256=bundle["evidence"]["independent_audit"]["sha256"],
                    bundle_digest=revision["bundle_digest"])
            for language in LANGUAGES:
                stage = "publish_" + language
                checkpoint = self._checkpoint(revision_id, language)
                target = self._target(lease["event_key"], language)
                target_id = target["post_id"] if target else None
                if not checkpoint["published"]:
                    if not checkpoint["draft_validated"]:
                        raise NeedsCorrection("both verified drafts required before publication")
                    # Re-read immediately before public mutation; do not trust an older checkpoint.
                    staged = self.transport.get_post(checkpoint["stage_post_id"])
                    if self._saved_errors(language, bundle["languages"][language], staged, revision["common_slug"]):
                        raise NeedsCorrection("saved staged artifact changed before publication")
                    payload = {"stage_post_id": checkpoint["stage_post_id"], "target_post_id": target_id,
                               "bundle_digest": revision["bundle_digest"], "status": "publish"}
                    post = self._operation(lease, language, "publish", payload, checkpoint["stage_post_id"],
                                            lambda key: self.transport.publish_post(checkpoint["stage_post_id"], key,
                                                                                   existing_id=target_id))
                    if post.get("status") != "publish" or (target_id and post["id"] != target_id):
                        raise NeedsReconciliation("publication response did not identify the preserved public target")
                    expected_url = "https://sggroup.jp/" + language + "/article/news/" + revision["common_slug"] + "/"
                    if post.get("public_url") != expected_url:
                        raise NeedsReconciliation("publication URL differs from reserved bilingual public URL")
                    with self.store.fenced(lease) as db:
                        db.execute("UPDATE checkpoints SET public_post_id=?,published=1,result=? WHERE revision_id=? AND language=?",
                                   (post["id"], canonical(post), revision_id, language))
                        db.execute("INSERT INTO posts VALUES(?,?,?,?) ON CONFLICT(event_key,language) DO UPDATE SET "
                                   "post_id=excluded.post_id,public_url=excluded.public_url",
                                   (lease["event_key"], language, post["id"], post["public_url"]))
            # Language links and live schema can depend on the peer already being public.
            # Preserve publication checkpoints even when either live verification fails.
            for language in LANGUAGES:
                stage = "public_verification_" + language
                checkpoint = self._checkpoint(revision_id, language)
                # A prior live pass cannot survive a recovery boundary unchecked.
                # Clear it before fresh reads so later failures never leave a stale pass.
                with self.store.fenced(lease) as db:
                    db.execute("UPDATE checkpoints SET public_verified=0 WHERE revision_id=? AND language=?", (revision_id, language))
                try:
                    post = self.transport.get_post(checkpoint["public_post_id"])
                except Exception as exc:
                    if getattr(exc, "status", None) == 404:
                        raise NeedsCorrection("known published article disappeared: " + language) from exc
                    raise
                if self._saved_errors(language, bundle["languages"][language], post, revision["common_slug"]):
                    raise NeedsCorrection("published content or metadata differs from verified artifact")
                report = self.transport.verify_public(post)
                if report.get("passed") is not True:
                    raise NeedsCorrection("live-only public verification failed: " + language)
                with self.store.fenced(lease) as db:
                    db.execute("UPDATE checkpoints SET public_verified=1 WHERE revision_id=? AND language=?", (revision_id, language))
            with self.store.fenced(lease) as db:
                db.execute("UPDATE revisions SET state='complete',retry_stage=NULL,last_error=NULL,updated_at=? WHERE id=?",
                           (self.store.clock(), revision_id))
            return {"revision_id": revision_id, "state": "complete", "languages": {
                lang: self._target(lease["event_key"], lang) for lang in LANGUAGES}, "viewer": bundle["viewer"]["path"]}
        except Exception as exc:
            try:
                with self.store.fenced(lease) as db:
                    state = "needs_correction" if isinstance(exc, NeedsCorrection) else "recoverable_failure"
                    db.execute("UPDATE revisions SET state=?,retry_stage=?,last_error=?,updated_at=? WHERE id=?",
                               (state, stage, canonical(safe_error(exc)), self.store.clock(), revision_id))
            except LeaseLost:
                pass
            raise
        finally:
            self.store.release(lease)

    def run_pending(self, owner: str, bundle_provider) -> dict:
        """Drain this execution's durable queue snapshot with no arbitrary story cap.

        bundle_provider(revision, renew_lease) is an injected native producer; it must
        execute the full prompt and return the artifact/evidence wrapper. Existing
        validated bundles resume directly. Unresolved work stays queued, and one
        failed event does not discard other independent qualifying events.
        The producer must call renew_lease during long research/QA work; losing
        ownership prevents its artifacts from reaching any CMS mutation.
        """
        self.store.require_ready(self.context)
        snapshot = self.store.status(self.context)["queue"]
        pending = [row for row in snapshot if row["state"] not in ("complete", "superseded_by_correction")]
        result = {"completed": [], "failures": [], "busy": [], "quiet_unchanged": not pending}
        for item in pending:
            revision_id = item["id"]
            lease = None
            try:
                lease = self.store.acquire(revision_id, owner)
                revision = self.store.revision(revision_id)
                public_side = any(self._checkpoint(revision_id, lang)["published"] for lang in LANGUAGES)
                # Recheck existing public artifacts after an integration repair;
                # changed public prose must use enqueue_correction, never overwrite a partial bundle.
                needs_producer = not revision["bundle_json"] or (revision["state"] == "needs_correction" and not public_side)
                bundle = bundle_provider(revision, lambda: self.store.renew(lease)) if needs_producer else None
                result["completed"].append(self.execute(revision_id, owner, bundle, owned_lease=lease))
            except LeaseLost:
                result["busy"].append(revision_id)
            except Exception as exc:
                result["failures"].append({"revision_id": revision_id, "error": safe_error(exc)})
                if isinstance(exc, GateBlocked):
                    break  # A global readiness/maintenance dependency stops publication.
            finally:
                if lease:
                    self.store.release(lease)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--runtime-binding", type=Path, help="configured current site/account/connection binding JSON")
    parser.add_argument("--original-prompt", type=Path, default=Path(__file__).resolve().parents[1] / "editorial-prompt-ja.txt")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    pause = sub.add_parser("pause")
    pause.add_argument("--reason", required=True)
    sub.add_parser("resume-workflow")
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--event", required=True, help="verified event JSON containing semantic_identity,material_facts,headline,sources,common_slug,baseline_date")
    source = sub.add_parser("register-source")
    source.add_argument("--source-key", required=True)
    source.add_argument("--initial-watermark", required=True, type=float)
    ingest = sub.add_parser("ingest-scan")
    ingest.add_argument("--scan", required=True, help="verified scan JSON {source_key,start,end,events,successful,error?}")
    correction = sub.add_parser("queue-public-correction")
    correction.add_argument("--revision-id", required=True, type=int)
    correction.add_argument("--correction", required=True, help="JSON {material_facts,headline,sources,reason}")
    gate = sub.add_parser("record-gate")
    gate.add_argument("--name", required=True)
    gate.add_argument("--evidence", required=True)
    gate.add_argument("--ttl-seconds", type=float, required=True)
    args = parser.parse_args()
    binding = json.loads(args.runtime_binding.read_text(encoding="utf-8")) if args.runtime_binding else None
    store = StateStore(args.database, original_prompt=args.original_prompt, runtime_binding=binding)
    if args.command == "pause":
        store.pause(args.reason)
    elif args.command == "resume-workflow":
        store.unpause(args.context)
    elif args.command == "enqueue":
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        revision_id, created = store.enqueue(**event)
        print(canonical({"revision_id": revision_id, "queued_new_material_revision": created}))
        return
    elif args.command == "record-gate":
        store.record_gate(args.name, args.evidence, args.context, args.ttl_seconds)
    elif args.command == "register-source":
        store.register_source(args.source_key, args.initial_watermark)
    elif args.command == "ingest-scan":
        record = json.loads(Path(args.scan).read_text(encoding="utf-8"))
        queued = store.ingest_scan(**record)
        print(canonical({"queued": [{"revision_id": revision, "new_material_revision": created} for revision, created in queued]}))
        return
    elif args.command == "queue-public-correction":
        record = json.loads(Path(args.correction).read_text(encoding="utf-8"))
        correction_id = store.enqueue_correction(args.revision_id, **record)
        print(canonical({"correction_revision_id": correction_id, "supersedes_revision_id": args.revision_id}))
        return
    print(canonical(store.status(args.context)))


if __name__ == "__main__":
    main()
