import { Check } from 'lucide-react';
import { Fragment, useEffect, useId, useMemo, useRef } from 'react';
import type { ReactNode, RefObject } from 'react';
import { createPortal } from 'react-dom';

import { cn } from '@/utils/cn';

export const STUDIO_MENU_OPEN_EVENT = 'studio-menu-open';

const VIEWPORT_PADDING = 8;
const DEFAULT_MENU_WIDTH = 184;
const DEFAULT_MENU_OFFSET = 6;

interface StudioMenuOpenEventDetail {
  id: string;
}

export interface StudioMenuRect {
  bottom: number;
  height: number;
  left: number;
  right: number;
  top: number;
  width: number;
}

export type StudioMenuPlacement =
  | 'bottom-start'
  | 'bottom-end'
  | 'left-start'
  | 'right-start'
  | 'top-end'
  | 'top-start';

export type StudioMenuAnchor =
  | {
      kind: 'point';
      x: number;
      y: number;
    }
  | {
      kind: 'element';
      offset?: number;
      placement?: StudioMenuPlacement;
      rect: StudioMenuRect;
    };

export interface StudioMenuState<TPayload = unknown> {
  anchor: StudioMenuAnchor;
  payload: TPayload;
}

export interface StudioMenuActionItem {
  checked?: boolean;
  danger?: boolean;
  disabled?: boolean;
  icon?: ReactNode;
  id: string;
  label: ReactNode;
  onSelect?: () => void;
  shortcut?: ReactNode;
  type?: 'item';
}

export interface StudioMenuSeparatorItem {
  id?: string;
  type: 'separator';
}

export type StudioMenuItem = StudioMenuActionItem | StudioMenuSeparatorItem;

interface StudioMenuProps<TPayload = unknown> {
  ariaLabel?: string;
  className?: string;
  closeOnScrollRef?: RefObject<HTMLElement | null>;
  dataAttributes?: Record<string, boolean | number | string | undefined>;
  items: StudioMenuItem[];
  maxHeight?: number;
  menu: StudioMenuState<TPayload> | null;
  onClose: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
  width?: number;
}

function estimateMenuHeight(items: StudioMenuItem[], maxHeight?: number) {
  const estimated = items.reduce(
    (height, item) => height + (item.type === 'separator' ? 9 : 32),
    8
  );
  return maxHeight ? Math.min(estimated, maxHeight) : estimated;
}

function resolveAnchorPosition(
  anchor: StudioMenuAnchor,
  width: number,
  height: number
) {
  if (anchor.kind === 'point') {
    return { left: anchor.x, top: anchor.y };
  }

  const offset = anchor.offset ?? DEFAULT_MENU_OFFSET;
  const placement = anchor.placement ?? 'bottom-start';

  if (placement === 'bottom-end') {
    return { left: anchor.rect.right - width, top: anchor.rect.bottom + offset };
  }

  if (placement === 'top-start') {
    return { left: anchor.rect.left, top: anchor.rect.top - height - offset };
  }

  if (placement === 'top-end') {
    return { left: anchor.rect.right - width, top: anchor.rect.top - height - offset };
  }

  if (placement === 'right-start') {
    return { left: anchor.rect.right + offset, top: anchor.rect.top };
  }

  if (placement === 'left-start') {
    return { left: anchor.rect.left - width - offset, top: anchor.rect.top };
  }

  return { left: anchor.rect.left, top: anchor.rect.bottom + offset };
}

function clampPosition(anchor: StudioMenuAnchor, width: number, height: number) {
  if (typeof window === 'undefined') return { left: 0, top: 0 };

  const ideal = resolveAnchorPosition(anchor, width, height);
  const maxLeft = Math.max(
    VIEWPORT_PADDING,
    window.innerWidth - width - VIEWPORT_PADDING
  );
  const maxTop = Math.max(
    VIEWPORT_PADDING,
    window.innerHeight - height - VIEWPORT_PADDING
  );

  return {
    left: Math.min(Math.max(VIEWPORT_PADDING, ideal.left), maxLeft),
    top: Math.min(Math.max(VIEWPORT_PADDING, ideal.top), maxTop),
  };
}

function renderItemIcon(item: StudioMenuActionItem) {
  if (item.icon) return item.icon;
  if (!item.checked) return <span className="h-3.5 w-3.5" />;
  return <Check size={14} className="text-emerald-300" />;
}

