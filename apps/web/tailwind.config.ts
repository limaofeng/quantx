import typography from '@tailwindcss/typography';
import type { Config } from 'tailwindcss';
import tailwindcssAnimate from 'tailwindcss-animate';

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
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
        sans: ['Inter', 'system-ui', 'sans-serif'],
        serif: ['var(--font-serif)'],
        mono: ['var(--font-mono)'],
      },
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
