"""Job queue and pipeline (FR-5, §12): SKIP LOCKED claims, stale reclaim, fencing,
retry/backoff, failure after MAX_ATTEMPTS, and job isolation. extract() is mocked."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.extraction.base import FunctionExtractor, PermanentExtractionError, TransientExtractionError
from app.ingest import pipeline, queue
from app.models import Document, Extraction
from app.sources.local import LocalFolderSource
from tests.ingest_helpers import make_extraction

# T5 validates the generic test extraction against these dummy files (its snippets are
# not in them), so a processed job ends `done` or `needs_review`; both are success here.
PROCESSED = ("done", "needs_review")


def add_docs(engine, n, *, status="pending", source="local", prefix="f"):
    ids = []
    with Session(engine) as s, s.begin():
        for i in range(n):
            d = Document(source=source, drive_file_id=f"{prefix}{i}.csv", name=f"{prefix}{i}.csv",
                         mime_type="text/csv", md5_checksum=f"stand-in:{prefix}{i}", status=status)
            s.add(d)
            s.flush()
            ids.append(d.id)
    return ids


def claim(engine, **kw):
    with Session(engine) as s, s.begin():
        return queue.claim_next(s, max_attempts=kw.pop("max_attempts", 3), **kw)


def get(engine, doc_id) -> Document:
    with Session(engine) as s:
        return s.get(Document, doc_id)


def extractions(engine, doc_id):
    with Session(engine) as s:
        return list(s.execute(select(Extraction).where(Extraction.document_id == doc_id)
                              .order_by(Extraction.id)).scalars())


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    for i in range(3):
        (d / f"f{i}.csv").write_bytes(f"Fund,Ret\nF{i},0.01\n".encode())
    return d


# ---- claiming --------------------------------------------------------------------------


def test_claim_sets_processing_and_increments_attempts(clean_db):
    (doc_id,) = add_docs(clean_db, 1)
    c = claim(clean_db)
    assert c.document_id == doc_id and c.attempt == 1 and c.job_id == f"doc{doc_id}-a1"
    d = get(clean_db, doc_id)
    assert (d.status, d.attempts) == ("processing", 1) and d.claimed_at is not None
    assert claim(clean_db) is None


def test_skip_locked_never_double_claims(clean_db):
    ids = add_docs(clean_db, 2)
    s1 = Session(clean_db)
    s1.begin()
    c1 = queue.claim_next(s1, max_attempts=3)  # row stays locked, uncommitted
    c2 = claim(clean_db)  # must skip the locked row, not wait
    assert {c1.document_id, c2.document_id} == set(ids)
    s1.commit()
    s1.close()
    assert claim(clean_db) is None


def test_concurrent_claimers_get_each_document_exactly_once(clean_db):
    ids = add_docs(clean_db, 30)
    got: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        start.wait()
        while True:
            c = claim(clean_db)
            if c is None:
                return
            with lock:
                got.append(c.document_id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == sorted(ids)


def test_claim_respects_next_attempt_at_latest_and_source(clean_db):
    (later,) = add_docs(clean_db, 1, prefix="later")
    (old,) = add_docs(clean_db, 1, prefix="old")
    (drive,) = add_docs(clean_db, 1, prefix="drv", source="drive")
    with Session(clean_db) as s, s.begin():
        s.execute(text("UPDATE documents SET next_attempt_at = now() + interval '1 hour' WHERE id = :i"), {"i": later})
        s.execute(text("UPDATE documents SET is_latest = false WHERE id = :i"), {"i": old})
    assert claim(clean_db, source_name="local") is None
    assert claim(clean_db, source_name="drive").document_id == drive


def test_stale_processing_is_reclaimed(clean_db):
    (doc_id,) = add_docs(clean_db, 1)
    first = claim(clean_db)
    assert claim(clean_db, stale_after=60) is None  # fresh claim is not stale
    with Session(clean_db) as s, s.begin():
        s.execute(text("UPDATE documents SET claimed_at = now() - interval '2 minutes'"))
    second = claim(clean_db, stale_after=60)
    assert second.document_id == doc_id and second.attempt == first.attempt + 1


def test_stale_job_out_of_attempts_is_failed(clean_db):
    (doc_id,) = add_docs(clean_db, 1)
    with Session(clean_db) as s, s.begin():
        s.execute(text("UPDATE documents SET status='processing', attempts=3, claimed_at = now() - interval '1 hour'"))
    with Session(clean_db) as s, s.begin():
        assert queue.fail_exhausted_stale(s, max_attempts=3, stale_after=60) == 1
    d = get(clean_db, doc_id)
    assert d.status == "failed" and "interrupted" in d.last_error and "3 attempts" in d.last_error


def test_heartbeat_and_release(clean_db):
    (doc_id,) = add_docs(clean_db, 1)
    c = claim(clean_db)
    with Session(clean_db) as s, s.begin():
        s.execute(text("UPDATE documents SET claimed_at = now() - interval '2 minutes'"))
    with Session(clean_db) as s, s.begin():
        assert queue.heartbeat(s, [c]) == 1
    assert claim(clean_db, stale_after=60) is None  # heartbeat kept it fresh
    with Session(clean_db) as s, s.begin():
        assert queue.release_claims(s, [c]) == 1
    d = get(clean_db, doc_id)
    assert (d.status, d.attempts, d.claimed_at) == ("pending", 0, None)


def test_backoff_grows_and_honours_retry_after():
    assert 4 <= queue.backoff_seconds(1) <= 6
    assert 8 <= queue.backoff_seconds(2) <= 12
    assert queue.backoff_seconds(20) <= queue.BACKOFF_MAX_SECONDS * 1.2
    assert queue.backoff_seconds(1, retry_after=30) >= 30


# ---- running jobs ----------------------------------------------------------------------


def _run(engine, folder, extractor, max_attempts=3, **kw):
    c = claim(engine, max_attempts=max_attempts)
    return c, pipeline.run_job(engine, c, LocalFolderSource(folder), extractor, max_attempts=max_attempts, **kw)


def _local_docs(engine, folder):
    from app.ingest.sync import reconcile

    with Session(engine) as s, s.begin():
        reconcile(s, LocalFolderSource(folder))


def test_successful_job_persists_extraction_and_marks_done(clean_db, folder):
    _local_docs(clean_db, folder)
    ex = make_extraction(text="Fund,Ret transcribed")
    c, status = _run(clean_db, folder, FunctionExtractor(lambda content, mime: ex, model="m1"))
    assert status in PROCESSED
    d = get(clean_db, c.document_id)
    assert (d.status, d.document_type, d.document_type_confidence) == (status, "factsheet", 0.95)
    assert d.processed_at is not None and d.claimed_at is None and d.last_error is None
    assert d.text_content.startswith("Fund,Ret")  # CSV text, not the transcription
    (row,) = extractions(clean_db, c.document_id)
    assert (row.status, row.source, row.model, row.input_tokens, row.output_tokens) == ("ok", "claude", "m1", 0, 0)
    assert row.latency_ms is not None and row.prompt_version
    assert row.raw_json == ex.model_dump(mode="json")


def test_hook_receives_extraction_in_same_transaction(clean_db, folder):
    _local_docs(clean_db, folder)
    seen = {}

    def hook(session, document_id, extraction, *, extraction_id):
        seen.update(doc=document_id, ex=extraction, ex_id=extraction_id)
        assert session.get(Extraction, extraction_id) is not None
        return "needs_review"

    c, status = _run(clean_db, folder, FunctionExtractor(lambda b, m: make_extraction()), hook=hook)
    assert status == "needs_review" and seen["doc"] == c.document_id
    assert get(clean_db, c.document_id).status == "needs_review"


def test_hook_failure_rolls_back_and_retries(clean_db, folder):
    _local_docs(clean_db, folder)

    def hook(*a, **k):
        raise RuntimeError("normalizer bug")

    c, status = _run(clean_db, folder, FunctionExtractor(lambda b, m: make_extraction()), hook=hook)
    assert status == "pending"
    assert extractions(clean_db, c.document_id) == []  # rolled back with the hook
    assert get(clean_db, c.document_id).last_error == "post-processing error (RuntimeError)"


def test_transient_errors_back_off_then_fail_after_max_attempts(clean_db, folder):
    _local_docs(clean_db, folder)
    calls = []

    def flaky(content, mime):
        calls.append(1)
        raise TransientExtractionError("Anthropic API 429 rate_limit_error")

    ext = FunctionExtractor(flaky)
    c1, s1 = _run(clean_db, folder, ext)
    assert s1 == "pending"
    d = get(clean_db, c1.document_id)
    assert d.next_attempt_at is not None and d.last_error == "Anthropic API 429 rate_limit_error"
    assert claim(clean_db) is None or claim(clean_db).document_id != c1.document_id  # backing off
    for attempt in (2, 3):
        with Session(clean_db) as s, s.begin():
            s.execute(text("UPDATE documents SET status='pending', next_attempt_at = now() WHERE id = :i AND "
                           "status IN ('pending','processing')"), {"i": c1.document_id})
            s.execute(text("UPDATE documents SET status='done' WHERE id <> :i"), {"i": c1.document_id})
        c, st = _run(clean_db, folder, ext)
        assert c.document_id == c1.document_id and c.attempt == attempt
    assert st == "failed"
    d = get(clean_db, c1.document_id)
    assert d.status == "failed" and d.attempts == 3
    assert d.last_error == "Anthropic API 429 rate_limit_error (gave up after 3 attempts)"
    failed = extractions(clean_db, c1.document_id)
    assert len(failed) == 3 and all(r.status == "failed" and r.error for r in failed)


def test_permanent_error_fails_immediately(clean_db, folder):
    _local_docs(clean_db, folder)

    def nope(content, mime):
        raise PermanentExtractionError("Anthropic API 400 invalid_request_error")

    c, status = _run(clean_db, folder, FunctionExtractor(nope))
    d = get(clean_db, c.document_id)
    assert status == "failed" and d.attempts == 1 and d.last_error == "Anthropic API 400 invalid_request_error"


def test_error_messages_never_contain_secrets_or_content(clean_db, folder):
    _local_docs(clean_db, folder)
    secret = "sk-ant-api-SECRETSECRET"

    def leaky(content, mime):
        raise ValueError(f"bad value {content!r} {secret}")

    c, _ = _run(clean_db, folder, FunctionExtractor(leaky), secrets=[secret])
    d = get(clean_db, c.document_id)
    assert d.last_error == "extraction error (ValueError)"
    c2, _ = _run(clean_db, folder, FunctionExtractor(lambda b, m: (_ for _ in ()).throw(
        TransientExtractionError(f"upstream said {secret}"))), secrets=[secret])
    assert secret not in get(clean_db, c2.document_id).last_error


def test_missing_source_file_fails_and_changed_file_retries(clean_db, folder):
    _local_docs(clean_db, folder)
    (folder / "f0.csv").unlink()
    (folder / "f1.csv").write_bytes(b"changed since sync")
    out = {}
    for _ in range(3):
        c, status = _run(clean_db, folder, FunctionExtractor(lambda b, m: make_extraction()))
        out[c.drive_file_id] = (status, get(clean_db, c.document_id).last_error)
    assert out["f0.csv"][0] == "failed" and "no longer exists" in out["f0.csv"][1]
    assert out["f1.csv"][0] == "pending" and "changed after it was queued" in out["f1.csv"][1]
    assert out["f2.csv"][0] in PROCESSED


def test_job_that_lost_its_claim_writes_nothing(clean_db, folder):
    """Fencing: a stalled job whose document was reclaimed must not write a duplicate."""
    _local_docs(clean_db, folder)
    reclaimed = {}

    def slow(content, mime):
        # While this job "stalls", another worker reclaims the document.
        with Session(clean_db) as s, s.begin():
            s.execute(text("UPDATE documents SET claimed_at = now() - interval '1 hour' WHERE status='processing'"))
        reclaimed["claim"] = claim(clean_db, stale_after=60)
        return make_extraction()

    c, status = _run(clean_db, folder, FunctionExtractor(slow))
    assert status == "discarded"
    assert reclaimed["claim"].document_id == c.document_id and reclaimed["claim"].attempt == 2
    assert extractions(clean_db, c.document_id) == []
    d = get(clean_db, c.document_id)
    assert (d.status, d.attempts) == ("processing", 2)  # still owned by the second claim
    status2 = pipeline.run_job(clean_db, reclaimed["claim"], LocalFolderSource(folder),
                               FunctionExtractor(lambda b, m: make_extraction()), max_attempts=3)
    assert status2 in PROCESSED and len(extractions(clean_db, c.document_id)) == 1


def test_removed_while_processing_is_discarded(clean_db, folder):
    _local_docs(clean_db, folder)

    def remove_then_return(content, mime):
        with Session(clean_db) as s, s.begin():
            s.execute(text("UPDATE documents SET status='removed' WHERE status='processing'"))
        return make_extraction()

    c, status = _run(clean_db, folder, FunctionExtractor(remove_then_return))
    assert status == "discarded" and get(clean_db, c.document_id).status == "removed"
    assert extractions(clean_db, c.document_id) == []
