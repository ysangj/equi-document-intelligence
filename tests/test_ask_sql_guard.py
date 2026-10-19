"""Ask `query_sql` guard (FR-11, §14 "SQL guard") and read-only execution (FR-8, §12)."""

from __future__ import annotations

import datetime as dt
import decimal

import pytest
import sqlglot

from app.ask.sql_guard import (
    ALLOWED_VIEWS,
    MAX_LIMIT,
    SqlExecutionError,
    SqlGuardError,
    effective_limit,
    execute_readonly,
    guard_sql,
    json_value,
)
from app.config import get_settings
from app.db import engine_for

ACCEPTED = [
    "SELECT fund_name, value_pct FROM v_returns WHERE period_year = 2026 AND period_month = 1",
    "select * from public.v_returns",
    "SELECT r.fund_name, f.aliases FROM v_returns r JOIN v_funds f ON f.fund_id = r.fund_id",
    "SELECT s.account_name, h.holding_name FROM v_statements s LEFT JOIN v_holdings h USING (statement_id)",
    "WITH jan AS (SELECT * FROM v_returns WHERE period_month = 1) SELECT fund_name FROM jan ORDER BY value_pct DESC",
    "WITH a AS (SELECT fund_id FROM v_funds), b AS (SELECT * FROM a) SELECT * FROM b",
    "SELECT fund_name FROM v_returns UNION SELECT account_name FROM v_accounts",
    "SELECT metric, count(*) FROM v_fund_facts GROUP BY metric HAVING count(*) > 1",
    "SELECT reason, count(*) FROM v_returns, unnest(review_reasons) AS reason GROUP BY reason",
    "SELECT * FROM (SELECT fund_name, rank() OVER (ORDER BY value_pct DESC) AS rk FROM v_returns) t WHERE rk <= 3",
    "SELECT document_name FROM v_documents WHERE document_name ILIKE '%kestrel%' AND created_at::date >= '2026-01-01'",
    "SELECT fund_name FROM v_returns WHERE fund_id IN (SELECT fund_id FROM v_funds WHERE has_unconfirmed_alias)",
    "SELECT round(avg(value_pct)::numeric, 2), date_trunc('month', period_end) FROM v_returns GROUP BY 2",
    "SELECT * FROM v_returns WHERE 'conflict' = ANY(review_reasons);",
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_accepts_selects_ctes_joins_over_views(sql):
    out = guard_sql(sql)
    assert sqlglot.parse_one(out, read="postgres").args["limit"] is not None
    assert effective_limit(out) <= MAX_LIMIT


REJECTED = {
    "insert": ("INSERT INTO returns (value) VALUES (1)", "Only SELECT"),
    "update": ("UPDATE returns SET value = 0", "Only SELECT"),
    "delete": ("DELETE FROM returns", "Only SELECT"),
    "drop": ("DROP TABLE returns", "Only SELECT"),
    "drop_view": ("DROP VIEW v_returns", "Only SELECT"),
    "create": ("CREATE TABLE x (a int)", "Only SELECT"),
    "create_as": ("CREATE TABLE x AS SELECT * FROM v_returns", "Only SELECT"),
    "alter": ("ALTER TABLE returns ADD COLUMN x int", "Only SELECT"),
    "truncate": ("TRUNCATE returns", "Only SELECT"),
    "grant": ("GRANT SELECT ON returns TO app_readonly", "Only SELECT"),
    "revoke": ("REVOKE SELECT ON v_returns FROM app_readonly", "Only SELECT"),
    "copy": ("COPY returns TO STDOUT", "Only SELECT"),
    "copy_program": ("COPY (SELECT 1) TO PROGRAM 'id'", "Only SELECT"),
    "set": ("SET statement_timeout = 0", "Only SELECT"),
    "explain": ("EXPLAIN ANALYZE SELECT * FROM v_returns", "Only SELECT"),
    "vacuum": ("VACUUM returns", "Only SELECT"),
    "multi": ("SELECT * FROM v_returns; SELECT * FROM v_funds", "Exactly one statement"),
    "multi_dml": ("SELECT 1; DROP TABLE returns", "Exactly one statement"),
    "base_returns": ("SELECT * FROM returns", "Only these views"),
    "base_documents": ("SELECT name FROM documents", "Only these views"),
    "base_join": ("SELECT f.canonical_name, r.value FROM returns r JOIN funds f ON f.id = r.fund_id",
                  "(found: returns, funds)"),
    "base_in_subquery": ("SELECT * FROM v_returns WHERE fund_id IN (SELECT id FROM funds)", "Only these views"),
    "base_in_cte": ("WITH x AS (SELECT * FROM oauth_credentials) SELECT * FROM x", "Only these views"),
    "review_queue": ("SELECT * FROM review_queue", "Only these views"),
    "pg_catalog": ("SELECT * FROM pg_catalog.pg_tables", "Only these views"),
    "pg_catalog_view_name": ("SELECT * FROM pg_catalog.v_returns", "Only these views"),
    "pg_roles": ("SELECT rolname FROM pg_roles", "Only these views"),
    "information_schema": ("SELECT * FROM information_schema.tables", "Only these views"),
    "other_schema": ("SELECT * FROM other.v_returns", "Only these views"),
    "cross_db": ("SELECT * FROM docintel.public.v_returns", "Only these views"),
    "pg_sleep": ("SELECT pg_sleep(10)", "pg_sleep"),
    "pg_sleep_qualified": ("SELECT pg_catalog.pg_sleep(1) FROM v_returns", "pg_sleep"),
    "pg_read_file": ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    "pg_ls_dir": ("SELECT pg_ls_dir('.')", "pg_ls_dir"),
    "lo_import": ("SELECT lo_import('/etc/passwd')", "lo_import"),
    "lo_export": ("SELECT lo_export(1, '/tmp/x')", "lo_export"),
    "dblink": ("SELECT * FROM v_returns WHERE dblink_exec('x', 'y') = ''", "dblink_exec"),
    "set_config": ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
    "current_setting": ("SELECT current_setting('data_directory')", "current_setting"),
    "query_to_xml": ("SELECT query_to_xml('select * from returns', true, true, '')", "query_to_xml"),
    "nextval": ("SELECT nextval('returns_id_seq')", "nextval"),
    "terminate": ("SELECT pg_terminate_backend(1)", "pg_terminate_backend"),
    "regclass": ("SELECT 'returns'::regclass", "reg"),
    "select_into": ("SELECT * INTO stolen FROM v_returns", "INTO"),
    "for_update": ("SELECT * FROM v_returns FOR UPDATE", "Locking"),
    "for_share": ("SELECT * FROM v_returns FOR SHARE", "Locking"),
    "dml_cte": ("WITH x AS (DELETE FROM returns RETURNING *) SELECT * FROM x", "Only SELECT"),
    "insert_cte": ("WITH x AS (INSERT INTO funds (canonical_name) VALUES ('x') RETURNING id) SELECT * FROM x",
                   "Only SELECT"),
    "generate_series": ("SELECT * FROM generate_series(1, 100000000)", "Table functions"),
    "rows_from": ("SELECT * FROM ROWS FROM (generate_series(1, 2))", "Table functions"),
    "empty": ("   ", "empty"),
    "garbage": ("SELEC * FRM", "Only SELECT"),
}


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_rejects(name):
    sql, fragment = REJECTED[name]
    with pytest.raises(SqlGuardError) as e:
        guard_sql(sql)
    assert fragment in str(e.value), str(e.value)


# ---- function allowlist (BUG-Q4-01) ---------------------------------------------

# Server-info / system functions. Several are typed sqlglot nodes whose class name
# differs from the SQL name (version() -> exp.CurrentVersion), which a name-only
# denylist missed. (sql, name shown in the message)
SYSTEM_FUNCTIONS = [
    ("SELECT version()", "version"),
    ("SELECT VERSION() FROM v_funds", "version"),
    ("SELECT current_database()", "current_database"),
    ("SELECT inet_server_addr()", "inet_server_addr"),
    ("SELECT inet_server_port()", "inet_server_port"),
    ("SELECT inet_client_addr()", "inet_client_addr"),
    ("SELECT inet_client_port()", "inet_client_port"),
    ("SELECT pg_backend_pid()", "pg_backend_pid"),
    ("SELECT pg_postmaster_start_time()", "pg_postmaster_start_time"),
    ("SELECT pg_current_logfile()", "pg_current_logfile"),
    ("SELECT txid_current()", "txid_current"),
    ("SELECT txid_current_snapshot()", "txid_current_snapshot"),
    ("SELECT current_user", "current_user"),
    ("SELECT session_user", "session_user"),
    ("SELECT current_schema", "current_schema"),
    ("SELECT current_schema()", "current_schema"),
    ("SELECT current_schemas(true)", "current_schemas"),
    ("SELECT current_catalog", "current_catalog"),
    ("SELECT current_role", "current_role"),
    ("SELECT current_role()", "current_role"),
    ("SELECT user", "user"),
    ("SELECT current_query()", "current_query"),
    ("SELECT has_table_privilege('returns', 'select')", "has_table_privilege"),
    ("SELECT has_column_privilege('returns', 'value', 'select')", "has_column_privilege"),
    ("SELECT to_regclass('returns')", "to_regclass"),
    ("SELECT to_regrole('app')", "to_regrole"),
    ("SELECT setseed(0.5)", "setseed"),
    ("SELECT pg_catalog.version()", "version"),
    ("SELECT pg_catalog.current_database()", "current_database"),
    # hidden in other clauses
    ("SELECT fund_name FROM v_funds WHERE version() IS NOT NULL", "version"),
    ("SELECT fund_name FROM v_funds ORDER BY current_database()", "current_database"),
    ("SELECT CASE WHEN current_user = 'app' THEN 1 END FROM v_funds", "current_user"),
    ("WITH x AS (SELECT version() AS v FROM v_funds) SELECT v FROM x", "version"),
    ("SELECT * FROM v_funds WHERE fund_id IN (SELECT pg_backend_pid())", "pg_backend_pid"),
    ("SELECT fund_name, row_number() OVER (ORDER BY session_user) FROM v_funds", "session_user"),
    ("SELECT coalesce(fund_name, current_schema) FROM v_funds", "current_schema"),
    ("SELECT fund_name FROM v_funds UNION SELECT version()", "version"),
    ("SELECT fund_name FROM v_funds WHERE fund_name = user", "user"),
]


@pytest.mark.parametrize("sql, name", SYSTEM_FUNCTIONS, ids=[s for s, _ in SYSTEM_FUNCTIONS])
def test_rejects_server_info_and_system_functions(sql, name):
    with pytest.raises(SqlGuardError) as e:
        guard_sql(sql)
    assert str(e.value).startswith(f"Function {name}"), str(e.value)
    assert "is not allowed" in str(e.value)


def test_version_message_matches_the_bug_report():
    with pytest.raises(SqlGuardError) as e:
        guard_sql("SELECT version()")
    assert str(e.value) == "Function version() is not allowed."


@pytest.mark.parametrize("sql, name", [
    ("SELECT gen_random_uuid()", "gen_random_uuid"),
    ("SELECT uuid_generate_v4()", "uuid_generate_v4"),
    ("SELECT random() FROM v_funds", "random"),
    ("SELECT clock_timestamp()", "clock_timestamp"),
    ("SELECT my_udf(fund_name) FROM v_funds", "my_udf"),
    ("SELECT kurtosis(value) FROM v_returns", "kurtosis"),
    ("SELECT encrypt_secret(fund_name) FROM v_funds", "encrypt_secret"),
])
def test_unlisted_functions_fail_closed(sql, name):
    with pytest.raises(SqlGuardError) as e:
        guard_sql(sql)
    msg = str(e.value)
    assert msg.startswith(f"Function {name}() is not allowed. Allowed: aggregates"), msg


@pytest.mark.parametrize("sql", [
    "SELECT pg_catalog.lower(fund_name) FROM v_funds",
    "SELECT public.lower(fund_name) FROM v_funds",
    "SELECT public.my_udf(1) FROM v_funds",
])
def test_rejects_schema_qualified_function_calls(sql):
    with pytest.raises(SqlGuardError) as e:
        guard_sql(sql)
    assert "Schema-qualified function calls are not allowed" in str(e.value)


@pytest.mark.parametrize("sql", [
    'SELECT "user" FROM v_funds',  # a quoted identifier is a column reference
    "SELECT f.user FROM v_funds f",  # a qualified column, not the keyword
    # boolean columns named like denied prefixes inside operator nodes (And/Or)
    "SELECT fund_name FROM v_funds WHERE has_unconfirmed_alias AND fund_name ILIKE '%a%'",
    "SELECT fund_name FROM v_funds WHERE has_unconfirmed_alias OR NOT has_unconfirmed_alias",
    "SELECT account_name FROM v_accounts WHERE has_unconfirmed_alias = true",
])
def test_column_names_that_look_like_functions_are_fine(sql):
    guard_sql(sql)


def test_allowlist_is_well_formed():
    from sqlglot import exp

    from app.ask import sql_guard as g

    # Every class name exists in the pinned sqlglot and is a Func subclass.
    missing = [n for n in g._ALLOWED_FUNC_CLASS_NAMES if not hasattr(exp, n)]
    assert missing == []
    assert all(issubclass(c, exp.Func) for c in g.ALLOWED_FUNC_CLASSES)
    # No server-info / session class is allowed.
    for name in ("CurrentVersion", "CurrentDatabase", "CurrentUser", "CurrentRole", "CurrentSchema",
                 "CurrentSchemas", "CurrentCatalog", "SessionUser", "Uuid", "Rand", "Anonymous",
                 "AnonymousAggFunc"):
        assert getattr(exp, name) not in g.ALLOWED_FUNC_CLASSES, name
    # The name allowlist never overlaps the denylist.
    assert [n for n in g.ALLOWED_FUNCTION_NAMES if g._denied_function(n)] == []


def test_every_session_or_server_func_class_is_rejected():
    """Sweep sqlglot's own Func classes: anything named like session/server info is not allowed."""
    from sqlglot import exp

    from app.ask import sql_guard as g

    def subclasses(c):
        for s in c.__subclasses__():
            yield s
            yield from subclasses(s)

    harmless = {"CurrentDate", "CurrentTime", "CurrentTimestamp", "CurrentDatetime"}
    for cls in set(subclasses(exp.Func)):
        n = cls.__name__
        if (n.startswith("Current") or n.startswith("Session") or "User" in n or "Version" in n) \
                and n not in harmless:
            assert cls not in g.ALLOWED_FUNC_CLASSES, n


# Legitimate analytics SQL (T8-style: rankings, compounding, dates, rounding,
# aggregation over the views). All columns are real view columns, so the
# readonly test below also runs every one in Postgres.
ANALYTICS = [
    # rankings
    "SELECT return_id, fund_name, value_pct, rank() OVER (ORDER BY value_pct DESC) AS rk FROM v_returns "
    "WHERE period_year = 2026 AND period_month = 1 AND return_type = 'MTD' AND net_gross = 'net'",
    "SELECT * FROM (SELECT fund_name, value_pct, dense_rank() OVER (PARTITION BY period_month ORDER BY value DESC) AS r, "
    "row_number() OVER (PARTITION BY period_month ORDER BY value) AS rn FROM v_returns) t WHERE r <= 3",
    "SELECT fund_name, ntile(4) OVER w, percent_rank() OVER w, cume_dist() OVER w FROM v_returns "
    "WINDOW w AS (ORDER BY value)",
    "SELECT fund_name, period_end, value, lag(value) OVER (PARTITION BY fund_id ORDER BY period_end) AS prev, "
    "lead(value, 1, 0) OVER (PARTITION BY fund_id ORDER BY period_end) AS nxt, "
    "first_value(value) OVER (PARTITION BY fund_id ORDER BY period_end) AS first, "
    "last_value(value) OVER (PARTITION BY fund_id ORDER BY period_end "
    "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last, "
    "nth_value(value, 2) OVER (PARTITION BY fund_id ORDER BY period_end) AS second FROM v_returns",
    # compounding
    "SELECT fund_id, fund_name, round(((exp(sum(ln(1 + value))) - 1) * 100)::numeric, 2) AS ytd_pct, count(*) AS months "
    "FROM v_returns WHERE return_type = 'MTD' AND period_year = 2026 GROUP BY fund_id, fund_name ORDER BY ytd_pct DESC",
    "SELECT fund_name, period_end, exp(sum(ln(1 + value)) OVER (PARTITION BY fund_id ORDER BY period_end)) - 1 AS cum "
    "FROM v_returns WHERE return_type = 'MTD'",
    "SELECT fund_name, power(1 + avg(value), 12) - 1 AS annualised, sqrt(12) * stddev_samp(value) AS vol "
    "FROM v_returns GROUP BY fund_name",
    # dates
    "SELECT date_trunc('month', period_end) AS m, extract(year FROM period_end) AS y, "
    "extract(month FROM period_end) AS mo, date_part('quarter', period_end) AS q, count(*) "
    "FROM v_returns GROUP BY 1, 2, 3, 4 ORDER BY 1",
    "SELECT to_char(period_end, 'Mon YYYY') AS label, make_date(period_year, period_month, 1) AS first_day, "
    "period_end + interval '1 month' AS next, age(period_end, make_date(2026, 1, 1)) AS since "
    "FROM v_returns",
    "SELECT document_id, document_name, created_at::date, date_trunc('day', processed_at), "
    "extract(epoch FROM created_at) FROM v_documents WHERE created_at >= now() - interval '30 days' "
    "AND created_at < current_timestamp AND created_at::date <= current_date",
    "SELECT period_end FROM v_returns WHERE period_end >= date_trunc('year', current_date) - interval '1 year' "
    "AND period_end BETWEEN '2026-01-01' AND '2026-12-31'",
    # rounding and math
    "SELECT round(avg(value_pct)::numeric, 2), round(stddev(value_pct)::numeric, 3), min(value), max(value), "
    "abs(min(value)), sign(sum(value)), ceil(max(value_pct)), floor(min(value_pct)), trunc(avg(value_pct)::numeric, 1), "
    "greatest(max(value), 0), least(min(value), 0), variance(value), var_pop(value), stddev_pop(value) FROM v_returns",
    "SELECT fund_name, mod(period_month, 3), period_month % 3, div(period_month, 3), width_bucket(value, -0.1, 0.1, 10), "
    "log(10, 100), ln(2), exp(1), cbrt(27), pi() FROM v_returns",
    # strings and aggregation
    "SELECT string_agg(fund_name, ', ' ORDER BY fund_name) AS funds, array_agg(DISTINCT manager) AS managers, "
    "count(DISTINCT fund_id) FROM v_funds",
    "SELECT lower(fund_name), upper(manager), initcap(strategy), length(fund_name), substring(fund_name, 1, 5), "
    "substr(fund_name, 1, 3), position('Fund' IN fund_name), strpos(fund_name, 'Fund'), replace(fund_name, 'LP', ''), "
    "trim(fund_name), btrim(fund_name), left(fund_name, 3), right(fund_name, 3), concat(fund_name, ' (', manager, ')'), "
    "concat_ws(' / ', fund_name, manager), split_part(fund_name, ' ', 1), regexp_replace(fund_name, '\\s+', ' ', 'g'), "
    "lpad(fund_name, 40, '.'), fund_name || ' - ' || coalesce(manager, 'n/a'), format('%s (%s)', fund_name, strategy) "
    "FROM v_funds",
    "SELECT coalesce(manager, 'unknown'), nullif(strategy, ''), "
    "CASE WHEN has_unconfirmed_alias THEN 'check' ELSE 'ok' END, CASE currency WHEN 'USD' THEN 1 ELSE 0 END "
    "FROM v_funds",
    # casts to non-reg types
    "SELECT value::numeric(10, 4), value::float, value::double precision, period_end::text, period_year::bigint, "
    "CAST(period_end AS timestamp), '2026-01-31'::date, value_pct::int FROM v_returns",
    # conditional aggregates, ordered-set aggregates, FILTER
    "SELECT fund_name, sum(CASE WHEN period_month = 1 THEN value END) AS jan, "
    "sum(CASE WHEN period_month = 2 THEN value END) AS feb, count(*) FILTER (WHERE status = 'conflict') AS conflicts "
    "FROM v_returns GROUP BY fund_name HAVING count(*) > 0",
    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value), percentile_disc(0.5) WITHIN GROUP (ORDER BY value), "
    "mode() WITHIN GROUP (ORDER BY fund_name), bool_and(value > 0), bool_or(value > 0), every(value > -1), "
    "corr(value, confidence), covar_samp(value, confidence), regr_slope(value, confidence) FROM v_returns",
    # arrays (review_reasons is text[])
    "SELECT return_id, array_length(review_reasons, 1), cardinality(review_reasons), "
    "array_to_string(review_reasons, ', '), review_reasons @> ARRAY['conflict'], array_position(review_reasons, 'conflict') "
    "FROM v_returns WHERE 'conflict' = ANY(review_reasons) OR status <> 'ok'",
    "SELECT reason, count(*) FROM v_returns, unnest(review_reasons) AS reason GROUP BY reason ORDER BY count(*) DESC",
    "SELECT fund_name, unnest(aliases) AS alias FROM v_funds",
    # statements / holdings arithmetic
    "SELECT s.statement_id, s.account_name, s.document_name, s.closing_balance - (s.opening_balance "
    "+ coalesce(s.contributions, 0) - coalesce(s.withdrawals, 0) + coalesce(s.pnl, 0) - coalesce(s.fees, 0)) AS gap, "
    "round(100.0 * h.market_value / nullif(sum(h.market_value) OVER (PARTITION BY h.statement_id), 0), 2) AS w "
    "FROM v_statements s JOIN v_holdings h USING (statement_id)",
    "SELECT fact_id, fund_name, metric, value, value_text, as_of_date FROM v_fund_facts "
    "WHERE metric IN ('aum', 'sharpe') AND value IS NOT NULL ORDER BY as_of_date DESC NULLS LAST",
    # subqueries, EXISTS, DISTINCT ON, regex predicates
    "SELECT fund_name FROM v_funds f WHERE EXISTS (SELECT 1 FROM v_returns r WHERE r.fund_id = f.fund_id "
    "AND r.value < 0) AND fund_name ~* 'fund' AND fund_name NOT ILIKE '%test%'",
    "SELECT DISTINCT ON (fund_id) fund_id, fund_name, period_end, value_pct FROM v_returns "
    "ORDER BY fund_id, period_end DESC",
    "SELECT fund_name, (SELECT max(value_pct) FROM v_returns) AS best_overall, value_pct IS DISTINCT FROM 0 "
    "FROM v_returns",
    "WITH m AS (SELECT fund_id, fund_name, period_month, value FROM v_returns WHERE return_type = 'MTD'), "
    "best AS (SELECT period_month, max(value) AS v FROM m GROUP BY period_month) "
    "SELECT m.fund_name, m.period_month, round((m.value * 100)::numeric, 2) FROM m JOIN best b "
    "ON b.period_month = m.period_month AND b.v = m.value ORDER BY m.period_month",
    # JSON building
    "SELECT json_agg(fund_name), jsonb_build_object('n', count(*)), json_build_object('a', 1) FROM v_funds",
]


