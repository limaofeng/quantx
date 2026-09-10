import { fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { Router } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';

import App from '@/App';
import type * as AuthModule from '@/core/auth';
import type { AuthContextValue } from '@/core/auth/auth-context';
import { UI_DENSITY_STORAGE_KEY } from '@/core/ui-density';
import type * as MonitorApi from '@/features/system/monitor-api';

// Only isolate service-backed dependencies. App, its density provider, the
// workspace, lazy routes and settings navigation all use their real wiring.
vi.mock('@/core/auth', async importOriginal => {
  const actual = await importOriginal<typeof AuthModule>();
  return {
    ...actual,
    AuthProvider: ({ children }: { children: ReactNode }) => children,
    useAuth: (): AuthContextValue => ({
      bootstrapStatus: 'ready',
      bootstrapError: null,
      isAuthenticated: true,
      user: {
        id: 'appearance-test',
        username: 'appearance-test',
        displayName: '界面测试',
        permissions: [],
        authorizedAccountIds: ['test'],
      },
      login: vi.fn(),
      logout: vi.fn(),
      retryBootstrap: vi.fn(),
    }),
  };
});

vi.mock('@/hooks/useWatchlist', () => ({ useWatchlist: vi.fn() }));
vi.mock('@/components/studio-workbench/useStudioGlobalActions', () => ({
  useStudioGlobalActions: () => ({
    currentUserLabel: '界面测试',
    globalActions: [],
    utilityActions: [],
  }),
}));
vi.mock('@/features/trading-safety/TradingSafetyProvider', () => ({
  TradingSafetyProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('@/features/system/monitor-api', async importOriginal => ({
  ...(await importOriginal<typeof MonitorApi>()),
  getMonitorSummary: vi.fn().mockRejectedValue(new Error('Offline UI test')),
}));

describe('App appearance settings integration', () => {
  let saved: Map<string, string>;

  beforeEach(() => {
    saved = new Map();
    vi.mocked(localStorage.getItem).mockImplementation(
      key => saved.get(key) ?? null
    );
    vi.mocked(localStorage.setItem).mockImplementation((key, value) => {
      saved.set(key, value);
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('Unexpected network request'))
    );
    document.documentElement.removeAttribute('data-ui-density');
  });

  afterEach(() => {
    document.documentElement.removeAttribute('data-ui-density');
    document.documentElement.classList.remove('dark');
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
    vi.unstubAllGlobals();
  });

  function renderApp(path: string) {
    const location = memoryLocation({ path, record: true });
    const view = render(
      <Router hook={location.hook}>
        <App />
      </Router>
    );
    return { ...view, location };
  }

  it('navigates to appearance and restores density through the real App provider', async () => {
    const view = renderApp('/settings');
    // This lazy module also loads service settings; the full suite transforms
    // it alongside other workers, so allow a bounded page-readiness wait.
    await screen.findByRole(
      'heading',
      { name: '系统概览' },
      { timeout: 5_000 }
    );
    expect(document.documentElement).toHaveAttribute(
      'data-ui-density',
      'standard'
    );
    const workspace = screen.getByTestId('studio-workspace-main');
    const navigation = screen.getAllByRole('navigation', {
      name: '系统设置导航',
    })[0];

    fireEvent.click(within(navigation).getByRole('button', { name: /^外观/ }));
    expect(await screen.findByRole('heading', { name: '外观' })).toHaveClass(
      'text-ui-page-title',
      'text-slate-100'
    );
    expect(view.location.history?.at(-1)).toBe('/settings/appearance');
    expect(screen.getByRole('radio', { name: '标准' })).toBeChecked();

    fireEvent.click(screen.getByRole('radio', { name: '紧凑' }));
    expect(document.documentElement).toHaveAttribute(
      'data-ui-density',
      'compact'
    );
    expect(saved.get(UI_DENSITY_STORAGE_KEY)).toBe('compact');
    expect(screen.getByTestId('studio-workspace-main')).toBe(workspace);
    expect(screen.getByRole('tab', { name: '系统设置' })).toHaveAttribute(
      'aria-selected',
      'true'
    );
    view.unmount();

    renderApp('/settings/appearance');
    expect(await screen.findByRole('radio', { name: '紧凑' })).toBeChecked();
    expect(document.documentElement).toHaveAttribute(
      'data-ui-density',
      'compact'
    );
    fireEvent.click(screen.getByRole('radio', { name: '标准' }));
    expect(saved.get(UI_DENSITY_STORAGE_KEY)).toBe('standard');
    expect(document.documentElement).toHaveAttribute(
      'data-ui-density',
      'standard'
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});
vi.mock('@/features/trading-safety/DeploymentEnvironmentLabel', () => ({
  DeploymentEnvironmentLabel: () => null,
}));
