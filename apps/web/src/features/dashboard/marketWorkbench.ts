import {
  getShanghaiDateKey,
  parseMarketDate,
} from '@/components/trading-chart/utils/time-utils';

export interface MarketIndexDefinition {
  code: string;
  group: string;
  name: string;
  shortName: string;
}

/** The browser-local index workspace has a deliberately small, versioned contract. */
export const MARKET_INDEX_PREFERENCES_STORAGE_KEY = 'quantx_market_indices_v1';
export const MARKET_INDEX_PREFERENCES_VERSION = 1 as const;
export const MAX_MARKET_INDEXES = 100;

export interface MarketIndexPreferenceItem extends MarketIndexDefinition {
  visible: boolean;
}

export interface StoredMarketIndexPreferences {
  items: MarketIndexPreferenceItem[];
  version: typeof MARKET_INDEX_PREFERENCES_VERSION;
}

export type MarketIndexPreferenceReadResult = {
  items: MarketIndexPreferenceItem[];
  storageAvailable: boolean;
};

export interface MarketQuoteSnapshot {
  change?: number | null;
  changePercent?: number | null;
  currentPrice: number;
  high: number;
  low: number;
  open: number;
  preClose?: number | null;
  source?: 'daily-close' | 'persisted-tick' | 'live';
  stockCode: string;
  time: string;
  volume: number;
}

export type MarketTone =
  'strong' | 'positive' | 'balanced' | 'negative' | 'weak' | 'waiting';

export type AMarketSessionPhase =
  | 'calendar-pending'
  | 'closed'
  | 'pre-open'
  | 'call-auction'
  | 'opening-wait'
  | 'morning'
  | 'lunch-break'
  | 'afternoon'
  | 'post-close';

export interface AMarketSessionStatus {
  detail: string;
  isOpen: boolean;
  label: string;
  phase: AMarketSessionPhase;
}

const shanghaiSessionFormatter = new Intl.DateTimeFormat('en-US', {
  hour: '2-digit',
  hour12: false,
  minute: '2-digit',
  timeZone: 'Asia/Shanghai',
});

const getShanghaiSessionMinutes = (date: Date) => {
  let hour = 0;
  let minute = 0;

  shanghaiSessionFormatter.formatToParts(date).forEach(part => {
    if (part.type === 'hour') {
      hour = Number(part.value === '24' ? '0' : part.value);
    }
    if (part.type === 'minute') minute = Number(part.value);
  });

  return hour * 60 + minute;
};

/**
 * Resolve the A-share session independently from quote freshness. A live quote
 * remains cached through lunch and after close, so it cannot represent whether
 * the exchange is currently accepting continuous-auction orders.
 */
export function resolveAMarketSession(
  now: Date,
  isTradingDay: boolean | undefined
): AMarketSessionStatus {
  if (isTradingDay === undefined) {
    return {
      detail: '正在读取交易日历',
      isOpen: false,
      label: '校验交易日',
      phase: 'calendar-pending',
    };
  }

  if (!isTradingDay) {
    return {
      detail: '今日为非交易日',
      isOpen: false,
      label: '休市',
      phase: 'closed',
    };
  }

  const minutes = getShanghaiSessionMinutes(now);

  if (minutes < 9 * 60 + 15) {
    return {
      detail: '09:15 集合竞价',
      isOpen: false,
      label: '未开盘',
      phase: 'pre-open',
    };
  }
  if (minutes < 9 * 60 + 25) {
    return {
      detail: '09:30 正式开盘',
      isOpen: true,
      label: '集合竞价',
      phase: 'call-auction',
    };
  }
  if (minutes < 9 * 60 + 30) {
    return {
      detail: '09:30 正式开盘',
      isOpen: false,
      label: '等待开盘',
      phase: 'opening-wait',
    };
  }
  if (minutes < 11 * 60 + 30) {
    return {
      detail: '11:30 午间休市',
      isOpen: true,
      label: '上午盘',
      phase: 'morning',
    };
  }
  if (minutes < 13 * 60) {
    return {
      detail: '13:00 继续开盘',
      isOpen: false,
      label: '午间休市',
      phase: 'lunch-break',
    };
  }
  if (minutes < 15 * 60) {
    return {
      detail: '15:00 收盘',
      isOpen: true,
      label: '下午盘',
      phase: 'afternoon',
    };
  }

  return {
    detail: '今日交易结束',
    isOpen: false,
    label: '已收盘',
    phase: 'post-close',
  };
}

