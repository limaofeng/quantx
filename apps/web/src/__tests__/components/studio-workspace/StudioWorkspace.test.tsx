import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import { vi } from 'vitest';

import type * as StudioWorkbenchModule from '@/components/studio-workbench';
import {
  StudioWorkspace,
  useStudioWorkspaceContext,
} from '@/components/studio-workspace';

vi.mock('@/components/studio-workbench', async () => {
  const actual = await vi.importActual<typeof StudioWorkbenchModule>(
    '@/components/studio-workbench'
  );

  return {
    ...actual,
    useStudioGlobalActions: () => ({
      currentUserLabel: 'QuantX 开发用户',
      globalActions: [],
      utilityActions: [],
    }),
  };
});

vi.mock('@/features/ai-assistant', () => ({
  AssistantDrawer: ({ onClose }: { onClose: () => void }) => (
    <aside aria-label="AI 助手" data-testid="mock-assistant-drawer">
      <input aria-label="AI 助手输入框" />
      <button type="button" onClick={onClose}>
        关闭 AI 助手
      </button>
    </aside>
  ),
}));

function RegisteredSidebarPage() {
  const workspace = useStudioWorkspaceContext();

  useEffect(() => {
    workspace?.setWorkspaceSidebar({
      content: <aside>Registered sidebar</aside>,
      ownerId: 'test-sidebar',
      showSidebar: true,
      sizing: {
        defaultWidth: 304,
        maxWidth: 420,
        minWidth: 248,
        storageScope: 'page-specific-sidebar',
      },
      themeName: 'red',
      title: 'Test Studio',
    });

    return () => {
      workspace?.clearWorkspaceSidebar('test-sidebar');
    };
  }, [workspace]);

  return <main>Workspace page content</main>;
}

