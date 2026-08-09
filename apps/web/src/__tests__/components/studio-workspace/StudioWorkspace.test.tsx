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

  it('renders the registered page sidebar as a left-side workspace dock', () => {
    render(
      <StudioWorkspace>
        <RegisteredSidebarPage />
      </StudioWorkspace>
    );

    const main = screen.getByTestId('studio-workspace-main');
    const dock = screen.getByTestId('studio-sidebar-dock');
    const resizer = screen.getByTestId('studio-sidebar-resizer');

    expect(screen.getByText('Workspace page content')).toBeInTheDocument();
    expect(screen.getByText('Registered sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('studio-current-user')).toHaveTextContent(
      'QuantX 开发用户'
    );
    expect(screen.getAllByTestId('studio-sidebar-dock')).toHaveLength(1);
    expect(dock).toHaveStyle({ width: '360px' });
    expect(resizer).toHaveAttribute('aria-valuenow', '360');
    expect(main).not.toContainElement(dock);
    expect(
      dock.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
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
