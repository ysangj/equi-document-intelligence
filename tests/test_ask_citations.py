"""Ask citation markers and the number citation check (FR-11, U3/U4 note 2)."""

from __future__ import annotations

import pytest

from app.ask.citations import collect_values, extract_answer_numbers, renumber_markers, verify_numbers
from app.schemas.ask import CITATION_MARKER_RE

# ---- markers ---------------------------------------------------------------------


def test_renumbers_in_order_of_first_appearance():
    text, mapping = renumber_markers("B is best [7]. A next [3]. Again B [7] and A [3].", [3, 7])
    assert text == "B is best [1]. A next [2]. Again B [1] and A [2]."
    assert mapping == {7: 1, 3: 2}


def test_drops_unresolvable_markers_with_their_space():
    text, mapping = renumber_markers("Kestrel led [4]. From history [1]. Unknown [99].", [4])
    assert text == "Kestrel led [1]. From history. Unknown."
    assert mapping == {4: 1}


def test_grouped_markers_are_split():
    text, mapping = renumber_markers("Both [5, 2] and [2,5] and [5 ,9].", [2, 5])
    assert text == "Both [1][2] and [2][1] and [1]."
    assert mapping == {5: 1, 2: 2}


def test_markers_never_inside_inline_code():
    text, mapping = renumber_markers("Run `SELECT a[2] FROM t [3]` to see [3].", [3])
    assert "`SELECT a FROM t `" in text and text.endswith("to see [1].")
    assert "`SELECT a FROM t `[1]" in text
    assert mapping == {3: 1}


def test_markers_moved_out_of_fenced_code():
    answer = "Query:\n```sql\nSELECT * FROM v_returns [2]\n```\nDone [2]."
    text, mapping = renumber_markers(answer, [2])
    assert "```sql\nSELECT * FROM v_returns\n```\n[1]\nDone [1]." == text.split("Query:\n", 1)[1]
    for block in text.split("```")[1::2]:
        assert not CITATION_MARKER_RE.search(block)


def test_no_markers_and_no_sources():
    assert renumber_markers("Nothing to cite.", []) == ("Nothing to cite.", {})
    assert renumber_markers("Stale [1] [2].", []) == ("Stale.", {})


def test_non_marker_brackets_kept():
    text, _ = renumber_markers("See [note] and [a1] and [3].", [3])
    assert text == "See [note] and [a1] and [1]."


# ---- number extraction -----------------------------------------------------------


def tokens(answer):
    return [t.text for t in extract_answer_numbers(answer)]


def test_extracts_bare_tokens_without_markdown():
    assert tokens("**Kestrel** had **+2.84%** [1], *-0.36%*, and `+9%` in code.") == ["+2.84%", "-0.36%"]


def test_extracts_units_and_formats():
    answer = ("NAV $1,200,000 and USD 5,000,000.00, AUM 1.18bn, 12.4mm, 110 bps, 0.92 percentage points, "
              "(0.36)%, 3.1x, 12k, −0.40%, 5 percent, 13.1 million")
    assert tokens(answer) == ["$1,200,000", "USD 5,000,000.00", "1.18bn", "12.4mm", "110 bps", "0.92", "(0.36)%",
                              "3.1x", "12k", "−0.40%", "5", "13.1"]


def test_skips_markers_years_dates_lists_and_glued():
    answer = ("1. Kestrel in January 2026 [12] on 2026-01-31, 01/31/2026, Jan-26, Jan 31, 2026, 31 December 2025, "
              "Q1 2026, FY2025, H2, v2 and the 1st and 3rd.\n2) Done in 2025.")
    assert tokens(answer) == []


def test_tokens_are_substrings_of_answer():
    answer = "Up **+1.92%** (factsheet) vs +1.89% (flash), net (0.36)% and $13,100,000."
    for t in extract_answer_numbers(answer):
        assert answer[t.start:t.start + len(t.text)] == t.text


def test_values_and_signs():
    by_text = {t.text: t for t in extract_answer_numbers("-0.36% and (0.36)% and +2.84% and 12,400,000.50")}
    assert by_text["-0.36%"].value == -0.36
    assert by_text["(0.36)%"].value == -0.36
    assert by_text["+2.84%"].value == 2.84 and by_text["+2.84%"].decimals == 2
    assert by_text["12,400,000.50"].value == 12400000.5


# ---- verification ----------------------------------------------------------------

SQL = [{"columns": ["fund_name", "value", "value_pct", "status"],
        "rows": [["Kestrel Quant Equity Fund LP", 0.0284, 2.84, "ok"],
                 ["Northwind Global Macro Fund", 0.0192, 1.92, "conflict"],
                 ["Meridian Rates Relative Value Fund", -0.0036, -0.36, "ok"]],
        "row_count": 3}]
