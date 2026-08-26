import { render, screen } from '@testing-library/react';
import { Bell, LayoutDashboard, Settings, Wallet } from 'lucide-react';
import { vi } from 'vitest';

import { StudioWorkbench } from '@/components/studio-workbench';
import { StudioWorkspaceContext } from '@/components/studio-workspace';

describe('StudioWorkbench', () => {
  it('renders only the local frame when hosted by the studio workspace', () => {
    const clearWorkspaceSidebar = vi.fn();
    const setWorkspaceSidebar = vi.fn();

    const { unmount } = render(
      <StudioWorkspaceContext.Provider
        value={{
          activeTabId: 'page:/hosted',
          clearWorkspaceSidebar,
          isWorkspaceHosted: true,
          openAssistant: vi.fn(),
          openStudioTab: vi.fn(),
          setWorkspaceSidebar,
          tabBar: <div data-testid="workspace-tabbar">Workspace tabs</div>,
          updateActiveTab: vi.fn(),
        }}
      >
        <StudioWorkbench
          activeMode="LOCAL"
          content={<div>Hosted content</div>}
          globalActions={[
            {
              icon: LayoutDashboard,
              id: 'nav:/',
              label: '仪表板',
              onSelect: vi.fn(),
            },
          ]}
          isPage
          modes={[
            {
              icon: LayoutDashboard,
              id: 'LOCAL',
              label: 'Local mode',
            },
          ]}
          onModeChange={vi.fn()}
          sidebar={<aside>Local sidebar</aside>}
          statusBarLeft={<span>Page specific left</span>}
          statusBarRight={<span>Page specific right</span>}
          tabBar={<div data-testid="local-tabbar">Local tabs</div>}
          theme={{
            icon: Settings,
            name: 'red',
            title: 'Local Studio',
          }}
          utilityActions={[
            {
              icon: Wallet,
              id: 'utility:assets',
              label: '总资产',
              onSelect: vi.fn(),
            },
            {
              icon: Bell,
              id: 'utility:notifications',
              label: '通知',
              onSelect: vi.fn(),
            },
          ]}
        />
      </StudioWorkspaceContext.Provider>
    );

    const localFrame = screen.getByTestId('studio-local-frame');
    const contentSurface = screen.getByTestId(
      'studio-workbench-content-surface'
    );

    expect(localFrame).toBeInTheDocument();
    expect(contentSurface).toHaveClass('studio-workspace-surface');
    expect(localFrame).toContainElement(contentSurface);
    expect(contentSurface).toContainElement(screen.getByText('Hosted content'));
    expect(screen.queryByText('Local sidebar')).not.toBeInTheDocument();
    expect(screen.getByText('Hosted content')).toBeInTheDocument();
    expect(screen.queryByTestId('studio-activity-bar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('studio-service-logo')).not.toBeInTheDocument();
    expect(screen.queryByTestId('studio-status-bar')).not.toBeInTheDocument();
    expect(screen.queryByText('Page specific left')).not.toBeInTheDocument();
    expect(screen.queryByText('Page specific right')).not.toBeInTheDocument();
    expect(screen.queryByTestId('workspace-tabbar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('local-tabbar')).not.toBeInTheDocument();
    expect(setWorkspaceSidebar).toHaveBeenCalledWith(
      expect.objectContaining({
        content: expect.any(Object),
        ownerId: expect.any(String),
        showSidebar: true,
        themeName: 'red',
        title: 'Local Studio',
      })
    );

    const ownerId = setWorkspaceSidebar.mock.calls[0][0].ownerId;
    unmount();

    expect(clearWorkspaceSidebar).toHaveBeenCalledWith(ownerId);
  });
});
