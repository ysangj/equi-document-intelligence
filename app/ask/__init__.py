"""Ask (FR-11): Claude tool-use loop over `query_sql` and `search_documents`.

- `sql_guard`: sqlglot guard + read-only execution as `app_readonly`.
- `retrieval`: hybrid FTS + vector retrieval merged with RRF (§8).
- `citations`: marker renumbering and the number citation check.
- `prompt`: system prompt and tool definitions.
- `loop`: the tool-use loop that builds an `AskResponse`.
"""
