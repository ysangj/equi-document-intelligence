/**
 * Safe rendering of search highlights (FR-10).
 *
 * `ChunkHit.headline_html` is escaped text whose only markup is `<mark>` / `</mark>`
 * (contract: /api/search "Highlight markup"). The UI does not trust that blindly and
 * does not use `[innerHTML]` at all: the string is parsed into plain-text segments,
 * each flagged as marked or not, and rendered with interpolation (`{{ }}`), so Angular
 * escapes every character. Anything that is not an exact `<mark>` / `</mark>` tag
 * (e.g. `<script>`, `<img onerror=...>`, `<mark class=x>`) is dropped, nesting is
 * flattened, and stray closers are ignored.
 */

export interface Segment {
  text: string;
  mark: boolean;
}

const ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
};

/** Decode the HTML entities the server emits (`&amp; &lt; &gt; &quot; &#x27;`, plus numeric). */
export function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, name: string) => {
    if (name[0] === '#') {
      const code = name[1] === 'x' || name[1] === 'X' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : m;
    }
    return ENTITIES[name.toLowerCase()] ?? m;
  });
}

/** Text between tags: remove any other tag, then any stray angle bracket, then decode. */
function cleanText(raw: string): string {
  const withoutTags = raw
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '')
    .replace(/<\/?[a-z!/?][^>]*>?/gi, '')
    .replace(/[<>]/g, '');
  return decodeEntities(withoutTags);
}

export function parseHeadline(html: string | null | undefined): Segment[] {
  if (!html) return [];
  const out: Segment[] = [];
  let open = false;
  const push = (text: string, mark: boolean) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.mark === mark) last.text += text;
    else out.push({ text, mark });
  };
  for (const part of html.split(/(<mark>|<\/mark>)/)) {
    if (part === '<mark>') open = true;
    else if (part === '</mark>') open = false;
    else push(cleanText(part), open);
  }
  return out;
}

/** Plain text of a headline (for titles / aria labels). */
export function headlineText(html: string | null | undefined): string {
  return parseHeadline(html)
    .map((s) => s.text)
    .join('');
}

/**
 * Client-side highlight of the query words in a plain-text name (fund, account,
 * document names are plain text in the contract). Case-insensitive; words shorter
 * than 2 characters are ignored.
 */
export function highlightTerms(text: string | null | undefined, query: string | null | undefined): Segment[] {
  if (!text) return [];
  const terms = (query ?? '')
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((t) => t.length >= 2);
  if (!terms.length) return [{ text, mark: false }];
  const escaped = [...new Set(terms)].sort((a, b) => b.length - a.length).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp(`(${escaped.join('|')})`, 'gi');
  const out: Segment[] = [];
  for (const part of text.split(re)) {
    if (!part) continue;
    out.push({ text: part, mark: terms.includes(part.toLowerCase()) });
  }
  return out;
}

/**
 * Segments for a provenance snippet with the value marked (FR-9, `SourceResponse.
 * snippet_highlight`). `start`/`end` are UTF-16 offsets into the snippet, half-open,
 * and the contract guarantees `snippet.slice(start, end) === text`. Pure text pieces,
 * so nothing in the snippet can become markup.
 *
 * Falls back to the plain snippet when there is no highlight, or when the offsets do
 * not describe a non-empty range inside the snippet that slices to `text` (not
 * integers, negative, past the end, start >= end, or computed on another snippet).
 * It never guesses another position, so a wrong number is never marked.
 */
export function snippetSegments(
  snippet: string | null | undefined,
  highlight: { start: number; end: number; text: string } | null | undefined,
): Segment[] {
  if (!snippet) return [];
  if (!highlight) return [{ text: snippet, mark: false }];
  const { start, end, text } = highlight;
  const fits =
    Number.isInteger(start) &&
    Number.isInteger(end) &&
    start >= 0 &&
    end <= snippet.length &&
    start < end &&
    snippet.slice(start, end) === text;
  if (!fits) return [{ text: snippet, mark: false }];
  const out: Segment[] = [];
  if (start > 0) out.push({ text: snippet.slice(0, start), mark: false });
  out.push({ text, mark: true });
  if (end < snippet.length) out.push({ text: snippet.slice(end), mark: false });
  return out;
}
