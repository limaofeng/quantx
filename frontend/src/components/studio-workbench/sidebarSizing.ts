export const DEFAULT_SIDEBAR_WIDTH = 288;
export const MIN_SIDEBAR_WIDTH = 240;
export const MAX_SIDEBAR_WIDTH = 560;
export const RESIZE_STEP = 10;
export const RESIZE_LARGE_STEP = 40;

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
