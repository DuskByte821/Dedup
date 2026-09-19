import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dedup import scanner, analyzer, backup, processor, verifier
from dedup.index import Index


def make_tree(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def test_basic_cross_and_internal():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'data'
        root.mkdir()
        make_tree(root, {
            'a.txt': b'apple\nbanana\napple\ncherry\n',
            'b.txt': b'banana\ndate\n',
            'nested/c.txt': b'apple\ndate\nelderberry\n',
        })
        scan = scanner.scan(root)
        assert [r for r, _ in scan.eligible] == sorted(r for r, _ in scan.eligible)

        idx = Index(Path(tmp) / 'idx.db')
        analysis = analyzer.analyze(root, scan.eligible, idx, empty_mode='keep')
        assert analysis.total_entries == 9
        assert analysis.unique_entries == 5
        assert analysis.internal_duplicates == 1
        assert analysis.cross_file_duplicates == 3
        assert analysis.files_affected == {'a.txt', 'b.txt', 'nested/c.txt'}

        files = [(r, p) for r, p in scan.eligible if r in analysis.files_affected]
        bk = backup.create_backup(root, files)
        backup.verify_backup(bk, files)
        processor.process_all(root, scan.eligible, idx, analysis, 'keep')
        idx.close()

        assert (root / 'a.txt').read_bytes() == b'apple\nbanana\ncherry\n'
        assert (root / 'b.txt').read_bytes() == b'date\n'
        assert (root / 'nested/c.txt').read_bytes() == b'elderberry\n'

        verifier.verify(root, bk, scan.eligible, analysis.files_affected,
                        empty_mode='keep', expected_unique=analysis.unique_entries)
        print("test_basic_cross_and_internal passed")


def test_crlf_and_cr_normalization():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'data'
        root.mkdir()
        make_tree(root, {
            'a.txt': b'one\r\ntwo\r\none\r\n',   # CRLF
            'b.txt': b'two\rthree\r',             # CR
        })
        scan = scanner.scan(root)
        idx = Index(Path(tmp) / 'idx.db')
        analysis = analyzer.analyze(root, scan.eligible, idx, empty_mode='keep')
        assert analysis.total_entries == 5
        assert analysis.unique_entries == 3   # one, two, three
        files = [(r, p) for r, p in scan.eligible if r in analysis.files_affected]
        bk = backup.create_backup(root, files)
        processor.process_all(root, scan.eligible, idx, analysis, 'keep')
        idx.close()
        # Output is normalized to LF.
        assert (root / 'a.txt').read_bytes() == b'one\ntwo\n'
        assert (root / 'b.txt').read_bytes() == b'three\n'
        print("test_crlf_and_cr_normalization passed")


def test_empty_lines_keep_and_drop():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'data'
        root.mkdir()
        make_tree(root, {'a.txt': b'one\n\none\n\ntwo\n'})

        scan = scanner.scan(root)
        idx = Index(Path(tmp) / 'idx.db')
        a = analyzer.analyze(root, scan.eligible, idx, empty_mode='keep')
        assert a.total_entries == 3        # empty lines are not entries
        assert a.unique_entries == 2
        assert a.internal_duplicates == 1
        assert a.empty_lines_kept == 2
        files = [(r, p) for r, p in scan.eligible if r in a.files_affected]
        bk = backup.create_backup(root, files)
        processor.process_all(root, scan.eligible, idx, a, 'keep')
        idx.close()
        assert (root / 'a.txt').read_bytes() == b'one\n\n\ntwo\n'
        print("test_empty_lines_keep passed")


def test_whitespace_not_stripped():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'data'
        root.mkdir()
        make_tree(root, {'a.txt': b'password\nPassword\npassword \n'})
        scan = scanner.scan(root)
        idx = Index(Path(tmp) / 'idx.db')
        a = analyzer.analyze(root, scan.eligible, idx, empty_mode='keep')
        assert a.unique_entries == 3       # all distinct
        assert a.internal_duplicates == 0
        idx.close()
        print("test_whitespace_not_stripped passed")


def test_verification_detects_external_tamper():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'data'
        root.mkdir()
        make_tree(root, {'a.txt': b'one\ntwo\none\n'})
        scan = scanner.scan(root)
        idx = Index(Path(tmp) / 'idx.db')
        a = analyzer.analyze(root, scan.eligible, idx, empty_mode='keep')
        files = [(r, p) for r, p in scan.eligible if r in a.files_affected]
        bk = backup.create_backup(root, files)
        processor.process_all(root, scan.eligible, idx, a, 'keep')
        idx.close()
        # Tamper: introduce a brand new entry.
        (root / 'a.txt').write_bytes(b'one\ntwo\ninjected\n')
        try:
            verifier.verify(root, bk, scan.eligible, a.files_affected,
                            empty_mode='keep', expected_unique=a.unique_entries)
        except verifier.VerificationError:
            print("test_verification_detects_external_tamper passed")
            return
        raise AssertionError("Verifier should have failed on tampered data")


if __name__ == '__main__':
    test_basic_cross_and_internal()
    test_crlf_and_cr_normalization()
    test_empty_lines_keep_and_drop()
    test_whitespace_not_stripped()
    test_verification_detects_external_tamper()
    print("\nAll tests passed.")
