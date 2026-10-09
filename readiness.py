#!/usr/bin/env python3
"""Report verified implementation and live blockers; never enable production.

This is a local diagnostic for the intended persistent worker workspace. An
offline test pass does not establish a usable remote publishing route. Live
readiness is determined by the context-bound gates in runtime.state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from runtime.state import StateStore


ROOT = Path(__file__).resolve().parent


def status(root: Path, context: str, database: Path, runtime_binding: dict | None = None) -> dict:
    goals = json.loads((root / "goals.json").read_text(encoding="utf-8"))
    prompt = root / goals["authoritative_prompt"]
    prompt_intact = hashlib.sha256(prompt.read_bytes()).hexdigest() == goals["authoritative_prompt_sha256"]
    store = StateStore(database, original_prompt=prompt, runtime_binding=runtime_binding)
    worker = store.status(context)
    auth_path = root / "runtime/runtime-auth-diagnostic.json"
    diagnostic = json.loads(auth_path.read_text(encoding="utf-8")) if auth_path.exists() else None
    implementation = {}
    for component, path in {
        "durable_queue": "runtime/state.py",
        "publication_recovery": "runtime/workflow.py",
        "wordpress_transport": "runtime/wordpress_transport.py",
        "scoped_wordpress_adapter": "runtime/wordpress_publisher_adapter.py",
        "publication_entrypoint": "runtime/publish.py",
        "scoped_wordpress_integration": "wordpress/sggroup-news-publisher.php",
        "article_static_qa": "runtime/article_qa.py",
        "copy_viewer": "runtime/copy_viewer.py",
        "browser_qa": "runtime/browser_qa.py",
        "article_evidence": "runtime/evidence.py",
    }.items():
        source = root / path
        implementation[component] = {
            "path": path,
            "source_present": source.is_file(),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None,
        }
    blockers = list(store.gate_blockers(context))
    if not prompt_intact:
        blockers.insert(0, "authoritative editorial prompt differs from the original")
    if runtime_binding is None:
        blockers.append("current authenticated site/account/connection binding has not been supplied")
    for component, source in implementation.items():
        if not source["source_present"]:
            blockers.append(component + ": implementation source unavailable")
    if worker["paused"]:
        blockers.append("publication workflow remains paused")
    # The transport diagnostic is informational; only current context-bound,
    # executed gate receipts can establish authenticated publishing readiness.
    # Local PHP source presence never establishes an installed remote route.
    workflow_ready = not blockers and worker["ready_for_workflow"]
    return {
        "objective": goals["objective"],
        "context": context,
        "authoritative_prompt_intact": prompt_intact,
        "implementation": implementation,
        "direct_auth_diagnostic": diagnostic,
        "worker": worker,
        "blockers": blockers,
        "publication_workflow_ready": workflow_ready,
        "hourly_operation_complete": False,
        "hourly_operation_evidence": "Requires separate controlled bilingual publication and first actual scheduled execution; this report never certifies either.",
        "does_not_enable_scheduling": True,
        "single_workspace_sqlite_scope": "All workers must share this persistent SQLite database. It is not a cross-host lock or a backup.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Actual execution environment/site/identity context; never use a test fixture for production")
    parser.add_argument("--database", type=Path, default=ROOT / "state/queue.sqlite3")
    parser.add_argument("--runtime-binding", type=Path, help="Current authenticated site/account/connection binding, independently verified by the adapter")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        binding = json.loads(args.runtime_binding.read_text(encoding="utf-8")) if args.runtime_binding else None
        report = status(ROOT, args.context, args.database, runtime_binding=binding)
    except (OSError, ValueError) as error:
        print(json.dumps({"publication_workflow_ready": False, "hourly_operation_complete": False, "error": type(error).__name__}))
        return 1
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["publication_workflow_ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
