import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// Keep these semantic names aligned with theme.extend in tailwind.config.ts.
// Register spacing in the theme so padding/gap axes and control sizes share
// Tailwind's normal override rules; studio/table heights are height-only.
const mergeClasses = extendTailwindMerge({
  extend: {
    theme: {
      spacing: [
        'control-compact',
        'control-default',
        'control-large',
        'ui-panel',
        'ui-section',
        'ui-empty',
        'ui-page',
        'ui-table-cell-y',
        'ui-table-multiline-y',
      ],
    },
    classGroups: {
      h: [
        {
          h: [
            'studio-header',
            'studio-status',
            'studio-tab',
            'ui-table-header',
            'ui-table-row',
          ],
        },
      ],
      // Unknown text-* names otherwise fall into the text-color group.
      'font-size': [
        {
          text: [
            'ui-micro',
            'ui-caption',
            'ui-label',
            'ui-body',
            'ui-title',
            'ui-heading',
            'ui-page-title',
            'ui-display',
            'ui-display-lg',
            'ui-display-xl',
          ],
        },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return mergeClasses(clsx(inputs));
}
