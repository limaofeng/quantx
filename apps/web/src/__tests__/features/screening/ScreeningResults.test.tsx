import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ScreeningResults } from '@/features/screening/components/ScreeningResults';
import { type StockScreeningResult } from '@/features/screening/types';

const baseStock: StockScreeningResult = {
  avgVolume20: 1000,
  changePct: 6.07,
  code: '600023.SH',
  consecutiveDownDays: 0,
  consecutiveDownPct: 0,
  currentPrice: 6.47,
  d: 41,
  daysSinceLow: 8,
  daysSincePeak: 8,
  industry: '公用事业',
  instrumentType: 'stock',
  isBullish: true,
  j: 62,
  k: 48,
  lowPrice: 5.6,
  lowerBand: 5.8,
  ma5: 6.2,
  ma10: 6.1,
  ma20: 5.9,
  matchedStrategies: [
    '强势股',
    'KDJ 金叉',
    '放量突破',
    '均线金叉',
    '布林上轨突破',
  ],
  middleBand: 6.1,
  name: '浙能电力',
  openPrice: 6.1,
  peakPrice: 6.8,
  priceDropPct: -4.4,
  priceRisePct: 12,
  rsi6: 91,
  rsi12: 59,
  rsi24: 61,
  score: 6.5,
  upperBand: 6.7,
  volume: 2000,
  volumeRatio: 1.9,
};