export const CORE_MARKET_INDICES: readonly MarketIndexDefinition[] = [
  {
    code: '000001.SH',
    group: '沪市',
    name: '上证指数',
    shortName: '上证',
  },
  {
    code: '399001.SZ',
    group: '深市',
    name: '深证成指',
    shortName: '深成',
  },
  {
    code: '399006.SZ',
    group: '深市',
    name: '创业板指',
    shortName: '创业板',
  },
  {
    code: '000680.SH',
    group: '沪市',
    name: '科创综指',
    shortName: '科创综指',
  },
  {
    code: '000688.SH',
    group: '沪市',
    name: '科创50',
    shortName: '科创50',
  },
  {
    code: '000510.SH',
    group: '沪市',
    name: '中证A500',
    shortName: '中证A500',
  },
  {
    code: '000300.SH',
    group: '沪市',
    name: '沪深300',
    shortName: '沪深300',
  },
  {
    code: '000852.SH',
    group: '沪市',
    name: '中证1000',
    shortName: '中证1000',
  },
  {
    code: '000016.SH',
    group: '沪市',
    name: '上证50',
    shortName: '上证50',
  },
  {
    code: '399330.SZ',
    group: '深市',
    name: '深证100',
    shortName: '深证100',
  },
  {
    code: '000905.SH',
    group: '沪市',
    name: '中证500',
    shortName: '中证500',
  },
  {
    code: '399673.SZ',
    group: '深市',
    name: '创业板50',
    shortName: '创业板50',
  },
  {
    code: '000698.SH',
    group: '沪市',
    name: '科创100',
    shortName: '科创100',
  },
] as const;

const defaultPreferenceItems = (): MarketIndexPreferenceItem[] =>
  CORE_MARKET_INDICES.map(definition => ({ ...definition, visible: true }));

const isMarketIndexDefinition = (
  value: unknown
): value is MarketIndexDefinition & { visible?: unknown } => {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.code === 'string' &&
    candidate.code.trim().length > 0 &&
    typeof candidate.name === 'string' &&
    candidate.name.trim().length > 0 &&
    typeof candidate.group === 'string' &&
    candidate.group.trim().length > 0 &&
    typeof candidate.shortName === 'string' &&
    candidate.shortName.trim().length > 0
  );
};

/**
 * Normalize untrusted localStorage data. Unknown catalog entries are retained
 * with their stored metadata so a valid directory selection survives reload;
 * the UI only creates entries from real directory rows.
 */
export function normalizeMarketIndexPreferenceItems(
  value: unknown,
  allowedCodes?: ReadonlySet<string>
): MarketIndexPreferenceItem[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const normalized: MarketIndexPreferenceItem[] = [];

  for (const item of value) {
    if (!isMarketIndexDefinition(item)) continue;
    const code = item.code.trim().toUpperCase();
    if (seen.has(code) || (allowedCodes && !allowedCodes.has(code))) continue;
    seen.add(code);
    normalized.push({
      code,
      group: item.group.trim(),
      name: item.name.trim(),
      shortName: item.shortName.trim(),
      visible: typeof item.visible === 'boolean' ? item.visible : true,
    });
    if (normalized.length >= MAX_MARKET_INDEXES) break;
  }

  return normalized;
}

export function createDefaultMarketIndexPreferences(): StoredMarketIndexPreferences {
  return {
    items: defaultPreferenceItems(),
    version: MARKET_INDEX_PREFERENCES_VERSION,
  };
}

export function readMarketIndexPreferences(
  storage: Pick<Storage, 'getItem'> | null | undefined = typeof window ===
  'undefined'
    ? undefined
    : window.localStorage
): MarketIndexPreferenceReadResult {
  const fallback = createDefaultMarketIndexPreferences().items;
  if (!storage) return { items: fallback, storageAvailable: false };

  try {
    const raw = storage.getItem(MARKET_INDEX_PREFERENCES_STORAGE_KEY);
    if (!raw) return { items: fallback, storageAvailable: true };
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return { items: fallback, storageAvailable: true };
    }
    if (!parsed || typeof parsed !== 'object') {
      return { items: fallback, storageAvailable: true };
    }
    const candidate = parsed as { items?: unknown; version?: unknown };
    if (candidate.version !== MARKET_INDEX_PREFERENCES_VERSION) {
      return { items: fallback, storageAvailable: true };
    }
    const items = normalizeMarketIndexPreferenceItems(candidate.items);
    if (!Array.isArray(candidate.items)) {
      return { items: fallback, storageAvailable: true };
    }
    return {
      items,
      storageAvailable: true,
    };
  } catch {
    return { items: fallback, storageAvailable: false };
  }
}