export function StudioMenu<TPayload = unknown>({
  ariaLabel = 'Studio menu',
  className,
  closeOnScrollRef,
  dataAttributes,
  items,
  maxHeight,
  menu,
  onClose,
  returnFocusRef,
  width = DEFAULT_MENU_WIDTH,
}: StudioMenuProps<TPayload>) {
  const instanceId = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const previousActiveElementRef = useRef<HTMLElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const actionItems = useMemo(
    () => items.filter((item): item is StudioMenuActionItem => item.type !== 'separator'),
    [items]
  );

  useEffect(() => {
    if (!menu) return;

    previousActiveElementRef.current =
      returnFocusRef?.current ||
      (document.activeElement instanceof HTMLElement ? document.activeElement : null);

    const closeMenu = () => onClose();
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && menuRef.current?.contains(target)) return;
      onClose();
    };
    const handleContextMenu = (event: MouseEvent) => {
      const target = event.target;
      if (target instanceof Node && menuRef.current?.contains(target)) {
        event.preventDefault();
        return;
      }
      onClose();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    const handleStudioMenuOpen = (event: Event) => {
      const detail = (event as CustomEvent<StudioMenuOpenEventDetail>).detail;
      if (detail?.id && detail.id !== instanceId) onClose();
    };
    const scrollElement = closeOnScrollRef?.current;

    document.addEventListener('pointerdown', handlePointerDown, true);
    document.addEventListener('contextmenu', handleContextMenu, true);
    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener(STUDIO_MENU_OPEN_EVENT, handleStudioMenuOpen);
    window.addEventListener('resize', closeMenu);
    scrollElement?.addEventListener('scroll', closeMenu, { passive: true });
    document.dispatchEvent(
      new CustomEvent<StudioMenuOpenEventDetail>(STUDIO_MENU_OPEN_EVENT, {
        detail: { id: instanceId },
      })
    );

    window.setTimeout(() => {
      itemRefs.current.find(item => item && !item.disabled)?.focus();
    }, 0);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true);
      document.removeEventListener('contextmenu', handleContextMenu, true);
      document.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener(STUDIO_MENU_OPEN_EVENT, handleStudioMenuOpen);
      window.removeEventListener('resize', closeMenu);
      scrollElement?.removeEventListener('scroll', closeMenu);

      const target = previousActiveElementRef.current;
      if (target && document.contains(target)) target.focus({ preventScroll: true });
    };
  }, [closeOnScrollRef, instanceId, menu, onClose, returnFocusRef]);

  if (!menu || typeof document === 'undefined') return null;
  const position = clampPosition(
    menu.anchor,
    width,
    estimateMenuHeight(items, maxHeight)
  );

  const focusItem = (index: number) => {
    const next = itemRefs.current[index];
    if (!next || next.disabled) return;
    next.focus();
  };

  const focusByDelta = (currentIndex: number, delta: 1 | -1) => {
    if (!actionItems.length) return;
    for (let offset = 1; offset <= actionItems.length; offset += 1) {
      const nextIndex =
        (currentIndex + offset * delta + actionItems.length) % actionItems.length;
      const next = itemRefs.current[nextIndex];
      if (next && !next.disabled) {
        next.focus();
        return;
      }
    }
  };

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={ariaLabel}
      data-studio-menu
      {...dataAttributes}
      className={cn(
        'fixed z-[10030] overflow-hidden rounded-lg border border-white/10 bg-[#0b1120]/95 py-1.5 text-xs font-bold text-slate-300 shadow-2xl shadow-black/40 backdrop-blur-xl',
        className
      )}
      style={{
        left: position.left,
        maxHeight,
        maxWidth: `calc(100vw - ${VIEWPORT_PADDING * 2}px)`,
        top: position.top,
        width,
      }}
      onClick={event => event.stopPropagation()}
      onContextMenu={event => event.preventDefault()}
      onKeyDown={event => {
        const activeIndex = itemRefs.current.findIndex(
          item => item === document.activeElement
        );

        if (event.key === 'ArrowDown') {
          event.preventDefault();
          focusByDelta(Math.max(activeIndex, 0), 1);
        }

        if (event.key === 'ArrowUp') {
          event.preventDefault();
          focusByDelta(activeIndex < 0 ? 0 : activeIndex, -1);
        }

        if (event.key === 'Home') {
          event.preventDefault();
          focusItem(0);
        }

        if (event.key === 'End') {
          event.preventDefault();
          focusItem(actionItems.length - 1);
        }
      }}
    >
      {items.map((item, itemIndex) => {
        if (item.type === 'separator') {
          return <div key={item.id || `separator-${itemIndex}`} className="my-1 h-px bg-white/10" />;
        }

        const actionIndex = actionItems.findIndex(action => action.id === item.id);
        const disabled = Boolean(item.disabled);
        const itemClass = disabled
          ? 'cursor-not-allowed text-slate-600'
          : item.danger
            ? 'text-rose-300 hover:bg-rose-500/10 hover:text-rose-200 focus-visible:bg-rose-500/10 focus-visible:text-rose-200'
            : 'text-slate-300 hover:bg-white/5 hover:text-slate-100 focus-visible:bg-white/5 focus-visible:text-slate-100';

        return (
          <Fragment key={item.id}>
            <button
              ref={element => {
                itemRefs.current[actionIndex] = element;
              }}
              type="button"
              role="menuitem"
              disabled={disabled}
              onClick={event => {
                event.stopPropagation();
                if (disabled) return;
                item.onSelect?.();
                onClose();
              }}
              onKeyDown={event => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                if (disabled) return;
                item.onSelect?.();
                onClose();
              }}
              className={cn(
                'flex w-full items-center gap-2.5 px-3 py-2 text-left outline-none transition-colors disabled:cursor-not-allowed',
                itemClass
              )}
            >
              <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center">
                {renderItemIcon(item)}
              </span>
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              {item.shortcut && (
                <span className="shrink-0 font-mono text-[10px] text-slate-600">
                  {item.shortcut}
                </span>
              )}
            </button>
          </Fragment>
        );
      })}
    </div>,
    document.body
  );
}
