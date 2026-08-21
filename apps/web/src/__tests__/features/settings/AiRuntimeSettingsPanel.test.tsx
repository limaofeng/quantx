import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppDialogProvider } from '@/components/ui/app-dialog-provider';
import { AiRuntimeSettingsPanel } from '@/features/settings/components/AiRuntimeSettingsPanel';

const mocks = vi.hoisted(() => ({
  permissions: [] as string[],
  refresh: vi.fn(),
  toast: vi.fn(),
  update: vi.fn(),
  useMutation: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock('urql', () => ({
  useMutation: mocks.useMutation,
  useQuery: mocks.useQuery,
}));

vi.mock('@/core/auth', () => ({
  useAuth: () => ({
    user: {
      id: 'user-1',
      username: 'admin',
      displayName: 'Admin',
      permissions: mocks.permissions,
      authorizedAccountIds: [],
    },
  }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

function runtimeSettings(overrides: Record<string, unknown> = {}) {
  return {
    version: 4,
    source: 'DATABASE_OVERRIDE',
    enabled: true,
    apiKeyConfigured: true,
    model: 'gpt-5.6',
    maxConcurrentRuns: 2,
    maxTurns: 12,
    maxToolCalls: 8,
    runTimeoutSeconds: 300,
    tracingEnabled: false,
    leaseSeconds: 60,
    runtimeStatus: 'READY',
    appliedVersion: 4,
    applyState: 'APPLIED',
    updatedAt: '2026-08-14T08:00:00Z',
    ...overrides,
  };
}

function arrange(overrides: Record<string, unknown> = {}) {
  mocks.useQuery.mockReturnValue([
    {
      data: { aiRuntimeSettings: runtimeSettings(overrides) },
      error: undefined,
      fetching: false,
    },
    mocks.refresh,
  ]);
  mocks.useMutation.mockReturnValue([
    { error: undefined, fetching: false },
    mocks.update,
  ]);
}

function renderPanel() {
  return render(
    <AppDialogProvider>
      <AiRuntimeSettingsPanel />
    </AppDialogProvider>
  );
}

describe('AiRuntimeSettingsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.permissions = [];
    mocks.update.mockResolvedValue({
      data: { updateAiRuntimeSettings: runtimeSettings({ version: 5 }) },
      error: undefined,
    });
    arrange();
  });

  it('makes every editable control read-only without system-config:write', () => {
    renderPanel();

    expect(screen.getByText('缺少 system-config:write')).toBeInTheDocument();
    expect(screen.getByLabelText('OpenAI 模型')).toBeDisabled();
    expect(screen.getByLabelText('最大并发')).toBeDisabled();
    expect(
      screen.getByRole('switch', { name: '接受新的 AI 任务' })
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
  });

  it('shows the offline pending state without discarding saved values', () => {
    arrange({
      appliedVersion: null,
      applyState: 'OFFLINE',
      runtimeStatus: 'OFFLINE',
    });

    renderPanel();

    expect(screen.getByText('Runtime 离线')).toBeInTheDocument();
    expect(screen.getByText('等待 Runtime 上线')).toBeInTheDocument();
    expect(screen.getByLabelText('OpenAI 模型')).toHaveValue('gpt-5.6');
  });

  it('validates bounds before submitting', async () => {
    mocks.permissions = ['system-config:write'];
    const user = userEvent.setup();
    renderPanel();

    const concurrency = screen.getByLabelText('最大并发');
    await user.clear(concurrency);
    await user.type(concurrency, '17');

    expect(screen.getByText('最大并发必须为 1 至 16。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it('confirms disable and submits the expected version', async () => {
    mocks.permissions = ['system-config:write'];
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole('switch', { name: '接受新的 AI 任务' }));
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    expect(
      screen.getByRole('alertdialog', { name: '停用 AI Assistant' })
    ).toBeInTheDocument();
    expect(mocks.update).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: '确认停用' }));

    expect(mocks.update).toHaveBeenCalledWith({
      input: {
        expectedVersion: 4,
        enabled: false,
        model: 'gpt-5.6',
        maxConcurrentRuns: 2,
        maxTurns: 12,
        maxToolCalls: 8,
        runTimeoutSeconds: 300,
      },
    });
    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'AI Runtime 配置已保存' })
      );
    });
  });
});