@pytest.mark.parametrize("sql", ANALYTICS)
def test_accepts_legitimate_analytics_sql(sql):
    out = guard_sql(sql)
    assert effective_limit(out) <= MAX_LIMIT


def test_unparseable_sql_is_a_guard_error():
    with pytest.raises(SqlGuardError):
        guard_sql("SELECT (((")


def test_view_error_names_the_views():
    with pytest.raises(SqlGuardError) as e:
        guard_sql("SELECT * FROM returns")
    msg = str(e.value)
    assert all(v in msg for v in ALLOWED_VIEWS)
    assert msg == ("Only these views may be queried: v_returns, v_fund_facts, v_statements, v_holdings, v_funds, "
                   "v_accounts, v_documents (found: returns).")


@pytest.mark.parametrize("sql, expected", [
    ("SELECT fund_name FROM v_returns", 200),
    ("SELECT fund_name FROM v_returns LIMIT 5", 5),
    ("SELECT fund_name FROM v_returns LIMIT 200", 200),
    ("SELECT fund_name FROM v_returns LIMIT 201", 200),
    ("SELECT fund_name FROM v_returns LIMIT 100000", 200),
    ("SELECT fund_name FROM v_returns LIMIT ALL", 200),
    ("SELECT fund_name FROM v_returns LIMIT 10 + 1000", 200),
    ("SELECT fund_name FROM v_returns LIMIT 3 OFFSET 2", 3),
    ("SELECT fund_name FROM v_returns FETCH FIRST 500 ROWS ONLY", 200),
    ("SELECT fund_name FROM v_returns FETCH FIRST 7 ROWS ONLY", 7),
    ("SELECT fund_name FROM v_returns UNION SELECT fund_name FROM v_funds", 200),
    ("SELECT * FROM (SELECT * FROM v_returns LIMIT 900) t", 200),  # the outer LIMIT governs
])
def test_limit_added_and_clamped(sql, expected):
    out = guard_sql(sql)
    assert effective_limit(out) == expected, out


