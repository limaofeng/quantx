import { CircleX, CopyX, PanelRightClose, X } from 'lucide-react';

import { StudioMenu } from './StudioMenu';
import type { StudioMenuItem, StudioMenuState } from './StudioMenu';

export type StudioTabContextMenuAction =
  | 'close'
  | 'closeAll'
  | 'closeOthers'
  | 'closeRight';

export interface StudioTabContextMenuState {
  tabId: string;
  tabIndex: number;
  x: number;
  y: number;
}

interface StudioTabContextMenuProps {
  menu: StudioTabContextMenuState | null;
  onAction: (action: StudioTabContextMenuAction, tabId: string) => void;
  onClose: () => void;
  tabCount: number;
}

const menuItems = [
  { action: 'close' as const, icon: X, label: '关闭' },
  { action: 'closeOthers' as const, icon: CopyX, label: '关闭其他' },
  { action: 'closeRight' as const, icon: PanelRightClose, label: '关闭右侧' },
  { action: 'closeAll' as const, icon: CircleX, label: '关闭全部' },
];

function isActionDisabled(
  action: StudioTabContextMenuAction,
  tabIndex: number,
  tabCount: number
) {
  if (action === 'closeOthers') return tabCount <= 1;
  if (action === 'closeRight') return tabIndex >= tabCount - 1;
  return tabCount === 0;
}

export function StudioTabContextMenu({
  menu,
  onAction,
  onClose,
  tabCount,
}: StudioTabContextMenuProps) {
  const studioMenu: StudioMenuState<StudioTabContextMenuState> | null = menu
    ? {
        anchor: {
          kind: 'point',
          x: menu.x,
          y: menu.y,
        },
        payload: menu,
      }
    : null;
  const items: StudioMenuItem[] = menu
    ? menuItems.flatMap((item, index) => {
        const Icon = item.icon;
        const menuItem: StudioMenuItem = {
          disabled: isActionDisabled(item.action, menu.tabIndex, tabCount),
          icon: <Icon size={14} />,
          id: item.action,
          label: item.label,
          onSelect: () => onAction(item.action, menu.tabId),
        };

        return index === 3
          ? [{ id: 'tab-close-separator', type: 'separator' }, menuItem]
          : [menuItem];
      })
    : [];

  return (
    <StudioMenu
      dataAttributes={{ 'data-studio-tab-context-menu': true }}
      items={items}
      menu={studioMenu}
      onClose={onClose}
      width={176}
    />
  );
}
