import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import { vi } from 'vitest';

import type * as StudioWorkbenchModule from '@/components/studio-workbench';
import {
  StudioWorkspace,
  useStudioWorkspaceContext,
} from '@/components/studio-workspace';

const studioWorkbenchMocks = vi.hoisted(() => ({
  onSelect: vi.fn(),
}));

vi.mock('@/components/studio-workbench', async () => {
  const actual = await vi.importActual<typeof StudioWorkbenchModule>(
    '@/components/studio-workbench'
  );

  return {
    ...actual,
    useStudioGlobalActions: () => {
      const Icon = () => null;
      const action = (id: string, label: string) => ({
        active: id === 'nav:/t-trade',
        icon: Icon,
        id,
        label,
        onSelect: () => studioWorkbenchMocks.onSelect(id),
      });
      return {
        currentUserLabel: 'QuantX 开发用户',
        globalActions: [
          action('nav:/', '行情'),
          action('nav:/holdings', '持仓'),
          action('nav:/entry-plans', '买入管理'),
          action('nav:/t-trade', '做T助手'),
          action('nav:/limit-up-board', '打板助手'),
          action('nav:/liquidation', '卖出管理'),
          action('nav:/strategies', '策略管理'),
          action('nav:/research', '研究中心'),
          action('nav:/screening', '股票筛选'),
        ],
        utilityActions: [
          action('utility:assets', '账户'),
          action('utility:notifications', '通知'),
          action('nav:/settings', '系统设置'),
        ],
      };
    },
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
      themeName: 'blue',
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
    studioWorkbenchMocks.onSelect.mockClear();
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
    const chrome = screen.getByTestId('studio-chrome-header');
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
    expect(chrome).toContainElement(tabBar);
    expect(chrome).toHaveClass('studio-shell-header');
    expect(chrome).not.toHaveClass('border-b');
    expect(chrome).toHaveStyle({ background: '#040b15' });
    expect(chrome.style.boxShadow).toBe('');
    expect(tabBar).toHaveAttribute('data-variant', 'workspace');
    const workspaceTabList = screen.getByRole('tablist', {
      name: '工作区标签',
    });
    expect(
      screen.getByRole('button', {
        name: 'QuantX Studio · 打开行情工作台',
      })
    ).not.toHaveClass('border-r');
    expect(screen.getByTestId('studio-brand-logo')).toHaveAttribute(
      'viewBox',
      '0 0 32 32'
    );
    expect(
      screen.queryByRole('navigation', { name: '固定工作区' })
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开功能启动器' })).toHaveClass(
      'focus-visible:ring-blue-400/70'
    );
    const fixedHomeTab = within(workspaceTabList).getByRole('tab', {
      name: '工作台',
    });
    expect(within(workspaceTabList).getAllByRole('tab')[0]).toBe(fixedHomeTab);
    expect(fixedHomeTab).toHaveAttribute('aria-selected', 'true');
    expect(fixedHomeTab.parentElement).toHaveClass('border-b-0');
    expect(fixedHomeTab.parentElement).toHaveStyle({
      background: '#07111f',
      borderColor: '#22364d',
      borderTopLeftRadius: '8px',
      borderTopRightRadius: '8px',
      height: '44px',
      zIndex: 10,
    });
    expect(
      screen.queryByRole('button', { name: '关闭 行情工作台' })
    ).not.toBeInTheDocument();
    const activityBar = screen.getByTestId('studio-activity-bar');
    expect(activityBar).toHaveAttribute('data-variant', 'studio');
    expect(activityBar).toHaveStyle({ background: '#040b15' });
    expect(activityBar).not.toHaveClass('border-t');
    expect(activityBar).not.toHaveClass('border-r');
    expect(activityBar.style.boxShadow).toBe('');
    expect(main).toHaveClass('overflow-hidden');
    expect(main).toHaveStyle({
      borderLeft: '1px solid #22364d',
      borderTop: '1px solid #22364d',
      borderTopLeftRadius: '12px',
    });
    expect(main.style.boxShadow).toBe('');
    expect(main).toHaveStyle({ background: '#07111f' });
    const primaryNavigation = screen.getByTestId('studio-primary-navigation');
    const railButtons = within(primaryNavigation).getAllByTestId(
      'studio-action-button'
    );
    expect(
      railButtons.map(button => button.getAttribute('data-studio-action-id'))
    ).toEqual([
      'rail:/holdings',
      'rail:/entry-plans',
      'rail:/t-trade',
      'rail:/liquidation',
      'rail:/strategies',
      'rail:/research',
      'rail:/settings/data',
    ]);
    expect(railButtons.map(button => button.textContent)).toEqual([
      '持仓',
      '买入',
      '做 T',
      '卖出',
      '策略',
      '研究',
      '数据',
    ]);
    expect(
      railButtons.filter(
        button => button.getAttribute('aria-pressed') === 'true'
      )
    ).toEqual([railButtons[2]]);
    ['回测与研究运行', '账户', '通知', '系统设置'].forEach(label =>
      expect(
        within(activityBar).queryByRole('button', { name: label })
      ).not.toBeInTheDocument()
    );
    expect(screen.getByRole('button', { name: '查看通知' })).toBeVisible();
    expect(screen.getByRole('button', { name: '打开系统设置' })).toBeVisible();
    expect(
      screen.getByRole('button', {
        name: '打开账户：QuantX 开发用户',
      })
    ).toBeVisible();
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

  it('opens each high-frequency destination and keeps low-frequency tools in the launcher', async () => {
    const user = userEvent.setup();

    render(
      <StudioWorkspace>
        <main>Workspace page content</main>
      </StudioWorkspace>
    );

    const primaryNavigation = screen.getByTestId('studio-primary-navigation');
    for (const label of [
      '持仓',
      '买入管理',
      '做T助手',
      '卖出管理',
      '策略管理',
      '研究中心',
    ]) {
      await user.click(
        within(primaryNavigation).getByRole('button', { name: label })
      );
    }
    expect(studioWorkbenchMocks.onSelect.mock.calls).toEqual([
      ['nav:/holdings'],
      ['nav:/entry-plans'],
      ['nav:/t-trade'],
      ['nav:/liquidation'],
      ['nav:/strategies'],
      ['nav:/research'],
    ]);

    await user.click(
      within(primaryNavigation).getByRole('button', { name: '数据管理' })
    );
    expect(
      await screen.findByRole('tab', { name: '数据管理门户' })
    ).toBeVisible();

    await user.click(screen.getByRole('button', { name: '打开功能启动器' }));
    expect(screen.getByRole('menuitem', { name: '打板助手' })).toBeVisible();
    expect(screen.getByRole('menuitem', { name: '股票筛选' })).toBeVisible();
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

  it('keeps the home tab first and non-closable while restoring regular tabs', () => {
    vi.mocked(window.localStorage.getItem).mockImplementation(key => {
      if (key === 'quantx-studio-workspace-tabs') {
        return JSON.stringify([
          { id: 'page:/screening', isPreview: false, path: '/screening' },
        ]);
      }

      return null;
    });

    render(
      <StudioWorkspace>
        <main>Workspace page content</main>
      </StudioWorkspace>
    );

    const workspaceTabList = screen.getByRole('tablist', {
      name: '工作区标签',
    });
    expect(
      within(workspaceTabList)
        .getAllByRole('tab')
        .map(tab => tab.textContent)
    ).toEqual(['工作台', '自选股']);
    expect(
      screen.queryByRole('button', { name: '关闭 行情工作台' })
    ).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '自选股' })).toBeVisible();
    expect(screen.getByRole('button', { name: '关闭 自选股' })).toBeVisible();
  });

  it('opens the AI assistant from the launcher and restores focus on close', async () => {
    const user = userEvent.setup();

    render(
      <StudioWorkspace>
        <main>Workspace page content</main>
      </StudioWorkspace>
    );

    const launcherTrigger = screen.getByRole('button', {
      name: '打开功能启动器',
    });
    expect(
      screen.queryByTestId('studio-assistant-tool-rail')
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('studio-assistant-panel')
    ).not.toBeInTheDocument();

    await user.click(launcherTrigger);
    await user.click(screen.getByRole('menuitem', { name: 'AI 助手' }));

    const panel = await screen.findByTestId('studio-assistant-panel');
    expect(
      await screen.findByTestId('mock-assistant-drawer')
    ).toBeInTheDocument();
    expect(panel).toHaveClass('right-0', '2xl:relative');

    await user.click(screen.getByRole('textbox', { name: 'AI 助手输入框' }));
    await user.keyboard('{Escape}');

    expect(
      screen.queryByTestId('studio-assistant-panel')
    ).not.toBeInTheDocument();
    expect(launcherTrigger).toHaveFocus();

    await user.click(launcherTrigger);
    await user.click(screen.getByRole('menuitem', { name: 'AI 助手' }));
    await user.click(
      await screen.findByRole('button', { name: '关闭 AI 助手' })
    );

    expect(
      screen.queryByTestId('studio-assistant-panel')
    ).not.toBeInTheDocument();
    expect(launcherTrigger).toHaveFocus();
  });

  it('resizes and persists the AI assistant panel from its left edge', async () => {
    const user = userEvent.setup();

    render(
      <StudioWorkspace>
        <main>Workspace page content</main>
      </StudioWorkspace>
    );

    await user.click(screen.getByRole('button', { name: '打开功能启动器' }));
    await user.click(screen.getByRole('menuitem', { name: 'AI 助手' }));

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
