import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';

import { AssistantDrawer } from '@/features/ai-assistant/components/AssistantDrawer';

const mocks = vi.hoisted(() => ({
  confirm: vi.fn(),
  useAiAssistant: vi.fn(),
}));

vi.mock('@/components/ui/app-dialog-context', () => ({
  useAppDialog: () => ({ confirm: mocks.confirm }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

vi.mock('@/features/ai-assistant/hooks/useAiAssistant', () => ({
  useAiAssistant: mocks.useAiAssistant,
}));

describe('AssistantDrawer', () => {
  beforeEach(() => {
    mocks.useAiAssistant.mockReturnValue({
      activeRun: null,
      cancelRun: vi.fn(),
      capabilities: {
        enabled: true,
        externalSearchAvailable: false,
        maxMessageLength: 12_000,
        model: 'gpt-5.6',
        runtimeStatus: 'ready',
      },
      createThread: vi.fn(),
      currentAccountId: null,
      deleteThread: vi.fn(),
      fetching: false,
      messages: [],
      pendingApprovals: [],
      resolveApproval: vi.fn(),
      retryRun: vi.fn(),
      selectedThread: null,
      selectedThreadId: null,
      sendMessage: vi.fn(),
      setExternalSearch: vi.fn(),
      setSelectedThreadId: vi.fn(),
      streamingText: '',
      threads: [],
    });
  });

  it('applies each page-provided draft to the canonical assistant composer', () => {
    const { rerender } = render(
      <AssistantDrawer
        currentPath="/strategies/run"
        draftRequest={{ id: 1, text: '审计第一份网格参数' }}
        onClose={vi.fn()}
      />
    );

    expect(
      screen.getByPlaceholderText('询问 QuantX 数据或研究任务…')
    ).toHaveValue('审计第一份网格参数');

    rerender(
      <AssistantDrawer
        currentPath="/strategies/run"
        draftRequest={{ id: 2, text: '审计第二份网格参数' }}
        onClose={vi.fn()}
      />
    );

    expect(
      screen.getByPlaceholderText('询问 QuantX 数据或研究任务…')
    ).toHaveValue('审计第二份网格参数');
  });
});
