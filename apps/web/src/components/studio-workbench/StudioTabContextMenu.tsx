import { CircleX, CopyX, PanelRightClose, Pin, PinOff, X } from 'lucide-react';

import { StudioMenu } from './StudioMenu';
import type { StudioMenuItem, StudioMenuState } from './StudioMenu';

export type StudioTabContextMenuAction =
  'close' | 'closeAll' | 'closeOthers' | 'closeRight' | 'pin' | 'unpin';

export interface StudioTabContextMenuState {
  canClose: boolean;
  closableTabCount: number;
  closableTabsRight: number;
  isPreview: boolean;
  isPreviewable: boolean;
  tabId: string;
  tabIndex: number;
  x: number;
  y: number;
}

interface StudioTabContextMenuProps {
  menu: StudioTabContextMenuState | null;
  onAction: (action: StudioTabContextMenuAction, tabId: string) => void;
  onClose: () => void;
}

const menuItems = [
  { action: 'close' as const, icon: X, label: '关闭' },
  { action: 'closeOthers' as const, icon: CopyX, label: '关闭其他' },
  { action: 'closeRight' as const, icon: PanelRightClose, label: '关闭右侧' },
  { action: 'closeAll' as const, icon: CircleX, label: '关闭全部' },
];

function isActionDisabled(
  action: StudioTabContextMenuAction,
  menu: StudioTabContextMenuState
) {
  if (action === 'close') return !menu.canClose;
  if (action === 'closeOthers') {
    return menu.closableTabCount - Number(menu.canClose) === 0;
  }
  if (action === 'closeRight') return menu.closableTabsRight === 0;
  if (action === 'closeAll') return menu.closableTabCount === 0;
  return false;
}

export function StudioTabContextMenu({
  menu,
  onAction,
  onClose,
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
    ? [
        ...(menu.isPreviewable
          ? [
              {
                action: menu.isPreview ? ('pin' as const) : ('unpin' as const),
                icon: menu.isPreview ? Pin : PinOff,
                label: menu.isPreview ? '固定标签' : '取消固定',
              },
            ]
          : []),
        ...menuItems,
      ].flatMap((item, index, allItems) => {
        const Icon = item.icon;
        const menuItem: StudioMenuItem = {
          disabled: isActionDisabled(item.action, menu),
          icon: <Icon size={14} />,
          id: item.action,
          label: item.label,
          onSelect: () => onAction(item.action, menu.tabId),
        };

        const isCloseAll = item.action === 'closeAll';
        const followsPinAction =
          index > 0 &&
          (allItems[index - 1].action === 'pin' ||
            allItems[index - 1].action === 'unpin');

        return isCloseAll || followsPinAction
          ? [
              {
                id: `tab-${item.action}-separator`,
                type: 'separator' as const,
              },
              menuItem,
            ]
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