PASSAGES = ["Opening balance 12.40mm, closing NAV 13,100,000.00. We cut gross from 3.1x to 2.6x.",
            "Performance was +110 bps for the month; MTD % (net) (0.36)%."]


def check(answer, sql=SQL, passages=PASSAGES):
    return verify_numbers(answer, collect_values(sql, passages))


@pytest.mark.parametrize("answer", [
    "Kestrel returned +2.84% [1].",
    "Kestrel returned 2.84% [1].",
    "Kestrel returned 2.8% [1].",  # rounding to the precision written
    "Kestrel returned 3% [1].",
    "Kestrel returned 0.0284 as a fraction.",  # fraction vs percent
    "Northwind: 1.92 percent.",
    "Meridian fell 0.36% [1].",  # magnitude
    "Meridian returned -0.36% / (0.36)% / −0.36%.",
    "Closing NAV $13,100,000 or 13.1mm or 13.10 million, opening 12.4mm.",
    "Opening 12,400,000.",
    "Gross went from 3.1x to 2.6x.",
    "Alder gained 110 bps, i.e. 1.10%, or 0.011 as a fraction.",
    "Three funds: 3 rows.",  # row count
])
def test_matched_numbers_are_not_flagged(answer):
    assert check(answer) == []


def test_unmatched_numbers_listed_as_bare_tokens():
    answer = "Kestrel **+2.84%** [1] led Northwind by **0.92** points; Harbor did **+5.55%** and +5.55% again."
    assert check(answer) == ["0.92", "+5.55%"]


def test_unverified_tokens_are_exact_substrings():
    answer = "Lead of 0.92 percentage points; NAV USD 99,000,000; drop (7.5)%."
    out = check(answer)
    assert out == ["0.92", "USD 99,000,000", "(7.5)%"]
    assert all(t in answer for t in out)


def test_precision_matters():
    assert check("Kestrel returned 2.85%.") == ["2.85%"]  # 2.84 does not round to 2.85
    assert check("Kestrel returned 2.9%.") == ["2.9%"]  # 2.84 rounds to 2.8, not 2.9
    assert check("Kestrel returned 2.8%.") == []


def test_no_sources_flags_every_number():
    assert verify_numbers("Up 2.84% and 1,000 units.", collect_values([], [])) == ["2.84%", "1,000"]


def test_numbers_from_string_cells_count():
    sql = [{"rows": [["MTD % (net) +2.84%", "12.40mm"]], "row_count": 1}]
    assert verify_numbers("It was +2.84% on 12.4mm.", collect_values(sql, [])) == []


def test_mm_scaling_from_source_text():
    assert verify_numbers("Opening was 12,400,000.", collect_values([], ["opening 12.40mm"])) == []


# ---- ordinals and rank columns (U6 request) -----------------------------------------

# The real-Claude answer captured in U6 ("Which fund had the best January return?").
U6_ANSWER = (
    "**Kestrel Quant Equity Fund LP** had the best January 2026 return, at **+2.84%** net MTD. Three of its "
    "factsheets (January, February and March 2026) report that same figure, and none of them are flagged [1].\n\n"
    "I've compared net month-to-date returns for January 2026, the only January in the data:\n\n"
    "| Rank | Fund | Jan 2026 net MTD |\n"
    "|---|---|---|\n"
    "| 1 | Kestrel Quant Equity Fund LP | **+2.84%** |\n"
    "| 2 | Northwind Global Macro Fund | +1.92% / +1.89% ⚠️ |\n"
    "| 3 | Alder Asia Fund | +1.10% |\n"
    "| 4 | Harbor Credit Opps Fund | +0.71% |\n"
    "| 5 | Alder Dubai Fund | +0.48% |\n"
    "| 6 | Meridian Rates Relative Value Fund | −0.36% |\n\n"
    "[1]\n\n"
    "**Northwind's January figure is flagged as a conflict** because its documents disagree. Its January and "
    "February monthly reports say **+1.92%**, but the January 2026 flash update email says **+1.89%** [1]. "
    "Either way it ranks second, so Kestrel is still first.\n\n"
    "Meridian is the only fund with a separate gross figure for January, at −0.15% [1]."
)
_U6_ROWS = [  # (return_id, fund, year, month, type, net_gross, value_pct, status, reasons, document)
    (31, "Kestrel Quant Equity Fund LP", 2.84, "ok"), (36, "Kestrel Quant Equity Fund LP", 2.84, "ok"),
    (33, "Kestrel Quant Equity Fund LP", 2.84, "ok"), (74, "Northwind Global Macro Fund", 1.92, "conflict"),
    (67, "Northwind Global Macro Fund", 1.92, "conflict"), (61, "Northwind Global Macro Fund", 1.89, "conflict"),
    (1, "Alder Asia Fund", 1.1, "ok"), (5, "Alder Asia Fund", 1.1, "ok"), (23, "Harbor Credit Opps Fund", 0.71, "ok"),
    (15, "Alder Dubai Fund", 0.48, "ok"), (10, "Alder Dubai Fund", 0.48, "ok"),
    (47, "Meridian Rates Relative Value Fund", -0.15, "ok"), (52, "Meridian Rates Relative Value Fund", -0.36, "ok"),
    (56, "Meridian Rates Relative Value Fund", -0.36, "ok"), (46, "Meridian Rates Relative Value Fund", -0.36, "ok"),
]
U6_SQL = [{"rows": [[rid, fund, 2026, 1, "MTD", "net", pct, st, ["conflict"] if st == "conflict" else [], "doc.pdf"]
                    for rid, fund, pct, st in _U6_ROWS],
           "row_count": 15}]


