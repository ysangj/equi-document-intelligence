/**
 * Dev-only stand-in for `GET /api/search` (FR-10). Not pg_trgm / ts_rank_cd, but it
 * honours every rule of the contract the UI depends on: trim + 2-character minimum,
 * four groups, `matched_alias` when an alias beats the canonical name, chunk hits with
 * `headline_html` whose only markup is `<mark>` around HTML-escaped text, fragments
 * joined with ` … `, `page: null` for CSV row groups, and `took_ms`.
 */
import {
  AccountHit,
  AccountItem,
  ChunkHit,
  DocumentHit,
  DocumentStatus,
  DocumentType,
  FundHit,
  FundItem,
  HitAlias,
  SEARCH_MIN_QUERY_LENGTH,
  SearchResponse,
} from '../api/models';
import { MockPassage } from './mock-passages';

export interface SearchDoc {
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
  status: DocumentStatus;
  web_view_link: string | null;
}

export interface SearchCorpus {
  funds: FundItem[];
  accounts: AccountItem[];
  documents: SearchDoc[];
  passages: MockPassage[];
}

const STOPWORDS = new Set(
  'a an and are as at be but by for from has have in into is it its of on or our the their this to was we were what which with about did say said how'.split(' '),
);

const words = (s: string) => s.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);

/** Crude English stem so "rates" finds "rate" and "managers" finds "manager". */
function stem(w: string): string {
  return w.replace(/(ing|ed|es|s)$/u, '').replace(/(.)\1$/u, '$1') || w;
}

/** Word-similarity-like score in 0-1: share of query words found as word prefixes, scaled by length fit. */
export function nameScore(query: string, candidate: string): number {
  const q = words(query).filter((w) => w.length >= 2);
  if (!q.length) return 0;
  const c = words(candidate);
  const hit = q.filter((w) => c.some((x) => x.startsWith(w) || (w.length >= 4 && x.startsWith(stem(w))))).length;
  if (!hit) return 0;
  const fit = Math.min(1, query.trim().length / Math.max(1, candidate.length));
  return Math.round((hit / q.length) * (0.55 + 0.45 * fit) * 1000) / 1000;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
}

interface ParsedQuery {
  /** groups of alternatives: every group must match (AND), any word in a group (OR) */
  groups: string[][];
  exclude: string[];
}

