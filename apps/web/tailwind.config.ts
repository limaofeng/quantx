import typography from '@tailwindcss/typography';
import type { Config } from 'tailwindcss';
import tailwindcssAnimate from 'tailwindcss-animate';

// Only whitespace follows density. Geometry (width/height/inset), chart sizes,
// breakpoints and the root rem size keep the existing Tailwind scale.
const densitySpacing = {
  1.5: 'var(--s1h)',
  2: 'var(--s2)',
  2.5: 'var(--s2h)',
  3: 'var(--space-panel)',
  3.5: 'var(--s3h)',
};

export default {
  darkMode: ['class'],
  content: [
    './index.html',
    './src/**/*.{js,jsx,ts,tsx}',
    '!./src/**/*.test.{js,jsx,ts,tsx}',
    '!./src/__tests__/**',
    '!./src/generated/**',
  ],
  theme: {
    extend: {
      borderRadius: {
        lg: 'var(--radius-panel)',
        md: 'var(--radius-control)',
        sm: 'var(--radius-element)',
        panel: 'var(--radius-panel)',
        control: 'var(--radius-control)',
        dialog: 'var(--radius-dialog)',
      },
      colors: {
        background: 'var(--background)',
        foreground: 'var(--foreground)',
        card: {
          DEFAULT: 'var(--card)',
          foreground: 'var(--card-foreground)',
        },
        popover: {
          DEFAULT: 'var(--popover)',
          foreground: 'var(--popover-foreground)',
        },
        primary: {
          DEFAULT: 'var(--primary)',
          foreground: 'var(--primary-foreground)',
        },
        secondary: {
          DEFAULT: 'var(--secondary)',
          foreground: 'var(--secondary-foreground)',
        },
        muted: {
          DEFAULT: 'var(--muted)',
          foreground: 'var(--muted-foreground)',
        },
        accent: {
          DEFAULT: 'var(--accent)',
          foreground: 'var(--accent-foreground)',
        },
        destructive: {
          DEFAULT: 'var(--destructive)',
          foreground: 'var(--destructive-foreground)',
        },
        border: 'var(--border)',
        input: 'var(--input)',
        ring: 'var(--ring)',
        chart: {
          '1': 'var(--chart-1)',
          '2': 'var(--chart-2)',
          '3': 'var(--chart-3)',
          '4': 'var(--chart-4)',
          '5': 'var(--chart-5)',
        },
        success: 'var(--success)',
        warning: 'var(--warning)',
        market: {
          up: 'rgb(var(--market-up) / <alpha-value>)',
          down: 'rgb(var(--market-down) / <alpha-value>)',
          flat: 'rgb(var(--market-flat) / <alpha-value>)',
          'buy-cta': 'rgb(var(--market-buy-cta) / <alpha-value>)',
        },
        holding: {
          down: 'rgb(var(--holding-down) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        serif: ['var(--font-serif)'],
        mono: ['var(--font-mono)'],
      },
      fontWeight: {
        extrabold: 'var(--font-weight-ui-heavy)',
        black: 'var(--font-weight-ui-strong)',
      },
      padding: densitySpacing,
      // Preserve fixed offsets; only the small title/metadata separation changes.
      margin: { 1: 'var(--s1)' },
      gap: densitySpacing,
      space: densitySpacing,
      fontSize: {
        'ui-micro': [
          'var(--font-size-ui-micro)',
          { lineHeight: 'var(--line-height-ui-micro)' },
        ],
        'ui-caption': [
          'var(--font-size-ui-caption)',
          { lineHeight: 'var(--line-height-ui-caption)' },
        ],
        'ui-label': [
          'var(--font-size-ui-label)',
          { lineHeight: 'var(--line-height-ui-label)' },
        ],
        'ui-body': [
          'var(--font-size-ui-body)',
          { lineHeight: 'var(--line-height-ui-body)' },
        ],
        'ui-title': [
          'var(--font-size-ui-title)',
          { lineHeight: 'var(--line-height-ui-title)' },
        ],
        'ui-heading': [
          'var(--font-size-ui-heading)',
          { lineHeight: 'var(--line-height-ui-heading)' },
        ],
        'ui-page-title': [
          'var(--font-size-ui-page-title)',
          { lineHeight: 'var(--line-height-ui-page-title)' },
        ],
        'ui-display': [
          'var(--font-size-ui-display)',
          { lineHeight: 'var(--line-height-ui-display)' },
        ],
        'ui-display-lg': [
          'var(--font-size-ui-display-lg)',
          { lineHeight: 'var(--line-height-ui-display-lg)' },
        ],
        'ui-display-xl': [
          'var(--font-size-ui-display-xl)',
          { lineHeight: 'var(--line-height-ui-display-xl)' },
        ],
      },
      height: {
        'control-compact': 'var(--control-height-compact)',
        'control-default': 'var(--control-height-default)',
        'control-large': 'var(--control-height-large)',
        'studio-header': 'var(--studio-header-height)',
        'studio-status': 'var(--studio-status-height)',
        'studio-tab': 'var(--studio-tab-height)',
        'ui-table-header': 'var(--table-header-height)',
        'ui-table-row': 'var(--table-row-height)',
      },
      minHeight: {
        'control-compact': 'var(--control-height-compact)',
        'control-default': 'var(--control-height-default)',
        'control-large': 'var(--control-height-large)',
      },
      spacing: {
        'control-compact': 'var(--control-height-compact)',
        'control-default': 'var(--control-height-default)',
        'control-large': 'var(--control-height-large)',
        'ui-panel': 'var(--space-panel)',
        'ui-section': 'var(--space-section)',
        'ui-empty': 'var(--space-empty)',
        'ui-page': 'var(--space-page)',
        'ui-table-cell-y': 'var(--table-cell-y)',
        'ui-table-multiline-y': 'var(--table-multiline-cell-y)',
      },
      keyframes: {
        'accordion-down': {
          from: {
            height: '0',
          },
          to: {
            height: 'var(--radix-accordion-content-height)',
          },
        },
        'accordion-up': {
          from: {
            height: 'var(--radix-accordion-content-height)',
          },
          to: {
            height: '0',
          },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [tailwindcssAnimate, typography],
} satisfies Config;
