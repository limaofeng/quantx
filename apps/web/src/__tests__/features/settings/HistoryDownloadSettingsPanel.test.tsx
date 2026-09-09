import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

import { HistoryDownloadSettingsPanel } from '@/features/settings/components/HistoryDownloadSettingsPanel';

const mocks = vi.hoisted(() => ({
  useQuery: vi.fn(),
  useMutation: vi.fn(),
  save: vi.fn(),
  refresh: vi.fn(),
  permissions: ['system-config:write'],
}));
vi.mock('urql', () => ({
  useQuery: mocks.useQuery,
  useMutation: mocks.useMutation,
}));
vi.mock('@/core/auth', () => ({
  useAuth: () => ({ user: { permissions: mocks.permissions } }),
}));

beforeEach(() => {
  mocks.permissions = ['system-config:write'];
  mocks.save.mockReset();
  mocks.useQuery.mockReturnValue([
    {
      data: {
        historyDownloadSettings: {
          mode: 'ALWAYS',
          version: 0,
          nonTradingDaysAllowed: true,
          windows: [],
        },
      },
    },
    mocks.refresh,
  ]);
  mocks.useMutation.mockReturnValue([{ fetching: false }, mocks.save]);
});

it('saves multiple windows without GraphQL metadata and can return to all-day mode', async () => {
  const user = userEvent.setup();
  mocks.save.mockResolvedValueOnce({
    data: {
      updateHistoryDownloadSettings: {
        mode: 'CUSTOM',
        version: 1,
        nonTradingDaysAllowed: true,
        windows: [
          {
            __typename: 'HistoryDownloadTimeWindow',
            start: '11:40',
            end: '13:00',
          },
          { start: '16:00', end: '08:30' },
        ],
      },
    },
  });
  render(<HistoryDownloadSettingsPanel />);
  await user.click(screen.getByLabelText('自定义允许时段'));
  expect(screen.getByText('至次日')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('时段 1 开始'), {
    target: { value: '11:40' },
  });
  await user.click(screen.getByRole('button', { name: '保存设置' }));
  await waitFor(() =>
    expect(mocks.save).toHaveBeenCalledWith({
      input: {
        expectedVersion: 0,
        mode: 'CUSTOM',
        nonTradingDaysAllowed: true,
        windows: [
          { start: '11:40', end: '13:00' },
          { start: '16:00', end: '08:30' },
        ],
      },
    })
  );
  await screen.findByText('已保存，下次补采检查即生效。');
  mocks.save.mockResolvedValueOnce({
    data: {
      updateHistoryDownloadSettings: {
        mode: 'ALWAYS',
        version: 2,
        nonTradingDaysAllowed: true,
        windows: [],
      },
    },
  });
  await user.click(screen.getByLabelText('全天允许（默认，盘中也可补采）'));
  await user.click(screen.getByRole('button', { name: '保存设置' }));
  expect(mocks.save).toHaveBeenLastCalledWith({
    input: {
      expectedVersion: 1,
      mode: 'ALWAYS',
      nonTradingDaysAllowed: true,
      windows: [],
    },
  });
});

it('prevents a read-only user from editing', () => {
  mocks.permissions = [];
  render(<HistoryDownloadSettingsPanel />);
  expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled();
  expect(screen.getByLabelText('自定义允许时段')).toBeDisabled();
});
