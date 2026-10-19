"""Background worker (`python -m app.worker`): sync + extraction (FR-4, FR-5).

Threads in one process:
- main thread: waits for migrations, recomputes every canonical fund/account name once
  (`app.ingest.canonical`, idempotent), then runs `sync_tick` every TICK_SECONDS (1 s):
    * a catch-up reconcile on boot (first successful tick of this process),
    * a reconcile whenever `sync_state.reconcile_requested_at` is set,
    * an incremental sync whenever `sync_requested_at` is set or SYNC_INTERVAL_SECONDS
      have passed since the last successful sync/reconcile.
  So api requests are honoured within ~1-2 s, independent of SYNC_INTERVAL_SECONDS.
  After a committed sync that removed documents, the canonical names of the
  funds/accounts they used are recomputed (own transaction, postprocess lock).
- WORKER_CONCURRENCY job threads: claim one document with SKIP LOCKED, process it, repeat.
- a heartbeat thread that keeps `claimed_at` fresh for in-flight jobs, so a crashed
  worker's jobs (stale `claimed_at`) are reclaimed by any other or later worker.
- an index thread (FR-7): every INDEX_INTERVAL_SECONDS it embeds documents left with
  `search_indexed = false` (embedding failed, or the process died between commit and
  embedding) or embedded with another EMBEDDING_MODEL. Job threads embed their own
  document right after its commit; this thread is the retry path. After a model
  failure it backs off exponentially (in memory; a restart simply retries).

Stateless (PRD §2): everything durable is in Postgres. The only in-memory state is
per-process bookkeeping (in-flight claims for the heartbeat, "boot reconcile done").
Any process can be killed at any time: an interrupted sync transaction rolls back, and
an interrupted job is reclaimed after `queue.STALE_AFTER_SECONDS`. SIGTERM/SIGINT hand
in-flight jobs straight back (`release_claims`) so a restart picks them up at once.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import ConfigError, Settings, get_settings, require_settings
from app.db import current_revision, get_engine
from app.extraction import Extractor, get_extractor
from app.ingest import canonical, queue
from app.ingest.pipeline import run_job, scrub
from app.ingest.sync import get_sync_state, incremental_sync, reconcile, record_sync_error
from app.logging_setup import configure_logging
from app.migrations_info import head_revisions
from app.search.embedding import EmbeddingError, get_embedder
from app.search.indexing import index_document, index_pending
from app.sources import FileSource, SourceAuthError, SourceError, resolve_source

log = logging.getLogger("app.worker")

MIGRATION_POLL_SECONDS = 2.0
TICK_SECONDS = 1.0
JOB_IDLE_SECONDS = 1.0
SYNC_ERROR_RETRY_SECONDS = 5.0
SHUTDOWN_JOIN_SECONDS = 2.0
INDEX_INTERVAL_SECONDS = 10.0
INDEX_MIN_AGE_SECONDS = 15.0  # leave freshly processed documents to their job thread
INDEX_BACKOFF_MAX_SECONDS = 300.0

SourceResolver = Callable[[Settings, Session], FileSource | None]


def wait_for_migrations(
    get_revision: Callable[[], str | None],
    heads: set[str],
    stop: threading.Event,
    poll_seconds: float = MIGRATION_POLL_SECONDS,
) -> bool:
    """Block until the DB revision is one of `heads`. Returns False if stopped first.

    DB connection errors are treated as "not ready yet" (Postgres may still be
    starting), so startup order never matters.
    """
    last_state: str | None = "__init__"
    while not stop.is_set():
        try:
            rev = get_revision()
            state = rev or "none"
        except OperationalError:
            rev, state = None, "db_unavailable"
        if rev is not None and rev in heads:
            log.info("migrations ready", extra={"event": "migrations_ready", "revision": rev})
            return True
        if state != last_state:
            log.info(
                "waiting for migrations",
                extra={"event": "waiting_for_migrations", "db_revision": state, "heads": sorted(heads)},
            )
            last_state = state
        stop.wait(poll_seconds)
    return False


class Worker:
    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        stop: threading.Event,
        *,
        extractor: Extractor | None = None,
        source_resolver: SourceResolver = resolve_source,
        concurrency: int | None = None,
        tick_seconds: float = TICK_SECONDS,
        job_idle_seconds: float = JOB_IDLE_SECONDS,
        heartbeat_seconds: float = queue.HEARTBEAT_SECONDS,
        stale_after: float = queue.STALE_AFTER_SECONDS,
        indexing: bool = True,
        index_interval_seconds: float = INDEX_INTERVAL_SECONDS,
        index_min_age_seconds: float = INDEX_MIN_AGE_SECONDS,
    ):
        self.settings = settings
        self.engine = engine
        self.stop = stop
        self._extractor = extractor
        self.source_resolver = source_resolver
        self.concurrency = concurrency or settings.worker_concurrency
        self.tick_seconds = tick_seconds
        self.job_idle_seconds = job_idle_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_after = stale_after
        self.indexing = indexing
        self.index_interval_seconds = index_interval_seconds
        self.index_min_age_seconds = index_min_age_seconds
        self._index_failures = 0
        self.secrets = settings.secret_values()
        self._inflight: set[queue.Claim] = set()
        self._lock = threading.Lock()
        self._booted = False  # boot reconcile done in this process
        self._sync_retry_at = 0.0  # monotonic time; backoff after a failed sync
        self._idle_logged = False
        self._threads: list[threading.Thread] = []

    @property
    def extractor(self) -> Extractor:
        if self._extractor is None:
            self._extractor = get_extractor(self.settings)
        return self._extractor

    # ---- sync -----------------------------------------------------------------------
    def sync_tick(self) -> str | None:
        """One check of sync_state. Returns 'reconcile', 'sync' or None (nothing due)."""
        if time.monotonic() < self._sync_retry_at:
            return None
        with Session(self.engine) as s:
            source = self.source_resolver(self.settings, s)
            if source is None:
                if not self._idle_logged:
                    log.info("no source to watch yet (Drive not connected or no folder); idling",
                             extra={"event": "sync_idle", "demo_mode": self.settings.demo_mode})
                    self._idle_logged = True
                s.rollback()
                return None
            self._idle_logged = False
            state = get_sync_state(s)
            now = s.execute(select(func.now())).scalar_one()
            last_ok = max((t for t in (state.last_synced_at, state.last_reconciled_at) if t), default=None)
            if not self._booted or state.reconcile_requested_at is not None or state.source != source.name:
                kind = "reconcile"
            elif (
                state.sync_requested_at is not None
                or last_ok is None
                or now - last_ok >= timedelta(seconds=self.settings.sync_interval_seconds)
            ):
                kind = "sync"
            else:
                s.rollback()
                return None
            s.commit()

        t0 = time.monotonic()
        try:
            with Session(self.engine) as s, s.begin():
                report = reconcile(s, source) if kind == "reconcile" else incremental_sync(s, source)
        except SourceAuthError as e:
            # Credentials rejected (revoked/expired): drop them and idle until the user
            # reconnects, instead of retrying every few seconds.
            msg = scrub(e.message, self.secrets)
            log.warning("sync failed: credentials rejected", extra={"event": "sync_auth_error", "kind": kind,
                                                                    "error": msg, "source": source.name})
            try:
                with Session(self.engine) as s, s.begin():
                    handler = getattr(source, "on_auth_failure", None)
                    if handler is not None:
                        handler(s, msg)
                    else:
                        record_sync_error(s, msg)
            except Exception:  # noqa: BLE001
                self._sync_retry_at = time.monotonic() + SYNC_ERROR_RETRY_SECONDS
            return None
        except Exception as e:  # noqa: BLE001 - sync errors never kill the worker
            msg = scrub(e.message if isinstance(e, SourceError) else f"{kind} failed ({type(e).__name__})",
                        self.secrets)
            self._sync_retry_at = time.monotonic() + SYNC_ERROR_RETRY_SECONDS
            log.warning("sync failed", extra={"event": "sync_error", "kind": kind, "error": msg,
                                              "source": source.name})
            try:
                with Session(self.engine) as s, s.begin():
                    record_sync_error(s, msg)
            except Exception:  # noqa: BLE001
                pass
            return None
        if report.kind == "stale":  # the watched folder changed meanwhile; next tick uses the new one
            log.info("watched folder changed during sync; retrying", extra={"event": "sync_stale"})
            return None
        if kind == "reconcile":
            self._booted = True
        if report.removed_document_ids:
            self.recompute_canonical_names(report.removed_document_ids)
        log.info("sync committed", extra={"event": f"{report.kind}_done", "source": source.name,
                                          "latency_ms": int((time.monotonic() - t0) * 1000), **report.as_log()})
        return report.kind

    # ---- canonical names ----------------------------------------------------------------
    def recompute_canonical_names(self, document_ids: list[int] | None = None) -> bool:
        """`app.ingest.canonical.run_recompute`: every fund/account at boot (None), or the
        ones removed documents used. Never fails the worker; the next boot run repairs
        anything missed."""
        try:
            res = canonical.run_recompute(self.engine, document_ids)
        except Exception as e:  # noqa: BLE001
            log.warning("canonical name recompute failed", extra={"event": "canonical_recompute_error",
                                                                  "error_type": type(e).__name__})
            return False
        log.info("canonical names recomputed", extra={"event": "canonical_recompute",
                                                      "scope": "all" if document_ids is None else "removed",
                                                      "renamed": len(res.renamed)})
        return True

    # ---- jobs -----------------------------------------------------------------------
    def claim_one(self) -> tuple[queue.Claim, FileSource] | None:
        with Session(self.engine) as s, s.begin():
            source = self.source_resolver(self.settings, s)
            if source is None:
                return None
            failed = queue.fail_exhausted_stale(s, max_attempts=self.settings.max_attempts,
                                                stale_after=self.stale_after)
            if failed:
                log.warning("interrupted jobs out of attempts marked failed",
                            extra={"event": "stale_failed", "count": failed})
            claim = queue.claim_next(s, max_attempts=self.settings.max_attempts, stale_after=self.stale_after,
                                     source_name=source.name)
        if claim is None:
            return None
        return claim, source

    def job_loop(self, index: int) -> None:
        while not self.stop.is_set():
            try:
                got = self.claim_one()
                if got is None:
                    self.stop.wait(self.job_idle_seconds)
                    continue
                claim, source = got
                with self._lock:
                    self._inflight.add(claim)
                log.info("job claimed", extra={"event": "job_claimed", "job_id": claim.job_id,
                                               "document_id": claim.document_id, "attempt": claim.attempt,
                                               "status": "processing", "worker_thread": index})
                try:
                    run_job(self.engine, claim, source, self.extractor, max_attempts=self.settings.max_attempts,
                            secrets=self.secrets, indexer=self.index_one if self.indexing else None)
                finally:
                    with self._lock:
                        self._inflight.discard(claim)
            except Exception as e:  # noqa: BLE001 - one failing job never kills the worker
                log.error("job loop error", extra={"event": "job_loop_error", "error_type": type(e).__name__})
                self.stop.wait(self.job_idle_seconds)

    # ---- search indexing (FR-7) -------------------------------------------------------------
    def index_one(self, engine: Engine, document_id: int) -> bool:
        return index_document(engine, document_id, embedder=get_embedder(self.settings.embedding_model))

    def index_tick(self) -> int:
        """One retry pass. Returns documents indexed; raises EmbeddingError."""
        return index_pending(self.engine, embedder=get_embedder(self.settings.embedding_model),
                             min_age_seconds=self.index_min_age_seconds)

    def index_loop(self) -> None:
        wait = self.index_interval_seconds
        while not self.stop.wait(wait):
            try:
                n = self.index_tick()
                self._index_failures = 0
                if n:
                    log.info("search index retry pass", extra={"event": "search_index_retry", "count": n})
            except EmbeddingError as e:
                self._index_failures += 1
                log.warning("embedding unavailable; backing off", extra={"event": "search_index_error",
                                                                        "error": str(e)})
            except Exception as e:  # noqa: BLE001 - the index thread never dies
                self._index_failures += 1
                log.warning("index pass failed", extra={"event": "search_index_error", "error_type": type(e).__name__})
            wait = self.index_interval_seconds if not self._index_failures else min(
                INDEX_BACKOFF_MAX_SECONDS, self.index_interval_seconds * 2 ** self._index_failures)

    def heartbeat_loop(self) -> None:
        while not self.stop.wait(self.heartbeat_seconds):
            with self._lock:
                claims = list(self._inflight)
            if not claims:
                continue
            try:
                with Session(self.engine) as s, s.begin():
                    queue.heartbeat(s, claims)
            except Exception as e:  # noqa: BLE001
                log.warning("heartbeat failed", extra={"event": "heartbeat_error", "error_type": type(e).__name__})

    def start_threads(self) -> None:
        for i in range(self.concurrency):
            t = threading.Thread(target=self.job_loop, args=(i,), name=f"job-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        hb = threading.Thread(target=self.heartbeat_loop, name="heartbeat", daemon=True)
        hb.start()
        self._threads.append(hb)
        if self.indexing:
            ix = threading.Thread(target=self.index_loop, name="indexer", daemon=True)
            ix.start()
            self._threads.append(ix)

    def shutdown(self) -> None:
        self.stop.set()
        with self._lock:
            claims = list(self._inflight)
        if claims:
            try:
                with Session(self.engine) as s, s.begin():
                    n = queue.release_claims(s, claims)
                log.info("released in-flight jobs", extra={"event": "claims_released", "count": n})
            except Exception as e:  # noqa: BLE001 - stale reclaim covers this
                log.warning("could not release claims", extra={"event": "release_error",
                                                               "error_type": type(e).__name__})
        for t in self._threads:
            t.join(timeout=SHUTDOWN_JOIN_SECONDS)

    def run(self, max_iterations: int | None = None) -> int:
        if not wait_for_migrations(lambda: current_revision(self.engine), head_revisions(), self.stop):
            return 0
        self.recompute_canonical_names()  # boot: idempotent, corrects names from older code
        log.info("worker running", extra={"event": "worker_running", "concurrency": self.concurrency,
                                          "demo_mode": self.settings.demo_mode,
                                          "extraction_source": "fixture" if self.settings.use_fixtures else "claude"})
        self.start_threads()
        n = 0
        try:
            while not self.stop.is_set():
                try:
                    run_iteration(self)
                except Exception as e:  # noqa: BLE001 - worker survives iteration failures (§12)
                    log.error("iteration failed", extra={"event": "iteration_error", "error_type": type(e).__name__})
                n += 1
                if max_iterations is not None and n >= max_iterations:
                    break
                self.stop.wait(self.tick_seconds)
        finally:
            self.shutdown()
        log.info("worker stopped", extra={"event": "shutdown"})
        return 0


def run_iteration(worker: Worker) -> None:
    """One main-loop iteration (a sync check)."""
    worker.sync_tick()


def run(settings: Settings, stop: threading.Event, max_iterations: int | None = None, **kwargs) -> int:
    return Worker(settings, kwargs.pop("engine", None) or get_engine(), stop, **kwargs).run(max_iterations)


def main() -> int:
    settings = get_settings()
    configure_logging(settings.secret_values())
    try:
        require_settings(settings)
    except ConfigError as e:
        log.error(str(e), extra={"event": "config_error", "missing": e.missing})
        return 2

    stop = threading.Event()

    def _handle(signum, _frame):
        log.info("signal received", extra={"event": "signal", "signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    log.info("worker starting", extra={"event": "startup", "demo_mode": settings.demo_mode})
    return run(settings, stop)


if __name__ == "__main__":
    raise SystemExit(main())
