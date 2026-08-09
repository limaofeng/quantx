import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type React from 'react';

import type { StudioSidebarSizing } from './types';

export const DEFAULT_SIDEBAR_WIDTH = 288;
export const MIN_SIDEBAR_WIDTH = 240;
export const MAX_SIDEBAR_WIDTH = 560;
export const RESIZE_STEP = 10;
export const RESIZE_LARGE_STEP = 40;
export const STUDIO_WORKSPACE_SIDEBAR_STORAGE_SCOPE =
  'studio-workspace-sidebar';
export const STUDIO_WORKSPACE_SIDEBAR_SIZING: StudioSidebarSizing = {
  defaultWidth: 304,
  maxWidth: 440,
  minWidth: 248,
  storageScope: STUDIO_WORKSPACE_SIDEBAR_STORAGE_SCOPE,
};

const STORAGE_KEY = 'quantx-studio-workbench';

export function clampSidebarWidth(
  width: number,
  minWidth: number,
  maxWidth: number
) {
  if (!Number.isFinite(width)) return minWidth;
  return Math.min(Math.max(Math.round(width), minWidth), maxWidth);
}

export function readStudioSidebarWidths(): Record<string, number> {
  if (typeof window === 'undefined') return {};

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as {
      sidebarWidths?: Record<string, number>;
    };
    return parsed.sidebarWidths || {};
  } catch {
    return {};
  }
}

export function writeStudioSidebarWidths(
  sidebarWidths: Record<string, number>
) {
  if (typeof window === 'undefined') return;

  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ sidebarWidths }));
  } catch {
    // localStorage may be unavailable in hardened browser contexts.
  }
}

export interface UseStudioSidebarSizingOptions {
  resizeEdge?: 'left' | 'right';
  sizing?: StudioSidebarSizing;
  storageFallback: string;
}

export function useStudioSidebarSizing({
  resizeEdge = 'right',
  sizing,
  storageFallback,
}: UseStudioSidebarSizingOptions) {
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [sidebarWidths, setSidebarWidths] = useState<Record<string, number>>(
    readStudioSidebarWidths
  );
  const resizeStateRef = useRef({
    startWidth: DEFAULT_SIDEBAR_WIDTH,
    startX: 0,
  });

  const minSidebarWidth = sizing?.minWidth ?? MIN_SIDEBAR_WIDTH;
  const maxSidebarWidth = Math.max(
    minSidebarWidth,
    sizing?.maxWidth ?? MAX_SIDEBAR_WIDTH
  );
  const defaultSidebarWidth = clampSidebarWidth(
    sizing?.defaultWidth ?? DEFAULT_SIDEBAR_WIDTH,
    minSidebarWidth,
    maxSidebarWidth
  );
  const sidebarWidthKey = useMemo(
    () => sizing?.storageScope || storageFallback || 'studio-workbench',
    [sizing?.storageScope, storageFallback]
  );
  const sidebarWidth = clampSidebarWidth(
    sidebarWidths[sidebarWidthKey] ?? defaultSidebarWidth,
    minSidebarWidth,
    maxSidebarWidth
  );
  const resizeDirection = resizeEdge === 'right' ? 1 : -1;

  const updateSidebarWidth = useCallback(
    (nextWidth: number) => {
      setSidebarWidths(current => {
        const next = {
          ...current,
          [sidebarWidthKey]: clampSidebarWidth(
            nextWidth,
            minSidebarWidth,
            maxSidebarWidth
          ),
        };
        writeStudioSidebarWidths(next);
        return next;
      });
    },
    [maxSidebarWidth, minSidebarWidth, sidebarWidthKey]
  );

  const handleSidebarResizeStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      resizeStateRef.current = {
        startWidth: sidebarWidth,
        startX: event.clientX,
      };
      setIsResizingSidebar(true);
    },
    [sidebarWidth]
  );

  const handleSidebarResizeKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const step = event.shiftKey ? RESIZE_LARGE_STEP : RESIZE_STEP;
      let nextWidth: number | null = null;

      if (event.key === 'ArrowLeft') {
        nextWidth = sidebarWidth - step * resizeDirection;
      } else if (event.key === 'ArrowRight') {
        nextWidth = sidebarWidth + step * resizeDirection;
      } else if (event.key === 'Home') {
        nextWidth = minSidebarWidth;
      } else if (event.key === 'End') {
        nextWidth = maxSidebarWidth;
      }

      if (nextWidth === null) return;
      event.preventDefault();
      updateSidebarWidth(nextWidth);
    },
    [
      maxSidebarWidth,
      minSidebarWidth,
      resizeDirection,
      sidebarWidth,
      updateSidebarWidth,
    ]
  );

  useEffect(() => {
    if (!isResizingSidebar) return;

    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handlePointerMove = (event: PointerEvent) => {
      const deltaX =
        (event.clientX - resizeStateRef.current.startX) * resizeDirection;
      updateSidebarWidth(resizeStateRef.current.startWidth + deltaX);
    };

    const handlePointerUp = () => {
      setIsResizingSidebar(false);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);

    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };
  }, [isResizingSidebar, resizeDirection, updateSidebarWidth]);

  return {
    handleSidebarResizeKeyDown,
    handleSidebarResizeStart,
    isResizingSidebar,
    maxSidebarWidth,
    minSidebarWidth,
    sidebarWidth,
    sidebarWidthKey,
    updateSidebarWidth,
  };
}
