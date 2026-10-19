"""Document retrieval support (FR-7, §8): chunking, local embeddings, indexing.

- `chunking`: pure chunker (pages -> paragraphs, ~500 tokens, ~50 overlap; CSV row
  groups with the header repeated).
- `embedding`: fastembed `EMBEDDING_MODEL` (default BAAI/bge-small-en-v1.5, 384-dim),
  cached in ./.cache/fastembed; `embed_query()` for Ask (T8).
- `indexing`: writes chunks in the job transaction and fills embeddings after commit.
"""
