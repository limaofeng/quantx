import { render, screen } from '@testing-library/react';
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
            'studio-workspace-sidebar': 360,
          },
        });
      }

      return null;
    });
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
});
