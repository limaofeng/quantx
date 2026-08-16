import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ManualPlanEditor } from '@/features/portfolio/components/SellManagementPanels';

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  refetch: vi.fn(),
  toast: vi.fn(),
  useMutation: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock('urql', () => ({
  useMutation: mocks.useMutation,
  useQuery: mocks.useQuery,
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

describe('ManualPlanEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useQuery.mockReturnValue([
      { data: undefined, error: undefined, fetching: false },
      mocks.refetch,
    ]);
    mocks.useMutation.mockReturnValue([
      { data: undefined, error: undefined, fetching: false },
      mocks.mutate,
    ]);
  });

  it('uses the full row when expanded', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <ManualPlanEditor
        accountId="300000013250"
        initialInstrumentCode="300917.SZ"
        onFinishedEditing={vi.fn()}
        onSaved={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));

    expect(container.querySelector('section')).toHaveClass(
      'w-full',
      'basis-full'
    );
  });

  it('synchronizes a new-plan draft with the selected holding', async () => {
    const user = userEvent.setup();
    const props = {
      accountId: '300000013250',
      onFinishedEditing: vi.fn(),
      onSaved: vi.fn(),
    };
    const { rerender } = render(
      <ManualPlanEditor
        {...props}
        initialInstrumentCode="300917.SZ"
      />
    );

    await user.click(screen.getByRole('button', { name: '手动添加计划' }));
    expect(screen.getByLabelText('股票')).toHaveValue('300917.SZ');

    rerender(
      <ManualPlanEditor
        {...props}
        initialInstrumentCode="302132.SZ"
      />
    );

    expect(screen.getByLabelText('股票')).toHaveValue('302132.SZ');
  });
});
