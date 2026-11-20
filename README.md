# Recursive Wordlist Deduplication Tool

Safely remove duplicate lines across one or more wordlist directories,
preserving directory structure, filenames, and the order of surviving
lines. Designed for collections in the 1.3 GB range and up.

Safety is the highest priority. Nothing is modified until the user types
`y` or `yes` at a prompt; a byte-verified backup is created before any
original file is touched; every file is replaced atomically; and an
independent verification pass runs before the tool reports success.

## Requirements

- Python 3.8+
- POSIX (Linux). Windows is not a supported target.
- Standard library only. No external dependencies.

## Quick start

```bash
# Inspect only. Never modifies, never prompts.
python dedup.py ./wordlists --dry-run

# Two independent roots. Duplicates are computed per root.
python dedup.py \
    --root username=./wordlists/username \
    --root password=./wordlists/password
```

## Pipeline

```
DISCOVER → ANALYZE → REPORT → DISK CHECK → CONFIRM
    → BACKUP → VERIFY BACKUP → MODIFY (atomic)
    → VERIFY OPERATION → WRITE MANIFEST → SUCCESS
```

Every step is abortive. A failure at any stage leaves the originals
untouched, or (once modification has begun) leaves the previously-created
backup as the recovery path.

## Command-line reference

| Flag | Description |
| --- | --- |
| `PATH` | Single-root shorthand. Mutually exclusive with `--root`. |
| `--root NAME=PATH` | Named root (repeatable). Roots are independent namespaces. |
| `--dry-run` | Scan, analyze, report. Never modify, never prompt, never back up. |
| `--extensions .txt,.lst` | Override the default eligible-extension set. |
| `--empty-lines keep\|dedup\|drop` | Empty-line policy. Default: `keep`. |
| `--follow-symlinks` | Follow symlinks (default: no). |
| `--max-line-bytes N` | Abort if a line exceeds this size. Default 16 MiB. |
| `--work-dir DIR` | SQLite index location. Default: fresh temp directory. |
| `--backup-dir DIR` | Backup home. Default: `<parent of first root>/.dedup-backups`. |
| `--json-progress` | Emit structured progress events as JSON lines on stderr. |
| `--i-know-what-im-doing` | Allow dangerous scopes like `/` or `~`. |
| `--verbose`, `-v` | Per-file progress lines on stderr. |

There is no `--yes` flag. Interactive `y` or `yes` is required.

## Duplicate definition

An *entry* is one complete line. Only line endings are normalized:

```
LF       (\n)
CRLF     (\r\n)
CR            (\r)
```

all represent the same terminator. `admin\n`, `admin\r\n`, and `admin\r`
are the same entry: `admin`.

Nothing else is normalized. These are five distinct entries:

```
Password
password
password 
 password
password		(tab)
```

Empty lines are governed by `--empty-lines`:

- **keep** (default) — preserved in place; not hashed, not deduplicated.
- **dedup** — treated as ordinary entries and deduplicated.
- **drop** — removed entirely.

## Multiple independent roots

The tool supports one or more roots. Each root is a completely independent
uniqueness namespace:

```
username/users.txt:      admin
username/other.txt:      admin
password/common.txt:     admin
```

Result:

```
username/users.txt:      admin
username/other.txt:      (empty)
password/common.txt:     admin
```

The `admin` in `username/other.txt` is a duplicate *within its root* and
is removed. The `admin` in `password/common.txt` is unrelated to anything
in `username/` and is preserved.

The report and the manifest show per-root statistics and a `TOTAL` block.
The totals are arithmetic sums — they never imply that an entry appearing
in two different roots is a duplicate.

## Deterministic ownership

Within a root, files are processed in sorted relative-path order
(`os.fsencode`). The first occurrence of an entry — earliest file, lowest
line number — is retained. Later occurrences are removed:

- same file as owner → *internal duplicate*
- different file, same root → *cross-file duplicate*

There is no ownership competition between roots. The order in which roots
are analyzed does not affect the outcome.

## File eligibility

Eligible by default:

```
.txt  .lst  .wordlist  .words  .dict  .dic
```

