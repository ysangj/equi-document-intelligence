"""An in-memory Google Drive for the T7 tests (not a test module). No network.

`FakeDrive` holds files (Drive v3 resources) and a change log, and `FakeDriveApi`
implements the `app.google.drive_api.DriveApi` surface over it, including the
`files.list` queries the app sends, pagination (`max_page` forces small pages),
`changes.list` page tokens, and error injection (`fail[method] = [exc, ...]`).
"""

from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass, field

from app.config import Settings
from app.google.drive_api import FOLDER_MIME, SHEET_MIME, DriveInvalidArgument, DriveNotFound

ROOT_ID = "0AROOTfake"
_ids = itertools.count(1)

_PARENT_RE = re.compile(r"^'((?:[^'\\]|\\.)*)' in parents$")
_MIME_RE = re.compile(r"^mimeType = '([^']*)'$")


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


@dataclass
class FakeDrive:
    files: dict[str, dict] = field(default_factory=dict)
    content: dict[str, bytes] = field(default_factory=dict)
    changes: list[dict] = field(default_factory=list)  # index + 1 = change sequence number
    max_page: int = 1000
    # tests can read these
    calls: list[str] = field(default_factory=list)
    fail: dict[str, list[Exception]] = field(default_factory=dict)

    def __post_init__(self):
        self.files[ROOT_ID] = {"id": ROOT_ID, "name": "My Drive", "mimeType": FOLDER_MIME, "parents": [],
                               "trashed": False, "version": "1", "modifiedTime": "2026-01-01T00:00:00.000Z"}

    # ---- mutations (each one is a change) -----------------------------------------------
    def _log(self, file_id: str, removed: bool = False) -> None:
        f = self.files.get(file_id)
        self.changes.append({"changeType": "file", "fileId": file_id, "removed": removed,
                             **({} if removed or f is None else {"file": dict(f)})})

    def add_folder(self, name: str, parent: str = ROOT_ID, *, folder_id: str | None = None,
                   trashed: bool = False) -> str:
        fid = folder_id or f"fld{next(_ids)}"
        self.files[fid] = {"id": fid, "name": name, "mimeType": FOLDER_MIME, "parents": [parent],
                           "trashed": trashed, "version": "1", "modifiedTime": "2026-10-15T09:12:44.000Z"}
        self._log(fid)
        return fid

    def add_file(self, name: str, parent: str, data: bytes = b"%PDF-1.4 x", *, mime: str = "application/pdf",
                 file_id: str | None = None) -> str:
        fid = file_id or f"file{next(_ids)}"
        res = {"id": fid, "name": name, "mimeType": mime, "parents": [parent], "trashed": False, "version": "1",
               "modifiedTime": "2026-10-15T09:20:03.000Z",
               "webViewLink": f"https://drive.google.com/file/d/{fid}/view?usp=drivesdk"}
        if not mime.startswith("application/vnd.google-apps."):
            res["md5Checksum"] = hashlib.md5(data).hexdigest()
        self.files[fid] = res
        self.content[fid] = data
        self._log(fid)
        return fid

    def add_sheet(self, name: str, parent: str, csv: bytes = b"a,b\n1,2\n") -> str:
        return self.add_file(name, parent, csv, mime=SHEET_MIME)

    def update(self, fid: str, data: bytes) -> None:
        f = self.files[fid]
        f["version"] = str(int(f["version"]) + 1)
        if "md5Checksum" in f:
            f["md5Checksum"] = hashlib.md5(data).hexdigest()
        self.content[fid] = data
        self._log(fid)

    def rename(self, fid: str, name: str) -> None:
        f = self.files[fid]
        f["name"] = name
        f["version"] = str(int(f["version"]) + 1)
        self._log(fid)

    def trash(self, fid: str, trashed: bool = True) -> None:
        self.files[fid]["trashed"] = trashed
        self._log(fid)

    def move(self, fid: str, new_parent: str) -> None:
        self.files[fid]["parents"] = [new_parent]
        self._log(fid)

    def delete(self, fid: str) -> None:
        del self.files[fid]
        self.content.pop(fid, None)
        self._log(fid, removed=True)

    @property
    def next_token(self) -> str:
        return str(len(self.changes) + 1)


