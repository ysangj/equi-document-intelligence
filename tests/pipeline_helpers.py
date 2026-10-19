"""Run documents through the real T4 pipeline (sync -> claim -> run_job -> T5 hook ->
post-commit indexing) with a mocked extractor (not a test module)."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.extraction.fixture import FixtureExtractor
from app.ingest import pipeline, queue
from app.ingest.sync import reconcile
from app.search.indexing import index_document
from app.sources.local import DEFAULT_LOCAL_DIR, LocalFolderSource
from tests.synthetic_samples import SAMPLE_BUILDERS, write_sample_fixtures

FIXED_TODAY = date(2026, 10, 16)


def copy_samples(dest: Path, names=None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in names or SAMPLE_BUILDERS:
        shutil.copy2(DEFAULT_LOCAL_DIR / name, dest / name)
    return dest


def sync_folder(engine: Engine, folder: Path) -> None:
    with Session(engine) as s, s.begin():
        reconcile(s, LocalFolderSource(folder))


def drain(engine: Engine, folder: Path, extractor, *, indexer=index_document, max_attempts: int = 3) -> dict[str, str]:
    """Claim and run every pending job, one at a time (claim order = document id, i.e.
    alphabetical for a fresh reconcile). Returns {file name: final status}."""
    source = LocalFolderSource(folder)
    out: dict[str, str] = {}
    while True:
        with Session(engine) as s, s.begin():
            c = queue.claim_next(s, max_attempts=max_attempts, source_name="local")
        if c is None:
            return out
        out[c.name] = pipeline.run_job(engine, c, source, extractor, max_attempts=max_attempts, indexer=indexer)


def ingest_samples(engine: Engine, tmp_path: Path, names=None, *, indexer=index_document) -> dict[str, str]:
    docs = copy_samples(tmp_path / "docs", names)
    fx = write_sample_fixtures(tmp_path / "fixtures")
    sync_folder(engine, docs)
    return drain(engine, docs, FixtureExtractor(fx), indexer=indexer)
