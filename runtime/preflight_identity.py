"""Pure validation for the one reviewed, bootstrap-only preflight recovery.

The original identity continues to derive every operation key. Recovery changes
only the code binding used for fresh CMS readback and actual browser proof.
Filesystem permissions, exact snapshot bytes and live operation reconciliation
are verified by the callers, never inferred from these records alone.
"""

from datetime import datetime
import hashlib
import json
import re


BOOTSTRAP_ORIGIN_SHA256 = "fd8ade39ee4b22352036bc3a6fc85bc86dbca580d036a1e2acb0ac4db5cf9ce8"
BOOTSTRAP_FIXED_SHA256 = "22656f369556a169bb048356bfb0f33bf02c77cb7b97d9c0ca3d9d1f11c42668"
RECOVERY_PURPOSE = "reviewed_aioseo_json_bootstrap_recovery"
IDENTITY_FIELDS = {"schema_version", "purpose", "runtime_binding", "bundle_digest", "common_slug"}
BINDING_FIELDS = {"site_url", "execution_context", "wordpress_identity_id", "connection_revision",
                  "publisher_policy_version", "publisher_code_sha256"}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _time(value):
    if not isinstance(value, str):
        raise ValueError("invalid_recovery_time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("invalid_recovery_time")


def _identity(value):
    if (not isinstance(value, dict) or set(value) != IDENTITY_FIELDS
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("purpose") != "controlled_private_bilingual_draft_preflight"
            or not _sha(value.get("bundle_digest"))
            or not isinstance(value.get("common_slug"), str) or not value["common_slug"]):
        raise ValueError("invalid_preflight_identity")
    binding = value.get("runtime_binding")
    if (not isinstance(binding, dict) or set(binding) != BINDING_FIELDS
            or binding.get("site_url") != "https://sggroup.jp"
            or type(binding.get("wordpress_identity_id")) is not int or binding["wordpress_identity_id"] != 5
            or binding.get("publisher_policy_version") != "sgnews-restricted-xml-css-1"
            or not _sha(binding.get("publisher_code_sha256"))
            or any(not isinstance(binding.get(field), str) or not binding[field]
                   for field in ("execution_context", "connection_revision"))):
        raise ValueError("invalid_preflight_binding")


def preflight_identities(plan):
    """Return (immutable operation identity, current verification identity)."""
    if not isinstance(plan, dict):
        raise ValueError("invalid_preflight_plan")
    origin = plan.get("identity")
    _identity(origin)
    payloads, operations = plan.get("payloads"), plan.get("operations")
    if (not isinstance(payloads, dict) or set(payloads) != {"ja", "en"}
            or not isinstance(operations, dict) or set(operations) != {"ja", "en"}):
        raise ValueError("invalid_preflight_operations")
    keys = {language: _fingerprint({**origin, "language": language, "operation": "draft", "payload": payloads[language]})
            for language in ("ja", "en")}
    if any(not isinstance(operations[language], dict) or operations[language].get("key") != keys[language]
           for language in ("ja", "en")):
        raise ValueError("invalid_preflight_operation_key")
    if "recovery" not in plan:
        if "recovery_reconciliations" in plan:
            raise ValueError("unbound_recovery_reconciliations")
        return origin, origin
    recovery = plan["recovery"]
    if (not isinstance(recovery, dict) or set(recovery) != {"schema_version", "purpose", "identity", "origin_plan_path",
            "origin_plan_sha256", "payloads_sha256", "operation_keys", "created_at"}
            or type(recovery.get("schema_version")) is not int or recovery["schema_version"] != 1
            or recovery.get("purpose") != RECOVERY_PURPOSE
            or not isinstance(recovery.get("origin_plan_path"), str) or not recovery["origin_plan_path"].startswith("/")
            or not _sha(recovery.get("origin_plan_sha256"))
            or recovery.get("payloads_sha256") != _fingerprint(payloads)
            or recovery.get("operation_keys") != keys):
        raise ValueError("invalid_reviewed_preflight_recovery")
    active = recovery["identity"]
    _identity(active)
    expected = {**origin, "runtime_binding": {**origin["runtime_binding"], "publisher_code_sha256": BOOTSTRAP_FIXED_SHA256}}
    if (origin["runtime_binding"]["publisher_code_sha256"] != BOOTSTRAP_ORIGIN_SHA256 or active != expected):
        raise ValueError("unreviewed_preflight_binding_transition")
    _time(recovery["created_at"])
    return origin, active


def validate_preflight_origin(plan, original_plan):
    """Require the exact original unpublished bootstrap-409 journal scope."""
    origin, _ = preflight_identities(plan)
    original_identity, original_active = preflight_identities(original_plan)
    if (original_identity != origin or original_active != origin or "recovery" in original_plan
            or original_plan.get("payloads") != plan.get("payloads")
            or original_plan.get("state") != "blocked"
            or original_plan.get("publication_performed") is not False
            or original_plan.get("production_ready") is not False
            or original_plan.get("previews") != {}):
        raise ValueError("invalid_bootstrap_recovery_origin")
    error = original_plan.get("last_error", {})
    ja, en = original_plan["operations"]["ja"], original_plan["operations"]["en"]
    if (error.get("code") != "wpvibe_tool_error" or error.get("http_status") != 409
            or error.get("ambiguous_write") is not False or ja.get("error") != error
            or ja.get("status") != "rejected" or ja.get("post_id") is not None
            or en.get("status") != "planned" or en.get("post_id") is not None):
        raise ValueError("bootstrap_409_origin_required")


def validate_recovery_reconciliations(plan, *, require_complete=True):
    origin, active = preflight_identities(plan)
    if "recovery" not in plan:
        return
    records = plan.get("recovery_reconciliations", {})
    if (not isinstance(records, dict) or not set(records) <= {"ja", "en"}
            or require_complete and set(records) != {"ja", "en"}):
        raise ValueError("both_recovery_reconciliations_required")
    for language, record in records.items():
        payload = plan["payloads"][language]
        outcome = record.get("outcome") if isinstance(record, dict) else None
        if (not isinstance(record, dict) or set(record) != {"key", "runtime_binding", "outcome", "post_id", "checked_at",
                                                          "source_sha256", "metadata_sha256"}
                or record.get("key") != plan["operations"][language]["key"]
                or record.get("runtime_binding") != active["runtime_binding"]
                or outcome not in {"complete", "safe_to_resume", "definitive_absence"}
                or record.get("source_sha256") != hashlib.sha256(payload["html"].encode()).hexdigest()
                or record.get("metadata_sha256") != _fingerprint(payload["metadata"])):
            raise ValueError("invalid_recovery_reconciliation")
        post_id = record.get("post_id")
        if ((outcome == "complete" and (type(post_id) is not int or post_id <= 0))
                or (outcome != "complete" and post_id is not None)
                or outcome == "complete" and plan["operations"][language].get("post_id") != post_id):
            raise ValueError("invalid_recovery_reconciliation_id")
        _time(record["checked_at"])
