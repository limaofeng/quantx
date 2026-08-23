import {
  InstrumentOrderField,
  InstrumentType,
  Market_IndicesDirectoryDocument,
  type Market_IndicesDirectoryQuery,
  type Market_IndicesDirectoryQueryVariables,
  OrderDirection,
  type InstrumentWhereInput,
} from './graphql/__generated__/graphql';
import {
  MAX_MARKET_INDEXES,
  type MarketIndexPreferenceItem,
  type MarketQuoteSnapshot,
} from './marketWorkbench';

export const MARKET_INDEX_PAGE_SIZE = 40;

export type MarketIndexDirectoryNode =
  Market_IndicesDirectoryQuery['instrumentsConnection']['edges'][number]['node'];

export type MarketIndexDirectoryQueryData = Market_IndicesDirectoryQuery;
export type MarketIndexDirectoryQueryVariables =
  Market_IndicesDirectoryQueryVariables;

/** Generated from marketIndexDirectory.graphql against the live schema. */
export const MarketIndexDirectoryQuery = Market_IndicesDirectoryDocument;

export interface MarketIndexDirectoryRow {
  code: string;
  group: string;
  name: string;
  shortName: string;
}

export function mergeMarketIndexDirectoryRows(
  current: readonly MarketIndexDirectoryRow[],
  incoming: readonly MarketIndexDirectoryRow[]
): MarketIndexDirectoryRow[] {
  const rows = new Map(current.map(row => [row.code, row]));
  incoming.forEach(row => rows.set(row.code, row));
  return Array.from(rows.values());
}

export type MarketIndexDirectoryPreferenceAction =
  'add' | 'limit' | 'remove' | 'show';

export interface MarketIndexDirectoryPreferenceUpdate {
  action: MarketIndexDirectoryPreferenceAction;
  items: MarketIndexPreferenceItem[];
}

/** Apply the directory button contract without coupling it to React state. */
export function updateMarketIndexDirectoryPreference(
  items: readonly MarketIndexPreferenceItem[],
  row: MarketIndexDirectoryRow,
  maxItems = MAX_MARKET_INDEXES
): MarketIndexDirectoryPreferenceUpdate {
  const existing = items.find(item => item.code === row.code);
  if (!existing) {
    if (items.length >= maxItems) {
      return { action: 'limit', items: [...items] };
    }
    return {
      action: 'add',
      items: [...items, { ...row, visible: true }],
    };
  }
  if (!existing.visible) {
    return {
      action: 'show',
      items: items.map(item =>
        item.code === row.code ? { ...item, visible: true } : item
      ),
    };
  }
  return {
    action: 'remove',
    items: items.filter(item => item.code !== row.code),
  };
}

export type MarketIndexDirectoryQuoteStatus = 'live' | 'missing' | 'stale';

export interface MarketIndexDirectoryQuoteDisplay {
  changePercent: number | null;
  currentPrice: number | null;
  status: MarketIndexDirectoryQuoteStatus;
  time: string | null;
}

export function getMarketIndexDirectoryQuoteDisplay(
  quote: MarketQuoteSnapshot | undefined,
  isFresh: boolean
): MarketIndexDirectoryQuoteDisplay {
  if (!quote) {
    return {
      changePercent: null,
      currentPrice: null,
      status: 'missing',
      time: null,
    };
  }
  return {
    changePercent: isFresh ? (quote.changePercent ?? null) : null,
    currentPrice: isFresh ? quote.currentPrice : null,
    status: isFresh ? 'live' : 'stale',
    time: quote.time || null,
  };
}

export function isDirectoryCodeSearch(value: string): boolean {
  return /\d/.test(value) || /\.(SH|SZ)$/i.test(value);
}

export function buildMarketIndexDirectoryWhere(
  search: string,
  market: string
): InstrumentWhereInput {
  const normalizedSearch = search.trim();
  const where: InstrumentWhereInput = { type: InstrumentType.Index };
  if (market !== 'ALL') where.market = market;
  if (normalizedSearch) {
    if (isDirectoryCodeSearch(normalizedSearch)) {
      where.stockCode_contains = normalizedSearch;
    } else {
      where.name_contains = normalizedSearch;
    }
  }
  return where;
}

export const marketIndexDirectoryOrder = {
  direction: OrderDirection.Asc,
  field: InstrumentOrderField.Code,
} as const;

export function toMarketIndexDirectoryRow(
  node: MarketIndexDirectoryNode
): MarketIndexDirectoryRow {
  const code = node.id || node.instrumentId;
  const name = node.name?.trim() || code;
  const group =
    node.market === 'SZ' ? '深市' : node.market === 'SH' ? '沪市' : '--';
  return {
    code,
    group,
    name,
    shortName: name.replace(/指数$/, '') || code,
  };
}