def test_u6_captured_rank_table_has_no_false_positives():
    assert verify_numbers(U6_ANSWER, collect_values(U6_SQL, [])) == []
    # the ranks are not even tokens; the table's values still are
    toks = tokens(U6_ANSWER)
    assert "4" not in toks and "6" not in toks
    assert "+0.71%" in toks and "−0.36%" in toks


def test_u6_table_values_are_still_checked():
    wrong = U6_ANSWER.replace("+0.71%", "+0.77%")
    assert verify_numbers(wrong, collect_values(U6_SQL, [])) == ["+0.77%"]


@pytest.mark.parametrize("header", ["Rank", "**Rank**", "#", "No.", "No", "Position", "Pos.", "Place", "rank"])
def test_rank_column_headers_skipped(header):
    answer = f"| {header} | Fund | Return |\n|:-:|---|--:|\n| 1 | A | 2.84% |\n| 2 | B | 1.92% |\n| 3 | C | -0.36% |"
    assert tokens(answer) == ["2.84%", "1.92%", "-0.36%"]


@pytest.mark.parametrize("cells", [["1", "2", "2", "4"], ["1", "1", "2"], ["**1**", "2.", "3"]])
def test_rank_column_ties_and_markdown(cells):
    rows = "".join(f"| {c} | F{i} |\n" for i, c in enumerate(cells))
    assert tokens("| Rank | Fund |\n|---|---|\n" + rows) == []


def test_rank_column_without_outer_pipes():
    assert tokens("Rank | Fund | Return\n--- | --- | ---\n1 | A | 2.84%\n2 | B | 1.92%") == ["2.84%", "1.92%"]


@pytest.mark.parametrize(("answer", "expected"), [
    ("| Funds | Strategy |\n|---|---|\n| 1 | Macro |\n| 2 | Credit |", ["1", "2"]),  # not a rank header
    ("| Rank | Fund |\n|---|---|\n| 2 | A |\n| 3 | B |", ["2", "3"]),  # does not start at 1
    ("| Rank | Fund |\n|---|---|\n| 1 | A |\n| 4 | B |", ["1", "4"]),  # gap
    ("| Rank | Fund |\n|---|---|\n| 2 | A |\n| 1 | B |", ["2", "1"]),  # not in row order
    ("| Rank | Fund |\n|---|---|\n| 1 | A |\n| 2.5 | B |", ["1", "2.5"]),  # not an integer
])
def test_non_rank_columns_are_checked(answer, expected):
    assert tokens(answer) == expected


def test_rank_skip_is_limited_to_the_rank_column():
    answer = "| Rank | Fund | Count |\n|---|---|---|\n| 1 | A | 6 |\n| 2 | B | 4 |"
    assert tokens(answer) == ["6", "4"]
    assert check(answer) == ["6", "4"]


def test_ordinals_skipped():
    answer = "Kestrel ranked #1, Northwind #2 and Alder 3rd; Harbor came 4th, the 21st and 102nd entries."
    assert tokens(answer) == []


def test_hash_after_word_or_entity_is_not_an_ordinal():
    # "#" glued to a word or an HTML entity is not an ordinal marker, so the number is checked
    assert tokens("Account&#35; ref A#7 has 9 lots.") == ["35", "7", "9"]


def test_small_integers_outside_rank_contexts_still_checked():
    # "6 funds" is checked: no source says 6, so it is flagged; "3 funds" matches the row count
    assert check("Of the 6 funds, Kestrel led [1].") == ["6"]
    assert check("Of the 3 funds, Kestrel led [1].") == []
    # a "#" table column whose values are not a ranking stays checked
    assert check("| # | Fund |\n|---|---|\n| 7 | A |") == ["7"]
