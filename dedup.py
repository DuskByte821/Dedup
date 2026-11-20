#!/usr/bin/env python3
"""Recursive Wordlist Deduplication Tool — CLI.

Normal pipeline::

    DISCOVER -> ANALYZE -> REPORT -> DISK CHECK -> CONFIRM
        -> BACKUP -> VERIFY BACKUP -> MODIFY
        -> VERIFY OPERATION -> WRITE MANIFEST -> SUCCESS

Resume pipeline (``--resume``)::

    FIND BACKUP -> VALIDATE BACKUP -> CHECK CONFIG
        -> RE-SCAN -> RECONSTRUCT ORIGINAL ANALYSIS
        -> REPORT -> CONFIRM -> MODIFY REMAINING
        -> VERIFY AGAINST ORIGINAL -> WRITE MANIFEST -> SUCCESS

Exit codes:
  0   success (verified)
  1   unexpected error
  2   safety refusal
  6   verification failed
  130 interrupted by Ctrl+C

There is no ``--yes`` flag. Interactive ``y``/``yes`` is the only way past
the gate.
"""

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from dedup import (
    safety, scanner, analyzer, reporter, confirm, backup,
    processor, verifier, manifest, resume as resume_mod,
)
from dedup.analyzer import DEFAULT_MAX_LINE_BYTES
from dedup.index import Index, IndexError_
from dedup.scanner import DEFAULT_EXTENSIONS, EligibleFile, ScanResult
from dedup.safety import format_bytes


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SAFETY = 2
EXIT_INTERRUPTED = 130
EXIT_VERIFICATION_FAILED = 6


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

class Progress:
    def __init__(self, json_mode: bool = False, verbose: bool = False,
                 stream=None):
        self.json = json_mode
        self.verbose = verbose
        self.stream = stream if stream is not None else sys.stderr

    def _emit(self, event: str, message=None, **fields) -> None:
        if self.json:
            payload = {"event": event}
            if message is not None:
                payload["message"] = message
            payload.update(fields)
            self.stream.write(json.dumps(payload, default=str) + "\n")
        elif message is not None:
            self.stream.write(message + "\n")
        self.stream.flush()

    def info(self, message: str, event: str = "info", **fields) -> None:
        self._emit(event, message=message, **fields)

    def per_file(self, message: str, event: str, **fields) -> None:
        if self.verbose or self.json:
            self._emit(event, message=message, **fields)

    def error(self, message: str, event: str = "error", **fields) -> None:
        if self.json:
            self._emit(event, message=message, **fields)
        else:
            self.stream.write(message + "\n")
            self.stream.flush()

    def success_event(self, backup_path, manifest_path) -> None:
        self._emit("success", backup=str(backup_path),
                   manifest=str(manifest_path))


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="dedup.py",
        description=(
            "Safely detect and remove duplicate lines from a wordlist "
            "collection. Duplicates are computed per root; roots are "
            "independent namespaces."),
    )
    p.add_argument("path", nargs="?", default=None,
                   help="Single-root shorthand. Mutually exclusive with --root.")
    p.add_argument("--root", action="append", metavar="NAME=PATH", default=None,
                   help="Named root (repeatable).")
    p.add_argument("--dry-run", action="store_true",
                   help="Scan, analyze, report; never modify.")
    p.add_argument("--resume", action="store_true",
                   help="Resume the most recent incomplete backup. "
                        "Requires the same roots/config as the original run.")
    p.add_argument("--resume-from", default=None, metavar="PATH",
                   help="Resume from a specific incomplete backup directory.")
    p.add_argument("--extensions", default=None,
                   help="Comma-separated extensions. "
                        "Default: " + ",".join(sorted(DEFAULT_EXTENSIONS)))
    p.add_argument("--empty-lines", choices=("keep", "dedup", "drop"),
                   default="keep", help="Empty-line policy. Default: keep.")
    p.add_argument("--follow-symlinks", action="store_true",
                   help="Follow symlinks (default: no).")
    p.add_argument("--max-line-bytes", type=int,
                   default=DEFAULT_MAX_LINE_BYTES,
                   help=f"Abort if any line exceeds this many bytes. "
                        f"Default: {DEFAULT_MAX_LINE_BYTES:,}")
    p.add_argument("--work-dir", default=None,
                   help="SQLite index location (default: system temp).")
    p.add_argument("--backup-dir", default=None,
                   help="Backup location. Default: "
                        "'<parent of first root>/.dedup-backups'.")
    p.add_argument("--json-progress", action="store_true",
                   help="Emit progress events as JSON lines on stderr.")
    p.add_argument("--i-know-what-im-doing", action="store_true",
                   help="Allow dangerous scopes like / or ~.")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------

