import type { CSSProperties } from 'react';

export const STUDIO_CHROME_BACKGROUND = '#040b15';
export const STUDIO_HEADER_HEIGHT = 40;
export const STUDIO_WORKSPACE_SURFACE = 'var(--studio-workspace-surface)';
export const STUDIO_WORKSPACE_SURFACE_RADIUS = 8;
export const STUDIO_WORKSPACE_TAB_RADIUS = 8;
export const STUDIO_WORKSPACE_WEAK_BORDER = '#22364d';

export const STUDIO_WORKSPACE_TAB_STYLE = {
  borderTopLeftRadius: STUDIO_WORKSPACE_TAB_RADIUS,
  borderTopRightRadius: STUDIO_WORKSPACE_TAB_RADIUS,
  height: 36,
} satisfies CSSProperties;

export const STUDIO_WORKSPACE_ACTIVE_TAB_STYLE = {
  background: STUDIO_WORKSPACE_SURFACE,
  borderColor: STUDIO_WORKSPACE_WEAK_BORDER,
  boxShadow:
    'inset 0 1px 0 rgba(148, 190, 230, 0.09), 0 -8px 24px rgba(0, 0, 0, 0.12)',
  zIndex: 10,
} satisfies CSSProperties;
