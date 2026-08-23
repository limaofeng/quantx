import { print } from 'graphql';

import {
  buildMarketIndexDirectoryWhere,
  getMarketIndexDirectoryQuoteDisplay,
  mergeMarketIndexDirectoryRows,
  MarketIndexDirectoryQuery,
  marketIndexDirectoryOrder,
  updateMarketIndexDirectoryPreference,
} from '@/features/dashboard/marketIndexCatalog';
import {
  CORE_MARKET_INDICES,
  MARKET_INDEX_PREFERENCES_STORAGE_KEY,
  MARKET_INDEX_PREFERENCES_VERSION,
  MAX_MARKET_INDEXES,
  normalizeMarketIndexPreferenceItems,
  preferenceItemsToDefinitions,
  readMarketIndexPreferences,
  saveMarketIndexPreferences,
  type MarketIndexPreferenceItem,
} from '@/features/dashboard/marketWorkbench';

const defaultCodes = CORE_MARKET_INDICES.map(index => index.code);

const item = (code: string, visible = true): MarketIndexPreferenceItem => ({
  code,
  group: code.endsWith('.SZ') ? '深市' : '沪市',
  name: code,
  shortName: code,
  visible,
});

describe('market index workspace preferences', () => {
  it('keeps the approved thirteen-index order and excludes Beijing indices', () => {
    expect(defaultCodes).toEqual([
      '000001.SH',
      '399001.SZ',
      '399006.SZ',
      '000680.SH',
      '000688.SH',
      '000510.SH',
      '000300.SH',
      '000852.SH',
      '000016.SH',
      '399330.SZ',
      '000905.SH',
      '399673.SZ',
      '000698.SH',
    ]);
    expect(CORE_MARKET_INDICES.some(index => index.code.endsWith('.BJ'))).toBe(
      false
    );
    expect(CORE_MARKET_INDICES.some(index => index.name.includes('北证'))).toBe(
      false
    );
  });

  it('normalizes, deduplicates, and caps untrusted preference entries', () => {
    const entries = [
      { ...item('000001.sh'), code: ' 000001.sh ' },
      { ...item('000001.SH', false) },
      ...Array.from({ length: MAX_MARKET_INDEXES + 5 }, (_, index) =>
        item(`600${String(index).padStart(3, '0')}.SH`)
      ),
    ];

    const normalized = normalizeMarketIndexPreferenceItems(entries);

    expect(normalized).toHaveLength(MAX_MARKET_INDEXES);
    expect(normalized[0]).toMatchObject({ code: '000001.SH', visible: true });
    expect(new Set(normalized.map(entry => entry.code)).size).toBe(
      normalized.length
    );
  });

  it('reads corrupted, versioned, and explicitly empty storage safely', () => {
    const storage = {
      getItem: vi.fn(() => '{not-json'),
    };
    expect(readMarketIndexPreferences(storage)).toMatchObject({
      items: expect.arrayContaining([
        expect.objectContaining({ code: '000001.SH' }),
      ]),
      storageAvailable: true,
    });

    storage.getItem.mockReturnValue(
      JSON.stringify({ items: [item('000001.SH')], version: 999 })
    );
    expect(readMarketIndexPreferences(storage)).toMatchObject({
      items: expect.arrayContaining([
        expect.objectContaining({ code: '000001.SH' }),
      ]),
      storageAvailable: true,
    });

    storage.getItem.mockReturnValue(
      JSON.stringify({ items: [], version: MARKET_INDEX_PREFERENCES_VERSION })
    );
    expect(readMarketIndexPreferences(storage)).toEqual({
      items: [],
      storageAvailable: true,
    });
  });

  it('marks storage unavailable only when the storage operation throws', () => {
    expect(
      readMarketIndexPreferences({
        getItem: vi.fn(() => {
          throw new Error('blocked');
        }),
      }).storageAvailable
    ).toBe(false);
  });

  it('persists a versioned payload and keeps hidden entries out of definitions', () => {
    const storage = { setItem: vi.fn() };
    const entries = [item('000001.SH'), item('399001.SZ', false)];

    expect(saveMarketIndexPreferences(entries, storage)).toBe(true);
    expect(storage.setItem).toHaveBeenCalledWith(
      MARKET_INDEX_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        items: entries,
        version: MARKET_INDEX_PREFERENCES_VERSION,
      })
    );
    expect(
      preferenceItemsToDefinitions(entries).map(index => index.code)
    ).toEqual(['000001.SH']);
  });

  it('reports storage failure without changing the pure session contract', () => {
    const storage = {
      setItem: vi.fn(() => {
        throw new Error('quota');
      }),
    };

    expect(saveMarketIndexPreferences([item('000001.SH')], storage)).toBe(
      false
    );
  });
});