def _parse_extensions(s):
    if s is None:
        return None
    out = set()
    for raw in s.split(","):
        e = raw.strip()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        out.add(e.lower())
    return out or None


def resolve_roots(args):
    if args.root and args.path:
        raise safety.SafetyError(
            "Cannot combine a positional path with --root.")
    roots = []
    if args.root:
        seen = set()
        for spec in args.root:
            if "=" not in spec:
                raise safety.SafetyError(
                    f"--root must be NAME=PATH, got: {spec!r}")
            name, _, path = spec.partition("=")
            name, path = name.strip(), path.strip()
            if not name or not path:
                raise safety.SafetyError(
                    f"--root must be NAME=PATH, got: {spec!r}")
            safety.validate_root_name(name)
            if name in seen:
                raise safety.SafetyError(f"Duplicate root name: {name!r}")
            seen.add(name)
            roots.append((name, safety.resolve_root_path(
                path, name, allow_dangerous=args.i_know_what_im_doing)))
    else:
        if args.path is None:
            raise safety.SafetyError(
                "No roots specified. Provide --root NAME=PATH or a path.")
        resolved = safety.resolve_root_path(
            args.path, "root", allow_dangerous=args.i_know_what_im_doing)
        roots.append((safety.derive_root_name(resolved), resolved))
    safety.check_root_overlap(roots)
    return roots


def _build_config(args):
    extensions = _parse_extensions(args.extensions) or set(DEFAULT_EXTENSIONS)
    return {
        "empty_mode": args.empty_lines,
        "extensions": sorted(extensions),
        "follow_symlinks": bool(args.follow_symlinks),
        "max_line_bytes": int(args.max_line_bytes),
    }, extensions


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def _bytes_removed(analysis) -> int:
    total = 0
    for ra in analysis.roots.values():
        for rel in ra.files_affected:
            fa = ra.per_file.get(rel)
            if fa is None:
                continue
            delta = fa.bytes_before - fa.bytes_after
            if delta > 0:
                total += delta
    return total


def _confirmation_gate(scan_results, analysis, plan, *,
                       input_fn=input, output_fn=None) -> bool:
    out = output_fn if output_fn is not None else sys.stderr.write
    out("\n" + "=" * 40 + "\n")
    out("CONFIRMATION REQUIRED\n")
    out("=" * 40 + "\n\n")
    out("  Files affected:\n")
    for sr in scan_results:
        ra = analysis.roots[sr.root_name]
        out(f"    {sr.root_name}: {len(ra.files_affected)}\n")
    out(f"  Total: {analysis.total_files_affected}\n\n")
    out(f"  Estimated bytes removed: "
        f"{format_bytes(_bytes_removed(analysis))}\n")
    out(f"  Backup will be created at:\n    {plan.root}\n")
    out(f"  Backup size: {format_bytes(plan.total_bytes)}\n\n")
    out("A verified backup is created before any original file is modified.\n\n")
    return confirm.confirm(
        "Proceed with deduplication? [y/N]: ",
        input_fn=input_fn, output_fn=out,
    )


# ---------------------------------------------------------------------------
# Normal pipeline
# ---------------------------------------------------------------------------