describe('ScreeningResults', () => {
  function renderResults(onSortChange = vi.fn()) {
    render(
      <ScreeningResults
        screeningLoading={false}
        results={[baseStock]}
        meta={{
          hasStaleData: false,
          isComplete: true,
          missingSnapshotDates: [],
          total: 1,
          warnings: [],
        }}
        onSortChange={onSortChange}
      />
    );

    return { onSortChange };
  }

  function getHeaderTexts() {
    return screen
      .getAllByRole('columnheader')
      .map(header => header.textContent?.replace(/\s+/g, ' ').trim() || '');
  }

  it('requests full-result sorting from sortable headers and header menu', () => {
    const onSortChange = vi.fn();

    renderResults(onSortChange);

    fireEvent.click(screen.getByTestId('screening-sort-changePct'));
    expect(onSortChange).toHaveBeenLastCalledWith({
      direction: 'DESC',
      field: 'CHANGE_PCT',
    });

    fireEvent.click(screen.getByTestId('screening-sort-menu-price'));
    fireEvent.click(screen.getByText('升序'));
    expect(onSortChange).toHaveBeenLastCalledWith({
      direction: 'ASC',
      field: 'CURRENT_PRICE',
    });
  });

  it('keeps the identity column frozen by default', () => {
    renderResults();

    const identityHeader = screen
      .getByTestId('screening-sort-identity')
      .closest('th')!;
    const identityCell = screen
      .getByTitle('浙能电力 600023.SH 公用事业')
      .closest('td')!;

    expect(identityHeader).toHaveStyle({
      left: '0px',
      position: 'sticky',
    });
    expect(identityHeader.draggable).toBe(false);
    expect(identityCell).toHaveStyle({
      left: '0px',
      position: 'sticky',
    });
  });

  it('opens column context menu and pins columns to the left', () => {
    renderResults();

    fireEvent.contextMenu(
      screen.getByTestId('screening-sort-price').closest('th')!
    );
    fireEvent.click(screen.getByText('固定列'));

    expect(getHeaderTexts()[0]).toContain('代码 / 名称');
    expect(getHeaderTexts()[1]).toContain('价格');

    fireEvent.contextMenu(
      screen.getByTestId('screening-sort-price').closest('th')!
    );
    expect(screen.getByText('取消固定列')).toBeInTheDocument();
  });

  it('reorders columns by dragging a column header', () => {
    renderResults();
    const payload: Record<string, string> = {};
    const dataTransfer = {
      dropEffect: '',
      effectAllowed: '',
      getData: vi.fn((key: string) => payload[key]),
      setData: vi.fn((key: string, value: string) => {
        payload[key] = value;
      }),
    };

    const priceHeader = screen
      .getByTestId('screening-sort-price')
      .closest('th')!;
    const signalsHeader = screen
      .getByTestId('screening-sort-signals')
      .closest('th')!;

    fireEvent.dragStart(priceHeader, { dataTransfer });
    fireEvent.dragOver(signalsHeader, { dataTransfer });
    fireEvent.drop(signalsHeader, { dataTransfer });

    expect(getHeaderTexts().slice(0, 4).join('|')).toContain(
      '代码 / 名称|涨跌幅|信号|价格'
    );
  });

  it('keeps signal badges horizontal without a visible scrollbar', () => {
    renderResults();

    const signalStrip = screen.getByTestId('screening-signal-strip');

    expect(signalStrip.className).toContain('overflow-x-auto');
    expect(signalStrip.className).toContain('[scrollbar-width:none]');
    expect(signalStrip.className).toContain('[&::-webkit-scrollbar]:hidden');
  });

  it('supports horizontal drag scrolling from the table body', () => {
    renderResults();

    const grid = screen.getByTestId('screening-results-grid') as HTMLDivElement;
    grid.scrollLeft = 20;
    grid.setPointerCapture = vi.fn();
    grid.releasePointerCapture = vi.fn();
    grid.hasPointerCapture = vi.fn(() => true);

    fireEvent.pointerDown(grid, {
      button: 0,
      clientX: 500,
      pointerId: 1,
    });
    fireEvent.pointerMove(grid, {
      clientX: 420,
      pointerId: 1,
    });

    expect(grid.scrollLeft).toBe(100);
    expect(grid.className).toContain('cursor-grabbing');
    expect(grid.classList.contains('scrollbar-active')).toBe(true);

    fireEvent.pointerUp(grid, { pointerId: 1 });
    expect(grid.releasePointerCapture).toHaveBeenCalledWith(1);
  });

  it('shows the instrument type badge in identity cells', () => {
    render(
      <ScreeningResults
        screeningLoading={false}
        results={[{ ...baseStock, instrumentType: 'etf' }]}
        meta={{
          hasStaleData: false,
          isComplete: true,
          missingSnapshotDates: [],
          total: 1,
          warnings: [],
        }}
      />
    );

    expect(screen.getByText('ETF')).toBeInTheDocument();
  });

  it('renders missing financial metrics as placeholders', () => {
    renderResults();

    expect(screen.getAllByText('--').length).toBeGreaterThanOrEqual(3);
  });

  it('renders available ROE with one decimal percent', () => {
    render(
      <ScreeningResults
        screeningLoading={false}
        results={[
          {
            ...baseStock,
            financialAnnounceDate: '2026-04-20',
            financialQualityFlags: ['valid'],
            financialReportDate: '2025-12-31',
            roe: 14.72,
          },
        ]}
        meta={{
          hasStaleData: false,
          isComplete: true,
          missingSnapshotDates: [],
          total: 1,
          warnings: [],
        }}
      />
    );

    expect(screen.getByText('14.7%')).toBeInTheDocument();
  });

  it('labels quarter growth columns and shows accumulated growth in tooltip', () => {
    render(
      <ScreeningResults
        screeningLoading={false}
        results={[
          {
            ...baseStock,
            financialAnnounceDate: '2026-04-20',
            financialReportDate: '2025-12-31',
            netProfitAccumGrowth: 33.2,
            netProfitGrowth: 12.34,
            revenueAccumGrowth: 22.1,
            yoyGrowth: 8.88,
          },
        ]}
        meta={{
          hasStaleData: false,
          isComplete: true,
          missingSnapshotDates: [],
          total: 1,
          warnings: [],
        }}
      />
    );

    expect(screen.getByText('净利单季同比')).toBeInTheDocument();
    expect(screen.getByText('营收单季同比')).toBeInTheDocument();

    const quarterGrowth = screen.getByText('12.3%');
    expect(quarterGrowth).toHaveAttribute(
      'title',
      expect.stringContaining('净利累计同比: 33.2%')
    );
    expect(quarterGrowth).toHaveAttribute(
      'title',
      expect.stringContaining('营收累计同比: 22.1%')
    );
  });
});
