import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
} from 'react';

import { cn } from '@/utils/cn';

const SCROLLBAR_ACTIVE_CLASS = 'scrollbar-active';
const SCROLLBAR_HIDE_DELAY_MS = 1000;

interface GridDragScrollState {
  isDragging: boolean;
  pointerId: number;
  previousCursor: string;
  previousUserSelect: string;
  startScrollLeft: number;
  startX: number;
}

interface StudioDataGridProps {
  ariaLabel?: string;
  children: ReactNode;
  className?: string;
  gridClassName?: string;
  loadingOverlay?: ReactNode;
  notice?: ReactNode;
  tableClassName?: string;
  tableStyle?: CSSProperties;
  testId?: string;
}

export function StudioDataGrid({
  ariaLabel,
  children,
  className,
  gridClassName,
  loadingOverlay,
  notice,
  tableClassName,
  tableStyle,
  testId,
}: StudioDataGridProps) {
  const [isGridDragScrolling, setIsGridDragScrolling] = useState(false);
  const [isGridScrollbarActive, setIsGridScrollbarActive] = useState(false);
  const gridDragScrollRef = useRef<GridDragScrollState | null>(null);
  const gridScrollbarTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (gridScrollbarTimerRef.current !== null) {
        window.clearTimeout(gridScrollbarTimerRef.current);
      }
    };
  }, []);

  const activateGridScrollbar = () => {
    if (gridScrollbarTimerRef.current !== null) {
      window.clearTimeout(gridScrollbarTimerRef.current);
    }

    setIsGridScrollbarActive(true);
    gridScrollbarTimerRef.current = window.setTimeout(() => {
      setIsGridScrollbarActive(false);
      gridScrollbarTimerRef.current = null;
    }, SCROLLBAR_HIDE_DELAY_MS);
  };

  const shouldIgnoreGridDragScroll = (target: EventTarget | null) => {
    if (!(target instanceof HTMLElement)) return true;
    return Boolean(
      target.closest(
        'thead, button, a, input, textarea, select, [role="button"], [data-drag-scroll-ignore="true"]'
      )
    );
  };

  const endGridDragScroll = (event: PointerEvent<HTMLDivElement>) => {
    const dragState = gridDragScrollRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;

    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    } catch {
      // Synthetic pointer events may not have an active browser capture target.
    }
    document.body.style.cursor = dragState.previousCursor;
    document.body.style.userSelect = dragState.previousUserSelect;
    gridDragScrollRef.current = null;
    setIsGridDragScrolling(false);
  };

  const handleGridPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || shouldIgnoreGridDragScroll(event.target)) return;

    gridDragScrollRef.current = {
      isDragging: false,
      pointerId: event.pointerId,
      previousCursor: document.body.style.cursor,
      previousUserSelect: document.body.style.userSelect,
      startScrollLeft: event.currentTarget.scrollLeft,
      startX: event.clientX,
    };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Non-native pointer events can still drive scroll math in tests/tools.
    }
  };

  const handleGridPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const dragState = gridDragScrollRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;

    const deltaX = event.clientX - dragState.startX;
    if (!dragState.isDragging && Math.abs(deltaX) < 4) return;

    if (!dragState.isDragging) {
      dragState.isDragging = true;
      document.body.style.cursor = 'grabbing';
      document.body.style.userSelect = 'none';
      setIsGridDragScrolling(true);
    }

    event.preventDefault();
    event.currentTarget.scrollLeft = dragState.startScrollLeft - deltaX;
    activateGridScrollbar();
    event.currentTarget.dispatchEvent(new Event('scroll'));
  };

  return (
    <div
      className={cn(
        'relative flex min-h-0 flex-1 flex-col overflow-hidden',
        className
      )}
    >
      {notice}
      {loadingOverlay}
      <div
        aria-label={ariaLabel}
        data-testid={testId}
        onPointerCancel={endGridDragScroll}
        onPointerDown={handleGridPointerDown}
        onPointerLeave={endGridDragScroll}
        onPointerMove={handleGridPointerMove}
        onPointerUp={endGridDragScroll}
        className={cn(
          'custom-scrollbar min-h-0 flex-1 overflow-auto bg-[#08101d] [touch-action:pan-y]',
          isGridDragScrolling ? 'cursor-grabbing' : 'cursor-grab',
          isGridScrollbarActive && SCROLLBAR_ACTIVE_CLASS,
          gridClassName
        )}
      >
        <table
          className={cn(
            'w-max min-w-full border-collapse text-xs',
            tableClassName
          )}
          style={tableStyle}
        >
          {children}
        </table>
      </div>
    </div>
  );
}
