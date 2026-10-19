"""`query_sql` guard and read-only execution (FR-11, FR-8, §12).

Guard rules (`guard_sql`), checked on the sqlglot postgres parse tree:
1. Exactly one statement. A trailing `;` is fine; `SELECT 1; SELECT 2` is not.
2. The statement is a SELECT, or a set operation (UNION / INTERSECT / EXCEPT) of
   SELECTs. CTEs are allowed only if every CTE body is a SELECT as well.
3. No data-changing or utility node anywhere in the tree (INSERT, UPDATE, DELETE,
   MERGE, CREATE, DROP, ALTER, TRUNCATE, GRANT, COPY, SET, any unparsed Command),
   which catches `WITH x AS (DELETE ... RETURNING *) SELECT ...`.
4. No `SELECT ... INTO` and no locking clause (`FOR UPDATE/SHARE`).
5. Relations: only the seven query views (`ALLOWED_VIEWS`), optionally qualified
   with `public.`, plus the query's own CTE names. Any other table, any other schema
   (`pg_catalog`, `information_schema`, ...) and table functions
   (`generate_series(...)`, `ROWS FROM`) are rejected. `unnest(...)` is allowed,
   since it only expands arrays already selected from a view.
6. Functions: an ALLOWLIST (BUG-Q4-01). Every node that is an `exp.Func` must be
   allowed, whether sqlglot parsed it into a typed class (`exp.Sum`,
   `exp.CurrentVersion`, ...) or kept it as `exp.Anonymous`:
   - typed nodes: their class must be in `ALLOWED_FUNC_CLASSES` (aggregates, window
     functions, math, string, date/time, array, JSON building, CASE / COALESCE /
     NULLIF / GREATEST / LEAST, casts, and operator nodes sqlglot models as Func:
     AND/OR, `@>`, `~*`, EXISTS, ...);
   - anonymous nodes: their lower-case name must be in `ALLOWED_FUNCTION_NAMES`.
   Anything else is rejected, so server-info and system functions are blocked even
   when sqlglot gives them a typed node whose class name differs from the SQL name
   (`version()` -> `exp.CurrentVersion`, `current_database()`, `current_user`,
   `session_user`, `current_schema`, `current_catalog`, `current_role()`, ...), and
   an unknown or newly-typed function fails closed. The old denylist (`pg_*`,
   `lo_*`, `dblink*`, `set_config`, `current_setting`, `*_to_xml`, sequence and
   server-info functions) is still checked first, against both the sqlglot name and
   the name the node renders as in Postgres, for a precise message and as defence in
   depth. Schema-qualified calls (`pg_catalog.lower(x)`) are rejected, as are the
   bare SQL value keywords sqlglot parses as columns (`user`, `current_role`, ...;
   a quoted `"user"` is a column reference and is left alone). Casts to `reg*`
   types (catalog lookups) are rejected; casts to other types are allowed.
7. LIMIT: added if missing (200). A literal LIMIT/FETCH above 200, or a
   non-literal one (`LIMIT ALL`, an expression), is replaced with 200.

`guard_sql` returns the rewritten SQL, which is what runs and what the citation
shows. `execute_readonly` runs it through `DATABASE_READONLY_URL` (`app_readonly`,
SELECT on the views only) in a READ ONLY transaction with a 5 s
`statement_timeout`, and always rolls back. The role itself also defaults to
read-only with a 5 s timeout (migration 0002), so this is defence in depth.
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import math
import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlalchemy import Engine
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

# sqlglot warns with the statement text when it falls back to a Command; the guard
# rejects those anyway, and SQL text is not logged (§12).
logging.getLogger("sqlglot").setLevel(logging.ERROR)

ALLOWED_VIEWS = ("v_returns", "v_fund_facts", "v_statements", "v_holdings", "v_funds", "v_accounts", "v_documents")
MAX_LIMIT = 200
STATEMENT_TIMEOUT_MS = 5000

# Function names (lower case) that are never allowed, and prefixes.
DENIED_FUNCTIONS = frozenset({
    "set_config", "current_setting", "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema",
    "table_to_xml", "table_to_xml_and_xmlschema", "table_to_xmlschema", "cursor_to_xml", "cursor_to_xmlschema",
    "schema_to_xml", "schema_to_xml_and_xmlschema", "schema_to_xmlschema", "database_to_xml",
    "database_to_xml_and_xmlschema", "database_to_xmlschema", "nextval", "setval", "currval", "lastval",
    "txid_current", "inet_server_addr", "inet_server_port", "inet_client_addr", "version",
    "has_table_privilege", "has_database_privilege", "has_schema_privilege", "to_regclass", "to_regproc",
    "current_user", "session_user", "current_database", "current_schema", "current_schemas",
    "current_version", "current_catalog", "current_role", "system_user", "user", "current_query",
    "inet_client_port", "setseed", "has_column_privilege", "has_function_privilege", "to_regtype",
    "to_regrole", "to_regnamespace", "to_regprocedure", "to_regoper", "to_regoperator", "to_regcollation",
})
DENIED_PREFIXES = ("pg_", "lo_", "dblink", "file_", "brin_", "gin_", "xact_", "txid_", "inet_", "has_")

# The function allowlist (rule 6). Typed sqlglot nodes are matched by class: these
# are the classes sqlglot 30.x produces for normal analytics SQL in the postgres
# dialect. Names that are missing from the installed sqlglot are ignored, and
# `tests/test_ask_sql_guard.py` checks that every one resolves to an exp.Func.
_ALLOWED_FUNC_CLASS_NAMES = (
    # aggregates
    "Count", "Sum", "Avg", "Min", "Max", "ArrayAgg", "GroupConcat", "Stddev", "StddevPop", "StddevSamp",
    "Variance", "VariancePop", "Corr", "CovarPop", "CovarSamp", "RegrSlope", "RegrIntercept", "RegrR2",
    "RegrCount", "RegrAvgx", "RegrAvgy", "RegrSxx", "RegrSyy", "RegrSxy", "LogicalAnd", "LogicalOr",
    "PercentileCont", "PercentileDisc", "Median", "Mode", "BitwiseAndAgg", "BitwiseOrAgg",
    "JSONArrayAgg", "JSONObjectAgg", "JSONBObjectAgg",
    # window functions
    "RowNumber", "Rank", "DenseRank", "Ntile", "PercentRank", "CumeDist", "Lag", "Lead", "FirstValue",
    "LastValue", "NthValue",
    # math
    "Abs", "Sign", "Ceil", "Floor", "Round", "Trunc", "Exp", "Ln", "Log", "Pow", "Sqrt", "Cbrt", "Pi",
    "Greatest", "Least", "WidthBucket", "Degrees", "Radians",
    # conditionals and casts
    "Case", "If", "Coalesce", "Nullif", "Cast", "TryCast",
    # strings
    "Lower", "Upper", "Initcap", "Length", "BitLength", "Substring", "StrPosition", "Replace", "Trim", "Left",
    "Right", "Concat", "ConcatWs", "SplitPart", "RegexpReplace", "RegexpSubstr", "RegexpExtract",
    "RegexpCount", "RegexpInstr", "RegexpSplit", "Pad", "Reverse", "Repeat", "MD5", "SHA2", "Format",
    "Translate", "StartsWith", "StringToArray", "Overlay", "Ascii", "Chr", "Encode", "Decode",
    # dates and times
    "TimestampTrunc", "DateTrunc", "Extract", "CurrentDate", "CurrentTimestamp", "CurrentTime", "Localtime",
    "Localtimestamp", "TimeToStr", "StrToDate", "StrToTime", "ToChar", "ToNumber", "DateBin", "MakeInterval",
    "JustifyDays", "JustifyHours", "JustifyInterval", "Date", "TimestampFromParts",
    # arrays
    "Array", "ArrayAppend", "ArrayPrepend", "ArrayConcat", "ArrayContains", "ArrayContainsAll",
    "ArrayContainedBy", "ArrayOverlaps", "ArrayPosition", "ArrayRemove", "ArraySize", "ArrayToString",
    "Explode", "Unnest",
    # JSON building / access (the views have no JSON columns, but these are harmless)
    "JSONObject", "JSONArray", "JSONExtract", "JSONExtractScalar", "JSONBExtract", "JSONBExtractScalar",
    "JSONBContains", "JSONArrayContains", "JSONBContainsTopKey", "JSONBContainsAllTopKeys",
    "JSONBContainsAnyTopKeys",
    # operators and predicates that sqlglot models as Func subclasses
    "And", "Or", "Xor", "Exists", "RegexpLike", "RegexpILike", "RegexpFullMatch", "Collate",
)
ALLOWED_FUNC_CLASSES = tuple(getattr(exp, n) for n in _ALLOWED_FUNC_CLASS_NAMES if hasattr(exp, n))

# Postgres function names (lower case) allowed when sqlglot leaves the call as
# exp.Anonymous. A name sqlglot types (sum, round, date_trunc, ...) is covered by
# the class list above and does not need to be here.
ALLOWED_FUNCTION_NAMES = frozenset({
    # math
    "div", "mod", "log10", "trim_scale", "scale", "min_scale", "gcd", "lcm", "factorial", "sinh", "cosh",
    "tanh", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    # null / comparison helpers
    "num_nonnulls", "num_nulls", "row",
    # strings
    "octet_length", "char_length", "character_length", "strpos", "substr", "btrim", "ltrim", "rtrim",
    "lpad", "rpad", "regexp_match", "regexp_matches", "regexp_split_to_array", "regexp_like", "to_hex",
    "quote_ident", "quote_literal", "quote_nullable", "normalize", "unistr", "convert_from", "convert_to",
    # dates and times
    "age", "isfinite", "make_date", "make_time", "make_timestamp", "make_timestamptz", "date_part",
    "date_trunc", "to_timestamp", "to_date", "to_char", "transaction_timestamp", "statement_timestamp",
    "timezone", "justify_days", "justify_hours", "justify_interval",
    # arrays
    "cardinality", "array_upper", "array_lower", "array_ndims", "array_dims", "array_replace", "array_fill",
    "array_length", "array_position", "array_positions", "array_to_string", "string_to_array", "unnest",
    # aggregates sqlglot leaves anonymous
    "every", "bit_xor", "string_agg", "array_agg", "jsonb_agg", "json_agg", "jsonb_object_agg",
    "regr_count", "percentile_cont", "percentile_disc",
    # JSON building / inspection
    "json_build_object", "jsonb_build_object", "json_build_array", "jsonb_build_array", "to_json", "to_jsonb",
    "row_to_json", "array_to_json", "json_typeof", "jsonb_typeof", "json_array_length", "jsonb_array_length",
    "jsonb_pretty",
})

# SQL value keywords that act as zero-argument functions but that sqlglot parses as
# plain (unquoted) column references.
_KEYWORD_VALUE_FUNCTIONS = frozenset({
    "user", "current_user", "session_user", "system_user", "current_role", "current_catalog", "current_schema",
})

_FUNCTION_POLICY = ("Allowed: aggregates, window functions, math, string, date/time and array functions, "
                    "CASE, COALESCE, NULLIF, GREATEST/LEAST and casts.")

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable", "Grant",
    "Revoke", "Copy", "Set", "Command", "Commit", "Rollback", "Transaction", "Use", "Pragma", "Into", "Lock",
    "LoadData", "Analyze", "Cache", "Uncache", "Refresh",
)
_FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))


class SqlGuardError(ValueError):
    """The SQL was rejected. The message is safe to show to Claude and the UI."""


@dataclass(frozen=True)
class SqlResult:
    sql: str  # as executed
    columns: list[str]
    rows: list[list[Any]]  # JSON-ready values
    truncated: bool  # the LIMIT was reached


def _views_message(found: list[str]) -> str:
    return (f"Only these views may be queried: {', '.join(ALLOWED_VIEWS)} "
            f"(found: {', '.join(found)}).")


def _function_name(node: exp.Expression) -> str:
    """The sqlglot name of a function node (`exp.CurrentVersion` -> `current_version`)."""
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    try:
        return node.sql_name().lower()
    except Exception:  # noqa: BLE001
        return type(node).__name__.lower()


# A rendering that starts with a call or is a bare keyword: `VERSION()`, `CURRENT_USER`.
# Operator nodes (`a AND b`, `a ~* b`) start with an operand instead and do not match.
_CALL_NAME = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|$)")


def _rendered_name(node: exp.Expression) -> str | None:
    """The function name as the node renders in Postgres (`exp.CurrentVersion` -> `version`)."""
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    try:
        m = _CALL_NAME.match(node.sql(dialect="postgres"))
    except Exception:  # noqa: BLE001
        return None
    return m.group(1).lower() if m else None


def _denied_function(name: str) -> bool:
    return name in DENIED_FUNCTIONS or name.startswith(DENIED_PREFIXES)


def _check_function(node: exp.Func) -> None:
    """Rule 6 for one function node. Raises SqlGuardError."""
    parent = node.parent
    qualified = isinstance(parent, exp.Dot) and parent.expression is node
    sqlglot_name = _function_name(node)
    if isinstance(node, exp.Anonymous):
        allowed = sqlglot_name in ALLOWED_FUNCTION_NAMES
        names = (sqlglot_name,)
        shown = sqlglot_name
    else:
        allowed = type(node) in ALLOWED_FUNC_CLASSES
        if allowed and not qualified:
            return  # a vetted class; nothing to name
        rendered = _rendered_name(node)
        names = (sqlglot_name, rendered)
        shown = rendered or sqlglot_name
    if any(n and _denied_function(n) for n in names):
        raise SqlGuardError(f"Function {shown}() is not allowed.")
    if qualified:
        raise SqlGuardError(f"Schema-qualified function calls are not allowed "
                            f"(found {parent.sql(dialect='postgres')}). Call the function without a schema.")
    if not allowed:
        raise SqlGuardError(f"Function {shown}() is not allowed. {_FUNCTION_POLICY}")


def _is_select_tree(node: exp.Expression) -> bool:
    if isinstance(node, exp.Select):
        return True
    if isinstance(node, exp.SetOperation):
        return _is_select_tree(node.left) and _is_select_tree(node.right)
    if isinstance(node, exp.Subquery):
        return _is_select_tree(node.this)
    return False


def _limit_value(node: exp.Expression | None) -> int | None:
    """The literal row count of a Limit/Fetch node, or None when it is not a literal."""
    if node is None:
        return None
    target = node.args.get("count") if isinstance(node, exp.Fetch) else node.expression
    if isinstance(target, exp.Literal) and not target.is_string:
        try:
            return int(str(target.this))
        except ValueError:
            return None
    return None


def guard_sql(sql: str, max_limit: int = MAX_LIMIT) -> str:
    """Validate `sql` and return the SQL to execute (LIMIT enforced). Raises SqlGuardError."""
    if not isinstance(sql, str) or not sql.strip():
        raise SqlGuardError("The query is empty. Send one SELECT statement.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except (ParseError, TokenError) as e:
        first = str(e).splitlines()[0] if str(e) else "syntax error"
        raise SqlGuardError(f"Could not parse the SQL ({first[:200]}).") from None
    if len(statements) != 1:
        raise SqlGuardError(f"Exactly one statement is allowed (found {len(statements)}).")
    root = statements[0]

    if not _is_select_tree(root):
        kind = type(root).__name__.upper()
        raise SqlGuardError(f"Only SELECT queries are allowed (found {kind}). The database is read-only.")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            if isinstance(node, exp.Into):
                raise SqlGuardError("SELECT ... INTO is not allowed. The database is read-only.")
            if isinstance(node, exp.Lock):
                raise SqlGuardError("Locking clauses (FOR UPDATE / FOR SHARE) are not allowed.")
            raise SqlGuardError(f"Only SELECT queries are allowed (found {type(node).__name__.upper()}). "
                                "The database is read-only.")
        if isinstance(node, exp.Select) and node.args.get("into"):
            raise SqlGuardError("SELECT ... INTO is not allowed. The database is read-only.")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise SqlGuardError("Locking clauses (FOR UPDATE / FOR SHARE) are not allowed.")

    cte_names: set[str] = set()
    for cte in root.find_all(exp.CTE):
        if not _is_select_tree(cte.this):
            raise SqlGuardError("Every CTE must be a SELECT.")
        if cte.alias:
            cte_names.add(cte.alias.lower())

    bad: list[str] = []
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            fn = _function_name(table.this) if isinstance(table.this, exp.Expression) else "table function"
            raise SqlGuardError(f"Table functions are not allowed in FROM (found {fn}). "
                                f"Query the views: {', '.join(ALLOWED_VIEWS)}.")
        name = table.name.lower()
        schema = (table.db or "").lower()
        catalog = (table.catalog or "").lower()
        if catalog or (schema and schema != "public"):
            bad.append(".".join(p for p in (catalog, schema, name) if p))
            continue
        if not schema and name in cte_names:
            continue
        if name not in ALLOWED_VIEWS:
            bad.append(f"{schema}.{name}" if schema else name)
    if bad:
        raise SqlGuardError(_views_message(sorted(set(bad), key=bad.index)))

    for node in root.find_all(exp.Func):
        _check_function(node)
    for node in root.find_all(exp.Column):
        # `SELECT user` / `current_role`: SQL value keywords that parse as columns.
        ident = node.this
        if (not node.table and isinstance(ident, exp.Identifier) and not ident.quoted
                and ident.name.lower() in _KEYWORD_VALUE_FUNCTIONS):
            raise SqlGuardError(f"Function {ident.name.lower()} is not allowed.")
    for node in root.find_all(exp.DataType):
        if node.sql(dialect="postgres").lower().startswith("reg"):
            raise SqlGuardError("Casts to reg* catalog types are not allowed.")
    for node in root.find_all(exp.Column):
        # A system-catalog lookup spelled as a qualified column (pg_catalog.x.y).
        if (node.args.get("catalog") or node.args.get("db")) and str(node.db).lower() not in ("", "public"):
            raise SqlGuardError(_views_message([node.sql(dialect="postgres")]))

    limit = root.args.get("limit")
    n = _limit_value(limit)
    if limit is None or n is None or n > max_limit or n < 0:
        root.set("limit", None)
        root = root.limit(max_limit)
    return root.sql(dialect="postgres")


def effective_limit(sql: str) -> int:
    """The literal outer LIMIT of guarded SQL (always present after `guard_sql`)."""
    root = sqlglot.parse_one(sql, read="postgres")
    return _limit_value(root.args.get("limit")) or MAX_LIMIT


def json_value(v: Any) -> Any:
    """A DB value as JSON: numerics -> numbers, dates -> YYYY-MM-DD, timestamps -> ISO Z."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, decimal.Decimal):
        if not v.is_finite():
            return None
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 and v.as_tuple().exponent >= 0 else float(v)
    if isinstance(v, dt.datetime):
        if v.tzinfo is not None:
            v = v.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return v.isoformat(timespec="seconds") + "Z"
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [json_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): json_value(x) for k, x in v.items()}
    if isinstance(v, (bytes, memoryview)):
        return "<binary>"
    return str(v)


