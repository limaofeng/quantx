import { Bell, BookOpen, LogOut, Wallet } from 'lucide-react';
import { useCallback, useMemo } from 'react';
import { useLocation } from 'wouter';

import { useAuth } from '@/core/auth';
import { useCurrentAccount } from '@/features/dashboard/hooks';
import { useToast } from '@/hooks/use-toast';
import {
  getStudioNavigation,
  isNavigationItemActive,
  preloadRoute,
} from '@/router';

import type { StudioAction } from './types';

const studioNavigationItems = getStudioNavigation().flatMap(
  group => group.items
);
const primaryNavigationItems = studioNavigationItems.filter(
  item => item.href !== '/settings'
);
const settingsNavigationItems = studioNavigationItems.filter(
  item => item.href === '/settings'
);

function formatAssetLabel(totalAsset?: number | null) {
  if (typeof totalAsset !== 'number') return '总资产';
  return `总资产 ¥${totalAsset.toLocaleString()}`;
}

export function useStudioGlobalActions() {
  const [location, setLocation] = useLocation();
  const { logout, user } = useAuth();
  const { toast } = useToast();
  const { data: accountData } = useCurrentAccount();
  const totalAsset = accountData?.currentAccount?.totalAsset;

  const handleLogout = useCallback(async () => {
    try {
      await logout();
    } catch (error) {
      toast({
        title: '退出登录失败',
        description:
          error instanceof Error
            ? error.message
            : '无法撤销服务端会话，请稍后重试。',
        variant: 'destructive',
      });
    }
  }, [logout, toast]);

  const openDeveloperDocs = useCallback(() => {
    window.open('/docs/', '_blank', 'noopener,noreferrer');
  }, []);

  const globalActions = useMemo<StudioAction[]>(
    () =>
      primaryNavigationItems.map(item => ({
        active: isNavigationItemActive(item.href, location),
        icon: item.icon,
        id: `nav:${item.href}`,
        label: item.label,
        onHover: () => void preloadRoute(item.href),
        onSelect: () => setLocation(item.href),
      })),
    [location, setLocation]
  );

  const utilityActions = useMemo<StudioAction[]>(
    () => [
      {
        icon: Wallet,
        id: 'utility:assets',
        label: formatAssetLabel(totalAsset),
        onHover: () => void preloadRoute('/account'),
        onSelect: () => setLocation('/account'),
      },
      {
        badge: true,
        icon: Bell,
        id: 'utility:notifications',
        label: '通知',
        onSelect: () => undefined,
      },
      {
        icon: BookOpen,
        id: 'utility:developer-docs',
        label: '开发者文档（在新标签页打开）',
        onSelect: openDeveloperDocs,
      },
      {
        icon: LogOut,
        id: 'utility:logout',
        label: '退出登录',
        onSelect: () => void handleLogout(),
      },
      ...settingsNavigationItems.map(item => ({
        active: isNavigationItemActive(item.href, location),
        icon: item.icon,
        id: `nav:${item.href}`,
        label: item.label,
        onHover: () => void preloadRoute(item.href),
        onSelect: () => setLocation(item.href),
      })),
    ],
    [handleLogout, location, openDeveloperDocs, setLocation, totalAsset]
  );

  return {
    currentUserLabel: user?.displayName || user?.username || 'QuantX 用户',
    globalActions,
    utilityActions,
  };
}