describe('StudioWorkspace', () => {
  beforeEach(() => {
    vi.mocked(window.localStorage.getItem).mockImplementation(key => {
      if (key === 'quantx-studio-workbench') {
        return JSON.stringify({
          sidebarWidths: {
            'page-specific-sidebar': 280,
            'studio-ai-assistant-panel': 400,
            'studio-workspace-sidebar': 360,
          },
        });
      }

      return null;
    });
    vi.mocked(window.localStorage.setItem).mockClear();
  });

  it('renders the registered page sidebar inside the active tab content', () => {
    render(
      <StudioWorkspace>
        <RegisteredSidebarPage />
      </StudioWorkspace>
    );

    const main = screen.getByTestId('studio-workspace-main');
    const content = screen.getByTestId('studio-workspace-content');
    const dock = screen.getByTestId('studio-sidebar-dock');
    const resizer = screen.getByTestId('studio-sidebar-resizer');
    const tabBar = screen.getByTestId('studio-tab-bar');
    const pageContent = screen.getByText('Workspace page content');

    expect(screen.getByText('Workspace page content')).toBeInTheDocument();
    expect(screen.getByText('Registered sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('studio-current-user')).toHaveTextContent(
      'QuantX 开发用户'
    );
    expect(screen.getAllByTestId('studio-sidebar-dock')).toHaveLength(1);
    expect(dock).toHaveStyle({ width: '280px' });
    expect(resizer).toHaveAttribute('aria-valuemin', '248');
    expect(resizer).toHaveAttribute('aria-valuemax', '420');
    expect(resizer).toHaveAttribute('aria-valuenow', '280');
    expect(screen.getByTestId('studio-sidebar-content')).toHaveClass(
      'min-h-0',
      'min-w-0',
      'overflow-hidden'
    );
    expect(main).toContainElement(dock);
    expect(content).toContainElement(dock);
    expect(content).toContainElement(pageContent);
    expect(
      tabBar.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    expect(
      dock.compareDocumentPosition(pageContent) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();

    expect(
      screen.queryByRole('button', { name: '打开Test Studio侧边栏' })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '关闭Test Studio侧边栏' })
    ).not.toBeInTheDocument();
  });

  it('renders a supplied global status bar in the workspace bottom slot', () => {
    render(
      <StudioWorkspace
        renderStatusBar={currentUserLabel => (
          <div data-testid="custom-global-status">{currentUserLabel}</div>
        )}
      >
        <main>Workspace page content</main>
      </StudioWorkspace>
    );

    expect(screen.getByTestId('custom-global-status')).toHaveTextContent(
      'QuantX 开发用户'
    );
    expect(screen.queryByTestId('studio-status-bar')).not.toBeInTheDocument();
  });

  it('opens the AI assistant from the right tool rail and restores focus on close', async () => {
    const user = userEvent.setup();

    render(
      <StudioWorkspace>
        <main>Workspace page content</main>
      </StudioWorkspace>
    );

    const activityBar = screen.getByTestId('studio-activity-bar');
    const toolRail = screen.getByTestId('studio-assistant-tool-rail');
    const trigger = screen.getByRole('button', { name: 'AI 助手' });

    expect(activityBar).not.toContainElement(trigger);
    expect(toolRail).toContainElement(trigger);
    expect(trigger).toHaveAttribute(
      'aria-controls',
      'studio-ai-assistant-panel'
    );
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveAttribute('aria-pressed', 'false');
    expect(
      screen.queryByTestId('studio-assistant-panel')
    ).not.toBeInTheDocument();

    await user.click(trigger);

    const panel = await screen.findByTestId('studio-assistant-panel');
    expect(
      await screen.findByTestId('mock-assistant-drawer')
    ).toBeInTheDocument();
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(trigger).toHaveAttribute('aria-pressed', 'true');
    expect(panel).toHaveClass('right-10', '2xl:relative');
    expect(
      panel.compareDocumentPosition(toolRail) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();

    await user.click(screen.getByRole('textbox', { name: 'AI 助手输入框' }));
    await user.keyboard('{Escape}');

    expect(
      screen.queryByTestId('studio-assistant-panel')
    ).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.click(
      await screen.findByRole('button', { name: '关闭 AI 助手' })
    );

    expect(
      screen.queryByTestId('studio-assistant-panel')
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('resizes and persists the AI assistant panel from its left edge', async () => {
    const user = userEvent.setup();

    render(
      <StudioWorkspace>
        <main>Workspace page content</main>
      </StudioWorkspace>
    );

    await user.click(screen.getByRole('button', { name: 'AI 助手' }));

    const panel = await screen.findByTestId('studio-assistant-panel');
    const resizer = screen.getByRole('separator', {
      name: 'AI 助手面板宽度',
    });

    expect(panel).toHaveStyle({ width: '400px' });
    expect(resizer).toHaveAttribute('aria-valuemin', '360');
    expect(resizer).toHaveAttribute('aria-valuemax', '560');
    expect(resizer).toHaveAttribute('aria-valuenow', '400');

    fireEvent.keyDown(resizer, { key: 'ArrowLeft' });
    expect(resizer).toHaveAttribute('aria-valuenow', '410');

    fireEvent.keyDown(resizer, { key: 'ArrowRight' });
    expect(resizer).toHaveAttribute('aria-valuenow', '400');

    fireEvent.keyDown(resizer, { key: 'Home' });
    expect(resizer).toHaveAttribute('aria-valuenow', '360');

    fireEvent.keyDown(resizer, { key: 'End' });
    expect(resizer).toHaveAttribute('aria-valuenow', '560');

    fireEvent.keyDown(resizer, { key: 'Home' });
    fireEvent.pointerDown(resizer, { clientX: 500 });
    fireEvent.pointerMove(window, { clientX: 450 });
    fireEvent.pointerUp(window);
    expect(resizer).toHaveAttribute('aria-valuenow', '410');
    expect(panel).toHaveStyle({ width: '410px' });

    const widthWrites = vi
      .mocked(window.localStorage.setItem)
      .mock.calls.filter(([key]) => key === 'quantx-studio-workbench');
    const persisted = JSON.parse(widthWrites.at(-1)?.[1] || '{}') as {
      sidebarWidths?: Record<string, number>;
    };
    expect(persisted.sidebarWidths?.['studio-ai-assistant-panel']).toBe(410);
  });
});