def _run_normal(args, prog, confirm_input_fn) -> int:
    try:
        roots = resolve_roots(args)
    except safety.SafetyError as e:
        prog.error(f"Error: {e}")
        return EXIT_SAFETY

    config, extensions = _build_config(args)

    scan_results = []
    for name, path in roots:
        prog.info(f"Discovering {name} ({path}) ...",
                  event="scan_start", root=name, path=str(path))
        try:
            sr = scanner.scan(name, path, extensions=extensions,
                              follow_symlinks=args.follow_symlinks)
        except Exception as e:
            prog.error(f"Error during scan of {name!r}: {e}")
            return EXIT_ERROR
        prog.info(
            f"  {len(sr.eligible):,} eligible, {len(sr.skipped):,} skipped, "
            f"{len(sr.unreadable):,} unreadable, {len(sr.symlinks):,} symlinks",
            event="scan_end", root=name,
            eligible=len(sr.eligible), skipped=len(sr.skipped),
            unreadable=len(sr.unreadable), symlinks=len(sr.symlinks))
        scan_results.append(sr)

    if args.work_dir:
        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup_workdir = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="wordlist-dedup-"))
        cleanup_workdir = True
    index_path = work_dir / "index.db"

    index = None
    try:
        prog.info(f"Analyzing (index: {index_path}) ...",
                  event="analyze_start", index_path=str(index_path))
        index = Index(index_path)

        def _analyze_progress(root_name, rel_path, i, total):
            prog.per_file(
                f"  [{root_name}] {i}/{total} {rel_path}",
                event="analyze_file", root=root_name,
                file=rel_path, i=i, n=total)

        try:
            analysis = analyzer.analyze_roots(
                scan_results, index,
                empty_mode=args.empty_lines,
                max_line_bytes=args.max_line_bytes,
                progress=_analyze_progress,
            )
        except (analyzer.AnalysisError, IndexError_) as e:
            prog.error(f"Error during analysis: {e}")
            return EXIT_ERROR

        reporter.print_report(scan_results, analysis, config,
                              dry_run=args.dry_run)

        if args.dry_run:
            return EXIT_OK

        if not analysis.any_duplicates():
            prog.info("No duplicates found. Nothing to do.")
            return EXIT_OK

        try:
            backup_home = safety.resolve_backup_home(
                args.backup_dir, roots, create=True)
        except safety.SafetyError as e:
            prog.error(f"Error: {e}")
            return EXIT_SAFETY

        # Advisory: mention any incomplete backups under this home.
        strays = resume_mod.find_incomplete_backups(backup_home)
        if strays:
            prog.info(
                f"Note: {len(strays)} incomplete backup(s) exist under "
                f"{backup_home}. To resume one, use --resume "
                f"(see --resume-from).",
                event="incomplete_backup_notice",
                count=len(strays))

        plan = backup.plan_backup(backup_home, scan_results, analysis,
                                  config=config)
        if not plan.files:
            prog.error("Backup plan is empty. Nothing to do.")
            return EXIT_ERROR

        try:
            safety.check_backup_disk_space(
                backup_home, roots,
                backup_bytes=plan.total_bytes,
                max_file_bytes=plan.max_file_bytes,
            )
        except safety.SafetyError as e:
            prog.error(f"Error: {e}")
            prog.error("No files have been modified.")
            return EXIT_SAFETY

        try:
            proceed = _confirmation_gate(scan_results, analysis, plan,
                                         input_fn=confirm_input_fn,
                                         output_fn=(prog.stream.write
                                                    if prog.json else None))
        except KeyboardInterrupt:
            raise
        if not proceed:
            prog.info("Cancelled. No files have been modified.")
            return EXIT_OK

        prog.info(f"Creating backup at {plan.root} ...",
                  event="backup_start", path=str(plan.root))

        def _backup_progress(p, i, total, bf):
            prog.per_file(
                f"  backup [{p.stamp}] {i}/{total} "
                f"{bf.root_name}/{bf.rel_path}",
                event="backup_file", root=bf.root_name,
                file=bf.rel_path, i=i, n=total, stamp=p.stamp)

        try:
            backup.execute_backup(plan, progress=_backup_progress)
            prog.info(
                f"  Copied {len(plan.files):,} file(s), "
                f"{format_bytes(plan.total_bytes)}",
                event="backup_copied", files=len(plan.files),
                bytes=plan.total_bytes)
            prog.info("Verifying backup ...", event="backup_verify_start")
            backup.verify_backup(plan, progress=_backup_progress)
            manifest_path = backup.write_operation_json(plan)
            prog.info(f"  Manifest: {manifest_path}",
                      event="backup_manifest", path=str(manifest_path))
        except backup.BackupError as e:
            prog.error("Backup failed.")
            prog.error("Deduplication cancelled.")
            prog.error("No original files were modified.")
            prog.error(f"  {e}")
            if plan.root.exists():
                prog.error(f"  Partial backup at {plan.root} "
                           f"(no operation.json => incomplete, do not use).")
            return EXIT_ERROR

        return _modify_verify_manifest(
            args, prog, roots, config, extensions,
            scan_results, analysis, index, plan,
            previous_modified=None,
        )

    finally:
        if index is not None:
            try:
                index.close()
            except Exception:
                pass
        if cleanup_workdir:
            try:
                for p in work_dir.iterdir():
                    p.unlink()
                work_dir.rmdir()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Shared tail: modify + verify + manifest
