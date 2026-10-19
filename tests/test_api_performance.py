"""§12 "table page loads < 1 s for <= 10k rows": 10 000 returns rows, then page loads,
every sort column, filters, a deep page and the full CSV export are timed through the
real app. Timings are printed (`pytest -s`) and each page load must stay under 1 s."""

from __future__ import annotations

import csv
import io
import time

import pytest

from app.schemas import ReturnsPage, ReturnsQuery
from tests.api_helpers import api_client, exec_sql, q1, truncate, validate

N_FUNDS, N_MONTHS, N_DOCS = 100, 100, 10
PAGE_BUDGET_S = 1.0

SEED_SQL = f"""
INSERT INTO funds (canonical_name, manager, strategy, currency)
SELECT 'Perf Fund ' || lpad(g::text, 3, '0'), 'Manager ' || (g % 7), 'Strategy ' || (g % 5),
       CASE WHEN g % 3 = 0 THEN 'EUR' ELSE 'USD' END
FROM generate_series(1, {N_FUNDS}) g;

INSERT INTO documents (source, drive_file_id, name, mime_type, md5_checksum, status, document_type, search_indexed)
SELECT 'local', 'perf_' || g || '.pdf', 'perf_' || g || '.pdf', 'application/pdf', md5(g::text), 'done',
       CASE WHEN g % 2 = 0 THEN 'factsheet' ELSE 'performance_report' END, true
FROM generate_series(1, {N_DOCS}) g;

INSERT INTO returns (fund_id, document_id, period_end, period_label, return_type, net_gross, value, page,
                     source_snippet, confidence, status, review_reasons)
SELECT f.id,
       (SELECT min(id) FROM documents) + ((f.id + m) % {N_DOCS}),
       (date_trunc('month', date '2010-01-01' + make_interval(months => m)) + interval '1 month - 1 day')::date,
       to_char(date '2010-01-01' + make_interval(months => m), 'Mon YYYY'),
       CASE WHEN m % 12 = 11 THEN 'YTD' ELSE 'MTD' END,
       CASE WHEN m % 4 = 0 THEN 'gross' ELSE 'net' END,
       CASE WHEN m % 97 = 0 THEN NULL ELSE round(((random() - 0.5) / 10)::numeric, 6) END,
       1, 'Net return ' || m, round((0.5 + random() / 2)::numeric, 2)::float8,
       CASE WHEN (f.id + m) % 50 = 0 THEN 'needs_review' ELSE 'ok' END,
       CASE WHEN (f.id + m) % 50 = 0 THEN ARRAY['low_confidence'] ELSE '{{}}'::text[] END
FROM funds f CROSS JOIN generate_series(0, {N_MONTHS - 1}) m;
ANALYZE funds; ANALYZE documents; ANALYZE returns;
"""


@pytest.fixture(scope="module")
def client(test_engine):
    truncate(test_engine)
    exec_sql(test_engine, SEED_SQL)
    assert q1(test_engine, "SELECT count(*) FROM v_returns") == N_FUNDS * N_MONTHS == 10_000
    with api_client() as c:
        c.get("/api/returns?page_size=1")  # warm the pool
        yield c
    truncate(test_engine)


def timed(client, url):
    t = time.perf_counter()
    r = client.get(url)
    elapsed = time.perf_counter() - t
    assert r.status_code == 200, r.text
    return r, elapsed


def best_of(client, url, n=3):
    times = []
    for _ in range(n):
        r, t = timed(client, url)
        times.append(t)
    return r, min(times), max(times)


def test_page_loads_under_1s(client):
    report = []
    for url in [
        "/api/returns",
        "/api/returns?page_size=500",
        "/api/returns?page=200&page_size=50",
        "/api/returns?status=needs_review&review_reason=low_confidence",
        "/api/returns?fund_id=1&fund_id=50&return_type=MTD&net_gross=net",
        "/api/returns?date_from=2012-01-01&date_to=2014-12-31&document_type=factsheet",
        "/api/returns?sort=-value_pct,fund_name",
    ]:
        r, best, worst = best_of(client, url)
        validate(ReturnsPage, r.json())
        report.append((url, best, worst))
        assert worst < PAGE_BUDGET_S, (url, worst)
    assert r.json()["total"] == 10_000
    print("\n10k returns page loads (best / worst of 3):")
    for url, best, worst in report:
        print(f"  {best * 1000:7.1f} / {worst * 1000:7.1f} ms  {url}")


@pytest.mark.parametrize("col", ReturnsQuery.SORTABLE)
def test_every_sort_under_1s(client, col):
    for prefix in ("", "-"):
        r, best, worst = best_of(client, f"/api/returns?sort={prefix}{col}&page_size=100", n=2)
        assert worst < PAGE_BUDGET_S, (col, prefix, worst)
        body = r.json()
        assert body["total"] == 10_000 and len(body["items"]) == 100
        values = [i[col] for i in body["items"] if i[col] is not None]
        assert values == sorted(values, reverse=bool(prefix))
    print(f"\n  sort {col}: worst {worst * 1000:.1f} ms")


def test_full_csv_export_of_10k_rows(client):
    t = time.perf_counter()
    r = client.get("/api/returns?format=csv&sort=-period_end,fund_name")
    elapsed = time.perf_counter() - t
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    assert len(rows) == 10_001
    print(f"\n  CSV export of 10k rows: {elapsed * 1000:.0f} ms, {len(r.content) / 1e6:.1f} MB")
    assert elapsed < 10