Override with `--extensions .txt,.csv`. Internal directories
(`.dedup-backups`, `.dedup-work`, `.dedup-tmp`) are always skipped, as are
symlinks (unless `--follow-symlinks` is passed). Leftover temp files from a
crashed run are detected and reported.

## Output format

Every retained entry is written as `entry_bytes + b"\n"`. Internal
terminators are normalized to LF.

**Final-newline state is preserved per file.** If the original file did not
end with a terminator, the output does not either. If it ended with `\n`,
`\r\n`, or `\r`, the output ends with `\n`.

## Backup

Before any original file is touched:

```
<backup_home>/<YYYY-MM-DD_HHMMSS>/
    <root_name_1>/
        ...mirrored tree of affected files...
    <root_name_2>/
        ...
    operation.json
```

Backup home defaults to `<parent of first root>/.dedup-backups` and must
lie outside every root. Override with `--backup-dir`.

Only files that will actually be modified are backed up. Every backed-up
file is re-read and its SHA-256 compared against the source before any
original is touched. `operation.json` is written only after all hashes
match; its absence marks the backup as incomplete and it must not be used
for recovery.

## Atomic replacement

For each modified file:

```
write temp file in same dir
    → flush
    → fsync
    → copystat (mode, times)
    → os.replace(temp, original)
    → fsync(parent directory)
```

The original is never truncated. On any failure the temp file is removed
and the original remains intact. A crash mid-replace leaves either the
original or the complete replacement on disk, never a partial file.

## Verification

Two independent verifiers run:

**Internal** (after modification, before success is reported):

- re-scan and re-analyze the modified tree
- zero internal duplicates, zero cross-file duplicates per root
- output unique-entry set identical to the original unique-entry set
- untouched files unchanged (`size`, `mtime_ns`)
- every entry in the backup still hashes to its recorded value
- entries shared between ≥2 roots remain present in each of those roots

**External** (`verify.py`, run separately):

```bash
python verify.py \
    --original ../Secn \
    --backup   .dedup-backups/2026-09-21_150233 \
    --processed ./Secn
```

Check A compares the backup byte-for-byte against the original.
Check B reconstructs the expected processed tree from the backup (using an
implementation that shares only line-parsing code with the tool) and
compares it byte-for-byte against the processed tree.

If the internal verifier fails, the CLI prints `VERIFICATION FAILED` and
exits with code 6. The external verifier exits 2 on failure.


## Resuming an interrupted run

If the tool is interrupted after the backup phase completes but before the
completion manifest is written — for example by a crash, a power failure,
or Ctrl+C during modification — the operation can be resumed.

Resume is **opt-in**. The tool never resumes automatically.

```bash
# Resume the most recent incomplete backup.
python dedup.py ./Wordlists --resume

# Two independent roots must be specified exactly as in the original run.
python dedup.py \
    --root username=./username \
    --root password=./password \
    --resume

# Resume from a specific backup directory.
python dedup.py ./Wordlists --resume-from .dedup-backups/2026-09-21_160124
```

### What resume does

1. **Locates the incomplete backup.** A backup directory is *incomplete* if
   it contains `operation.json` (backup done and verified) but not
   `operation.complete.json` (operation did not finish). If there are
   multiple, the user must select one with `--resume-from`.
2. **Validates the backup.** Every backed-up file is re-hashed and compared
   against its recorded SHA-256. Any mismatch aborts.
3. **Checks compatibility.** The roots and the config (empty-line policy,
   extensions, symlink handling, max line size) must match what the
   original run recorded. Any mismatch aborts.
4. **Re-scans the current tree.** The tree may contain a mix of
   already-modified files and not-yet-modified files. Re-scanning captures
   the current state.
5. **Reconstructs the original analysis.** For every file the backup
   recorded, the backup copy is used; for every other file, the live tree
   is used. This re-derives exactly what the original analysis saw.
6. **Reports both.** The report shows the *original* intent. A resume
   status block then shows how many files are already done, how many
   remain, and the estimated bytes to remove.
7. **Prompts.** `Resume and apply remaining modifications? [y/N]:`.
   Anything other than `y` or `yes` cancels with no changes.
8. **Modifies only what remains.** Files with no duplicates in the current
   tree are skipped.