def test_limit_rewrite_keeps_the_query():
    out = guard_sql("SELECT fund_name, value_pct FROM v_returns WHERE period_month = 1 ORDER BY value_pct DESC")
    assert out == ("SELECT fund_name, value_pct FROM v_returns WHERE period_month = 1 "
                   "ORDER BY value_pct DESC LIMIT 200")
    assert guard_sql("SELECT 1 FROM v_funds LIMIT 1000 OFFSET 5").endswith("LIMIT 200 OFFSET 5")


def test_custom_max_limit():
    assert effective_limit(guard_sql("SELECT * FROM v_funds", max_limit=10)) == 10


def test_json_value_conversions():
    assert json_value(decimal.Decimal("2.84")) == 2.84
    assert json_value(decimal.Decimal("100")) == 100 and isinstance(json_value(decimal.Decimal("100")), int)
    assert json_value(decimal.Decimal("12400000.00")) == 12400000.0
    assert json_value(dt.date(2026, 1, 31)) == "2026-01-31"
    assert json_value(dt.datetime(2026, 1, 31, 12, 0, tzinfo=dt.timezone.utc)) == "2026-01-31T12:00:00Z"
    assert json_value(["conflict"]) == ["conflict"]
    assert json_value(float("nan")) is None
    assert json_value({"a": decimal.Decimal("1.5")}) == {"a": 1.5}


