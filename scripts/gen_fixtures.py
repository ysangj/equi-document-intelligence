#!/usr/bin/env python
"""Generate `fixtures/<filename>.json` by running ClaudeExtractor over every supported
file in `sample_docs/` (FR-14, §13). Run from the repo root with the shared venv:

    .venv/bin/python scripts/gen_fixtures.py               # all files missing a current fixture
    .venv/bin/python scripts/gen_fixtures.py --force       # regenerate everything
    .venv/bin/python scripts/gen_fixtures.py --only kestrel_factsheet_2026-01.pdf

ANTHROPIC_API_KEY and CLAUDE_MODEL are read through app.config (environment, then .env).
The key is never printed. Each fixture is skipped when it already exists with the same
source md5 and PROMPT_VERSION, unless --force. Prints per-file tokens and latency (no
document contents) and the total token usage.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.extraction.base import ExtractionError  # noqa: E402
from app.extraction.claude import ClaudeExtractor  # noqa: E402
from app.extraction.content import is_supported  # noqa: E402
from app.extraction.fixture import FIXTURE_FORMAT, FIXTURES_DIR, fixture_path  # noqa: E402
from app.extraction.schema import PROMPT_VERSION  # noqa: E402
from app.sources.local import DEFAULT_LOCAL_DIR, LocalFolderSource  # noqa: E402


def fixture_document(sf, result, latency_ms: int) -> dict:
    return {
        "fixture_format": FIXTURE_FORMAT,
        "source_file": sf.name,
        "source_md5": sf.md5,
        "mime_type": sf.mime_type,
        "model": result.model,
        "prompt_version": result.prompt_version,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "latency_ms": latency_ms,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "extraction": result.extraction.model_dump(mode="json"),
    }


def is_current(path: Path, md5: str) -> bool:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return data.get("source_md5") == md5 and data.get("prompt_version") == PROMPT_VERSION


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="regenerate even if the fixture is current")
    ap.add_argument("--only", action="append", default=[], help="file name to (re)generate; repeatable")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--source-dir", type=Path, default=DEFAULT_LOCAL_DIR)
    ap.add_argument("--out-dir", type=Path, default=FIXTURES_DIR)
    args = ap.parse_args(argv)

    settings = get_settings()
    if settings.anthropic_api_key is None:
        print("ANTHROPIC_API_KEY is not set (in the environment or .env)", file=sys.stderr)
        return 2
    extractor = ClaudeExtractor(settings.anthropic_api_key.get_secret_value(), settings.claude_model)
    source = LocalFolderSource(args.source_dir)
    files = [f for f in source.list_files() if is_supported(f.mime_type)]
    if args.only:
        files = [f for f in files if f.name in set(args.only)]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    todo = [f for f in files if args.force or args.only or not is_current(fixture_path(args.out_dir, f.name), f.md5)]
    print(f"model={settings.claude_model} prompt_version={PROMPT_VERSION} files={len(files)} to_generate={len(todo)}")

    def work(sf):
        content = source.fetch(sf.file_id)
        t0 = time.monotonic()
        result = extractor.extract_with_meta(content, sf.mime_type, name=sf.name)
        latency_ms = int((time.monotonic() - t0) * 1000)
        doc = fixture_document(sf, result, latency_ms)
        fixture_path(args.out_dir, sf.name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        return sf, result, latency_ms

    total_in = total_out = 0
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(work, f): f for f in todo}
        for fut in as_completed(futures):
            sf = futures[fut]
            try:
                _, result, latency_ms = fut.result()
            except ExtractionError as e:
                failures += 1
                print(f"FAIL {sf.name}: {e.message}")
                continue
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL {sf.name}: {type(e).__name__}")
                continue
            total_in += result.input_tokens or 0
            total_out += result.output_tokens or 0
            ex = result.extraction
            print(
                f"ok   {sf.name}: type={ex.document_type} funds={len(ex.funds)} statements={len(ex.statements)} "
                f"pages={len(ex.pages)} in={result.input_tokens} out={result.output_tokens} "
                f"latency={latency_ms / 1000:.1f}s model={result.model}"
            )
    print(f"total input_tokens={total_in} output_tokens={total_out} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