class FakeDriveApi:
    """Same methods as `app.google.drive_api.DriveApi`."""

    def __init__(self, drive: FakeDrive):
        self.drive = drive

    def _enter(self, method: str) -> None:
        self.drive.calls.append(method)
        queue = self.drive.fail.get(method)
        if queue:
            raise queue.pop(0)

    def _resolve(self, fid: str) -> str:
        return ROOT_ID if fid == "root" else fid

    def files_list(self, *, q: str, fields: str, page_token: str | None = None, page_size: int = 1000,
                   order_by: str | None = None) -> dict:
        self._enter("files_list")
        parent, mime, want_trashed = None, None, None
        for clause in q.split(" and "):
            clause = clause.strip()
            if m := _PARENT_RE.match(clause):
                parent = self._resolve(_unescape(m.group(1)))
            elif m := _MIME_RE.match(clause):
                mime = m.group(1)
            elif clause == "trashed = false":
                want_trashed = False
            else:
                raise AssertionError(f"unexpected query clause {clause!r}")
        assert parent is not None and want_trashed is False, q
        hits = [dict(f) for f in self.drive.files.values()
                if parent in f.get("parents", []) and not f.get("trashed") and (mime is None or f["mimeType"] == mime)]
        if order_by == "name":
            hits.sort(key=lambda f: f["name"])  # Drive's own order; the api re-sorts case-insensitively
        start = 0
        if page_token:
            if not page_token.startswith("p"):
                raise DriveInvalidArgument("Drive files.list failed: HTTP 400 (invalid)", 400)
            start = int(page_token[1:])
        size = min(page_size, self.drive.max_page)
        page = hits[start:start + size]
        out: dict = {"files": page}
        if start + size < len(hits):
            out["nextPageToken"] = f"p{start + size}"
        return out

    def files_get(self, file_id: str, *, fields: str) -> dict:
        self._enter("files_get")
        f = self.drive.files.get(self._resolve(file_id))
        if f is None:
            raise DriveNotFound("Drive files.get: not found", 404)
        return dict(f)

    def get_media(self, file_id: str) -> bytes:
        self._enter("get_media")
        f = self.drive.files.get(file_id)
        if f is None:
            raise DriveNotFound("Drive files.get_media: not found", 404)
        assert not f["mimeType"].startswith("application/vnd.google-apps."), "get_media on a Google-native file"
        return self.drive.content[file_id]

    def export(self, file_id: str, mime_type: str) -> bytes:
        self._enter("export")
        f = self.drive.files.get(file_id)
        if f is None:
            raise DriveNotFound("Drive files.export: not found", 404)
        assert f["mimeType"] == SHEET_MIME and mime_type == "text/csv"
        return self.drive.content[file_id]

    def start_page_token(self) -> str:
        self._enter("start_page_token")
        return self.drive.next_token

    def changes_list(self, *, page_token: str, fields: str, page_size: int = 1000) -> dict:
        self._enter("changes_list")
        if not page_token.isdigit():
            raise DriveInvalidArgument("Drive changes.list failed: HTTP 400 (invalid page token)", 400)
        start = int(page_token) - 1
        size = min(page_size, self.drive.max_page)
        page = self.drive.changes[start:start + size]
        out: dict = {"changes": [dict(c) for c in page]}
        if start + size < len(self.drive.changes):
            out["nextPageToken"] = str(start + size + 1)
        else:
            out["newStartPageToken"] = self.drive.next_token
        return out


@dataclass
class FactoryRecorder:
    """An api factory: records each refresh token it was given (the test asserts on
    the decrypted value) and returns a FakeDriveApi over `drive`."""

    drive: FakeDrive
    tokens: list[str] = field(default_factory=list)

    def __call__(self, settings: Settings, refresh_token: str) -> FakeDriveApi:
        self.tokens.append(refresh_token)
        return FakeDriveApi(self.drive)