describe('market index directory contract', () => {
  const row = (code: string) => ({
    code,
    group: code.endsWith('.SZ') ? '深市' : '沪市',
    name: code,
    shortName: code,
  });

  it('merges cursor pages by code while retaining first-seen order', () => {
    expect(
      mergeMarketIndexDirectoryRows(
        [row('000001.SH'), row('000300.SH')],
        [row('000300.SH'), row('000688.SH')]
      )
    ).toEqual([row('000001.SH'), row('000300.SH'), row('000688.SH')]);
  });

  it('reports missing, stale, and live directory quote states honestly', () => {
    const quote = {
      currentPrice: 3123.45,
      high: 3130,
      low: 3100,
      open: 3110,
      preClose: 3100,
      stockCode: '000001.SH',
      time: '2026-08-23T10:00:00+08:00',
      volume: 100,
      changePercent: 0.75,
    };
    expect(getMarketIndexDirectoryQuoteDisplay(undefined, false)).toEqual({
      changePercent: null,
      currentPrice: null,
      status: 'missing',
      time: null,
    });
    expect(getMarketIndexDirectoryQuoteDisplay(quote, false)).toMatchObject({
      changePercent: null,
      currentPrice: null,
      status: 'stale',
      time: quote.time,
    });
    expect(getMarketIndexDirectoryQuoteDisplay(quote, true)).toMatchObject({
      changePercent: quote.changePercent,
      currentPrice: quote.currentPrice,
      status: 'live',
      time: quote.time,
    });
  });

  it('adds, shows hidden, removes visible, and blocks the 100-item limit', () => {
    const directoryRow = row('000688.SH');
    const added = updateMarketIndexDirectoryPreference([], directoryRow);
    expect(added.action).toBe('add');
    expect(added.items).toEqual([{ ...directoryRow, visible: true }]);

    const hidden = updateMarketIndexDirectoryPreference(
      [{ ...directoryRow, visible: false }],
      directoryRow
    );
    expect(hidden.action).toBe('show');
    expect(hidden.items[0].visible).toBe(true);

    const removed = updateMarketIndexDirectoryPreference(
      [{ ...directoryRow, visible: true }],
      directoryRow
    );
    expect(removed.action).toBe('remove');
    expect(removed.items).toEqual([]);

    const full = Array.from({ length: MAX_MARKET_INDEXES }, (_, index) =>
      item(`600${String(index).padStart(3, '0')}.SH`)
    );
    const blocked = updateMarketIndexDirectoryPreference(full, directoryRow);
    expect(blocked.action).toBe('limit');
    expect(blocked.items).toEqual(full);
  });

  it('uses real index filtering and cursor pagination without isTrading', () => {
    const byName = buildMarketIndexDirectoryWhere('科创', 'ALL');
    const byCode = buildMarketIndexDirectoryWhere('000688.SH', 'SH');
    const operation = print(MarketIndexDirectoryQuery);

    expect(byName).toMatchObject({ type: 'INDEX', name_contains: '科创' });
    expect(byName).not.toHaveProperty('isTrading');
    expect(byCode).toMatchObject({
      type: 'INDEX',
      market: 'SH',
      stockCode_contains: '000688.SH',
    });
    expect(marketIndexDirectoryOrder).toEqual({
      direction: 'ASC',
      field: 'CODE',
    });
    expect(operation).toContain('instrumentsConnection');
    expect(operation).toContain('$after: String');
    expect(operation).not.toContain('isTrading');
  });
});
