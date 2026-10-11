"""Durable queue on ONE persistent workspace, never a distributed CMS lock.

All workers must use this same SQLite file on a filesystem supporting SQLite
locking. An external mutation holds BEGIN IMMEDIATE so another local worker
cannot take an expired lease while a WordPress request is still in flight.
An intent is committed before the request; process loss requires reconciliation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
import stat
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable


FIRST_PREPUBLICATION_GATES = ("runtime_authentication", "mandatory_html_preservation", "saved_preview", "recovery_tests")

CORE_GATES = (
    "runtime_authentication", "mandatory_html_preservation", "saved_preview",
    "language_slug_metadata", "execution_capacity", "recovery_tests",
)


class StateError(RuntimeError):
    pass


class LeaseLost(StateError):
    pass


class GateBlocked(StateError):
    pass


class TransportCooldown(StateError):
    """A persisted temporary transport outage, separate from readiness gates."""
    code = "transport_cooldown"
    retryable = True
    ambiguous_write = False

    def __init__(self, record: dict):
        self.record = record
        self.retry_not_before = record["retry_not_before"]
        super().__init__("publication transport cooldown remains active")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class StateStore:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time,
                 test_mode: bool = False, original_prompt: str | Path | None = None,
                 runtime_binding: dict | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.test_mode = test_mode
        self.runtime_binding = runtime_binding
        self.original_prompt = Path(original_prompt) if original_prompt else Path(__file__).resolve().parents[1] / "editorial-prompt-ja.txt"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES('paused','true');
                INSERT OR IGNORE INTO settings VALUES('pause_reason','"initial readiness has not passed"');
                CREATE TABLE IF NOT EXISTS gates(
                    name TEXT PRIMARY KEY, passed INTEGER NOT NULL, checked_at REAL NOT NULL,
                    expires_at REAL NOT NULL, context TEXT NOT NULL, evidence TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    event_key TEXT PRIMARY KEY, semantic_identity TEXT NOT NULL,
                    common_slug TEXT NOT NULL UNIQUE, baseline_date TEXT NOT NULL,
                    created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL REFERENCES events,
                    revision_key TEXT NOT NULL, material_facts TEXT NOT NULL,
                    headline TEXT NOT NULL, sources TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    retry_stage TEXT, last_error TEXT, bundle_json TEXT, bundle_digest TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(event_key,revision_key));
                CREATE TABLE IF NOT EXISTS leases(
                    event_key TEXT PRIMARY KEY REFERENCES events,
                    owner TEXT NOT NULL, fence INTEGER NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS posts(
                    event_key TEXT NOT NULL REFERENCES events, language TEXT NOT NULL,
                    post_id INTEGER NOT NULL, public_url TEXT NOT NULL,
                    PRIMARY KEY(event_key,language));
                CREATE TABLE IF NOT EXISTS checkpoints(
                    revision_id INTEGER NOT NULL REFERENCES revisions,
                    language TEXT NOT NULL, stage_post_id INTEGER, public_post_id INTEGER,
                    draft_validated INTEGER NOT NULL DEFAULT 0,
                    published INTEGER NOT NULL DEFAULT 0, public_verified INTEGER NOT NULL DEFAULT 0,
                    result TEXT, PRIMARY KEY(revision_id,language));
                CREATE TABLE IF NOT EXISTS operations(
                    operation_key TEXT PRIMARY KEY, revision_id INTEGER NOT NULL REFERENCES revisions,
                    language TEXT NOT NULL, stage TEXT NOT NULL, payload_digest TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, error TEXT, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS first_publication_permit(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    revision_id INTEGER NOT NULL REFERENCES revisions, scope TEXT NOT NULL,
                    files TEXT NOT NULL, status TEXT NOT NULL, owner TEXT, fence INTEGER,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS first_publication_attempts(
                    capacity_sha256 TEXT PRIMARY KEY, revision_id INTEGER NOT NULL REFERENCES revisions,
                    owner TEXT NOT NULL, fence INTEGER NOT NULL, started_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS transport_cooldowns(
                    context TEXT NOT NULL, binding_digest TEXT NOT NULL, runtime_binding TEXT NOT NULL,
                    retry_not_before REAL NOT NULL, recorded_at REAL NOT NULL, error TEXT NOT NULL,
                    PRIMARY KEY(context,binding_digest));
                CREATE TABLE IF NOT EXISTS sources(
                    source_key TEXT PRIMARY KEY, watermark REAL NOT NULL,
                    last_attempt_end REAL, last_error TEXT);
                CREATE TABLE IF NOT EXISTS scans(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL REFERENCES sources,
                    start REAL NOT NULL, end REAL NOT NULL, successful INTEGER NOT NULL,
                    error TEXT, recorded_at REAL NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(revisions)")}
            if "supersedes_revision_id" not in columns:
                db.execute("ALTER TABLE revisions ADD COLUMN supersedes_revision_id INTEGER REFERENCES revisions(id)")
            if "correction_reason" not in columns:
                db.execute("ALTER TABLE revisions ADD COLUMN correction_reason TEXT")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _setting(db, key, value):
        db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, canonical(value)))

    def pause(self, reason: str):
        with self.transaction() as db:
            self._setting(db, "paused", True)
            self._setting(db, "pause_reason", reason)

    def unpause(self, context: str):
        with self.transaction() as db:
            blockers = self.gate_blockers(context, db=db)
            if blockers:
                raise GateBlocked("; ".join(blockers))
            self._setting(db, "paused", False)
            self._setting(db, "pause_reason", "")

    @staticmethod
    def implementation_digest() -> str:
        files = sorted(Path(__file__).resolve().parent.glob("*.py"))
        return fingerprint({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files})

    def _validate_gate_evidence(self, name: str, record: dict, context: str):
        """Validate operator-attested receipts; this is NOT cryptographic execution attestation."""
        now = self.clock()
        if not isinstance(record, dict):
            raise GateBlocked(name + ": structured evidence object required")
        if (record.get("schema_version") != 1 or record.get("gate") != name or record.get("context") != context or
                record.get("passed") is not True or record.get("implementation_sha256") != self.implementation_digest() or
                record.get("original_prompt_sha256") != hashlib.sha256(self.original_prompt.read_bytes()).hexdigest()):
            raise GateBlocked(name + ": evidence does not bind the gate, context, implementation and original prompt")
        checked_at = record.get("checked_at")
        valid_until = record.get("valid_until")
        if (type(checked_at) not in (int, float) or type(valid_until) not in (int, float) or
                not math.isfinite(checked_at) or not math.isfinite(valid_until) or
                checked_at > now + 5 or checked_at < now - 86400 or valid_until <= now or valid_until > checked_at + 86400):
            raise GateBlocked(name + ": evidence timeframe invalid or stale")
        mode = record.get("execution_mode")
        if not self.test_mode:
            allowed_modes = ("live", "offline") if name == "recovery_tests" else ("live",)
            if record.get("test_only") is not False or mode not in allowed_modes:
                raise GateBlocked(name + ": offline/simulated evidence is not a production readiness check")
        elif mode not in ("live", "offline", "simulated"):
            raise GateBlocked(name + ": explicit execution mode required")
        binding = record.get("runtime_binding", {})
        if (binding.get("site_url") != "https://sggroup.jp" or binding.get("execution_context") != context or
                type(binding.get("wordpress_identity_id")) is not int or binding["wordpress_identity_id"] <= 0 or
                not binding.get("connection_revision")):
            raise GateBlocked(name + ": actual site, identity and connection binding required")
        if self.runtime_binding is None or binding != self.runtime_binding:
            raise GateBlocked(name + ": configured current runtime identity/connection binding differs")
        receipts = record.get("receipts", [])
        if not receipts:
            raise GateBlocked(name + ": recorded tool/action receipts required")
        for receipt in receipts:
            try:
                if not receipt.get("tool") or not receipt.get("action") or hashlib.sha256(Path(receipt["path"]).read_bytes()).hexdigest() != receipt["sha256"]:
                    raise GateBlocked(name + ": action receipt missing or changed")
            except (KeyError, OSError) as exc:
                raise GateBlocked(name + ": action receipt unavailable") from exc
        m = record.get("measurements", {})
        required_true = {
            "runtime_authentication": ("authenticated", "can_create_draft", "can_publish", "verified_from_runtime"),
            "mandatory_html_preservation": ("style_preserved", "article_preserved", "exact_content_match"),
            "saved_preview": ("browser_rendered", "css_effect_verified", "no_theme_breakage"),
            "language_slug_metadata": ("existing_terms_verified", "shared_public_slug_verified", "metadata_roundtrip", "single_news_article_schema", "free_access_verified"),
            "execution_capacity": ("successful_complete_bilingual_run", "rate_limit_headroom_verified"),
            "recovery_tests": (),
        }[name]
        if any(m.get(key) is not True for key in required_true):
            raise GateBlocked(name + ": measured required outcomes have not passed")
        if name == "runtime_authentication" and (m.get("http_status") != 200 or m.get("identity_id") != binding["wordpress_identity_id"]):
            raise GateBlocked(name + ": live authenticated identity read did not pass")
        if name in ("mandatory_html_preservation", "saved_preview") and (type(m.get("probe_post_id")) is not int or m["probe_post_id"] <= 0):
            raise GateBlocked(name + ": actual unpublished saved probe required")
        if name == "mandatory_html_preservation" and (type(m.get("svg_used")) is not bool or (m["svg_used"] and m.get("svg_preserved") is not True)):
            raise GateBlocked(name + ": used SVG has not survived roundtrip")
        if name == "saved_preview" and m.get("http_status") != 200:
            raise GateBlocked(name + ": saved preview unavailable")
        if name == "language_slug_metadata" and (m.get("news_category_id") != 258 or any(type(m.get(k)) is not int or m[k] <= 0 for k in ("ja_term_id", "en_term_id"))):
            raise GateBlocked(name + ": existing category and language term identities required")
        if name == "execution_capacity":
            for key in ("quota_required", "quota_available", "execution_timeout_seconds", "observed_total_seconds"):
                if type(m.get(key)) not in (int, float) or not math.isfinite(m[key]) or m[key] <= 0:
                    raise GateBlocked(name + ": actual quota and execution measurements required")
            if m["quota_available"] < m["quota_required"] or m["execution_timeout_seconds"] < m["observed_total_seconds"]:
                raise GateBlocked(name + ": insufficient observed quota or execution capacity")
        if name == "recovery_tests":
            cases = ("dedup_cosmetic", "dedup_syndicated", "material_update", "retrieval_gap", "ambiguous_write", "ja_success_en_failure", "concurrency", "lease_expiration")
            if any(m.get("cases", {}).get(case) != "passed" for case in cases) or m.get("unresolved_failures") != 0:
                raise GateBlocked(name + ": required recovery cases have not passed")

    def record_gate(self, name: str, evidence: str | Path, context: str, ttl_seconds: float):
        if name not in CORE_GATES or not math.isfinite(ttl_seconds) or ttl_seconds <= 0 or not context:
            raise ValueError("invalid gate, context or lifetime")
        evidence = Path(evidence).resolve()
        raw = evidence.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        record = json.loads(raw)
        self._validate_gate_evidence(name, record, context)
        now = self.clock()
        with self.transaction() as db:
            db.execute("INSERT INTO gates VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                       "passed=excluded.passed,checked_at=excluded.checked_at,expires_at=excluded.expires_at,"
                       "context=excluded.context,evidence=excluded.evidence,evidence_sha256=excluded.evidence_sha256",
                       (name, 1, record["checked_at"], min(now + ttl_seconds, record["valid_until"]), context, str(evidence), digest))

    def gate_blockers(self, context: str, db=None) -> list[str]:
        if db is None:
            with self.connect() as connection:
                return self.gate_blockers(context, connection)
        gates = {row["name"]: dict(row) for row in db.execute("SELECT * FROM gates")}
        blockers = []
        for name in CORE_GATES:
            gate = gates.get(name)
            if not gate or not gate["passed"]:
                blockers.append(name + ": no passing executed evidence")
            elif gate["context"] != context:
                blockers.append(name + ": different runtime context")
            elif gate["expires_at"] <= self.clock():
                blockers.append(name + ": evidence expired")
            else:
                try:
                    digest = hashlib.sha256(Path(gate["evidence"]).read_bytes()).hexdigest()
                    if digest != gate["evidence_sha256"]:
                        blockers.append(name + ": evidence changed")
                    else:
                        self._validate_gate_evidence(name, json.loads(Path(gate["evidence"]).read_bytes()), context)
                except (OSError, ValueError, GateBlocked):
                    blockers.append(name + ": evidence unavailable")
        return blockers

    def require_ready(self, context: str, db=None):
        if db is None:
            with self.connect() as connection:
                return self.require_ready(context, connection)
        paused = json.loads(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0])
        if paused:
            reason = json.loads(db.execute("SELECT value FROM settings WHERE key='pause_reason'").fetchone()[0])
            raise GateBlocked("maintenance pause: " + reason)
        blockers = self.gate_blockers(context, db=db)
        if blockers:
            raise GateBlocked("; ".join(blockers))


    def _transport_scope(self, context: str):
        if (not isinstance(context, str) or not context
                or not self.test_mode and (not isinstance(self.runtime_binding, dict) or not self.runtime_binding)):
            raise StateError("exact runtime binding required for transport cooldown")
        return context, fingerprint(self.runtime_binding), canonical(self.runtime_binding)

    def transport_cooldown(self, context: str, db=None) -> dict | None:
        if db is None:
            with self.connect() as connection:
                return self.transport_cooldown(context, connection)
        scoped_context, digest, binding = self._transport_scope(context)
        row = db.execute("SELECT * FROM transport_cooldowns WHERE context=? AND binding_digest=?",
                         (scoped_context, digest)).fetchone()
        if row is None or row["retry_not_before"] <= self.clock():
            return None
        if row["runtime_binding"] != binding:
            raise StateError("transport cooldown binding mismatch")
        return {"context": scoped_context, "runtime_binding": self.runtime_binding,
                "retry_not_before": row["retry_not_before"], "recorded_at": row["recorded_at"],
                "error": json.loads(row["error"])}

    def require_transport_available(self, context: str, db=None):
        record = self.transport_cooldown(context, db)
        if record is not None:
            raise TransportCooldown(record)

    def record_transport_cooldown(self, context: str, error: dict, db=None) -> dict:
        """Commit a confirmed global outage without changing queue facts or gates."""
        if db is None:
            with self.transaction() as connection:
                return self.record_transport_cooldown(context, error, connection)
        scoped_context, digest, binding = self._transport_scope(context)
        if not isinstance(error, dict) or error.get("retryable") is not True:
            raise StateError("confirmed retryable transport error required")
        safe = {key: error.get(key) for key in ("type", "code", "http_status", "ambiguous_write", "retryable")}
        delay = error.get("retry_after_seconds")
        try:
            valid_delay = type(delay) in (int, float) and math.isfinite(delay) and delay > 0
        except OverflowError:
            valid_delay = False
        if valid_delay:
            safe["retry_after_seconds"] = delay
        else:
            delay = 300  # No immediate probe/replay when a deadline is absent or malformed.
        now = self.clock()
        deadline = now + delay
        if not math.isfinite(now) or not math.isfinite(deadline):
            delay, deadline = 300, now + 300
        if not math.isfinite(deadline):
            raise StateError("finite transport cooldown deadline required")
        previous = db.execute("SELECT retry_not_before FROM transport_cooldowns WHERE context=? AND binding_digest=?",
                              (scoped_context, digest)).fetchone()
        if previous:
            deadline = max(deadline, previous["retry_not_before"])
        db.execute("INSERT INTO transport_cooldowns VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(context,binding_digest) DO UPDATE SET runtime_binding=excluded.runtime_binding,"
                   "retry_not_before=excluded.retry_not_before,recorded_at=excluded.recorded_at,error=excluded.error",
                   (scoped_context, digest, binding, deadline, now, canonical(safe)))
        return {"context": scoped_context, "runtime_binding": self.runtime_binding,
                "retry_not_before": deadline, "recorded_at": now, "error": safe}


    @staticmethod
    def _private_bytes(path: str | Path) -> bytes:
        """Read bounded, user-private evidence without following a symlink."""
        path = Path(path).absolute()
        try:
            parent = path.parent.stat()
            if (parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700):
                raise GateBlocked("first publication evidence directory must be private")
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16 * 1024 * 1024):
                    raise GateBlocked("first publication evidence file must be bounded and private")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise GateBlocked("first publication evidence exceeds bound")
                return raw
            finally:
                os.close(fd)
        except OSError as exc:
            raise GateBlocked("first publication evidence unavailable") from exc

    @classmethod
    def _private_record(cls, path: str | Path) -> tuple[dict, str]:
        raw = cls._private_bytes(path)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        try:
            record = json.loads(raw, object_pairs_hook=pairs,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(record, dict):
                raise ValueError("object required")
        except (ValueError, UnicodeError) as exc:
            raise GateBlocked("first publication evidence must be exact JSON") from exc
        return record, hashlib.sha256(raw).hexdigest()

    def _first_scope(self, revision_id: int, digest: str, context: str, db) -> dict:
        revision = self.revision(revision_id, db)
        if (not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                or self.runtime_binding is None or self.runtime_binding.get("wordpress_identity_id") != 5
                or self.runtime_binding.get("site_url") != "https://sggroup.jp"
                or self.runtime_binding.get("execution_context") != context):
            raise GateBlocked("controlled first publication requires the fixed Author 5 runtime and full bilingual digest")
        return {"context": context, "runtime_binding": self.runtime_binding,
                "implementation_sha256": self.implementation_digest(),
                "connector_sha256": hashlib.sha256(Path(__file__).with_name("wpvibe_driver.js").read_bytes()).hexdigest(),
                "original_prompt_sha256": hashlib.sha256(self.original_prompt.read_bytes()).hexdigest(),
                "revision_id": revision_id, "revision_key": revision["revision_key"],
                "baseline_date": revision["baseline_date"], "event_key": revision["event_key"],
                "common_slug": revision["common_slug"], "bundle_digest": digest}

    def _first_receipt(self, record: dict, scope: dict, purpose: str, lifetime: int):
        now = self.clock()
        expected = {key: value for key, value in scope.items() if key not in ("connector_sha256", "stages", "revision_key", "baseline_date")}
        if (record.get("schema_version") != 1 or record.get("purpose") != purpose
                or any(record.get(key) != value for key, value in expected.items())
                or record.get("execution_mode") != "live" or record.get("test_only") is not False):
            raise GateBlocked("first publication receipt does not bind actual action and immutable scope")
        checked, until = record.get("checked_at"), record.get("valid_until")
        if (type(checked) not in (int, float) or type(until) not in (int, float)
                or not math.isfinite(checked) or not math.isfinite(until) or checked > now + 5
                or checked < now - lifetime or until <= now or until > checked + lifetime):
            raise GateBlocked("first publication receipt expired or timeframe invalid")
        receipts = record.get("receipts")
        if not isinstance(receipts, list) or not receipts:
            raise GateBlocked("first publication requires actual action receipts")
        for item in receipts:
            if (not isinstance(item, dict) or not item.get("tool") or not item.get("action")
                    or hashlib.sha256(self._private_bytes(item.get("path", ""))).hexdigest() != item.get("sha256")):
                raise GateBlocked("first publication action receipt unavailable or changed")

    def _first_plan(self, plan: dict, scope: dict) -> dict:
        from .evidence import WIDTHS, LANDSCAPES
        from .preflight_identity import (preflight_identities, validate_preflight_origin,
                                         validate_recovery_reconciliations)
        try:
            identity, active_identity = preflight_identities(plan)
            if "recovery" in plan:
                recovery = plan["recovery"]
                original, original_sha = self._private_record(recovery["origin_plan_path"])
                if original_sha != recovery["origin_plan_sha256"]:
                    raise GateBlocked("original bootstrap recovery journal changed")
                validate_preflight_origin(plan, original)
                validate_recovery_reconciliations(plan)
        except (ValueError, KeyError, TypeError) as exc:
            raise GateBlocked("preflight identity or reviewed bootstrap recovery is invalid") from exc
        if (active_identity["runtime_binding"] != scope["runtime_binding"] or active_identity["bundle_digest"] != scope["bundle_digest"]
                or active_identity["common_slug"] != scope["common_slug"] or plan.get("state") != "private_drafts_verified"
                or plan.get("publication_performed") is not False or plan.get("production_ready") is not False
                or plan.get("structural_validation", {}).get("passed") is not True
                or plan.get("structural_validation", {}).get("errors") != []):
            raise GateBlocked("complete unpublished exact-runtime preflight required")
        from datetime import datetime
        try:
            completed = datetime.fromisoformat(plan["completed_at"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError, TypeError) as exc:
            raise GateBlocked("preflight completion time unavailable") from exc
        if not math.isfinite(completed) or completed > self.clock() + 5 or completed < self.clock() - 3600:
            raise GateBlocked("actual private preview preflight is stale")
        payloads = plan.get("payloads", {})
        if set(payloads) != {"ja", "en"} or set(plan.get("operations", {})) != {"ja", "en"} or set(plan.get("previews", {})) != {"ja", "en"}:
            raise GateBlocked("complete bilingual preflight required")
        languages, stages = {}, {}
        for language in ("ja", "en"):
            payload = payloads[language]
            if (not isinstance(payload, dict) or set(payload) != {"language", "html", "metadata", "categories", "is_accessible_for_free", "status"}
                    or payload.get("language") != language or payload.get("categories") != [258]
                    or payload.get("is_accessible_for_free") is not True or payload.get("status") != "draft"
                    or not isinstance(payload.get("html"), str) or not payload["html"]
                    or not isinstance(payload.get("metadata"), dict) or set(payload["metadata"]) != {"title", "aioseo_title", "aioseo_description", "slug"}):
                raise GateBlocked("preflight payload differs from fixed unpublished scope")
            languages[language] = {"html": payload["html"], "metadata": payload["metadata"]}
            operation = plan["operations"][language]
            key = fingerprint({**identity, "language": language, "operation": "draft", "payload": payload})
            source = hashlib.sha256(payload["html"].encode()).hexdigest()
            if (not isinstance(operation, dict) or operation.get("key") != key or operation.get("status") != "saved"
                    or type(operation.get("post_id")) is not int or operation["post_id"] <= 0
                    or operation.get("source_sha256") != source or operation.get("metadata_sha256") != fingerprint(payload["metadata"])):
                raise GateBlocked("preflight saved operation identity incomplete")
            post = operation.get("saved_readback", {})
            if "recovery" in plan and operation.get("readback_runtime_binding") != active_identity["runtime_binding"]:
                raise GateBlocked("recovered CMS readback does not bind the current verified publisher")
            metadata = payload["metadata"]
            expected = {"id": operation["post_id"], "status": "draft", "language": language, "author": 5,
                        "kind": "stage", "idempotency_key": key, "source_sha256": source,
                        "seo_verified": True, "content_raw": payload["html"], "common_slug": scope["common_slug"],
                        "title": metadata.get("title"), "seo_title": metadata.get("aioseo_title"),
                        "meta_description": metadata.get("aioseo_description"), "categories": [258],
                        "is_accessible_for_free": True, "public_url": None}
            if any(post.get(k) != value for k, value in expected.items()):
                raise GateBlocked("preflight CMS readback differs from bilingual artifact")
            report = plan["previews"][language]
            if (report.get("passed") is not True or report.get("errors") != [] or report.get("execution_mode") != "live"
                    or report.get("test_only") is not False or report.get("actual_authenticated_preview") is not True
                    or report.get("actual_live_browser") is not False or report.get("wordpress_identity_id") != 5
                    or report.get("verification_context") != "authenticated_saved_theme_preview"
                    or report.get("post_id") != post["id"] or report.get("source_sha256") != source
                    or report.get("runtime_binding") != scope["runtime_binding"]):
                raise GateBlocked("actual authenticated saved-theme preview proof required")
            runs = report.get("runs", [])
            expected_runs = {(width, 900) for width in WIDTHS} | set(LANDSCAPES)
            if (not isinstance(runs, list) or len(runs) != len(expected_runs)
                    or any(not isinstance(run, dict) or not isinstance(run.get("measurement"), dict)
                           or run["measurement"].get("errors") != [] for run in runs)
                    or {(run.get("width"), run.get("height")) for run in runs} != expected_runs):
                raise GateBlocked("all actual 28 saved-preview viewport checks required")
            raw_report, report_sha = self._private_record(report.get("preview_report_path", ""))
            stripped = {k: v for k, v in report.items() if k not in ("preview_report_path", "preview_report_sha256")}
            snapshot = self._private_bytes(report.get("saved_theme_document_path", ""))
            if (report_sha != report.get("preview_report_sha256") or raw_report != stripped
                    or hashlib.sha256(snapshot).hexdigest() != report.get("saved_theme_document_sha256")
                    or payload["html"].encode() not in snapshot):
                raise GateBlocked("actual saved-theme browser artifacts changed")
            stages[language] = {"post_id": post["id"], "key": key, "source_sha256": source,
                                "metadata_sha256": operation["metadata_sha256"]}
        if stages["ja"]["post_id"] == stages["en"]["post_id"] or fingerprint(languages) != scope["bundle_digest"]:
            raise GateBlocked("preflight does not bind two distinct full bilingual stages")
        return stages

    def _first_rows(self, db, scope: dict, stages: dict):
        rid, event = scope["revision_id"], scope["event_key"]
        for row in db.execute("SELECT * FROM posts"):
            checkpoint = db.execute("SELECT * FROM checkpoints WHERE revision_id=? AND language=?", (rid, row["language"])).fetchone()
            expected_url = "https://sggroup.jp/" + row["language"] + "/article/news/" + scope["common_slug"] + "/"
            if (row["event_key"] != event or row["language"] not in stages or not checkpoint or not checkpoint["published"]
                    or checkpoint["public_post_id"] != row["post_id"] or row["public_url"] != expected_url):
                raise GateBlocked("prior unrelated public posts exclude controlled first publication")
        for row in db.execute("SELECT * FROM checkpoints"):
            if ((row["published"] or row["public_post_id"]) and row["revision_id"] != rid
                    or row["revision_id"] == rid and (row["language"] not in stages
                    or row["stage_post_id"] != stages[row["language"]]["post_id"])):
                raise GateBlocked("publication checkpoint differs from the sole permitted staged pair")
        revision = self.revision(rid, db)
        if revision["bundle_digest"] and revision["bundle_digest"] != scope["bundle_digest"]:
            raise GateBlocked("permitted full bilingual payload cannot change")
        for row in db.execute("SELECT * FROM operations"):
            if row["revision_id"] != rid:
                if row["status"] != "succeeded":
                    raise GateBlocked("unresolved unrelated mutation excludes first publication")
                continue
            language = row["language"]
            payload = {"stage_post_id": stages.get(language, {}).get("post_id"), "target_post_id": None,
                       "bundle_digest": scope["bundle_digest"], "status": "publish"}
            key = fingerprint({"event": event, "revision": revision["revision_key"], "language": language,
                               "stage": "publish", "payload": payload})
            if language not in stages or row["stage"] != "publish" or row["operation_key"] != key or row["payload_digest"] != fingerprint(payload):
                raise GateBlocked("operation journal contains an unrelated or unknown first-publication mutation")

    def _first_files(self, files: dict, scope: dict, db) -> dict:
        records = {}
        for name in ("plan", "authorization", "capacity"):
            record, digest = self._private_record(files[name]["path"])
            if digest != files[name]["sha256"]:
                raise GateBlocked("controlled first publication receipt changed")
            records[name] = record
        self._first_receipt(records["authorization"], scope, "user_authorized_controlled_first_publication", 3600)
        authorization = records["authorization"]
        if (authorization.get("source") != "user_message" or authorization.get("user_instruction") != "公開を開始すること"
                or not any(item.get("tool") == "user_message" for item in authorization["receipts"])):
            raise GateBlocked("actual user instruction for initial bilingual publication required")
        self._first_receipt(records["capacity"], scope, "controlled_first_publication_capacity", 600)
        stages = self._first_plan(records["plan"], scope)
        published = db.execute("SELECT count(*) FROM checkpoints WHERE revision_id=? AND published=1", (scope["revision_id"],)).fetchone()[0]
        minimum_calls = 16 + 7 * (2 - published)
        measurements = records["capacity"].get("measurements", {})
        for name in ("quota_available", "estimated_remaining_calls", "recovery_headroom"):
            if type(measurements.get(name)) is not int or measurements[name] <= 0:
                raise GateBlocked("actual remaining quota and recovery headroom required")
        if (measurements["estimated_remaining_calls"] < minimum_calls or measurements["recovery_headroom"] < 10
                or measurements["quota_available"] < measurements["estimated_remaining_calls"] + measurements["recovery_headroom"]):
            raise GateBlocked("insufficient actual quota for remaining pair and recovery")
        self._first_rows(db, scope, stages)
        blockers = [item for item in self.gate_blockers(scope["context"], db) if item.split(":", 1)[0] in FIRST_PREPUBLICATION_GATES]
        if blockers:
            raise GateBlocked("; ".join(blockers))
        paused = json.loads(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0])
        if paused is not True:
            raise GateBlocked("controlled first publication requires hourly maintenance pause to remain in place")
        return stages

    def record_first_publication_permit(self, revision_id: int, bundle_digest: str,
            preflight_plan_path: str | Path, authorization_receipt_path: str | Path,
            capacity_receipt_path: str | Path, context: str):
        """Record one explicit user's first pair; never promote gates or resume automation."""
        paths = {"plan": preflight_plan_path, "authorization": authorization_receipt_path, "capacity": capacity_receipt_path}
        files = {name: {"path": str(Path(path).absolute()), "sha256": self._private_record(path)[1]} for name, path in paths.items()}
        with self.transaction() as db:
            scope = self._first_scope(revision_id, bundle_digest, context, db)
            stages = self._first_files(files, scope, db)
            scope["stages"] = stages
            old = db.execute("SELECT * FROM first_publication_permit WHERE singleton=1").fetchone()
            if old:
                if json.loads(old["scope"]) != scope or old["status"] == "complete":
                    raise GateBlocked("first-publication permit is permanently bound to its sole revision and pair")
                lease = db.execute("SELECT * FROM leases WHERE event_key=?", (scope["event_key"],)).fetchone()
                if lease and lease["expires_at"] > self.clock():
                    raise LeaseLost("active first-publication attempt cannot refresh receipts")
            elif db.execute("SELECT 1 FROM posts LIMIT 1").fetchone() or db.execute("SELECT 1 FROM checkpoints WHERE published=1 LIMIT 1").fetchone():
                raise GateBlocked("controlled first publication cannot adopt preexisting public posts")
            if db.execute("SELECT 1 FROM first_publication_attempts WHERE capacity_sha256=?", (files["capacity"]["sha256"],)).fetchone():
                raise GateBlocked("fresh remaining-capacity receipt required for another attempt")
            db.execute("INSERT INTO first_publication_permit VALUES(1,?,?,?,'pending',NULL,NULL,?,?) "
                       "ON CONFLICT(singleton) DO UPDATE SET files=excluded.files,status='pending',owner=NULL,fence=NULL,updated_at=excluded.updated_at",
                       (revision_id, canonical(scope), canonical(files), self.clock(), self.clock()))
        return {"revision_id": revision_id, "bundle_digest": bundle_digest, "stages": stages,
                "hourly_automation_changed": False, "production_gates_changed": False}

    def require_first_publication_ready(self, revision_id: int, context: str, db=None, *, lease: dict | None = None) -> dict:
        if db is None:
            with self.connect() as connection:
                return self.require_first_publication_ready(revision_id, context, connection, lease=lease)
        self.require_transport_available(context, db)
        row = db.execute("SELECT * FROM first_publication_permit WHERE singleton=1").fetchone()
        if not row or row["revision_id"] != revision_id or row["status"] not in ("pending", "active"):
            raise GateBlocked("sole initial-publication permit unavailable")
        scope, files = json.loads(row["scope"]), json.loads(row["files"])
        current = self._first_scope(revision_id, scope["bundle_digest"], context, db)
        if any(scope.get(key) != value for key, value in current.items()):
            raise GateBlocked("initial-publication permit runtime, code or article scope changed")
        stages = self._first_files(files, scope, db)
        if stages != scope.get("stages"):
            raise GateBlocked("permitted staged pair changed")
        if lease is not None:
            self.assert_lease(db, lease)
            if (row["status"] != "active" or row["owner"] != lease["owner"] or row["fence"] != lease["fence"]):
                raise LeaseLost("controlled first-publication permit is not owned by this attempt")
        return {"scope": scope, "files": files}

    def begin_first_publication_attempt(self, lease: dict, context: str):
        with self.fenced(lease) as db:
            permit = self.require_first_publication_ready(lease["revision_id"], context, db)
            row = db.execute("SELECT * FROM first_publication_permit WHERE singleton=1").fetchone()
            if row["status"] != "pending":
                raise GateBlocked("fresh remaining-capacity receipt required after interruption")
            sha = permit["files"]["capacity"]["sha256"]
            if db.execute("SELECT 1 FROM first_publication_attempts WHERE capacity_sha256=?", (sha,)).fetchone():
                raise GateBlocked("remaining-capacity receipt already consumed")
            db.execute("INSERT INTO first_publication_attempts VALUES(?,?,?,?,?)", (sha, lease["revision_id"], lease["owner"], lease["fence"], self.clock()))
            db.execute("UPDATE first_publication_permit SET status='active',owner=?,fence=?,updated_at=? WHERE singleton=1", (lease["owner"], lease["fence"], self.clock()))

    def end_first_publication_attempt(self, lease: dict, complete: bool):
        with self.transaction() as db:
            db.execute("UPDATE first_publication_permit SET status=?,owner=NULL,fence=NULL,updated_at=? WHERE singleton=1 AND revision_id=? AND owner=? AND fence=?",
                       ("complete" if complete else "partial", self.clock(), lease["revision_id"], lease["owner"], lease["fence"]))

    def enqueue(self, semantic_identity: dict, material_facts: dict, headline: str,
                sources: list[dict], common_slug: str, baseline_date: str) -> tuple[int, bool]:
        """Caller supplies verified event identity/facts, not headline strings or retrieval metadata.

        Cosmetic/syndicated reporting differs only in headline/sources and is ignored.
        Same-event material development keeps the first baseline and reserved slug.
        """
        with self.transaction() as db:
            return self._enqueue(db, semantic_identity, material_facts, headline, sources, common_slug, baseline_date)

    def _enqueue(self, db, semantic_identity, material_facts, headline, sources, common_slug, baseline_date):
        if not isinstance(semantic_identity, dict) or not semantic_identity or not isinstance(material_facts, dict) or not material_facts or not sources:
            raise ValueError("verified keyed identity, keyed material facts and sources are required")
        event_key, revision_key = fingerprint(semantic_identity), fingerprint(material_facts)
        now = self.clock()
        event = db.execute("SELECT * FROM events WHERE event_key=?", (event_key,)).fetchone()
        if event is None:
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (event_key, canonical(semantic_identity),
                       common_slug, baseline_date, now))
        elif event["common_slug"] != common_slug or event["baseline_date"] != baseline_date:
            raise StateError("existing event must retain its slug and baseline date")
        existing = db.execute("SELECT id FROM revisions WHERE event_key=? AND revision_key=?",
                              (event_key, revision_key)).fetchone()
        if existing:
            return existing[0], False
        cur = db.execute("INSERT INTO revisions(event_key,revision_key,material_facts,headline,sources,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?,?)", (event_key, revision_key, canonical(material_facts), headline,
                         canonical(sources), now, now))
        return cur.lastrowid, True

    def acquire(self, revision_id: int, owner: str, ttl_seconds: float = 300) -> dict:
        if not owner or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("owner and positive lease lifetime required")
        now = self.clock()
        with self.transaction() as db:
            revision = db.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
            if not revision or revision["state"] in ("complete", "superseded_by_correction"):
                raise StateError("revision unavailable or already complete")
            older = db.execute("SELECT id FROM revisions WHERE event_key=? AND id<? AND state NOT IN ('complete','superseded_by_correction') LIMIT 1",
                               (revision["event_key"], revision_id)).fetchone()
            if older:
                raise StateError("earlier material revision must complete first")
            row = db.execute("SELECT * FROM leases WHERE event_key=?", (revision["event_key"],)).fetchone()
            if row and row["expires_at"] > now:
                raise LeaseLost("event already leased")
            fence = (row["fence"] if row else 0) + 1
            db.execute("INSERT INTO leases VALUES(?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET "
                       "owner=excluded.owner,fence=excluded.fence,expires_at=excluded.expires_at",
                       (revision["event_key"], owner, fence, now + ttl_seconds))
            return {"event_key": revision["event_key"], "revision_id": revision_id,
                    "owner": owner, "fence": fence}

    def enqueue_correction(self, revision_id: int, material_facts: dict, headline: str,
                           sources: list[dict], reason: str) -> int:
        """Supersede an unresolved public revision without declaring it complete.

        The correction remains on the same event/slug/date and reuses all known
        public post IDs. An ambiguous mutation must be reconciled first; an active
        lease cannot be superseded. Original checkpoint/errors remain for audit.
        """
        if not reason.strip() or not material_facts or not sources:
            raise ValueError("specific correction reason, full facts and sources required")
        with self.transaction() as db:
            old = self.revision(revision_id, db)
            if old["state"] != "needs_correction":
                raise StateError("only a publicly unresolved correction state may be superseded")
            published = db.execute("SELECT 1 FROM checkpoints WHERE revision_id=? AND published=1 LIMIT 1", (revision_id,)).fetchone()
            if not published:
                raise StateError("unpublished corrections should resubmit the existing bundle")
            uncertain = db.execute("SELECT 1 FROM operations WHERE revision_id=? AND status IN ('intent','ambiguous') LIMIT 1", (revision_id,)).fetchone()
            if uncertain:
                raise StateError("reconcile ambiguous writes before public correction")
            lease = db.execute("SELECT expires_at FROM leases WHERE event_key=?", (old["event_key"],)).fetchone()
            if lease and lease["expires_at"] > self.clock():
                raise LeaseLost("active worker must finish before public correction")
            event = db.execute("SELECT semantic_identity FROM events WHERE event_key=?", (old["event_key"],)).fetchone()
            corrected_facts = {"verified_facts": material_facts,
                               "public_correction": {"supersedes_revision_id": revision_id, "reason": reason}}
            new_id, created = self._enqueue(db, json.loads(event[0]), corrected_facts, headline, sources,
                                            old["common_slug"], old["baseline_date"])
            if not created:
                raise StateError("correction revision already exists")
            db.execute("UPDATE revisions SET supersedes_revision_id=?,correction_reason=? WHERE id=?", (revision_id, reason, new_id))
            db.execute("UPDATE revisions SET state='superseded_by_correction',updated_at=? WHERE id=?", (self.clock(), revision_id))
            return new_id

    def assert_lease(self, db, lease: dict):
        row = db.execute("SELECT * FROM leases WHERE event_key=?", (lease["event_key"],)).fetchone()
        if not row or row["owner"] != lease["owner"] or row["fence"] != lease["fence"] or row["expires_at"] <= self.clock():
            raise LeaseLost("expired or superseded lease")

    @contextmanager
    def fenced(self, lease: dict, renew_seconds: float = 300):
        if not math.isfinite(renew_seconds) or renew_seconds <= 0:
            raise ValueError("positive finite ownership lifetime required")
        with self.transaction() as db:
            self.assert_lease(db, lease)
            db.execute("UPDATE leases SET expires_at=? WHERE event_key=?", (self.clock() + renew_seconds, lease["event_key"]))
            yield db

    def release(self, lease: dict):
        # Keep the monotonically increasing fence; stale release cannot free a newer owner.
        with self.transaction() as db:
            db.execute("UPDATE leases SET expires_at=0 WHERE event_key=? AND owner=? AND fence=?",
                       (lease["event_key"], lease["owner"], lease["fence"]))

    def renew(self, lease: dict, ttl_seconds: float = 300):
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("positive finite renewal lifetime required")
        with self.fenced(lease, renew_seconds=ttl_seconds):
            pass

    def revision(self, revision_id: int, db=None) -> dict:
        if db is None:
            with self.connect() as connection:
                return self.revision(revision_id, connection)
        row = db.execute("SELECT r.*,e.common_slug,e.baseline_date FROM revisions r JOIN events e "
                         "ON e.event_key=r.event_key WHERE r.id=?", (revision_id,)).fetchone()
        if row is None:
            raise StateError("unknown revision")
        return dict(row)

    def register_source(self, source_key: str, initial_watermark: float):
        if not source_key or not math.isfinite(initial_watermark) or initial_watermark < 0:
            raise ValueError("source and finite initial coverage timestamp required")
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO sources(source_key,watermark) VALUES(?,?)", (source_key, initial_watermark))

    def record_scan(self, source_key: str, start: float, end: float, successful: bool, error: str | None = None):
        """Record coverage AFTER selected events are durable; prefer atomic ingest_scan.

        Success means complete retrieval/classification of this exact interval,
        not one successful HTTP response or a feed's latest page.
        """
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start or type(successful) is not bool or (not successful and not error):
            raise ValueError("invalid scan interval or missing failure reason")
        with self.transaction() as db:
            self._record_scan(db, source_key, start, end, successful, error)

    def _record_scan(self, db, source_key, start, end, successful, error):
        source = db.execute("SELECT * FROM sources WHERE source_key=?", (source_key,)).fetchone()
        if not source:
            raise StateError("register source before scanning")
        db.execute("INSERT INTO scans(source_key,start,end,successful,error,recorded_at) VALUES(?,?,?,?,?,?)",
                   (source_key, start, end, int(successful), error, self.clock()))
        watermark = source["watermark"]
        for interval in db.execute("SELECT start,end FROM scans WHERE source_key=? AND successful=1 ORDER BY start,end",
                                   (source_key,)):
            if interval["start"] <= watermark:
                watermark = max(watermark, interval["end"])
            else:
                break
        db.execute("UPDATE sources SET watermark=?,last_attempt_end=MAX(COALESCE(last_attempt_end,?),?),last_error=? "
                   "WHERE source_key=?", (watermark, end, end, error, source_key))

    def ingest_scan(self, source_key: str, start: float, end: float, events: list[dict],
                    successful: bool, error: str | None = None) -> list[tuple[int, bool]]:
        """Commit all selected events and scan coverage together, with no event cap.

        A partial retrieval can enqueue verified discoveries but records a failed
        interval, so the missed portion is still fetched next time.
        """
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start or type(successful) is not bool or (not successful and not error):
            raise ValueError("invalid scan interval or missing failure reason")
        with self.transaction() as db:
            queued = [self._enqueue(db, **event) for event in events]
            self._record_scan(db, source_key, start, end, successful, error)
            return queued

    def status(self, context: str) -> dict:
        with self.connect() as db:
            settings = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM settings")}
            queue = [dict(r) for r in db.execute("SELECT id,event_key,revision_key,state,retry_stage,last_error,updated_at,supersedes_revision_id,correction_reason "
                                                "FROM revisions ORDER BY id")]
            sources = [dict(r) for r in db.execute("SELECT *,CASE WHEN last_attempt_end>watermark THEN 1 ELSE 0 END AS retrieval_gap "
                                                  "FROM sources ORDER BY source_key")]
            posts = [dict(r) for r in db.execute("SELECT * FROM posts ORDER BY event_key,language")]
            operations = [dict(r) for r in db.execute("SELECT operation_key,revision_id,language,stage,status,error,updated_at "
                                                      "FROM operations WHERE status!='succeeded' ORDER BY updated_at")]
            cooldown = self.transport_cooldown(context, db) if self.runtime_binding is not None or self.test_mode else None
            return {"storage_scope": "one persistent SQLite workspace; all workers must share this file",
                    "database": str(self.path.resolve()), "context": context, **settings,
                    "gate_blockers": self.gate_blockers(context, db), "queue": queue,
                    "sources": sources, "posts": posts, "unresolved_operations": operations,
                    "transport_cooldown": cooldown,
                    "ready_for_workflow": not settings["paused"] and not self.gate_blockers(context, db) and cooldown is None,
                    "hourly_schedule_readiness": "requires separately verified controlled publication and actual schedule execution"}
