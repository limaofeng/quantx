import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  TrainingPreviewSummary,
} from '@/features/research/components/StockSelectionTrainingWorkbench';
import { createPendingIdempotencyKeys } from '@/features/research/idempotency';

describe('StockSelectionTrainingWorkbench preview contract', () => {
  it('announces a blocked preview and keeps the submit state disabled', () => {
    render(
      <TrainingPreviewSummary
        blockers={['GPU_REQUIRED 当前不可用']}
        warnings={['资源估算需要重新确认']}
        resolvedBackend="CPU"
        canSubmit={false}
      />
    );

    expect(screen.getByRole('alert')).toHaveTextContent('提交已禁用');
    expect(screen.getByRole('alert')).toHaveTextContent('GPU_REQUIRED 当前不可用');
    expect(screen.getByLabelText('存在预检阻塞')).toBeInTheDocument();
    expect(screen.getByText('CPU · 已锁定')).toBeInTheDocument();
    expect(screen.getByText('资源估算需要重新确认')).toBeInTheDocument();
    expect(screen.getByRole('alert').parentElement).toHaveAttribute('aria-live', 'polite');
  });

  it('announces the clear state when all preview blockers are gone', () => {
    render(
      <TrainingPreviewSummary
        blockers={[]}
        warnings={[]}
        resolvedBackend="LIGHTGBM_OPENCL_GPU"
        canSubmit
      />
    );

    expect(screen.getByLabelText('预检通过')).toBeInTheDocument();
    expect(screen.getByText('LIGHTGBM_OPENCL_GPU · 已锁定')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('reuses a pending idempotency key across a failed retry and clears it after success', () => {
    const keys = createPendingIdempotencyKeys();
    const first = keys.get('development:preview-1');
    expect(keys.get('development:preview-1')).toBe(first);
    keys.clear('development:preview-1');
    expect(keys.get('development:preview-1')).not.toBe(first);
  });

  it('renders the complete typed preview evidence without turning missing quality into zero', () => {
    render(
      <TrainingPreviewSummary
        blockers={[]}
        warnings={['AUTO 已解析为 CPU']}
        resolvedBackend="CPU"
        canSubmit
        preview={{
          datasetVersion: 'dataset-v2',
          requestedBackend: 'AUTO',
          resolvedBackend: 'CPU',
          canSubmit: true,
          folds: [],
          coverage: {},
          leakage: {},
          resourceEstimate: { memoryMib: 256, diskMib: 64, gpuMemoryMib: 128, estimatedMinutes: 1, durationLevel: 'LOW' },
          shadowReasons: [],
          blockers: [],
          warnings: [],
        }}
      />
    );

    expect(screen.getByTestId('training-preview-evidence')).toHaveTextContent('正样本比例');
    expect(screen.getByTestId('training-preview-evidence')).toHaveTextContent('不可用');
    expect(screen.getByTestId('training-preview-evidence')).toHaveTextContent('内存 / 显存 / 磁盘');
    expect(screen.getByTestId('training-preview-evidence')).toHaveTextContent('AUTO / CPU');
  });
});
