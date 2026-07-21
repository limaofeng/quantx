import { Bell, Wallet } from 'lucide-react';
import { useMemo } from 'react';
import { useLocation } from 'wouter';

import { useCurrentAccount } from '@/features/dashboard/hooks';
import {
  getDesktopNavigation,
  isNavigationItemActive,
  preloadRoute,
} from '@/router';

import type { StudioAction } from './types';

const desktopNavigationItems = getDesktopNavigation().flatMap(
  group => group.items
);

function formatAssetLabel(totalAsset?: number | null) {
  if (typeof totalAsset !== 'number') return '总资产';
  return `总资产 ¥${totalAsset.toLocaleString()}`;
}

export function useStudioGlobalActions() {
  const [location, setLocation] = useLocation();
  const { data: accountData } = useCurrentAccount();
  const totalAsset = accountData?.currentAccount?.totalAsset;

  const globalActions = useMemo<StudioAction[]>(
    () =>
      desktopNavigationItems.map(item => ({
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
    ],
    [setLocation, totalAsset]
  );

  return { globalActions, utilityActions };
}
