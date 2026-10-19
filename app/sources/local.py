"""LocalFolderSource: demo-mode source over `./sample_docs` (FR-14). Non-recursive.

- file_id = the file's path relative to the folder (just the file name, since the
  watch is non-recursive). Stable across restarts.
- md5 = md5 of the file bytes (`documents.md5_checksum` is NOT NULL).
- Ignored: `MANIFEST.md`, hidden files (`.DS_Store`, editor temp files), and
  subdirectories.
- Cursor: a JSON snapshot `{"v": 1, "files": {file_id: md5}}`. `changes_since` rescans
  the folder and diffs against it, which gives the same semantics as Drive's
  `changes.list` (added/changed files, removed ids, new cursor).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import PROJECT_ROOT
from app.extraction.content import mime_for_filename
from app.sources.base import ChangeSet, SourceError, SourceFile, SourceFileGone, TransientSourceError

DEFAULT_LOCAL_DIR = PROJECT_ROOT / "sample_docs"
IGNORED_NAMES = frozenset({"MANIFEST.md"})
CURSOR_VERSION = 1


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalFolderSource:
    name = "local"
    folder_id = None

    def __init__(self, root: Path = DEFAULT_LOCAL_DIR):
        self.root = Path(root)
        self.folder_name = self.root.name

    # ---- listing ------------------------------------------------------------------
    def _entries(self) -> list[Path]:
        if not self.root.is_dir():
            raise TransientSourceError(f"local folder {self.root.name}/ does not exist")
        out = []
        for p in sorted(self.root.iterdir()):
            if p.name in IGNORED_NAMES or p.name.startswith(".") or p.name.startswith("~$"):
                continue
            if p.is_file() and not p.is_symlink():
                out.append(p)
        return out

    def _describe(self, p: Path) -> SourceFile:
        st = p.stat()
        return SourceFile(
            file_id=p.relative_to(self.root).as_posix(),
            name=p.name,
            mime_type=mime_for_filename(p.name),
            md5=_md5(p),
            modified_at=datetime.fromtimestamp(st.st_mtime, UTC),
        )

    def list_files(self) -> list[SourceFile]:
        files = []
        for p in self._entries():
            try:
                files.append(self._describe(p))
            except FileNotFoundError:  # deleted between iterdir and stat
                continue
            except OSError as e:
                raise TransientSourceError(f"cannot read {p.name}: {type(e).__name__}") from None
        return files

    # ---- content ------------------------------------------------------------------
    def fetch(self, file_id: str) -> bytes:
        p = (self.root / file_id).resolve()
        if p.parent != self.root.resolve():  # non-recursive; also blocks path traversal
            raise SourceFileGone(f"{file_id} is not in the watched folder")
        try:
            return p.read_bytes()
        except FileNotFoundError:
            raise SourceFileGone(f"{file_id} no longer exists in {self.folder_name}/") from None
        except OSError as e:
            raise TransientSourceError(f"cannot read {file_id}: {type(e).__name__}") from None

    # ---- cursor -------------------------------------------------------------------
    @staticmethod
    def _encode(files: list[SourceFile]) -> str:
        return json.dumps({"v": CURSOR_VERSION, "files": {f.file_id: f.md5 for f in files}}, sort_keys=True)

    @staticmethod
    def _decode(cursor: str | None) -> dict[str, str] | None:
        if not cursor:
            return None
        try:
            data = json.loads(cursor)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION or not isinstance(data.get("files"), dict):
            return None
        return data["files"]

    def start_cursor(self) -> str:
        return self._encode(self.list_files())

    def snapshot_cursor(self, files: list[SourceFile]) -> str:
        """Cursor for a listing the caller already has (avoids a second scan)."""
        return self._encode(files)

    def changes_since(self, cursor: str) -> ChangeSet:
        previous = self._decode(cursor)
        files = self.list_files()
        if previous is None:
            # Unknown/invalid cursor: report everything as changed (idempotent upserts).
            return ChangeSet(changed=files, removed_ids=[], cursor=self._encode(files))
        current = {f.file_id for f in files}
        changed = [f for f in files if previous.get(f.file_id) != f.md5]
        removed = sorted(fid for fid in previous if fid not in current)
        return ChangeSet(changed=changed, removed_ids=removed, cursor=self._encode(files))


__all__ = ["LocalFolderSource", "DEFAULT_LOCAL_DIR", "SourceError"]