class SqlExecutionError(RuntimeError):
    """The query passed the guard but failed in Postgres. Message is safe for Claude."""


def execute_readonly(engine: Engine, sql: str, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> SqlResult:
    """Run guarded SQL as the read-only role. Raises SqlExecutionError."""
    import psycopg

    limit = effective_limit(sql)
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        try:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            cur.execute(sql)  # no params: '%' and ':' in the SQL are literal
            columns = [d[0] for d in (cur.description or [])]
            fetched = cur.fetchmany(limit + 1)
        except psycopg.errors.QueryCanceled:
            raise SqlExecutionError(f"The query was cancelled after the {timeout_ms // 1000} s statement timeout. "
                                    "Simplify it or add filters.") from None
        except psycopg.errors.InsufficientPrivilege:
            raise SqlExecutionError("Permission denied: only the query views can be read.") from None
        except psycopg.Error as e:
            diag = getattr(e, "diag", None)
            primary = (getattr(diag, "message_primary", None) or str(e).splitlines()[0] or type(e).__name__)
            hint = getattr(diag, "message_hint", None)
            msg = f"The query failed: {primary[:300]}"
            if hint:
                msg += f" (hint: {hint[:200]})"
            raise SqlExecutionError(msg) from None
        finally:
            cur.close()
    finally:
        try:
            raw.rollback()
        finally:
            raw.close()
    rows = [[json_value(v) for v in r] for r in fetched[:limit]]
    return SqlResult(sql=sql, columns=columns, rows=rows, truncated=len(fetched) >= limit)
