# Recursive Wordlist Deduplication Tool

Safely detect and remove duplicate lines from a large (~1.3 GB) collection of
wordlists, including arbitrarily nested subdirectories.

Data safety is the highest priority. Nothing is modified until you explicitly
confirm. A verified backup is created before any original file is touched, and
every file is replaced atomically. An independent post-operation verification
pass runs before the tool reports success.

## Usage

```bash
python dedup.py /path/to/Wordlists/Wi-Fi
python dedup.py /path/to/Wordlists/Wi-Fi --dry-run
python dedup.py /path/to/Wordlists/Wi-Fi --extensions .txt,.lst
python dedup.py /path/to/Wordlists/Wi-Fi --empty-lines=dedup
```

### Options

| Flag | Description |
| --- | --- |
| `--dry-run` | Scan, analyze, report — never modify anything. |
| `--extensions .txt,.lst` | Override the default eligible-extension set. |
| `--empty-lines keep\|dedup\|drop` | Empty-line policy. Default `keep`. |
| `--follow-symlinks` | Follow symlinks (off by default). |
| `--yes` / `-y` | Skip the interactive confirmation. |
| `--work-dir DIR` | Location for the SQLite index (default: system temp). |
| `--verbose` / `-v` | Per-file progress output. |
| `--i-know-what-im-doing` | Allow dangerous scopes like `/` or `~`. |

## Duplicate definition

A duplicate is an identical line after normalizing only the **line ending**
(`LF`, `CRLF`, `CR` are all equivalent). Output is normalized to `LF`.

Whitespace, case and Unicode normalization are **not** applied. Therefore
`password`, `Password`, and `password ` are three distinct entries.

### Empty lines

Default policy is `keep`: empty lines are preserved in place and are not
deduplicated. Two other modes are available:

* `dedup` — empty lines are treated as ordinary entries.
* `drop` — empty lines are removed entirely.

## Deterministic ownership

Files are processed in sorted relative-path order (`rel_path.sort()`). The
first occurrence of an entry — across the whole scope — becomes the retained
occurrence. Within a file, the first occurrence is retained and later
duplicates are removed. Directory structure, file names, and the order of
surviving entries are never changed.

## Pipeline

```
DISCOVER → ANALYZE → REPORT → CONFIRM → BACKUP → ATOMIC REPLACE → VERIFY
```

If anything goes wrong — invalid confirmation, backup failure, verification
failure, insufficient disk space, unexpected error — the tool stops and
leaves the originals untouched. A crash during replacement always leaves
either the original file or the complete replacement.

## Backup location

```
<scope>/../.dedup-backups/<timestamp>/
    <original relative structure>
    operation.json
```

The backup directory is a sibling of the scope and its name is excluded from
scanning, so it can never be rediscovered as part of the dataset.

## What is verified

After modification the tool re-scans the scope and checks:

* no duplicate entries remain
* the total number of entries equals the expected unique count
* every retained entry existed in the original dataset
* no new entries appeared
* the eligible file set is unchanged

If any of these fail, the tool prints `VERIFICATION FAILED` and does **not**
report success. The backup remains available for recovery.

## Testing

```bash
python tests/test_basic.py
```

Tests cover: internal and cross-file duplicates, CRLF/CR normalization,
empty-line policies, whitespace preservation, and verification detecting
externally introduced data.

## Design notes

* **SQLite** index (`files`, `entries`) keeps memory usage flat regardless of
  dataset size. Full content bytes are stored so hash collisions can never
  cause data loss.
* **Two-pass per modified file.** The analyzer records ownership; the
  processor performs a second streaming pass, keeping only owner lines.
* **Atomic replacement.** `os.replace()` after `flush + fsync` on a
  uniquely-named temp file in the same directory. Metadata (mode, owner,
  timestamps) is restored where possible.
* **No external dependencies.** Python standard library only.
