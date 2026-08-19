import { act, fireEvent, render, screen, within } from '@testing-library/react';
import type { ComponentProps } from 'react';

import { LimitUpRadarPanel } from '@/features/strategies/components/LimitUpRadarPanel';
import type { RadarCandidate } from '@/features/strategies/hooks/useLimitUpRadar';

type TestResizeCallback = ConstructorParameters<
  typeof globalThis.ResizeObserver
>[0];

let resizeObserverCallback: TestResizeCallback | null = null;

class TestResizeObserver {
  constructor(callback: TestResizeCallback) {
    resizeObserverCallback = callback;
  }

  disconnect() {}
  observe() {}
  unobserve() {}
}

function emitResize(
  entries: Array<{ height: number; target: Element; width: number }>
) {
  act(() => {
    resizeObserverCallback?.(
      entries.map(
        ({ height, target, width }) =>
          ({
            borderBoxSize: [],
            contentBoxSize: [],
            contentRect: {
              bottom: height,
              height,
              left: 0,
              right: width,
              toJSON: () => ({}),
              top: 0,
              width,
              x: 0,
              y: 0,
            },
            devicePixelContentBoxSize: [],
            target,
          }) as Parameters<TestResizeCallback>[0][number]
      ),
      {} as Parameters<TestResizeCallback>[1]
    );
  });
}

function buildCandidate(
  overrides: Partial<RadarCandidate> = {}
): RadarCandidate {
  return {
    amount: 1_000_000,
    amountPaceRatio: 1.2,
    ask1Price: 19.7,
    ask1Volume: 1_000,
    bid1Price: 19.69,
    bid1Volume: 900,
    blockedReasons: [],
    boardSegment: 'GROWTH',
    breakCount: 0,
    canCreateInstance: true,
    candidatePreference: null,
    changePct: 8.8,
    code: '300001.SZ',
    currentPrice: 19.7,
    cvar95LossPct: 1.4,
    depthImbalance5: 1.1,
    distanceToLimitPct: 0.05,
    distanceToLimitTicks: 1,
    events: [],
    existingInstanceId: null,
    exitPolicyVersion: 'v1',
    expectedNetReturnPct: 2.4,
    firstBoardCloseProbability: 0.76,
    firstSealedAt: null,
    firstTouchAt: null,
    highPositionType: 'BASE_BREAKOUT',
    industry: '电气设备',
    intradayTurnoverRatePct: 6.5,
    isStale: false,
    last5mVolumeRatio: 1.3,
    lastStageAt: null,
    limitUpPrice: 19.71,
    name: '特锐德',
    nextDayLimitSealProbability: 0.31,
    nextDayLimitTouchProbability: 0.55,
    normalizedLimitProgress: 0.98,
    oneWordLimitUp: false,
    priceChange5mPct: 1.1,
    promotionEligible: true,
    promotionFactors: [
      {
        code: 'breakout',
        contribution: 0.8,
        explanation: '量价同步突破',
        label: '突破质量',
      },
    ],
    promotionModelVersion: 'v1',
    promotionObserved: true,
    promotionScore: 81.5,
    promotionSnapshotVersion: 'v1',
    radarScore: 82,
    researchArtifact: null,
    scoreBreakdown: [],
    scoreVersion: 'v1',
    stage: 'NEAR_LIMIT',
    stageLabel: '临板',
    updatedAt: '2026-08-19T15:00:00+08:00',
    volumePaceRatio: 1.4,
    ...overrides,
  };
}

const summary = {
  brokenCount: 0,
  candidateCount: 0,
  discoveredCount: 0,
  eligibleCount: 0,
  excludedCount: 0,
  nearLimitCount: 0,
  scannedCount: 0,
  sealedCount: 0,
  staleCount: 0,
};

function panelProps(
  overrides: Partial<ComponentProps<typeof LimitUpRadarPanel>> = {}
): ComponentProps<typeof LimitUpRadarPanel> {
  return {
    armedCodes: new Set(),
    assistantEnabled: false,
    candidates: [],
    exitPlanCodes: new Set(),
    fetching: false,
    industries: [],
    industry: 'ALL',
    isScannerRunning: false,
    onArm: vi.fn(),
    onDisarm: vi.fn(),
    onIndustryChange: vi.fn(),
    onSearchChange: vi.fn(),
    onSelectCandidate: vi.fn(),
    onStageChange: vi.fn(),
    pendingCodes: new Set(),
    search: '',
    selectedCode: null,
    stage: 'ALL',
    summary,
    systemWarnings: [],
    ...overrides,
  };
}