export function saveMarketIndexPreferences(
  items: readonly MarketIndexPreferenceItem[],
  storage: Pick<Storage, 'setItem'> | null | undefined = typeof window ===
  'undefined'
    ? undefined
    : window.localStorage
): boolean {
  if (!storage) return false;
  const normalized = normalizeMarketIndexPreferenceItems(items);
  try {
    storage.setItem(
      MARKET_INDEX_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        items: normalized,
        version: MARKET_INDEX_PREFERENCES_VERSION,
      } satisfies StoredMarketIndexPreferences)
    );
    return true;
  } catch {
    return false;
  }
}

export function preferenceItemsToDefinitions(
  items: readonly MarketIndexPreferenceItem[]
): MarketIndexDefinition[] {
  return items
    .filter(item => item.visible)
    .slice(0, MAX_MARKET_INDEXES)
    .map(({ visible: _visible, ...definition }) => definition);
}

export interface RankedMarketIndex {
  definition: MarketIndexDefinition;
  quote: MarketQuoteSnapshot;
}

export interface MarketSummary {
  advancers: number;
  averageChange: number | null;
  coverage: number;
  decliners: number;
  flats: number;
  leader: RankedMarketIndex | null;
  laggard: RankedMarketIndex | null;
  ranked: RankedMarketIndex[];
  tone: MarketTone;
  toneLabel: string;
}

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

const quoteSourcePriority: Record<
  NonNullable<MarketQuoteSnapshot['source']>,
  number
> = {
  'daily-close': 0,
  'persisted-tick': 1,
  live: 2,
};

const getMarketQuoteEpoch = (quote: MarketQuoteSnapshot): number =>
  parseMarketDate(quote.time)?.getTime() ?? Number.NEGATIVE_INFINITY;

/**
 * Pick the actually newest quote instead of blindly preferring the Redis hot
 * cache. Its projection deliberately survives restarts for several days, so a
 * stale cached tick must never override a persisted tick from today's session.
 */
export function selectLatestMarketQuote(
  ...quotes: Array<MarketQuoteSnapshot | null | undefined>
): MarketQuoteSnapshot | undefined {
  return quotes
    .filter(Boolean)
    .reduce<MarketQuoteSnapshot | undefined>((latest, candidate) => {
      if (!candidate) return latest;
      if (!latest) return candidate;

      const timeDifference =
        getMarketQuoteEpoch(candidate) - getMarketQuoteEpoch(latest);
      if (timeDifference > 0) return candidate;
      if (timeDifference < 0) return latest;

      const candidatePriority = candidate.source
        ? quoteSourcePriority[candidate.source]
        : -1;
      const latestPriority = latest.source
        ? quoteSourcePriority[latest.source]
        : -1;
      return candidatePriority > latestPriority ? candidate : latest;
    }, undefined);
}

export function isMarketQuoteFromShanghaiDate(
  value: string | null | undefined,
  expectedDate: Date
): boolean {
  if (!value) return false;
  const parsed = parseMarketDate(value);
  return Boolean(
    parsed && getShanghaiDateKey(parsed) === getShanghaiDateKey(expectedDate)
  );
}

export function isMarketQuoteFromTradingDate(
  value: string | null | undefined,
  expectedDate: string | null | undefined
): boolean {
  if (!value || !expectedDate) return false;
  const parsed = parseMarketDate(value);
  return Boolean(parsed && getShanghaiDateKey(parsed) === expectedDate);
}

const currentTradingDatePhases: ReadonlySet<AMarketSessionPhase> = new Set([
  'call-auction',
  'opening-wait',
  'morning',
  'lunch-break',
  'afternoon',
  'post-close',
]);

/**
 * Resolve the trading date the workbench must represent. During a session and
 * after its close that is today; before the auction, on weekends and on
 * holidays it is the latest completed trading day from the calendar.
 */
export function resolveMarketTargetTradingDate(
  now: Date,
  isTradingDay: boolean | undefined,
  phase: AMarketSessionPhase,
  tradingDays: readonly string[]
): string | null {
  const today = getShanghaiDateKey(now);
  if (isTradingDay && currentTradingDatePhases.has(phase)) return today;

  return (
    [...new Set(tradingDays)]
      .filter(day => day < today || (!isTradingDay && day === today))
      .sort()
      .at(-1) ?? null
  );
}

/**
 * Never mix yesterday's close into a workbench that is meant to represent a
 * newer trading date. If the target date is unavailable, return no quote so
 * the UI can report the missing snapshot instead of presenting stale content
 * as current market data.
 */
