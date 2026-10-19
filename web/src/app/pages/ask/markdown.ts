/**
 * A small, safe markdown parser for Ask answers (FR-11). It returns a tree that the
 * template renders with interpolation only, so nothing in the answer can become
 * markup: there is no `[innerHTML]` and no sanitizer bypass anywhere.
 *
 * - Raw HTML is stripped before inline parsing (`<script>`/`<style>` with their
 *   content, comments, and any tag such as `<img onerror=...>`). Text like `a < b`
 *   is kept. Whatever is left renders as plain text.
 * - Links are kept only for http(s) and mailto URLs.
 * - `[n]` markers (contract regex `\[(\d+)\]`) become citation nodes when `n` is a
 *   known citation id, wherever they sit (next to punctuation, back to back), but
 *   never inside code spans or code blocks.
 * - `unverified_numbers` are highlighted inside text nodes after parsing, so the
 *   markdown structure is never changed by the highlighting.
 *
 * Supported: paragraphs, ATX headings, bullet and numbered lists (nested by
 * indentation), fenced and indented code blocks, block quotes, rules, GFM tables,
 * and inline bold, italic, code, links and hard line breaks.
 */

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'strong'; c: Inline[] }
  | { t: 'em'; c: Inline[] }
  | { t: 'code'; v: string }
  | { t: 'link'; href: string; c: Inline[] }
  | { t: 'cite'; id: number }
  | { t: 'unverified'; v: string }
  | { t: 'br' };

export type Align = 'left' | 'center' | 'right' | null;

export type Block =
  | { t: 'p'; c: Inline[] }
  | { t: 'h'; level: number; c: Inline[] }
  | { t: 'list'; ordered: boolean; start: number; items: Block[][] }
  | { t: 'code'; lang: string | null; v: string }
  | { t: 'quote'; c: Block[] }
  | { t: 'hr' }
  | { t: 'table'; align: Align[]; head: Inline[][]; rows: Inline[][][] };

export interface ParseOptions {
  /** citation ids that exist; other `[n]` stay as text */
  citationIds?: ReadonlySet<number> | readonly number[];
  /** exact substrings to highlight as unverified numbers */
  unverified?: readonly string[];
}

interface Ctx {
  cites: ReadonlySet<number>;
}

export function parseMarkdown(src: string, opts: ParseOptions = {}): Block[] {
  const ids = opts.citationIds;
  const ctx: Ctx = { cites: ids instanceof Set ? ids : new Set(ids ?? []) };
  const blocks = parseBlocks(normalize(src).split('\n'), ctx);
  const unverified = [...new Set((opts.unverified ?? []).filter((s) => s.trim().length > 0))];
  return unverified.length ? highlightBlocks(blocks, unverified) : blocks;
}

function normalize(src: string): string {
  return (src ?? '').replace(/\r\n?/g, '\n').replace(/\t/g, '    ').replace(/\u0000/g, '');
}

// ---- blocks ---------------------------------------------------------------------

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)\s*$/;
const HEADING = /^ {0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$/;
const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const BULLET = /^( {0,3})([-*+])\s+(.*)$/;
const ORDERED = /^( {0,3})(\d{1,9})[.)]\s+(.*)$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

const isBlank = (l: string) => l.trim() === '';

function parseBlocks(lines: string[], ctx: Ctx): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isBlank(line)) {
      i++;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1];
      const body: string[] = [];
      i++;
      while (i < lines.length && !new RegExp(`^ {0,3}${marker[0]}{${marker.length},}\\s*$`).test(lines[i])) body.push(lines[i++]);
      i++; // closing fence (or end of input)
      out.push({ t: 'code', lang: fence[2] || null, v: body.join('\n') });
      continue;
    }

    if (/^ {4}/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && (/^ {4}/.test(lines[i]) || (isBlank(lines[i]) && /^ {4}/.test(lines[i + 1] ?? '')))) {
        body.push(lines[i].slice(4));
        i++;
      }
      out.push({ t: 'code', lang: null, v: body.join('\n') });
      continue;
    }

    const h = HEADING.exec(line);
    if (h) {
      out.push({ t: 'h', level: h[1].length, c: parseInline(h[2] ?? '', ctx) });
      i++;
      continue;
    }

    if (HR.test(line)) {
      out.push({ t: 'hr' });
      i++;
      continue;
    }

    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && !isBlank(lines[i])) {
        const q = QUOTE.exec(lines[i]);
        body.push(q ? q[1] : lines[i]);
        i++;
      }
      out.push({ t: 'quote', c: parseBlocks(body, ctx) });
      continue;
    }

    if (BULLET.test(line) || ORDERED.test(line)) {
      i = parseList(lines, i, ctx, out);
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const head = splitRow(line);
      const align = splitRow(lines[i + 1]).map(alignOf);
      i += 2;
      const rows: Inline[][][] = [];
      while (i < lines.length && !isBlank(lines[i]) && lines[i].includes('|')) {
        const cells = splitRow(lines[i]);
        rows.push(head.map((_, k) => parseInline(cells[k] ?? '', ctx)));
        i++;
      }
      out.push({ t: 'table', align: head.map((_, k) => align[k] ?? null), head: head.map((c) => parseInline(c, ctx)), rows });
      continue;
    }

    // paragraph: until a blank line or the start of another block
    const para: string[] = [];
    while (i < lines.length && !isBlank(lines[i])) {
      const l = lines[i];
      if (para.length && (FENCE.test(l) || HEADING.test(l) || HR.test(l) || QUOTE.test(l) || BULLET.test(l) || /^ {0,3}1[.)]\s/.test(l))) break;
      para.push(l);
      i++;
    }
    out.push({ t: 'p', c: parseInline(para.join('\n'), ctx) });
  }
  return out;
}

