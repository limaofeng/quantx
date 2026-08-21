import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ExitPlansPanel } from '@/features/portfolio/components/SellManagementPanels';
import type { Position } from '@/features/portfolio/types';

const mocks = vi.hoisted(() => ({
  exitPlans: [] as Array<Record<string, unknown>>,
  refetch: vi.fn(),
}));

vi.mock('urql', () => ({
  useMutation: () => [{ fetching: false }, vi.fn()],
  useQuery: ({ query }: { query: { definitions?: unknown[] } }) => {
    const operationName = (
      query.definitions?.[0] as { name?: { value?: string } } | undefined
    )?.name?.value;
    if (operationName === 'ExitPlans') {
      return [
        {
          data: { exitPlans: mocks.exitPlans },
          error: undefined,
          fetching: false,
        },
        mocks.refetch,
      ];
    }
    return [{ data: undefined, error: undefined, fetching: false }, vi.fn()];
  },
  useSubscription: () => [{ data: undefined }],
}));

vi.mock('@/components/ui/app-dialog-context', () => ({
  useAppDialog: () => ({ confirm: vi.fn() }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

function makePlan(instrumentCode: string) {
  return {
    accountId: '300000013250',
    autoExitAuthorizationConfigVersion: null,
    autoExitAuthorizationExpiresAt: null,
    autoExitAuthorized: false,
    bucket: 'SWING',
    canEditRules: false,
    capacityError: null,
    capacityStatus: 'READY',
    completionNote: null,
    completionStrategy: null,
    configVersion: 1,
    costBasis: {},
    createdAt: '2026-08-21T09:30:00+08:00',
    dataQuality: 'MARKET_DATA_STALE',
    editRoute: null,
    enabled: true,
    entryAvgPrice: 28.3628,
    executionMode: 'live',
    exitedVolume: 0,
    groupId: null,
    instrumentCode,
    lastDecision: 'market_data_stale',
    lastError: null,
    lastEvaluatedAt: '2026-08-21T15:00:00+08:00',
    metadata: {},
    peakDrawdownPct: 0,
    peakPrice: 0,
    pendingClientOrderId: null,
    pendingIntentId: null,
    phase: 'WAITING_ARM',
    planId: `plan-${instrumentCode}`,
    protectedVolume: 400,
    remainingVolume: 400,
    rules: [],
    sourceId: 'manual',
    sourceType: 'MANUAL_POSITION',
    status: 'ACTIVE',
    strategyRunId: null,
    trailingFloorPct: null,
    updatedAt: '2026-08-21T15:00:00+08:00',
  };
}

describe('ExitPlansPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.exitPlans = [makePlan('300917.SZ')];
  });

  it('shows the holding name with the instrument code on an exit plan', () => {
    const holdings = [
      {
        instrumentName: '特发服务',
        stockCode: '300917.SZ',
      } as Position,
    ];

    render(
      <ExitPlansPanel
        accountId="300000013250"
        holdings={holdings}
        onNavigate={vi.fn()}
      />
    );

    expect(
      screen.getByRole('heading', { name: /特发服务.*300917\.SZ/ })
    ).toBeVisible();
  });

  it('falls back to the instrument code when the holding name is unavailable', () => {
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByRole('heading', { name: '300917.SZ' })).toBeVisible();
  });

  it('shows a meaningful label instead of the internal rule code', () => {
    mocks.exitPlans = [
      {
        ...makePlan('300917.SZ'),
        rules: [
          {
            rule_id: 'adaptive-volume-price',
            strategy: 'ADAPTIVE_VOLUME_PRICE_TRAILING',
          },
        ],
      },
    ];

    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByText('量价动态止盈')).toBeVisible();
    expect(
      screen.queryByText('ADAPTIVE_VOLUME_PRICE_TRAILING')
    ).not.toBeInTheDocument();
  });
});