export function selectMarketQuoteForTradingDate(
  expectedDate: string | null | undefined,
  ...quotes: Array<MarketQuoteSnapshot | null | undefined>
): MarketQuoteSnapshot | undefined {
  if (!expectedDate) return selectLatestMarketQuote(...quotes);
  return selectLatestMarketQuote(
    ...quotes.filter(quote =>
      isMarketQuoteFromTradingDate(quote?.time, expectedDate)
    )
  );
}

export function isMarketQuoteFreshForSession(
  value: string | null | undefined,
  now: Date,
  phase: AMarketSessionPhase,
  maxOpenSessionAgeMs = 90_000
): boolean {
  if (!isMarketQuoteFromShanghaiDate(value, now)) return false;

  const parsed = parseMarketDate(value);
  if (!parsed) return false;
  const quoteMinutes = getShanghaiSessionMinutes(parsed);
  if (phase === 'lunch-break') return quoteMinutes >= 11 * 60 + 29;
  if (phase === 'post-close') return quoteMinutes >= 14 * 60 + 59;
  if (phase !== 'morning' && phase !== 'afternoon') return true;

  const ageMs = now.getTime() - parsed.getTime();
  return ageMs >= -5_000 && ageMs <= maxOpenSessionAgeMs;
}

function resolveTone(
  averageChange: number | null,
  advancers: number,
  decliners: number
): Pick<MarketSummary, 'tone' | 'toneLabel'> {
  if (averageChange === null) {
    return { tone: 'waiting', toneLabel: '等待行情' };
  }
  if (averageChange >= 0.75 && advancers >= decliners + 2) {
    return { tone: 'strong', toneLabel: '整体强势' };
  }
  if (averageChange >= 0.2) {
    return { tone: 'positive', toneLabel: '震荡偏强' };
  }
  if (averageChange <= -0.75 && decliners >= advancers + 2) {
    return { tone: 'weak', toneLabel: '整体偏弱' };
  }
  if (averageChange <= -0.2) {
    return { tone: 'negative', toneLabel: '震荡偏弱' };
  }
  return { tone: 'balanced', toneLabel: '多空均衡' };
}

export function summarizeCoreMarket(
  definitions: readonly MarketIndexDefinition[],
  quotes: ReadonlyMap<string, MarketQuoteSnapshot>
): MarketSummary {
  const ranked = definitions
    .map(definition => {
      const quote = quotes.get(definition.code);
      return quote && isFiniteNumber(quote.changePercent)
        ? { definition, quote }
        : null;
    })
    .filter((item): item is RankedMarketIndex => item !== null)
    .sort(
      (left, right) =>
        (right.quote.changePercent || 0) - (left.quote.changePercent || 0)
    );
  const changes = ranked.map(item => item.quote.changePercent || 0);
  const averageChange = changes.length
    ? changes.reduce((sum, value) => sum + value, 0) / changes.length
    : null;
  const advancers = changes.filter(value => value > 0.01).length;
  const decliners = changes.filter(value => value < -0.01).length;
  const flats = changes.length - advancers - decliners;
  const tone = resolveTone(averageChange, advancers, decliners);

  return {
    advancers,
    averageChange,
    coverage: ranked.length,
    decliners,
    flats,
    leader: ranked[0] || null,
    laggard: ranked.at(-1) || null,
    ranked,
    ...tone,
  };
}

export function formatMarketPrice(value: unknown): string {
  return isFiniteNumber(value)
    ? value.toLocaleString('zh-CN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    : '--';
}

export function formatMarketPercent(value: unknown): string {
  if (!isFiniteNumber(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

export function formatMarketVolume(value: unknown): string {
  if (!isFiniteNumber(value)) return '--';
  const absolute = Math.abs(value);
  if (absolute >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (absolute >= 10_000) return `${(value / 10_000).toFixed(2)}万`;
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

export function formatMarketTime(value: string | null | undefined): string {
  if (!value) return '--';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '--';
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(parsed);
}

export function formatMarketDate(value: string | null | undefined): string {
  if (!value) return '--';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '--';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    timeZone: 'Asia/Shanghai',
  }).format(parsed);
}

export function getRangePosition(
  quote: MarketQuoteSnapshot | null | undefined
): number | null {
  if (!quote || !isFiniteNumber(quote.currentPrice)) return null;
  if (!isFiniteNumber(quote.high) || !isFiniteNumber(quote.low)) return null;
  const range = quote.high - quote.low;
  if (range <= 0) return null;
  return Math.max(
    0,
    Math.min(100, ((quote.currentPrice - quote.low) / range) * 100)
  );
}