# ---- execution as app_readonly (real Postgres) ----------------------------------


@pytest.fixture
def ro_engine(test_engine):
    return engine_for(get_settings().database_readonly_url)


@pytest.fixture
def seeded(clean_db):
    from sqlalchemy import text

    with clean_db.begin() as c:
        doc = c.execute(text("""INSERT INTO documents (source, drive_file_id, name, mime_type, md5_checksum, status,
                                document_type) VALUES ('local', 'k.pdf', 'k.pdf', 'application/pdf', 'x', 'done',
                                'factsheet') RETURNING id""")).scalar()
        fund = c.execute(text("INSERT INTO funds (canonical_name) VALUES ('Kestrel Quant Equity Fund LP') "
                              "RETURNING id")).scalar()
        for month, value in ((1, 0.0284), (2, -0.0092), (3, 0.0135)):
            c.execute(text("""INSERT INTO returns (fund_id, document_id, period_end, return_type, net_gross, value,
                              page, source_snippet, confidence, status)
                              VALUES (:f, :d, make_date(2026, :m, 1) + interval '1 month' - interval '1 day',
                                      'MTD', 'net', :v, 1, 'x', 0.9, 'ok')"""),
                      {"f": fund, "d": doc, "m": month, "v": value})
    return clean_db


def test_execute_reads_views_as_readonly(seeded, ro_engine):
    sql = guard_sql("SELECT return_id, fund_name, period_end, value, value_pct, status, review_reasons "
                    "FROM v_returns ORDER BY period_end")
    res = execute_readonly(ro_engine, sql)
    assert res.sql == sql
    assert res.columns == ["return_id", "fund_name", "period_end", "value", "value_pct", "status", "review_reasons"]
    assert [r[2] for r in res.rows] == ["2026-01-31", "2026-02-28", "2026-03-31"]
    assert res.rows[0][3] == 0.0284 and res.rows[0][4] == 2.84
    assert res.rows[0][6] == []
    assert res.truncated is False


