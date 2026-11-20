"""Top-level operation manifest.

Written only after the entire pipeline (including post-modification
verification) has succeeded. Its presence certifies that this run
completed successfully.

Path:  <backup_root>/operation.complete.json

Distinct from <backup_root>/operation.json, which is written by the backup
phase and means only "backup is complete and verified".
"""

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import backup as backup_mod


SCHEMA_VERSION = 1
FILENAME = "operation.complete.json"


class ManifestError(Exception):
    """Raised when the completion manifest cannot be written."""


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def build_payload(
    *,
    scope_roots: List,                # [(name, Path)]
    config: Dict[str, Any],
    scan_results: List,
    analysis,
    modified: Dict[str, List[str]],
    plan: backup_mod.BackupPlan,
    verification_report,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    by_name = {sr.root_name: sr for sr in scan_results}
    roots_block: List[Dict[str, Any]] = []

    for name, path in scope_roots:
        sr = by_name[name]
        ra = analysis.roots[name]
        mod_rels = modified.get(name, [])
        roots_block.append({
            "name": name,
            "path": str(path),
            "directories_discovered": ra.directories_discovered,
            "files_discovered": ra.files_discovered,
            "files_eligible": ra.files_eligible,
            "files_skipped": ra.files_skipped,
            "files_unreadable": ra.files_unreadable,
            "symlinks_skipped": ra.symlinks_skipped,
            "files_affected": len(ra.files_affected),
            "files_modified": len(mod_rels),
            "files_emptied": len(ra.files_emptied),
            "entries_before": ra.total_entries,
            "entries_after": ra.unique_entries,
            "duplicates_removed": ra.duplicates,
            "internal_duplicates": ra.internal_duplicates,
            "cross_file_duplicates": ra.cross_file_duplicates,
            "empty_lines_kept": ra.empty_lines_kept,
            "empty_lines_dropped": ra.empty_lines_dropped,
        })

    totals = {
        "roots": len(roots_block),
        "files_discovered": sum(r["files_discovered"] for r in roots_block),
        "files_eligible": sum(r["files_eligible"] for r in roots_block),
        "files_skipped": sum(r["files_skipped"] for r in roots_block),
        "files_unreadable": sum(r["files_unreadable"] for r in roots_block),
        "files_modified": sum(r["files_modified"] for r in roots_block),
        "entries_before": sum(r["entries_before"] for r in roots_block),
        "entries_after": sum(r["entries_after"] for r in roots_block),
        "duplicates_removed": sum(r["duplicates_removed"] for r in roots_block),
        "internal_duplicates": sum(r["internal_duplicates"] for r in roots_block),
        "cross_file_duplicates": sum(r["cross_file_duplicates"] for r in roots_block),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": "operation_complete",
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "config": {
            "empty_mode": config.get("empty_mode"),
            "extensions": sorted(config.get("extensions") or []),
            "follow_symlinks": bool(config.get("follow_symlinks")),
            "max_line_bytes": int(config.get("max_line_bytes") or 0),
        },
        "backup_location": str(plan.root),
        "backup_stamp": plan.stamp,
        "verification_status": (
            "passed" if verification_report.passed else "failed"),
        "roots": roots_block,
        "totals": totals,
        "warnings": list(warnings or []),
        "note": (
            "Totals are arithmetic sums of independent per-root statistics. "
            "An entry appearing in more than one root is NOT a duplicate and "
            "is NOT counted as one."),
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_manifest(plan: backup_mod.BackupPlan, payload: Dict[str, Any]) -> Path:
    path = plan.root / FILENAME
    try:
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        # Make the manifest itself durable.
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        backup_mod._fsync_dir(plan.root)
    except OSError as e:
        raise ManifestError(f"Cannot write {path}: {e}")
    return path


# ---------------------------------------------------------------------------
# Convenience: gather warnings from scan results
# ---------------------------------------------------------------------------

def collect_warnings(scan_results: List) -> List[str]:
    warnings: List[str] = []
    for sr in scan_results:
        if sr.unreadable:
            warnings.append(
                f"root {sr.root_name!r}: {len(sr.unreadable)} unreadable "
                f"file(s) were skipped during scan")
        if sr.symlinks:
            warnings.append(
                f"root {sr.root_name!r}: {len(sr.symlinks)} symlink(s) "
                f"were skipped (follow_symlinks is off)")
        tool_temp = [i for i in sr.issues if i.kind == "tool_temp_file"]
        if tool_temp:
            warnings.append(
                f"root {sr.root_name!r}: {len(tool_temp)} leftover temp "
                f"file(s) were detected and skipped")
    return warnings