describe('LimitUpRadarPanel', () => {
  beforeEach(() => {
    resizeObserverCallback = null;
    vi.stubGlobal('ResizeObserver', TestResizeObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps an offline error explicit without inventing candidates', () => {
    render(
      <LimitUpRadarPanel
        {...panelProps({
          errorMessage: '行情服务暂不可用',
          systemWarnings: ['Engine 全市场打板雷达尚未就绪'],
        })}
      />
    );

    expect(screen.getByRole('alert')).toHaveTextContent('系统保护');
    expect(screen.getByRole('alert')).toHaveTextContent('行情服务暂不可用');
    expect(screen.queryByText('雷达数据提示')).not.toBeInTheDocument();
    expect(screen.getByText('暂无匹配候选')).toBeVisible();
    expect(screen.getByText('雷达离线')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: '创建监控实例' })
    ).not.toBeInTheDocument();
  });

  it('exposes controlled selection with mouse and keyboard semantics', () => {
    const candidate = buildCandidate();
    const onArm = vi.fn();
    const onSelectCandidate = vi.fn();
    const props = panelProps({
      candidates: [candidate],
      onArm,
      onSelectCandidate,
    });
    const { rerender } = render(<LimitUpRadarPanel {...props} />);
    const row = screen.getByTestId(`limit-up-candidate-${candidate.code}`);

    expect(
      screen.getByRole('heading', { level: 2, name: '首板晋级候选' })
    ).toBeVisible();
    expect(
      screen.getByRole('grid', { name: '首板晋级候选列表' })
    ).toHaveAttribute('aria-rowcount', '2');
    expect(row).toHaveAttribute('role', 'row');
    expect(row).toHaveAttribute('tabindex', '0');
    expect(row).toHaveAttribute('aria-selected', 'false');
    expect(row).toHaveAttribute('aria-rowindex', '2');

    fireEvent.click(row);
    fireEvent.keyDown(row, { key: 'Enter' });
    fireEvent.keyDown(row, { key: ' ' });
    expect(onSelectCandidate).toHaveBeenCalledTimes(3);
    expect(onSelectCandidate).toHaveBeenLastCalledWith(candidate.code);

    onSelectCandidate.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '关注' }));
    expect(onArm).toHaveBeenCalledWith(candidate.code);
    expect(onSelectCandidate).not.toHaveBeenCalled();

    rerender(<LimitUpRadarPanel {...props} selectedCode={candidate.code} />);
    expect(row).toHaveAttribute('aria-selected', 'true');
  });

  it('uses measured width and height for compact and narrow virtualization', () => {
    const candidates = Array.from({ length: 100 }, (_, index) =>
      buildCandidate({
        code: `${String(index + 1).padStart(6, '0')}.SZ`,
        name: `候选 ${index + 1}`,
      })
    );
    render(<LimitUpRadarPanel {...panelProps({ candidates })} />);
    const panel = screen.getByTestId('limit-up-radar-panel');
    const viewport = screen.getByTestId('limit-up-radar-viewport');

    emitResize([
      { height: 640, target: panel, width: 800 },
      { height: 192, target: viewport, width: 774 },
    ]);
    expect(panel).toHaveAttribute('data-layout', 'compact');
    expect(
      screen.getByRole('columnheader', { name: '判断依据' })
    ).toBeVisible();
    expect(within(viewport).getAllByRole('row')).toHaveLength(12);
    expect(viewport.firstElementChild).toHaveStyle({ height: '9600px' });

    emitResize([
      { height: 640, target: panel, width: 640 },
      { height: 510, target: viewport, width: 614 },
    ]);
    expect(panel).toHaveAttribute('data-layout', 'narrow');
    expect(
      screen.getByRole('columnheader', { name: '候选卡片' })
    ).toBeInTheDocument();
    expect(within(viewport).getAllByRole('row')).toHaveLength(13);
    expect(viewport.firstElementChild).toHaveStyle({ height: '17000px' });
    expect(panel.querySelector('.overflow-x-auto')).not.toBeInTheDocument();
  });
});