# ---------------------------------------------------------------------------

def _modify_verify_manifest(
    args, prog, roots, config, extensions,
    scan_results,            # live scans for the current state
    analysis,                # ORIGINAL analysis (what should happen)
    index,                   # index backing `analysis`
    plan,                    # backup plan (real or synthetic)
    *,
    current_analysis=None,   # analysis of the current state; if None, use `analysis`
    current_index=None,      # index backing `current_analysis`
    previous_modified=None,  # per-root list of files already modified by a prior run
    confirm_input_fn=None,
    resumed_from=None,
) -> int:
    if current_analysis is None:
        current_analysis = analysis
    if current_index is None:
        current_index = index

    modified: dict = {}
    if current_analysis.any_duplicates():
        prog.info("Applying deduplication (atomic per-file replace) ...",
                  event="modify_start")

        def _modify_progress(root_name, rel_path, i, total):
            prog.per_file(
                f"  rewrite [{root_name}] {i}/{total} {rel_path}",
                event="modify_file", root=root_name,
                file=rel_path, i=i, n=total)

        try:
            modified = processor.process_roots(
                scan_results, current_analysis, current_index,
                empty_mode=args.empty_lines,
                max_line_bytes=args.max_line_bytes,
                progress=_modify_progress,
            )
        except processor.ProcessingError as e:
            prog.error("Modification failed.")
            prog.error(f"  {e}")
            prog.error(f"Backup is at: {plan.root}")
            prog.error("Some files may already have been modified. "
                       "Restore from the backup if needed.")
            return EXIT_ERROR

        total_mod = sum(len(v) for v in modified.values())
        prog.info(f"  Modified {total_mod:,} file(s).",
                  event="modify_end", files=total_mod)
    else:
        prog.info("No duplicates remain in the current tree.",
                  event="modify_skipped")

    # ---- VERIFY ---------------------------------------------------------
    prog.info("Verifying operation (independent re-analysis) ...",
              event="verify_start")

    def _verify_progress(message):
        prog.info(message, event="verify_progress")

    try:
        vreport = verifier.verify_operation(
            scan_results, analysis, index, modified, plan,
            empty_mode=args.empty_lines,
            max_line_bytes=args.max_line_bytes,
            extensions=extensions,
            progress=_verify_progress,
            work_dir=Path(args.work_dir) if args.work_dir else None,
        )
    except Exception as e:
        prog.error("VERIFICATION FAILED")
        prog.error(f"  verifier crashed: {e}")
        prog.error(f"Backup is at: {plan.root}")
        return EXIT_VERIFICATION_FAILED

    if not vreport.passed:
        prog.error("VERIFICATION FAILED")
        for f in vreport.failures:
            prog.error(f"  {f}")
        prog.error(f"Backup is at: {plan.root}")
        return EXIT_VERIFICATION_FAILED

    prog.info("  Verification passed.", event="verify_end")

    # ---- MANIFEST -------------------------------------------------------
    # Aggregate "modified" = everything affected in the original analysis.
    manifest_modified = {
        name: sorted(analysis.roots[name].files_affected)
        for name, _ in roots
    }

    warnings = manifest.collect_warnings(scan_results)
    payload = manifest.build_payload(
        scope_roots=roots,
        config=config,
        scan_results=scan_results,
        analysis=analysis,
        modified=manifest_modified,
        plan=plan,
        verification_report=vreport,
        warnings=warnings,
    )
    if resumed_from is not None:
        payload["resumed_from"] = str(resumed_from)

    try:
        manifest_path = manifest.write_manifest(plan, payload)
    except manifest.ManifestError as e:
        prog.error(f"Manifest write failed: {e}")
        prog.error(f"Backup is at: {plan.root}")
        return EXIT_ERROR

    print()
    print("SUCCESS")
    print(f"Backup:   {plan.root}")
    print(f"Manifest: {manifest_path}")
    if args.json_progress:
        prog.success_event(plan.root, manifest_path)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Resume pipeline
