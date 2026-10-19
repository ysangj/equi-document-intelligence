"""LocalFolderSource (FR-14 demo source)."""

from __future__ import annotations

import hashlib

import pytest

from app.sources.base import FileSource, SourceFileGone, TransientSourceError
from app.sources.local import DEFAULT_LOCAL_DIR, LocalFolderSource


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"pdf-a")
    (tmp_path / "b.csv").write_bytes(b"x,y\n")
    (tmp_path / "notes.txt").write_bytes(b"hi")
    (tmp_path / "MANIFEST.md").write_text("# manifest")
    (tmp_path / ".DS_Store").write_bytes(b"\0")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.pdf").write_bytes(b"deep")
    return tmp_path


def test_lists_non_recursively_and_ignores_manifest_and_hidden(folder):
    src = LocalFolderSource(folder)
    assert isinstance(src, FileSource)
    files = {f.file_id: f for f in src.list_files()}
    assert set(files) == {"a.pdf", "b.csv", "notes.txt"}
    a = files["a.pdf"]
    assert a.md5 == hashlib.md5(b"pdf-a").hexdigest() and a.mime_type == "application/pdf"
    assert a.modified_at is not None and a.modified_at.tzinfo is not None
    assert files["notes.txt"].mime_type == "text/plain"
    assert src.name == "local" and src.folder_name == folder.name and src.folder_id is None


def test_fetch_reads_bytes_and_rejects_escape(folder):
    src = LocalFolderSource(folder)
    assert src.fetch("a.pdf") == b"pdf-a"
    with pytest.raises(SourceFileGone):
        src.fetch("missing.pdf")
    with pytest.raises(SourceFileGone):
        src.fetch("../etc/passwd")
    with pytest.raises(SourceFileGone):
        src.fetch("sub/deep.pdf")


def test_missing_folder_is_transient(tmp_path):
    with pytest.raises(TransientSourceError):
        LocalFolderSource(tmp_path / "nope").list_files()


def test_changes_since_cursor(folder):
    src = LocalFolderSource(folder)
    cursor = src.start_cursor()
    assert src.changes_since(cursor).changed == []
    (folder / "a.pdf").write_bytes(b"pdf-a v2")
    (folder / "c.html").write_bytes(b"<p>new</p>")
    (folder / "b.csv").unlink()
    ch = src.changes_since(cursor)
    assert sorted(f.file_id for f in ch.changed) == ["a.pdf", "c.html"]
    assert ch.removed_ids == ["b.csv"]
    assert src.changes_since(ch.cursor).changed == []


def test_invalid_cursor_reports_everything(folder):
    src = LocalFolderSource(folder)
    for bad in ("", "not json", '{"v": 0}'):
        ch = src.changes_since(bad)
        assert len(ch.changed) == 3 and ch.removed_ids == []


def test_sample_docs_listing():
    files = LocalFolderSource(DEFAULT_LOCAL_DIR).list_files()
    names = {f.name for f in files}
    assert "MANIFEST.md" not in names
    assert len(files) == 21