def test_execute_marks_truncated_when_limit_reached(seeded, ro_engine):
    res = execute_readonly(ro_engine, guard_sql("SELECT return_id FROM v_returns LIMIT 2"))
    assert len(res.rows) == 2 and res.truncated is True
    res = execute_readonly(ro_engine, guard_sql("SELECT return_id FROM v_returns LIMIT 3"))
    assert len(res.rows) == 3 and res.truncated is True
    res = execute_readonly(ro_engine, guard_sql("SELECT return_id FROM v_returns LIMIT 4"))
    assert len(res.rows) == 3 and res.truncated is False


def test_percent_and_colons_are_literal(seeded, ro_engine):
    res = execute_readonly(ro_engine, guard_sql(
        "SELECT fund_name, period_end::text AS p FROM v_returns WHERE fund_name ILIKE '%kestrel%' LIMIT 1"))
    assert res.rows == [["Kestrel Quant Equity Fund LP", "2026-01-31"]]


def test_readonly_role_cannot_read_base_tables(seeded, ro_engine):
    """Defence in depth: even SQL that skips the guard cannot read base tables."""
    for sql in ("SELECT * FROM returns LIMIT 1", "SELECT * FROM documents LIMIT 1",
                "SELECT * FROM oauth_credentials LIMIT 1", "SELECT * FROM review_queue LIMIT 1"):
        with pytest.raises(SqlExecutionError) as e:
            execute_readonly(ro_engine, sql)
        assert "Permission denied" in str(e.value)


