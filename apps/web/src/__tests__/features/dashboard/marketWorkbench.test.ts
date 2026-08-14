import {
  CORE_MARKET_INDICES,
  formatMarketPercent,
  getRangePosition,
  isMarketQuoteFreshForSession,
  isMarketQuoteFromShanghaiDate,
  resolveAMarketSession,
  resolveMarketTargetTradingDate,
  selectLatestMarketQuote,
  selectMarketQuoteForTradingDate,
  summarizeCoreMarket,
  type MarketQuoteSnapshot,
} from '@/features/dashboard/marketWorkbench';

const quote = (
  stockCode: string,
  changePercent: number
): MarketQuoteSnapshot => ({
  stockCode,
  changePercent,
  change: changePercent * 10,
  currentPrice: 3_000 + changePercent,
  high: 3_100,
  low: 2_900,
  open: 2_980,
  preClose: 3_000,
  time: '2026-08-13T02:30:00Z',
  volume: 100_000,
});

describe('market workbench summary', () => {
  it('ranks live indices and derives a transparent equal-weight tone', () => {
    const quotes = new Map<string, MarketQuoteSnapshot>([
      ['000001.SH', quote('000001.SH', 1.2)],
      ['399001.SZ', quote('399001.SZ', 0.6)],
      ['399006.SZ', quote('399006.SZ', -0.3)],
    ]);

    const summary = summarizeCoreMarket(CORE_MARKET_INDICES, quotes);

    expect(summary.coverage).toBe(3);
    expect(summary.advancers).toBe(2);
    expect(summary.decliners).toBe(1);
    expect(summary.averageChange).toBeCloseTo(0.5);
    expect(summary.tone).toBe('positive');
    expect(summary.leader?.definition.name).toBe('上证指数');
    expect(summary.laggard?.definition.name).toBe('创业板指');
  });

  it('does not invent a market status before quotes arrive', () => {
    const summary = summarizeCoreMarket(CORE_MARKET_INDICES, new Map());

    expect(summary.averageChange).toBeNull();
    expect(summary.toneLabel).toBe('等待行情');
    expect(summary.coverage).toBe(0);
    expect(formatMarketPercent(null)).toBe('--');
  });

  it('locates the current price inside the intraday range', () => {
    const current = quote('000001.SH', 0.5);
    current.currentPrice = 3_050;

    expect(getRangePosition(current)).toBe(75);
  });

  it('uses a current persisted tick instead of a stale live-cache projection', () => {
    const dailyClose = {
      ...quote('000001.SH', 0.3),
      source: 'daily-close' as const,
      time: '2026-08-12T07:00:00.000Z',
    };
    const staleLive = {
      ...quote('000001.SH', 0.4),
      source: 'live' as const,
      time: '2026-08-12T07:01:00.000Z',
    };
    const currentTick = {
      ...quote('000001.SH', 0.8),
      source: 'persisted-tick' as const,
      time: '2026-08-13T06:35:00.000Z',
    };

    expect(selectLatestMarketQuote(dailyClose, currentTick, staleLive)).toBe(
      currentTick
    );
  });

  it('prefers the live projection when two sources have the same timestamp', () => {
    const persistedTick = {
      ...quote('000001.SH', 0.8),
      source: 'persisted-tick' as const,
    };
    const live = {
      ...persistedTick,
      source: 'live' as const,
    };

    expect(selectLatestMarketQuote(persistedTick, live)).toBe(live);
  });

  it('does not mix the previous close into a newer target trading date', () => {
    const previousClose = {
      ...quote('000001.SH', -0.4),
      source: 'daily-close' as const,
      time: '2026-08-12T07:00:00.000Z',
    };
    const currentTick = {
      ...quote('000001.SH', 0.2),
      source: 'persisted-tick' as const,
      time: '2026-08-13T06:43:00.000Z',
    };

    expect(
      selectMarketQuoteForTradingDate('2026-08-13', previousClose, currentTick)
    ).toBe(currentTick);
    expect(
      selectMarketQuoteForTradingDate('2026-08-13', previousClose)
    ).toBeUndefined();
  });

  it('detects whether a quote belongs to the current Shanghai trading date', () => {
    const now = new Date('2026-08-13T06:35:00.000Z');

    expect(isMarketQuoteFromShanghaiDate('2026-08-13T01:31:00.000Z', now)).toBe(
      true
    );
    expect(isMarketQuoteFromShanghaiDate('2026-08-12T07:00:00.000Z', now)).toBe(
      false
    );
  });

  it('rejects an old same-day quote during continuous trading', () => {
    const now = new Date('2026-08-13T06:35:00.000Z');

    expect(
      isMarketQuoteFreshForSession('2026-08-13T06:34:00.000Z', now, 'afternoon')
    ).toBe(true);
    expect(
      isMarketQuoteFreshForSession('2026-08-13T06:30:00.000Z', now, 'afternoon')
    ).toBe(false);
  });

  it('keeps the last current-day quote valid during lunch and after close', () => {
    expect(
      isMarketQuoteFreshForSession(
        '2026-08-13T03:30:00.000Z',
        new Date('2026-08-13T04:35:00.000Z'),
        'lunch-break'
      )
    ).toBe(true);
    expect(
      isMarketQuoteFreshForSession(
        '2026-08-13T07:00:00.000Z',
        new Date('2026-08-13T07:30:00.000Z'),
        'post-close'
      )
    ).toBe(true);
  });

  it('rejects same-day ticks that stopped well before a session boundary', () => {
    expect(
      isMarketQuoteFreshForSession(
        '2026-08-13T02:00:00.000Z',
        new Date('2026-08-13T04:35:00.000Z'),
        'lunch-break'
      )
    ).toBe(false);
    expect(
      isMarketQuoteFreshForSession(
        '2026-08-13T06:43:00.000Z',
        new Date('2026-08-13T07:05:00.000Z'),
        'post-close'
      )
    ).toBe(false);
  });

  it.each([
    ['2026-08-13T01:14:00.000Z', 'pre-open', '未开盘'],
    ['2026-08-13T01:15:00.000Z', 'call-auction', '集合竞价'],
    ['2026-08-13T01:25:00.000Z', 'opening-wait', '等待开盘'],
    ['2026-08-13T01:30:00.000Z', 'morning', '上午盘'],
    ['2026-08-13T03:30:00.000Z', 'lunch-break', '午间休市'],
    ['2026-08-13T04:35:00.000Z', 'lunch-break', '午间休市'],
    ['2026-08-13T05:00:00.000Z', 'afternoon', '下午盘'],
    ['2026-08-13T07:00:00.000Z', 'post-close', '已收盘'],
  ])(
    'resolves the Shanghai session at %s',
    (timestamp, expectedPhase, expectedLabel) => {
      const session = resolveAMarketSession(new Date(timestamp), true);

      expect(session.phase).toBe(expectedPhase);
      expect(session.label).toBe(expectedLabel);
    }
  );

  it('uses the authoritative trading calendar before intraday time rules', () => {
    const closed = resolveAMarketSession(
      new Date('2026-08-13T02:00:00.000Z'),
      false
    );
    const pending = resolveAMarketSession(
      new Date('2026-08-13T04:35:00.000Z'),
      undefined
    );

    expect(closed).toMatchObject({ phase: 'closed', label: '休市' });
    expect(pending).toMatchObject({
      phase: 'calendar-pending',
      label: '校验交易日',
    });
  });

  it('uses today after close and the previous session on non-trading days', () => {
    expect(
      resolveMarketTargetTradingDate(
        new Date('2026-08-13T07:30:00.000Z'),
        true,
        'post-close',
        ['2026-08-12', '2026-08-13']
      )
    ).toBe('2026-08-13');
    expect(
      resolveMarketTargetTradingDate(
        new Date('2026-08-15T02:00:00.000Z'),
        false,
        'closed',
        ['2026-08-12', '2026-08-13', '2026-08-14']
      )
    ).toBe('2026-08-14');
  });

  it('keeps the previous completed session before the auction starts', () => {
    expect(
      resolveMarketTargetTradingDate(
        new Date('2026-08-14T00:30:00.000Z'),
        true,
        'pre-open',
        ['2026-08-12', '2026-08-13', '2026-08-14']
      )
    ).toBe('2026-08-13');
  });
});
