import { act, renderHook } from '@testing-library/react';
import { vi } from 'vitest';

import { useStudioGlobalActions } from '@/components/studio-workbench/useStudioGlobalActions';

const mocks = vi.hoisted(() => ({
  logout: vi.fn(),
  preloadRoute: vi.fn(),
  setLocation: vi.fn(),
}));

vi.mock('wouter', () => ({
  useLocation: () => ['/', mocks.setLocation],
}));

vi.mock('@/core/auth', () => ({
  useAuth: () => ({ logout: mocks.logout, user: { username: 'quantx' } }),
}));

vi.mock('@/features/dashboard/hooks', () => ({
  useCurrentAccount: () => ({ data: null }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

vi.mock('@/router', () => {
  const Icon = () => null;
  return {
    getStudioNavigation: () => [
      {
        items: [
          { href: '/', icon: Icon, label: '行情', order: 10 },
          { href: '/holdings', icon: Icon, label: '持仓', order: 20 },
        ],
        title: '主菜单',
      },
      {
        items: [
          { href: '/settings', icon: Icon, label: '系统设置', order: 10 },
        ],
        title: '系统设置',
      },
    ],
    isNavigationItemActive: (href: string, location: string) =>
      href === location,
    preloadRoute: mocks.preloadRoute,
  };
});

describe('useStudioGlobalActions', () => {
  it('moves system settings out of primary navigation and to the bottom utility slot', () => {
    const { result } = renderHook(() => useStudioGlobalActions());

    expect(result.current.globalActions.map(action => action.label)).toEqual([
      '行情',
      '持仓',
    ]);
    expect(result.current.utilityActions.at(-1)?.label).toBe('系统设置');
    expect(result.current.utilityActions.at(-1)?.id).toBe('nav:/settings');

    act(() => result.current.utilityActions.at(-1)?.onSelect());
    expect(mocks.setLocation).toHaveBeenCalledWith('/settings');
  });
});
