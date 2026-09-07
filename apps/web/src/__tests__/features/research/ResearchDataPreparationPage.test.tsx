import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { ResearchDataPreparationPage } from '@/features/system/pages/ResearchDataPreparationPage';

const mocks = vi.hoisted(() => ({
  config: {
    date_start: '2021-07-29',
    date_end: '2025-07-29',
    stock_codes: ['600000.SH'],
    benchmark_code: '000300.SH',
    minimum_listing_days: 252,
    st_file: null,
    industry_file: null,
    delisting_file: null,
  },
  mutate: vi.fn(),
  refresh: vi.fn(),
  query: vi.fn(),
}));
vi.mock('urql', () => ({
  useQuery: () => [
    {
      data: {
        researchPreparation: {
          config: mocks.config,
          evidenceFiles: ['history.csv'],
          jobs: [],
        },
      },
    },
    mocks.refresh,
  ],
  useMutation: () => [{}, mocks.mutate],
  useClient: () => ({ query: mocks.query }),
}));
vi.mock('@/features/research/hooks', () => ({
  useStockSelectionDatasetVersions: () => ({
    data: [],
    refresh: mocks.refresh,
  }),
  useStockSelectionTrainingCapabilities: () => ({
    data: {
      cpuAvailable: true,
      fresh: true,
      gpuStatus: 'GPU_UNAVAILABLE_BUILD',
    },
    refresh: mocks.refresh,
  }),
}));
vi.mock('@/features/system/components/DataStudioPageFrame', () => ({
  DataStudioPageFrame: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}));
beforeEach(() => {
  vi.clearAllMocks();
  mocks.mutate.mockResolvedValue({ data: {} });
  mocks.query.mockReturnValue({
    toPromise: async () => ({
      data: {
        previewResearchDownload: {
          start: '2020-03-12',
          end: '2025-07-30',
          warmup_days: 504,
          label_available: true,
        },
      },
    }),
  });
});
afterEach(cleanup);

it('does not offer a download from a preview of an edited configuration', async () => {
  let complete: (value: unknown) => void = () => {};
  const response = new Promise(resolve => {
    complete = resolve;
  });
  mocks.query.mockReturnValue({ toPromise: () => response });
  const user = userEvent.setup();
  render(<ResearchDataPreparationPage />);
  await user.click(screen.getByRole('button', { name: '预览下载范围' }));
  await user.clear(screen.getByLabelText('基准代码'));
  await user.type(screen.getByLabelText('基准代码'), '000905.SH');
  await act(async () => {
    complete({
      data: {
        previewResearchDownload: {
          start: '2020-03-12',
          end: '2025-07-30',
          warmup_days: 504,
          label_available: true,
        },
      },
    });
    await response;
  });
  expect(screen.queryByRole('button', { name: '提交行情下载' })).toBeNull();
  expect(mocks.mutate).not.toHaveBeenCalled();
});

it('does not download, certify or train when the page opens', () => {
  render(<ResearchDataPreparationPage />);
  expect(screen.getByRole('heading', { name: '研究训练数据' })).toBeVisible();
  expect(screen.getByRole('button', { name: '检查数据覆盖' })).toBeEnabled();
  expect(
    screen.getByRole('button', { name: '生成并认证数据集' })
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: '运行 GPU 资格验证' })
  ).toBeDisabled();
  expect(mocks.mutate).not.toHaveBeenCalled();
});

it('requires saving edited settings before submitting jobs', async () => {
  const user = userEvent.setup();
  render(<ResearchDataPreparationPage />);
  await user.clear(screen.getByLabelText('基准代码'));
  await user.type(screen.getByLabelText('基准代码'), '000905.SH');
  expect(screen.getByRole('button', { name: '检查数据覆盖' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: '保存配置' }));
  await waitFor(() => expect(mocks.mutate).toHaveBeenCalledOnce());
  expect(mocks.mutate.mock.calls[0][0].config.benchmark_code).toBe('000905.SH');
  expect(mocks.mutate.mock.calls[0][0].kind).toBeUndefined();
});

it('previews the expanded range before explicit download submission', async () => {
  const user = userEvent.setup();
  render(<ResearchDataPreparationPage />);
  expect(screen.queryByRole('button', { name: '提交行情下载' })).toBeNull();
  await user.click(screen.getByRole('button', { name: '预览下载范围' }));
  expect(await screen.findByText(/2020-03-12.*2025-07-30/)).toBeVisible();
  expect(mocks.mutate).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '提交行情下载' }));
  await waitFor(() => expect(mocks.mutate).toHaveBeenCalledOnce());
  expect(mocks.mutate.mock.calls[0][0].kind).toBe('DOWNLOAD');
});