function parseList(lines: string[], start: number, ctx: Ctx, out: Block[]): number {
  const first = BULLET.exec(lines[start]) ?? ORDERED.exec(lines[start])!;
  const ordered = !BULLET.test(lines[start]);
  const indent = first[1].length;
  const items: Block[][] = [];
  let i = start;
  while (i < lines.length) {
    const m = ordered ? ORDERED.exec(lines[i]) : BULLET.exec(lines[i]);
    if (!m || m[1].length !== indent) break;
    const contentIndent = lines[i].length - m[3].length;
    const body: string[] = [m[3]];
    i++;
    while (i < lines.length) {
      const l = lines[i];
      if (isBlank(l)) {
        // a blank line continues the item only when indented content follows
        const next = lines[i + 1] ?? '';
        if (!isBlank(next) && leading(next) >= contentIndent) {
          body.push('');
          i++;
          continue;
        }
        break;
      }
      const lead = leading(l);
      if (lead >= contentIndent) {
        body.push(l.slice(contentIndent));
        i++;
        continue;
      }
      if ((BULLET.test(l) || ORDERED.test(l)) && lead <= indent) break;
      if (lead > indent && (BULLET.test(l.trimStart()) || ORDERED.test(l.trimStart()))) {
        body.push(l.slice(Math.min(lead, contentIndent)));
        i++;
        continue;
      }
      if (FENCE.test(l) || HEADING.test(l) || HR.test(l) || QUOTE.test(l)) break;
      body.push(l.trim()); // lazy continuation
      i++;
    }
    items.push(parseBlocks(body, ctx));
    if (i < lines.length && isBlank(lines[i])) {
      const next = lines[i + 1] ?? '';
      const again = ordered ? ORDERED.exec(next) : BULLET.exec(next);
      if (again && again[1].length === indent) i++;
    }
  }
  out.push({ t: 'list', ordered, start: ordered ? Number(first[2]) : 1, items });
  return i;
}

const leading = (l: string) => l.length - l.trimStart().length;

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  const cells: string[] = [];
  let cur = '';
  let inCode = false;
  for (let k = 0; k < s.length; k++) {
    const ch = s[k];
    if (ch === '\\' && s[k + 1] === '|') {
      cur += '|';
      k++;
    } else if (ch === '`') {
      inCode = !inCode;
      cur += ch;
    } else if (ch === '|' && !inCode) {
      cells.push(cur.trim());
      cur = '';
    } else cur += ch;
  }
  cells.push(cur.trim());
  return cells;
}

function alignOf(sep: string): Align {
  const s = sep.trim();
  const l = s.startsWith(':');
  const r = s.endsWith(':');
  return l && r ? 'center' : r ? 'right' : l ? 'left' : null;
}

// ---- raw HTML -------------------------------------------------------------------

const TAG = /^<\/?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?\/?>/;

/**
 * Remove raw HTML from inline markdown source, leaving code spans untouched.
 * `<script>` / `<style>` lose their content too; comments are dropped; any other
 * tag is removed and its text content kept. Stray `<` (e.g. `a < b`) stays.
 */