9. **Verifies against the original analysis**, not against the current
   state. This makes resume's guarantee as strong as a fresh run.
10. **Writes the completion manifest** into the same backup directory.

### What resume refuses to do

Resume aborts rather than proceed if any of these is true:

- No incomplete backup exists.
- The backup is missing, mis-shaped, or fails its own hash check.
- The roots or the config differ from the original run.
- The backup files cannot be re-read (permission, deletion, corruption).
- The verifier fails after modification.

Resume never merges two different backup directories, never guesses at
the config, and never proceeds on an ambiguous state.

### Why resume is safe

Modification only ever removes duplicate occurrences; it never introduces,
reorders, or alters surviving entries. Ownership is determined by sorted
relative paths, which do not change across modification. Therefore:

- The *distinct-entry set* of the tree is invariant under modification.
- The *owner* of each entry is invariant under modification.
- Re-analyzing a mixed tree yields the same "files still to process" as
  the original analysis would have produced.
- Deduplicating the mixed tree is equivalent to deduplicating the original
  tree from scratch.

If the user edited any file between the crash and the resume, those edits
become the new starting state. The backup is unchanged, so restoration is
still possible.

### Crash-safe properties of resume itself

Resume re-uses the existing backup. It does not create a new one. If
resume is interrupted again, the same backup directory is still available,
and a subsequent `--resume` will pick up where the previous attempt left
off.


## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. The operation is verified. |
| 1 | Unexpected error (I/O, manifest write, etc.). |
| 2 | Safety refusal (bad scope, insufficient space, bad backup home). |
| 6 | Verification failed. Backup is available for recovery. |
| 130 | Interrupted (Ctrl+C). |

## Manifest

`operation.complete.json` is written into the backup directory only after
the entire pipeline, including verification, has passed. It contains the
configuration, per-root and total statistics, the backup location, and the
verification status. Its presence certifies that this backup represents a
fully successful run.

## Performance

- Streaming reads. No dataset is ever loaded into Python memory.
- SQLite index (`PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, 200 MiB
  page cache). Pending inserts are buffered up to 16 MiB before a
  transaction is committed.
- SHA-256 for hashing, but equality is always decided on exact content
  bytes. Hash collisions cannot cause data loss.
- A ~1.3 GB dataset typically needs 3–4 GB of index space. Provide a fast
  `--work-dir` on the same filesystem if possible.

## Testing

```bash
for t in lineio safety scanner index analyzer reporter \
         confirm confirmation_gate backup processor verifier manifest; do
    python tests/test_$t.py || break
done
```

Each test file runs standalone; no pytest required. The suite covers
internal and cross-file duplicates, line-ending variants, Unicode,
whitespace preservation, empty-line policies, root isolation, deterministic
ownership, concurrent-modification detection, backup and verification
tampering, and crash-safety behavior.

## Architecture

```
wordlist-dedup/
├── dedup.py              # CLI entry point
├── verify.py             # Independent external verifier
├── dedup/
│   ├── __init__.py
│   ├── safety.py         # Scope validation, disk space, backup home
│   ├── scanner.py        # Recursive discovery, deterministic order
│   ├── lineio.py         # LF/CRLF/CR line iteration (bytes)
│   ├── index.py          # Root-scoped SQLite index
│   ├── analyzer.py       # Streaming analysis, per-root ownership
│   ├── reporter.py       # Human-readable report
│   ├── confirm.py        # Interactive gate
│   ├── backup.py         # Verified backup
│   ├── processor.py      # Atomic replacement
│   ├── verifier.py       # Internal post-modification verification
│   └── manifest.py       # Completion manifest
├── tests/
└── README.md
```

## Design principles

1. **Not losing data beats speed.** Every failure mode aborts rather than
   continuing on assumption.
2. **Line endings are the only normalization.** Entry content is preserved
   byte-for-byte.
3. **Ownership is deterministic.** Sorted paths, first occurrence, no
   randomness.
4. **Roots are independent.** No global uniqueness across semantically
   different datasets.
5. **Verification is mandatory and independent.** Both internal and
   external verifiers must agree.
6. **Backups are verified before originals are touched.** A corrupt backup
   is discovered before any harm can occur.