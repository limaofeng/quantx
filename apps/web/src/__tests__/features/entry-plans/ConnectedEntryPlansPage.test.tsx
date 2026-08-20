import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConnectedEntryPlansPage } from '@/features/entry-plans/pages/ConnectedEntryPlansPage';

const mocks = vi.hoisted(() => ({
  refresh: vi.fn().mockResolvedValue(undefined),
  useEntryPlansWorkspace: vi.fn(),
  useSubscription: vi.fn(),
}));

vi.mock('urql', () => ({
  useSubscription: mocks.useSubscription,
}));

vi.mock('@/generated/gql/graphql', () => ({
  EntryIntentUpdatedDocument: 'ENTRY_INTENT_UPDATED_DOCUMENT',
  EntryPlanUpdatedDocument: 'ENTRY_PLAN_UPDATED_DOCUMENT',
}));

vi.mock('@/features/entry-plans/hooks/useEntryPlansWorkspace', () => ({
  useEntryPlansWorkspace: mocks.useEntryPlansWorkspace,
}));

vi.mock('@/features/entry-plans/pages/EntryPlansPage', () => ({
  EntryPlansPage: () => <div>权威买入计划工作区</div>,
}));

vi.mock('@/features/entry-plans/components/EntryPlanConfirmations', () => ({
  EntryAuthorizationConfirmationDialog: () => null,
  EntryIntentConfirmationDialog: () => null,
}));

describe('ConnectedEntryPlansPage realtime updates', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.useEntryPlansWorkspace.mockReturnValue({
      authorizationChallenge: null,
      clearAuthorizationChallenge: vi.fn(),
      clearIntentConfirmation: vi.fn(),
      confirmationBusy: false,
      confirmationError: null,
      confirmAuthorizationChallenge: vi.fn(),
      confirmPendingIntent: vi.fn(),
      controller: { refresh: mocks.refresh },
      fetching: false,
      intentConfirmation: null,
      view: {
        plans: [
          { id: 'active-plan', status: 'ARMED' },
          { id: 'completed-plan', status: 'COMPLETED' },
          { id: 'cancelled-plan', status: 'CANCELLED' },
          { id: 'expired-plan', status: 'EXPIRED' },
        ],
      },
    });
    mocks.useSubscription.mockImplementation(options => [
      options.query === 'ENTRY_PLAN_UPDATED_DOCUMENT'
        ? { data: { entryPlanUpdated: { planId: options.variables.planId } } }
        : { data: { entryIntentUpdated: [] } },
    ]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('subscribes only live plans and coalesces both notifications into one authoritative refresh', async () => {
    render(<ConnectedEntryPlansPage />);

    expect(screen.getByText('权威买入计划工作区')).toBeVisible();
    expect(mocks.useSubscription).toHaveBeenCalledTimes(2);
    expect(mocks.useSubscription).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ variables: { planId: 'active-plan' } })
    );
    expect(mocks.useSubscription).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ variables: { planId: 'active-plan' } })
    );

    await act(async () => {
      vi.advanceTimersByTime(80);
      await Promise.resolve();
    });

    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });
});