export function stripHtml(src: string): string {
  let out = '';
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '`') {
      const run = /^`+/.exec(src.slice(i))![0];
      const close = src.indexOf(run, i + run.length);
      if (close !== -1) {
        out += src.slice(i, close + run.length);
        i = close + run.length;
        continue;
      }
      out += run;
      i += run.length;
      continue;
    }
    if (ch === '<') {
      const rest = src.slice(i);
      if (rest.startsWith('<!--')) {
        const end = rest.indexOf('-->');
        i += end === -1 ? rest.length : end + 3;
        continue;
      }
      const raw = /^<(script|style|iframe|object|embed|template|textarea|noscript)\b[^>]*>/i.exec(rest);
      if (raw) {
        const closeRe = new RegExp(`</${raw[1]}\\s*>`, 'i');
        const m = closeRe.exec(rest.slice(raw[0].length));
        i += m ? raw[0].length + m.index + m[0].length : rest.length;
        continue;
      }
      const tag = TAG.exec(rest);
      if (tag) {
        i += tag[0].length;
        continue;
      }
    }
    out += ch;
    i++;
  }
  return out;
}

// ---- inline ---------------------------------------------------------------------

const ESCAPABLE = /[\\`*_{}[\]()#+\-.!|<>~]/;
const CITE = /^\[(\d+)\]/;
const LINK = /^\[([^\]\n]*)\]\(\s*<?([^()\s<>]*)>?(?:\s+"[^"]*")?\s*\)/;
const SAFE_URL = /^(https?:\/\/|mailto:)/i;

export function safeHref(url: string): string | null {
  const u = url.trim();
  return SAFE_URL.test(u) ? u : null;
}

/** Parse inline markdown (raw HTML is stripped first). */
export function parseInline(src: string, ctx: Ctx = { cites: new Set() }): Inline[] {
  return parseSpan(stripHtml(src), ctx, true);
}

function parseSpan(s: string, ctx: Ctx, allowLinks: boolean): Inline[] {
  const out: Inline[] = [];
  let buf = '';
  const flush = () => {
    if (buf) out.push({ t: 'text', v: buf });
    buf = '';
  };
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    const rest = s.slice(i);

    if (ch === '\\') {
      if (s[i + 1] === '\n') {
        flush();
        out.push({ t: 'br' });
        i += 2;
        continue;
      }
      if (i + 1 < s.length && ESCAPABLE.test(s[i + 1])) {
        buf += s[i + 1];
        i += 2;
        continue;
      }
    }

    if (ch === '`') {
      const run = /^`+/.exec(rest)![0];
      const close = findRun(s, run, i + run.length);
      if (close !== -1) {
        flush();
        let code = s.slice(i + run.length, close).replace(/\n/g, ' ');
        if (code.length > 2 && code.startsWith(' ') && code.endsWith(' ') && code.trim()) code = code.slice(1, -1);
        out.push({ t: 'code', v: code });
        i = close + run.length;
        continue;
      }
      buf += run;
      i += run.length;
      continue;
    }

    if (ch === '[') {
      const link = allowLinks ? LINK.exec(rest) : null;
      if (link) {
        flush();
        const href = safeHref(link[2]);
        const label = parseSpan(link[1], ctx, false);
        if (href) out.push({ t: 'link', href, c: label.length ? label : [{ t: 'text', v: href }] });
        else out.push(...label);
        i += link[0].length;
        continue;
      }
      const cite = CITE.exec(rest);
      if (cite && ctx.cites.has(Number(cite[1]))) {
        flush();
        out.push({ t: 'cite', id: Number(cite[1]) });
        i += cite[0].length;
        continue;
      }
    }

    if ((ch === '*' || ch === '_') && s[i + 1] === ch) {
      const delim = ch + ch;
      const close = findDelim(s, delim, i + 2);
      if (close !== -1 && canOpen(s, i, 2) && (ch === '*' || !isWord(s[i - 1]))) {
        flush();
        out.push({ t: 'strong', c: parseSpan(s.slice(i + 2, close), ctx, allowLinks) });
        i = close + 2;
        continue;
      }
    }

    if (ch === '*' || ch === '_') {
      const close = findDelim(s, ch, i + 1);
      if (close !== -1 && canOpen(s, i, 1) && (ch === '*' || !isWord(s[i - 1]))) {
        flush();
        out.push({ t: 'em', c: parseSpan(s.slice(i + 1, close), ctx, allowLinks) });
        i = close + 1;
        continue;
      }
    }

    if (ch === '\n') {
      if (/ {2,}$/.test(buf)) {
        buf = buf.replace(/ +$/, '');
        flush();
        out.push({ t: 'br' });
      } else {
        buf = buf.replace(/ +$/, '') + ' ';
      }
      i++;
      while (s[i] === ' ') i++;
      continue;
    }

    buf += ch;
    i++;
  }
  flush();
  return out;
}

const isWord = (c: string | undefined) => !!c && /[\p{L}\p{N}]/u.test(c);

/** An opening delimiter must be followed by non-space. */
function canOpen(s: string, i: number, n: number): boolean {
  const next = s[i + n];
  return next !== undefined && !/\s/.test(next);
}

/** Next closing delimiter: preceded by non-space; `_` must not be followed by a word char (snake_case stays). */
function findDelim(s: string, delim: string, from: number): number {
  let k = from;
  while (k < s.length) {
    if (s[k] === '\\') {
      k += 2;
      continue;
    }
    if (s[k] === '`') {
      const run = /^`+/.exec(s.slice(k))![0];
      const close = findRun(s, run, k + run.length);
      k = close === -1 ? k + run.length : close + run.length;
      continue;
    }
    if (s.startsWith(delim, k) && k > from && !/\s/.test(s[k - 1])) {
      const after = s[k + delim.length];
      // for single delimiters, skip the inside of a double one (e.g. *a **b** c*)
      if (delim.length === 1 && after === delim[0]) {
        const inner = findDelim(s, delim + delim, k + 2);
        if (inner !== -1) {
          k = inner + 2;
          continue;
        }
      }
      if (delim[0] === '_' && isWord(after)) {
        k++;
        continue;
      }
      return k;
    }
    k++;
  }
  return -1;
}

function findRun(s: string, run: string, from: number): number {
  let k = s.indexOf(run, from);
  while (k !== -1) {
    const before = s[k - 1];
    const after = s[k + run.length];
    if (before !== '`' && after !== '`') return k;
    k = s.indexOf(run, k + 1);
  }
  return -1;
}

// ---- unverified numbers ---------------------------------------------------------

function highlightBlocks(blocks: Block[], needles: string[]): Block[] {
  const sorted = [...needles].sort((a, b) => b.length - a.length);
  const inl = (c: Inline[]) => highlightInlines(c, sorted);
  return blocks.map((b): Block => {
    switch (b.t) {
      case 'p':
      case 'h':
        return { ...b, c: inl(b.c) };
      case 'list':
        return { ...b, items: b.items.map((it) => highlightBlocks(it, sorted)) };
      case 'quote':
        return { ...b, c: highlightBlocks(b.c, sorted) };
      case 'table':
        return { ...b, head: b.head.map(inl), rows: b.rows.map((r) => r.map(inl)) };
      default:
        return b;
    }
  });
}

function highlightInlines(nodes: Inline[], needles: string[]): Inline[] {
  const out: Inline[] = [];
  for (const n of nodes) {
    if (n.t === 'text') out.push(...splitUnverified(n.v, needles));
    else if (n.t === 'strong' || n.t === 'em' || n.t === 'link') out.push({ ...n, c: highlightInlines(n.c, needles) });
    else out.push(n);
  }
  return out;
}

/**
 * Split text on every occurrence of the needles. A match must not continue a larger
 * number (no digit, or decimal point plus digit, directly before or after it), so
 * "0.92" is not highlighted inside "10.92" or "0.925".
 */
export function splitUnverified(text: string, needles: readonly string[]): Inline[] {
  const sorted = [...needles].sort((a, b) => b.length - a.length);
  const out: Inline[] = [];
  let buf = '';
  let i = 0;
  outer: while (i < text.length) {
    for (const n of sorted) {
      if (text.startsWith(n, i) && boundaryOk(text, i, n)) {
        if (buf) out.push({ t: 'text', v: buf });
        buf = '';
        out.push({ t: 'unverified', v: n });
        i += n.length;
        continue outer;
      }
    }
    buf += text[i];
    i++;
  }
  if (buf) out.push({ t: 'text', v: buf });
  return out;
}

function boundaryOk(text: string, i: number, n: string): boolean {
  const before = text[i - 1];
  const before2 = text[i - 2];
  const after = text[i + n.length];
  const after2 = text[i + n.length + 1];
  const digit = (c: string | undefined) => !!c && /[0-9]/.test(c);
  if (/^[0-9]/.test(n) && (digit(before) || (before === '.' && digit(before2)) || (before === ',' && digit(before2)))) return false;
  if (/[0-9]$/.test(n) && (digit(after) || ((after === '.' || after === ',') && digit(after2)))) return false;
  return true;
}

/** Plain text of inline nodes (tests, aria labels). */
export function inlineText(nodes: Inline[]): string {
  return nodes
    .map((n) => {
      switch (n.t) {
        case 'text':
        case 'code':
        case 'unverified':
          return n.v;
        case 'cite':
          return `[${n.id}]`;
        case 'br':
          return '\n';
        default:
          return inlineText(n.c);
      }
    })
    .join('');
}