# ---------------------------------------------------------------------------

def _build_original_scans(scan_results, backup_root: Path,
                          backed_up: set):
    """Return new ScanResults where backed-up files point at the backup copy.

    Ownership order is preserved: the eligible list is already sorted by
    rel_path, and we only swap absolute paths and stat values.
    """
    out = []
    for sr in scan_results:
        new_eligible = []
        for ef in sr.eligible:
            key = (sr.root_name, ef.rel_path)
            if key in backed_up:
                bp = backup_root / sr.root_name / ef.rel_path
                try:
                    st = bp.stat()
                except OSError as e:
                    raise RuntimeError(
                        f"Cannot stat backup file {bp}: {e}")
                new_eligible.append(EligibleFile(
                    root_name=ef.root_name,
                    rel_path=ef.rel_path,
                    abs_path=bp,
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                ))
            else:
                new_eligible.append(ef)
        out.append(ScanResult(
            root_name=sr.root_name,
            root_path=sr.root_path,
            directories_discovered=sr.directories_discovered,
            eligible=new_eligible,
            issues=list(sr.issues),
        ))
    return out


def _run_resume(args, prog, confirm_input_fn) -> int:
    # ---- Roots ----------------------------------------------------------
    try:
        roots = resolve_roots(args)
    except safety.SafetyError as e:
        prog.error(f"Error: {e}")
        return EXIT_SAFETY

    config, extensions = _build_config(args)

    # ---- Backup home ----------------------------------------------------
    try:
        backup_home = safety.resolve_backup_home(
            args.backup_dir, roots, create=False)
    except safety.SafetyError as e:
        prog.error(f"Error: {e}")
        return EXIT_SAFETY

    # ---- Select backup --------------------------------------------------
    try:
        inc = resume_mod.select_backup(backup_home, args.resume_from, roots)
    except resume_mod.ResumeError as e:
        prog.error(f"Cannot resume: {e}")
        return EXIT_ERROR

    prog.info(f"Resuming from: {inc.path}",
              event="resume_start", path=str(inc.path))

    # ---- Validate backup integrity --------------------------------------
    prog.info("Validating backup integrity ...",
              event="resume_verify_backup")

    def _resume_progress(i, total, rel):
        prog.per_file(
            f"  backup [{i}/{total}] {rel}",
            event="resume_backup_file", i=i, n=total, file=rel)

    try:
        resume_mod.validate_backup_files(inc, progress=_resume_progress)
    except resume_mod.ResumeError as e:
        prog.error(f"Backup validation failed: {e}")
        return EXIT_ERROR
    prog.info("  Backup is intact.", event="resume_backup_ok")

    # ---- Compatibility --------------------------------------------------
    try:
        resume_mod.check_roots_match(inc.roots, roots)
        resume_mod.check_config_match(inc.config, config)
    except resume_mod.ResumeError as e:
        prog.error(f"Error: {e}")
        return EXIT_ERROR

    # ---- Re-scan current tree -------------------------------------------
    scan_results = []
    for name, path in roots:
        prog.info(f"Re-scanning {name} ({path}) ...",
                  event="rescan_start", root=name)
        sr = scanner.scan(name, path, extensions=extensions,
                          follow_symlinks=args.follow_symlinks)
        prog.info(f"  {len(sr.eligible):,} eligible",
                  event="rescan_end", root=name, eligible=len(sr.eligible))
        scan_results.append(sr)

    # ---- Build original scans (backup for affected files) ---------------
    try:
        original_scans = _build_original_scans(
            scan_results, inc.path, inc.backed_up_rel_paths)
    except RuntimeError as e:
        prog.error(f"Error reconstructing original state: {e}")
        return EXIT_ERROR

    # ---- Two-index setup ------------------------------------------------
    if args.work_dir:
        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup_workdir = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="wordlist-dedup-resume-"))
        cleanup_workdir = True

    index = None
    current_index = None
    try:
        # Original analysis (what was intended)
        prog.info("Analyzing original state ...",
                  event="analyze_original")
        index = Index(work_dir / "index_original.db")
        try:
            original_analysis = analyzer.analyze_roots(
                original_scans, index,
                empty_mode=args.empty_lines,
                max_line_bytes=args.max_line_bytes,
            )
        except (analyzer.AnalysisError, IndexError_) as e:
            prog.error(f"Error during original analysis: {e}")
            return EXIT_ERROR

        # Current analysis (what is left to do)
        prog.info("Analyzing current state ...",
                  event="analyze_current")
        current_index = Index(work_dir / "index_current.db")
        try:
            current_analysis = analyzer.analyze_roots(
                scan_results, current_index,
                empty_mode=args.empty_lines,
                max_line_bytes=args.max_line_bytes,
            )
        except (analyzer.AnalysisError, IndexError_) as e:
            prog.error(f"Error during current analysis: {e}")
            return EXIT_ERROR

        # ---- Report (original) ------------------------------------------
        reporter.print_report(original_scans, original_analysis, config,
                              dry_run=False)

        # ---- Resume status ----------------------------------------------
        already_done = {}
        remaining = {}
        for name, _ in roots:
            orig_set = set(original_analysis.roots[name].files_affected)
            curr_set = set(current_analysis.roots[name].files_affected)
            already_done[name] = sorted(orig_set - curr_set)
            remaining[name] = sorted(curr_set)

        total_done = sum(len(v) for v in already_done.values())
        total_remaining = sum(len(v) for v in remaining.values())

        prog.info("")
        prog.info("Resume status", event="resume_status")
        prog.info(f"  Backup:                {inc.path}")
        prog.info(f"  Files already done:    {total_done}")
        prog.info(f"  Files remaining:       {total_remaining}")
        prog.info(
            f"  Estimated bytes to remove: "
            f"{format_bytes(_bytes_removed(current_analysis))}")
        prog.info("")

        if total_remaining == 0:
            prog.info("No duplicates remain; will verify and write the "
                      "completion manifest.", event="resume_nothing_left")
        else:
            proceed = confirm.confirm(
                "Resume and apply remaining modifications? [y/N]: ",
                input_fn=confirm_input_fn,
                output_fn=prog.stream.write,
            )
            if not proceed:
                prog.info("Cancelled. No files have been modified.")
                return EXIT_OK

        # Synthetic plan so the verifier and manifest can locate the backup
        synthetic_plan = backup.BackupPlan(
            home=inc.path.parent,
            stamp=inc.stamp,
            root=inc.path,
            roots=list(roots),
            config=dict(config),
        )

        return _modify_verify_manifest(
            args, prog, roots, config, extensions,
            scan_results, original_analysis, index,
            synthetic_plan,
            current_analysis=current_analysis,
            current_index=current_index,
            previous_modified=already_done,
            confirm_input_fn=confirm_input_fn,
            resumed_from=inc.path,
        )

    finally:
        for ix in (index, current_index):
            if ix is not None:
                try:
                    ix.close()
                except Exception:
                    pass
        if cleanup_workdir:
            try:
                for p in work_dir.iterdir():
                    p.unlink()
                work_dir.rmdir()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def run(argv=None, *, confirm_input_fn=input, stderr=None) -> int:
    args = parse_args(argv)
    prog = Progress(json_mode=args.json_progress, verbose=args.verbose,
                    stream=stderr)

    resume_mode = bool(args.resume or args.resume_from)
    if resume_mode and args.dry_run:
        prog.error("Error: --resume and --dry-run cannot be combined.")
        return EXIT_SAFETY

    if resume_mode:
        return _run_resume(args, prog, confirm_input_fn)
    return _run_normal(args, prog, confirm_input_fn)


def main(argv=None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("\nInterrupted. Some files may have been modified; restore from "
              "the backup if needed.", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())