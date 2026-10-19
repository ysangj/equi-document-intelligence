"""T2 schema tests (PRD §7, FR-4, FR-6, FR-7, FR-8, FR-13) against docintel_test."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app import models as m
from app.migrations_info import alembic_config, head_revisions
from tests.conftest import MAINTENANCE_URL, TEST_DATABASE_URL, TEST_DB_NAME, TEST_READONLY_URL

ALL_TABLES = {
    "oauth_credentials", "sync_state", "documents", "extractions", "funds", "fund_aliases",
    "returns", "fund_facts", "accounts", "account_aliases", "statements", "statement_holdings",
    "document_chunks", "review_actions",
}

# The Ask system prompt and the UI rely on these exact names/order (docs/progress.md
# "DB views"). Changing them is a contract change.
VIEW_COLUMNS = {
    "v_documents": [
        "document_id", "document_name", "document_type", "document_type_confidence", "mime_type", "status",
        "version", "source", "drive_file_id", "web_view_link", "search_indexed", "attempts", "last_error",
        "drive_modified_at", "processed_at", "created_at",
    ],
    "v_funds": ["fund_id", "fund_name", "manager", "strategy", "currency", "aliases", "has_unconfirmed_alias"],
    "v_accounts": [
        "account_id", "account_name", "account_number_masked", "custodian", "currency", "aliases",
        "has_unconfirmed_alias",
    ],
    "v_returns": [
        "return_id", "fund_id", "fund_name", "manager", "strategy", "fund_currency", "period_end",
        "period_year", "period_month", "period_label", "return_type", "net_gross", "value", "value_pct",
        "status", "review_reasons", "confidence", "page", "source_snippet", "document_id", "document_name",
        "document_type",
    ],
    "v_fund_facts": [
        "fact_id", "fund_id", "fund_name", "manager", "strategy", "metric", "value", "value_text", "unit",
        "as_of_date", "status", "review_reasons", "confidence", "page", "source_snippet", "document_id",
        "document_name", "document_type",
    ],
    "v_statements": [
        "statement_id", "account_id", "account_name", "account_number_masked", "custodian", "fund_id",
        "fund_name", "manager", "period_start", "period_end", "opening_balance", "contributions",
        "withdrawals", "pnl", "fees", "closing_balance", "currency", "status", "review_reasons",
        "holdings_count", "document_id", "document_name", "document_type",
    ],
    "v_holdings": [
        "holding_id", "statement_id", "account_id", "account_name", "custodian", "fund_id", "fund_name",
        "period_end", "currency", "line_no", "holding_name", "identifier", "quantity", "market_value",
        "weight", "weight_pct", "status", "review_reasons", "confidence", "page", "source_snippet",
        "document_id", "document_name",
    ],
}


# ---- helpers ---------------------------------------------------------------------------


def add_doc(s, drive_file_id="f1", md5="aaa", version=1, status="done", is_latest=True, name="doc.pdf"):
    d = m.Document(
        drive_file_id=drive_file_id, md5_checksum=md5, version=version, status=status,
        is_latest=is_latest, name=name, mime_type="application/pdf", document_type="factsheet",
    )
    s.add(d)
    s.flush()
    return d


def add_fund(s, name="Kestrel Global Macro"):
    f = m.Fund(canonical_name=name, manager="Kestrel Capital", strategy="Macro", currency="USD")
    s.add(f)
    s.flush()
    return f


def add_return(s, fund, doc, value="0.012", **kw):
    r = m.Return(
        fund_id=fund.id, document_id=doc.id, period_end=kw.pop("period_end", date(2026, 1, 31)),
        return_type=kw.pop("return_type", "MTD"), net_gross=kw.pop("net_gross", "net"),
        value=Decimal(value), page=1, source_snippet="Jan 1.2%", confidence=kw.pop("confidence", 0.9), **kw,
    )
    s.add(r)
    s.flush()
    return r


def expect_integrity_error(s, fn):
    with pytest.raises(IntegrityError):
        with s.begin_nested():
            fn()


@pytest.fixture
def ro_engine(test_engine):
    eng = create_engine(TEST_READONLY_URL, connect_args={"connect_timeout": 5})
    yield eng
    eng.dispose()


# ---- migration chain ----------------------------------------------------------------------


def test_0002_follows_0001_in_single_chain():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    assert len(head_revisions()) == 1
    assert script.get_revision("0002").down_revision == "0001"
    chain = [r.revision for r in script.walk_revisions()]
    assert chain[-2:] == ["0002", "0001"]


def test_all_tables_exist(test_engine):
    assert ALL_TABLES <= set(inspect(test_engine).get_table_names())


def test_models_match_migrated_schema(test_engine):
    """app/models.py and the migration must describe the same schema."""
    with test_engine.connect() as conn:
        mc = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(mc, m.Base.metadata) == []


def test_downgrade_upgrade_round_trip(test_engine):
    """Full round trip on a throwaway database so the session test DB stays intact."""
    scratch = f"{TEST_DB_NAME}_migrations"
    url = make_url(TEST_DATABASE_URL).set(database=scratch).render_as_string(hide_password=False)
    admin = create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{scratch}"'))
    cfg = alembic_config(url)
    cfg.attributes["configure_logger"] = False
    eng = create_engine(url)
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0001")
        with eng.connect() as c:
            names = set(inspect(c).get_table_names())
            assert names & ALL_TABLES == set()
            assert c.execute(text("SELECT count(*) FROM pg_views WHERE viewname LIKE 'v\\_%'")).scalar() == 0
            assert c.execute(text("SELECT 1 FROM pg_extension WHERE extname='pg_trgm'")).first() is None
            assert c.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).first() is not None
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        with eng.connect() as c:
            assert ALL_TABLES <= set(inspect(c).get_table_names())
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() in head_revisions()
    finally:
        eng.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        admin.dispose()


# ---- indexes / types (FR-8) -------------------------------------------------------------------


def _indexdefs(conn) -> dict[str, str]:
    rows = conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public'"))
    return {r[0]: r[1] for r in rows}


def test_required_indexes_exist(test_engine):
    with test_engine.connect() as c:
        idx = _indexdefs(c)
    assert "USING hnsw (embedding vector_cosine_ops)" in idx["ix_document_chunks_embedding_hnsw"]
    assert "USING gin (tsv)" in idx["ix_document_chunks_tsv"]
    for name, col in [
        ("ix_documents_name_trgm", "name"),
        ("ix_fund_aliases_alias_trgm", "alias"),
        ("ix_account_aliases_alias_trgm", "alias"),
        ("ix_funds_canonical_name_trgm", "canonical_name"),
        ("ix_accounts_canonical_name_trgm", "canonical_name"),
    ]:
        assert f"USING gin ({col} gin_trgm_ops)" in idx[name], name
    assert "(fund_id, period_end, return_type, net_gross)" in idx["ix_returns_natural_key"]
    assert "UNIQUE" not in idx["ix_returns_natural_key"]
    assert "(fund_id, name, as_of_date)" in idx["ix_fund_facts_natural_key"]
    assert "(account_id, period_end, fund_id)" in idx["ix_statements_natural_key"]
    assert "UNIQUE" in idx["uq_documents_file_checksum"]


def test_every_foreign_key_column_is_indexed(test_engine):
    """B-tree on foreign keys (FR-8): each FK column leads some index."""
    insp = inspect(test_engine)
    missing = []
    for table in ALL_TABLES:
        leading = {tuple(i["column_names"][:1]) for i in insp.get_indexes(table)}
        leading |= {tuple(u["column_names"][:1]) for u in insp.get_unique_constraints(table)}
        leading.add(tuple(insp.get_pk_constraint(table)["constrained_columns"][:1]))
        for fk in insp.get_foreign_keys(table):
            if tuple(fk["constrained_columns"][:1]) not in leading:
                missing.append(f"{table}.{fk['constrained_columns'][0]}")
    assert missing == []


def test_extensions_and_column_types(test_engine):
    with test_engine.connect() as c:
        exts = set(c.execute(text("SELECT extname FROM pg_extension")).scalars())
        assert {"vector", "pg_trgm"} <= exts
        types = dict(c.execute(text(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = 'document_chunks'::regclass AND a.attnum > 0"
        )).all())
        gen = c.execute(text(
            "SELECT attgenerated FROM pg_attribute WHERE attrelid='document_chunks'::regclass AND attname='tsv'"
        )).scalar()
    assert types["embedding"] == "vector(384)"
    assert types["tsv"] == "tsvector"
    assert gen == "s"  # stored generated column


# ---- documents: idempotency / versions (FR-4, §12) -----------------------------------------------


def test_duplicate_drive_file_id_and_checksum_fails(db_session):
    add_doc(db_session, "f1", "aaa")
    expect_integrity_error(db_session, lambda: add_doc(db_session, "f1", "aaa", version=2, is_latest=False))


def test_changed_checksum_creates_new_version(db_session):
    v1 = add_doc(db_session, "f1", "aaa")
    v1.is_latest = False
    db_session.flush()
    v2 = add_doc(db_session, "f1", "bbb", version=2)
    assert (v1.version, v2.version) == (1, 2)


def test_only_one_latest_version_per_file(db_session):
    add_doc(db_session, "f1", "aaa")
    expect_integrity_error(db_session, lambda: add_doc(db_session, "f1", "bbb", version=2))


def test_duplicate_version_number_fails(db_session):
    add_doc(db_session, "f1", "aaa", is_latest=False)
    expect_integrity_error(db_session, lambda: add_doc(db_session, "f1", "bbb", version=1))


@pytest.mark.parametrize("status", m.DOCUMENT_STATUSES)
def test_document_statuses_accepted(db_session, status):
    assert add_doc(db_session, status=status).status == status


def test_document_enums_rejected(db_session):
    expect_integrity_error(db_session, lambda: add_doc(db_session, status="archived"))
    def bad_type():
        d = add_doc(db_session, "f9", "zzz")
        d.document_type = "invoice"
        db_session.flush()
    expect_integrity_error(db_session, bad_type)


def test_document_defaults_and_updated_at_trigger(db_session):
    d = m.Document(drive_file_id="f1", md5_checksum="a", name="n", mime_type="text/csv")
    db_session.add(d)
    db_session.flush()
    db_session.refresh(d)
    assert (d.status, d.version, d.is_latest, d.search_indexed, d.attempts, d.source) == (
        "pending", 1, True, False, 0, "drive",
    )
    # now() is fixed within a transaction, so backdate and check the trigger rewrites it.
    db_session.execute(text("ALTER TABLE documents DISABLE TRIGGER trg_documents_updated_at"))
    db_session.execute(text("UPDATE documents SET updated_at = now() - interval '1 day' WHERE id = :i"), {"i": d.id})
    db_session.execute(text("ALTER TABLE documents ENABLE TRIGGER trg_documents_updated_at"))
    db_session.execute(text("UPDATE documents SET attempts = 1 WHERE id = :i"), {"i": d.id})
    fresh = db_session.execute(text("SELECT updated_at >= now() FROM documents WHERE id = :i"), {"i": d.id}).scalar()
    assert fresh is True


def test_singletons(db_session):
    db_session.add(m.SyncState(folder_id="abc"))
    db_session.flush()
    expect_integrity_error(db_session, lambda: (db_session.add(m.SyncState(id=2)), db_session.flush()))


# ---- value rows: uniqueness, conflicts, review state (FR-6, FR-13) ------------------------------------


def test_value_row_unique_per_source_document(db_session):
    f, d = add_fund(db_session), add_doc(db_session)
    add_return(db_session, f, d)
    expect_integrity_error(db_session, lambda: add_return(db_session, f, d, value="0.02"))


def test_conflicting_values_from_two_documents_are_both_kept(db_session):
    f = add_fund(db_session)
    d1, d2 = add_doc(db_session, "f1", "a"), add_doc(db_session, "f2", "b")
    add_return(db_session, f, d1, value="0.012", status="conflict", review_reasons=["conflict"])
    add_return(db_session, f, d2, value="0.015", status="conflict", review_reasons=["conflict"])
    n = db_session.execute(text("SELECT count(*) FROM returns WHERE fund_id = :f"), {"f": f.id}).scalar()
    assert n == 2


def test_fund_fact_with_null_date_unique_per_document(db_session):
    f, d = add_fund(db_session), add_doc(db_session)
    db_session.add(m.FundFact(fund_id=f.id, document_id=d.id, name="aum", value=Decimal("1e8")))
    db_session.flush()
    expect_integrity_error(
        db_session,
        lambda: (db_session.add(m.FundFact(fund_id=f.id, document_id=d.id, name="aum")), db_session.flush()),
    )


@pytest.mark.parametrize(
    "status,reasons",
    [
        ("ok", ["low_confidence"]),          # ok must have no reasons
        ("needs_review", []),                # flagged must say why
        ("conflict", ["low_confidence"]),    # conflict needs the conflict reason
        ("needs_review", ["conflict"]),      # conflict reason implies conflict status
        ("needs_review", ["bogus_reason"]),  # unknown reason
        ("maybe", ["low_confidence"]),       # unknown status
    ],
)
def test_review_state_invariants(db_session, status, reasons):
    f, d = add_fund(db_session), add_doc(db_session)
    expect_integrity_error(db_session, lambda: add_return(db_session, f, d, status=status, review_reasons=reasons))


def test_all_review_reasons_accepted(db_session):
    f, d = add_fund(db_session), add_doc(db_session)
    r = add_return(db_session, f, d, status="conflict", review_reasons=list(m.REVIEW_REASONS))
    assert set(r.review_reasons) == set(m.REVIEW_REASONS)


@pytest.mark.parametrize("field,bad", [("return_type", "weekly"), ("net_gross", "both"), ("confidence", 1.5)])
def test_return_enum_and_range_checks(db_session, field, bad):
    f, d = add_fund(db_session), add_doc(db_session)
    expect_integrity_error(db_session, lambda: add_return(db_session, f, d, **{field: bad}))


@pytest.mark.parametrize("rt", m.RETURN_TYPES)
def test_return_types_accepted(db_session, rt):
    f, d = add_fund(db_session), add_doc(db_session)
    assert add_return(db_session, f, d, return_type=rt).return_type == rt


def test_alias_unique_case_insensitive(db_session):
    f = add_fund(db_session)
    db_session.add(m.FundAlias(fund_id=f.id, alias="Kestrel Macro"))
    db_session.flush()
    expect_integrity_error(
        db_session, lambda: (db_session.add(m.FundAlias(fund_id=f.id, alias="KESTREL MACRO")), db_session.flush())
    )


def test_deleting_document_cascades_to_values_and_chunks(db_session):
    f, d = add_fund(db_session), add_doc(db_session)
    add_return(db_session, f, d)
    db_session.add(m.DocumentChunk(document_id=d.id, chunk_index=0, text="hello"))
    db_session.flush()
    db_session.execute(text("DELETE FROM documents WHERE id = :i"), {"i": d.id})
    assert db_session.execute(text("SELECT count(*) FROM returns")).scalar() == 0
    assert db_session.execute(text("SELECT count(*) FROM document_chunks")).scalar() == 0


# ---- chunks (FR-7) --------------------------------------------------------------------------------


def test_tsv_is_generated_and_searchable(db_session):
    d = add_doc(db_session)
    c = m.DocumentChunk(
        document_id=d.id, page=2, chunk_index=0, text="We reduced duration ahead of the Fed meeting.",
        embedding=[0.1] * m.EMBEDDING_DIM, embedding_model="BAAI/bge-small-en-v1.5",
    )
    db_session.add(c)
    db_session.flush()
    tsv = db_session.execute(text("SELECT tsv::text FROM document_chunks WHERE id = :i"), {"i": c.id}).scalar()
    assert "'durat'" in tsv  # english stemming
    hit = db_session.execute(
        text("SELECT id FROM document_chunks WHERE tsv @@ websearch_to_tsquery('english', 'durations')")
    ).scalar()
    assert hit == c.id
    db_session.execute(text("UPDATE document_chunks SET text = 'credit spreads' WHERE id = :i"), {"i": c.id})
    tsv2 = db_session.execute(text("SELECT tsv::text FROM document_chunks WHERE id = :i"), {"i": c.id}).scalar()
    assert "spread" in tsv2 and "durat" not in tsv2


def test_embedding_round_trip_and_cosine_order(db_session):
    d = add_doc(db_session)
    a = [1.0] + [0.0] * (m.EMBEDDING_DIM - 1)
    b = [0.0, 1.0] + [0.0] * (m.EMBEDDING_DIM - 2)
    db_session.add_all([
        m.DocumentChunk(document_id=d.id, chunk_index=0, text="a", embedding=a),
        m.DocumentChunk(document_id=d.id, chunk_index=1, text="b", embedding=b),
    ])
    db_session.flush()
    q = m.DocumentChunk.embedding.cosine_distance(a)
    first = db_session.execute(select(m.DocumentChunk.text).order_by(q).limit(1)).scalar()
    assert first == "a"


def test_wrong_embedding_dimension_rejected(db_session):
    d = add_doc(db_session)
    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(m.DocumentChunk(document_id=d.id, chunk_index=0, text="x", embedding=[0.1] * 10))
            db_session.flush()


# ---- views (§7) ------------------------------------------------------------------------------------


def test_view_columns_are_stable(test_engine):
    insp = inspect(test_engine)
    assert set(insp.get_view_names()) >= set(m.QUERY_VIEWS)
    for view, cols in VIEW_COLUMNS.items():
        assert [c["name"] for c in insp.get_columns(view)] == cols, view
    assert set(VIEW_COLUMNS) == set(m.QUERY_VIEWS)


def _seed_visible_and_hidden(s):
    f = add_fund(s)
    acct = m.Account(canonical_name="Acme Family Office", account_number_masked="****1234", custodian="Harbor")
    s.add(acct)
    s.flush()
    docs = {
        "live": add_doc(s, "live", "a", name="live.pdf"),
        "removed": add_doc(s, "gone", "b", status="removed", name="gone.pdf"),
        "old": add_doc(s, "ver", "c1", version=1, is_latest=False, name="v1.pdf"),
        "new": add_doc(s, "ver", "c2", version=2, name="v2.pdf"),
    }
    for key, d in docs.items():
        add_return(s, f, d, status="needs_review", review_reasons=["ytd_mismatch"])
        s.add(m.FundFact(fund_id=f.id, document_id=d.id, name="aum", value=Decimal("100")))
        st = m.Statement(account_id=acct.id, fund_id=f.id, document_id=d.id, period_end=date(2026, 1, 31))
        s.add(st)
        s.flush()
        s.add(m.StatementHolding(statement_id=st.id, line_no=0, name=f"holding-{key}", weight=Decimal("0.25")))
    s.flush()
    return f, docs


def test_views_exclude_removed_and_superseded_documents(db_session):
    _, docs = _seed_visible_and_hidden(db_session)
    visible = {docs["live"].id, docs["new"].id}
    for view in ("v_returns", "v_fund_facts", "v_statements", "v_holdings", "v_documents"):
        ids = set(db_session.execute(text(f"SELECT document_id FROM {view}")).scalars())
        assert ids == visible, view
    names = set(db_session.execute(text("SELECT document_name FROM v_documents")).scalars())
    assert names == {"live.pdf", "v2.pdf"}


def test_views_denormalize_names_and_expose_review_state(db_session):
    f, docs = _seed_visible_and_hidden(db_session)
    db_session.add(m.FundAlias(fund_id=f.id, alias="Kestrel GM", match_method="fuzzy", match_score=91))
    db_session.flush()
    r = db_session.execute(text("SELECT * FROM v_returns WHERE document_id = :d"), {"d": docs["live"].id}).mappings().one()
    assert r["fund_name"] == "Kestrel Global Macro" and r["manager"] == "Kestrel Capital"
    assert r["strategy"] == "Macro" and r["document_name"] == "live.pdf" and r["document_type"] == "factsheet"
    assert r["status"] == "needs_review" and r["review_reasons"] == ["ytd_mismatch"]
    assert r["value_pct"] == Decimal("1.2") and (r["period_year"], r["period_month"]) == (2026, 1)
    st = db_session.execute(text("SELECT * FROM v_statements WHERE document_id = :d"), {"d": docs["live"].id}).mappings().one()
    assert st["account_name"] == "Acme Family Office" and st["custodian"] == "Harbor"
    assert st["fund_name"] == "Kestrel Global Macro" and st["holdings_count"] == 1
    h = db_session.execute(text("SELECT * FROM v_holdings WHERE document_id = :d"), {"d": docs["live"].id}).mappings().one()
    assert h["holding_name"] == "holding-live" and h["weight_pct"] == Decimal("25") and h["account_name"] == "Acme Family Office"
    fund = db_session.execute(text("SELECT * FROM v_funds WHERE fund_id = :f"), {"f": f.id}).mappings().one()
    assert fund["aliases"] == ["Kestrel GM"] and fund["has_unconfirmed_alias"] is True


def test_review_queue_lists_flagged_rows_and_fuzzy_aliases(db_session):
    f, docs = _seed_visible_and_hidden(db_session)
    db_session.add(m.FundAlias(fund_id=f.id, alias="Kestrel GM", match_method="fuzzy", match_score=91))
    db_session.add(m.FundAlias(fund_id=f.id, alias="Kestrel Global Macro", match_method="exact", confirmed=True))
    db_session.flush()
    rows = db_session.execute(text("SELECT item_key, target_table, review_reasons, document_id FROM review_queue")).all()
    by_table = {}
    for key, table, reasons, doc_id in rows:
        by_table.setdefault(table, []).append((reasons, doc_id))
        assert key.startswith(f"{table}:")
    # flagged returns only from visible documents; the exact alias is not a review item
    assert {d for _, d in by_table["returns"]} == {docs["live"].id, docs["new"].id}
    assert by_table["fund_aliases"] == [(["alias_match"], None)]
    assert "fund_facts" not in by_table


# ---- read-only role (FR-8, FR-11) --------------------------------------------------------------------


def test_readonly_can_select_every_view(ro_engine):
    with ro_engine.connect() as c:
        for view in m.QUERY_VIEWS:
            c.execute(text(f"SELECT * FROM {view} LIMIT 1")).all()


@pytest.mark.parametrize("table", sorted(ALL_TABLES | {"alembic_version", "review_queue"}))
def test_readonly_cannot_select_base_tables(ro_engine, table):
    with ro_engine.connect() as c, pytest.raises(ProgrammingError, match="permission denied"):
        c.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


def test_readonly_cannot_write_or_create(ro_engine):
    stmts = [
        "INSERT INTO funds (canonical_name) VALUES ('x')",
        "UPDATE documents SET status = 'removed'",
        "DELETE FROM returns",
        "CREATE TABLE evil (id int)",
        "CREATE TEMP TABLE evil (id int)",
        "CREATE VIEW v_evil AS SELECT 1",
    ]
    for sql in stmts:
        with ro_engine.connect() as c:
            # Read-only transactions are the default for the role; turn that off to prove
            # privileges alone also block the write.
            c.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE"))
            c.commit()
            with pytest.raises(ProgrammingError, match="permission denied"):
                c.execute(text(sql))


def test_readonly_role_defaults(ro_engine):
    with ro_engine.connect() as c:
        assert c.execute(text("SHOW default_transaction_read_only")).scalar() == "on"
        assert c.execute(text("SHOW statement_timeout")).scalar() == "5s"
        with pytest.raises(DBAPIError, match="read-only transaction"):
            c.execute(text("CREATE TEMP TABLE t (i int)"))


def test_readonly_url_matches_env_example():
    from pathlib import Path

    example = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
    line = next(x for x in example.splitlines() if x.startswith("DATABASE_READONLY_URL="))
    url = make_url(line.split("=", 1)[1])
    assert (url.username, url.password) == (m.READONLY_ROLE, "app_readonly")