def test_readonly_transaction_rejects_writes(seeded, ro_engine):
    with pytest.raises(SqlExecutionError) as e:
        execute_readonly(ro_engine, "CREATE TEMP TABLE t AS SELECT 1 LIMIT 1")
    assert "read-only" in str(e.value) or "Permission denied" in str(e.value)


def test_statement_timeout_fires(seeded, ro_engine):
    with pytest.raises(SqlExecutionError) as e:
        execute_readonly(ro_engine, "SELECT pg_sleep(3) LIMIT 1", timeout_ms=300)
    assert "statement timeout" in str(e.value)


def test_execution_error_message_is_returned(seeded, ro_engine):
    with pytest.raises(SqlExecutionError) as e:
        execute_readonly(ro_engine, guard_sql("SELECT no_such_column FROM v_returns"))
    assert "no_such_column" in str(e.value)


def test_connection_is_reusable_after_errors(seeded, ro_engine):
    for _ in range(3):
        with pytest.raises(SqlExecutionError):
            execute_readonly(ro_engine, "SELECT * FROM returns LIMIT 1")
    assert execute_readonly(ro_engine, guard_sql("SELECT count(*) AS n FROM v_returns")).rows == [[3]]


def test_analytics_sql_runs_as_readonly(seeded, ro_engine):
    """Every accepted analytics query, as rewritten by the guard, is valid Postgres over the views."""
    failures = []
    for sql in ANALYTICS:
        try:
            execute_readonly(ro_engine, guard_sql(sql))
        except SqlExecutionError as e:
            failures.append((sql[:80], str(e)))
    assert failures == []


def test_blocked_function_would_have_run_without_the_guard(seeded, ro_engine):
    """version() is executable by app_readonly, so the guard is the only layer that stops it (BUG-Q4-01)."""
    res = execute_readonly(ro_engine, "SELECT version() LIMIT 1")
    assert res.rows and "PostgreSQL" in res.rows[0][0]
    with pytest.raises(SqlGuardError):
        guard_sql("SELECT version()")
