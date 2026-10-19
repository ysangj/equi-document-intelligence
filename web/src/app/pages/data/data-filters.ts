import {
  DocumentType,
  FundFactsQuery,
  NetGross,
  ReturnType,
  ReturnsQuery,
  StatementsQuery,
  ValueStatus,
} from '../../api/models';

export type DataView = 'returns' | 'facts' | 'statements';

export const DATA_VIEWS: { id: DataView; label: string }[] = [
  { id: 'returns', label: 'Funds & Returns' },
  { id: 'facts', label: 'Fund facts' },
  { id: 'statements', label: 'Statements' },
];

/** FR-9 filter state shared by the Data tables. Empty arrays / nulls mean "All". */
export interface DataFilters {
  fundIds: number[];
  accountIds: number[];
  /** C4 `document_id`, set from a Search document / passage link */
  documentIds: number[];
  dateFrom: string | null;
  dateTo: string | null;
  documentTypes: DocumentType[];
  statuses: ValueStatus[];
  returnTypes: ReturnType[];
  netGross: NetGross[];
}

export const EMPTY_FILTERS: DataFilters = {
  fundIds: [],
  accountIds: [],
  documentIds: [],
  dateFrom: null,
  dateTo: null,
  documentTypes: [],
  statuses: [],
  returnTypes: [],
  netGross: [],
};

export interface PageState {
  /** 1-based (C3) */
  page: number;
  pageSize: number;
  /** contract sort string, or null for the endpoint default */
  sort: string | null;
}

/** A click on a value: which provenance to load (`/api/sources/{table}/{id}?field=`). */
export type { SourceRef } from '../../shared/source-loader';
export { sameRef } from '../../shared/source-loader';

function common(f: DataFilters) {
  return {
    document_id: f.documentIds,
    date_from: f.dateFrom,
    date_to: f.dateTo,
    document_type: f.documentTypes,
    status: f.statuses,
  };
}

export function returnsQuery(f: DataFilters, p: PageState): ReturnsQuery {
  return {
    fund_id: f.fundIds,
    return_type: f.returnTypes,
    net_gross: f.netGross,
    ...common(f),
    page: p.page,
    page_size: p.pageSize,
    sort: p.sort,
  };
}

export function factsQuery(f: DataFilters, p: PageState): FundFactsQuery {
  return { fund_id: f.fundIds, ...common(f), page: p.page, page_size: p.pageSize, sort: p.sort };
}

export function statementsQuery(f: DataFilters, p: PageState): StatementsQuery {
  return { account_id: f.accountIds, ...common(f), page: p.page, page_size: p.pageSize, sort: p.sort };
}

/** True when any filter that applies to `view` is set. */
export function hasActiveFilters(f: DataFilters, view: DataView): boolean {
  const shared =
    !!f.dateFrom || !!f.dateTo || f.documentTypes.length > 0 || f.statuses.length > 0 || f.documentIds.length > 0;
  if (view === 'returns') return shared || f.fundIds.length > 0 || f.returnTypes.length > 0 || f.netGross.length > 0;
  if (view === 'facts') return shared || f.fundIds.length > 0;
  return shared || f.accountIds.length > 0;
}

/** Clear only the filters that apply to `view`. */
export function clearFilters(f: DataFilters, view: DataView): DataFilters {
  const base = { ...f, dateFrom: null, dateTo: null, documentTypes: [], statuses: [], documentIds: [] };
  if (view === 'returns') return { ...base, fundIds: [], returnTypes: [], netGross: [] };
  if (view === 'facts') return { ...base, fundIds: [] };
  return { ...base, accountIds: [] };
}

/** Chip summary: "All (6)", the single label, or "3 selected". */
export function summarize<T>(selected: T[], options: { value: T; label: string }[], allCount?: number): string {
  if (!selected.length) return allCount !== undefined ? `All (${allCount})` : 'All';
  if (selected.length === 1) return options.find((o) => o.value === selected[0])?.label ?? String(selected[0]);
  return `${selected.length} selected`;
}