/** websearch_to_tsquery-ish: quotes are ignored, `-word` excludes, `or` joins neighbours. */
function parseQuery(q: string): ParsedQuery {
  const tokens = q.replace(/"/g, ' ').split(/\s+/).filter(Boolean);
  const groups: string[][] = [];
  const exclude: string[] = [];
  let orNext = false;
  for (const t of tokens) {
    if (t.toLowerCase() === 'or') {
      orNext = groups.length > 0;
      continue;
    }
    if (t.startsWith('-') && t.length > 1) {
      exclude.push(...words(t.slice(1)).map(stem));
      continue;
    }
    const ws = words(t).filter((w) => !STOPWORDS.has(w)).map(stem);
    if (!ws.length) continue;
    if (orNext) groups[groups.length - 1].push(...ws);
    else groups.push(...ws.map((w) => [w]));
    orNext = false;
  }
  return { groups, exclude };
}

const matchesTerm = (word: string, term: string) => stem(word) === term || word.startsWith(term);

function chunkHit(p: MockPassage, doc: SearchDoc, pq: ParsedQuery): ChunkHit | null {
  const tokens = p.text.split(/([^\p{L}\p{N}]+)/u); // words at even indexes
  const lower = tokens.map((t) => t.toLowerCase());
  const terms = pq.groups.flat();
  const isHit = (i: number) => i % 2 === 0 && !!lower[i] && terms.some((t) => matchesTerm(lower[i], t));
  if (pq.exclude.some((x) => lower.some((w, i) => i % 2 === 0 && matchesTerm(w, x)))) return null;
  if (!pq.groups.every((g) => lower.some((w, i) => i % 2 === 0 && g.some((t) => matchesTerm(w, t))))) return null;

  const hits = tokens.map((_, i) => i).filter(isHit);
  // up to 2 fragments of ~25 words around the first hits
  const span = 24; // tokens on each side (words + separators)
  const frags: [number, number][] = [];
  for (const h of hits) {
    if (frags.length && h <= frags[frags.length - 1][1]) continue;
    if (frags.length === 2) break;
    frags.push([Math.max(0, h - span), Math.min(tokens.length - 1, h + span)]);
  }
  const render = ([a, b]: [number, number]) =>
    tokens
      .slice(a, b + 1)
      .map((t, k) => (isHit(a + k) ? `<mark>${escapeHtml(t)}</mark>` : escapeHtml(t)))
      .join('')
      .trim();
  const headline = frags.map(render).join(' … ');
  const rank = Math.round((hits.length / Math.sqrt(tokens.length / 2)) * 1000) / 1000;
  return {
    chunk_id: p.chunk_id,
    document_id: doc.document_id,
    document_name: doc.document_name,
    document_type: doc.document_type,
    page: p.page,
    chunk_index: p.chunk_index,
    headline_html: headline,
    rank,
    web_view_link: doc.web_view_link,
  };
}

/** Contract: every alias except the canonical name, `{alias, confirmed}`, sorted by alias. */
function hitAliases(aliases: readonly { alias: string; confirmed: boolean }[], canonical: string): HitAlias[] {
  return aliases
    .filter((a) => a.alias !== canonical)
    .map((a) => ({ alias: a.alias, confirmed: a.confirmed }))
    .sort((a, b) => (a.alias < b.alias ? -1 : a.alias > b.alias ? 1 : 0));
}

export function mockSearch(rawQuery: string, limit: number, corpus: SearchCorpus): SearchResponse {
  const query = rawQuery.trim();
  const empty: SearchResponse = {
    query,
    min_query_length: SEARCH_MIN_QUERY_LENGTH,
    groups: { funds: [], accounts: [], documents: [], chunks: [] },
    total: 0,
    took_ms: 0,
  };
  if (query.length < SEARCH_MIN_QUERY_LENGTH) return empty;

  const byScore = <T extends { score: number }>(xs: T[]) => xs.filter((x) => x.score >= 0.3).sort((a, b) => b.score - a.score).slice(0, limit);

  const funds: FundHit[] = byScore(
    corpus.funds.map((f) => {
      const canonical = nameScore(query, f.fund_name);
      let best = canonical;
      let alias: string | null = null;
      for (const a of f.aliases) {
        if (a.alias === f.fund_name) continue;
        const s = nameScore(query, a.alias);
        if (s > best) {
          best = s;
          alias = a.alias;
        }
      }
      return { fund_id: f.fund_id, fund_name: f.fund_name, manager: f.manager, matched_alias: alias, aliases: hitAliases(f.aliases, f.fund_name), score: best };
    }),
  );

  const accounts: AccountHit[] = byScore(
    corpus.accounts.map((a) => {
      const canonical = Math.max(nameScore(query, a.account_name), nameScore(query, a.account_number_masked ?? ''));
      let best = canonical;
      let alias: string | null = null;
      for (const al of a.aliases) {
        if (al.alias === a.account_name) continue;
        const s = nameScore(query, al.alias);
        if (s > best) {
          best = s;
          alias = al.alias;
        }
      }
      return {
        account_id: a.account_id,
        account_name: a.account_name,
        account_number_masked: a.account_number_masked,
        custodian: a.custodian,
        matched_alias: alias,
        aliases: hitAliases(a.aliases, a.account_name),
        score: best,
      };
    }),
  );

  const documents: DocumentHit[] = byScore(
    corpus.documents.map((d) => ({ ...d, score: nameScore(query, d.document_name.replace(/[_.-]+/g, ' ')) })),
  );

  const pq = parseQuery(query);
  const docs = new Map(corpus.documents.map((d) => [d.document_id, d]));
  const chunks: ChunkHit[] = pq.groups.length
    ? corpus.passages
        .map((p) => (docs.has(p.document_id) ? chunkHit(p, docs.get(p.document_id)!, pq) : null))
        .filter((c): c is ChunkHit => c !== null)
        .sort((a, b) => b.rank - a.rank || a.chunk_id - b.chunk_id)
        .slice(0, limit)
    : [];

  const total = funds.length + accounts.length + documents.length + chunks.length;
  return { ...empty, groups: { funds, accounts, documents, chunks }, total, took_ms: 9 + ((query.length * 7) % 31) };
}
