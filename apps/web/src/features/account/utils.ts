const SHANGHAI_TIME_ZONE = 'Asia/Shanghai';

export interface IntradayReferencePosition {
  volume: number;
  quote?: {
    lastPrice: number;
    preClose: number;
    time: string;
  } | null;
}

export interface IntradayReferenceSnapshot {
  tradeDate: string;
  snapshotAt: string;
  dailyPnlCny?: number | null;
  dailyReturnPct?: number | null;
}

export function calculateIntradayReference(
  positions: IntradayReferencePosition[],
  snapshots: IntradayReferenceSnapshot[],
  today: string
) {
  const quoted = positions.filter(
    position =>
      position.quote &&
      position.quote.preClose > 0 &&
      position.quote.lastPrice > 0
  );
  if (quoted.length > 0) {
    const value = quoted.reduce(
      (sum, position) =>
        sum +
        (position.quote!.lastPrice - position.quote!.preClose) *
          position.volume,
      0
    );
    const base = quoted.reduce(
      (sum, position) => sum + position.quote!.preClose * position.volume,
      0
    );
    return {
      value,
      percent: base > 0 ? (value / base) * 100 : null,
      covered: quoted.length,
      total: positions.length,
      quoteTime: quoted
        .map(position => position.quote!.time)
        .sort()
        .at(-1),
      snapshotAt: null,
      source: 'REALTIME_QUOTE' as const,
    };
  }
  const snapshot = snapshots.find(
    item => item.tradeDate === today && item.dailyPnlCny !== null
  );
  if (snapshot && typeof snapshot.dailyPnlCny === 'number') {
    return {
      value: snapshot.dailyPnlCny,
      percent: snapshot.dailyReturnPct ?? null,
      covered: 0,
      total: positions.length,
      quoteTime: null,
      snapshotAt: snapshot.snapshotAt,
      source: 'SAME_DAY_SNAPSHOT' as const,
    };
  }
  return {
    value: null,
    percent: null,
    covered: 0,
    total: positions.length,
    quoteTime: null,
    snapshotAt: null,
    source: 'UNAVAILABLE' as const,
  };
}

export function shanghaiDateKey(value = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value);
  const get = (type: string) => parts.find(part => part.type === type)?.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

export function daysAgoKey(days: number) {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return shanghaiDateKey(date);
}

export function formatMoney(value?: number | null, signed = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  const prefix = value < 0 ? '-' : signed && value > 0 ? '+' : '';
  return `${prefix}¥${Math.abs(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatPercent(value?: number | null, signed = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  return `${signed && value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function formatDateTime(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '--';
  const date =
    typeof value === 'number'
      ? new Date(value < 10_000_000_000 ? value * 1000 : value)
      : new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

export function pnlClass(
  value?: number | null,
  context: 'market' | 'holding' = 'market'
) {
  if (typeof value !== 'number') return 'text-slate-400';
  if (value > 0) return 'text-market-up';
  if (value < 0) {
    return context === 'holding' ? 'text-holding-down' : 'text-market-down';
  }
  return 'text-slate-300';
}

export function downloadCsv(
  filename: string,
  headers: string[],
  rows: Array<Array<string | number | null | undefined>>
) {
  const escape = (value: string | number | null | undefined) => {
    const text = value === null || value === undefined ? '' : String(value);
    return `"${text.replace(/"/g, '""')}"`;
  };
  const csv = [headers, ...rows]
    .map(row => row.map(escape).join(','))
    .join('\r\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
