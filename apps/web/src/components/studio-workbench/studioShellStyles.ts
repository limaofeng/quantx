import type { CSSProperties } from 'react';

export const STUDIO_CHROME_BACKGROUND = '#040b15';
export const STUDIO_HEADER_HEIGHT = 52;
export const STUDIO_WORKSPACE_SURFACE_TOP = '#0b1a2b';
export const STUDIO_WORKSPACE_CONTENT_BACKGROUND = '#07111f';
export const STUDIO_WORKSPACE_WEAK_BORDER = '#22364d';

export const STUDIO_WORKSPACE_TAB_STYLE = {
  borderTopLeftRadius: 8,
  borderTopRightRadius: 8,
  height: 44,
} satisfies CSSProperties;

export const STUDIO_WORKSPACE_ACTIVE_TAB_STYLE = {
  background: STUDIO_WORKSPACE_SURFACE_TOP,
  borderColor: STUDIO_WORKSPACE_WEAK_BORDER,
  boxShadow:
    'inset 0 1px 0 rgba(148, 190, 230, 0.09), 0 -8px 24px rgba(0, 0, 0, 0.12)',
  zIndex: 10,
} satisfies CSSProperties;

export const STUDIO_WORKSPACE_SURFACE_BACKGROUND = `linear-gradient(180deg, ${STUDIO_WORKSPACE_SURFACE_TOP} 0px, #091725 36px, ${STUDIO_WORKSPACE_CONTENT_BACKGROUND} 96px)`;
