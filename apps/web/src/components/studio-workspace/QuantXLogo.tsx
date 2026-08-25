import { cn } from '@/utils/cn';

export type QuantXLogoTone = 'dark' | 'light' | 'mono';
export type QuantXLogoVariant = 'mark' | 'quantx' | 'studio';

const logoPalettes = {
  dark: {
    accent: '#38BDF8',
    foreground: '#F8FAFC',
    secondary: '#CBD5E1',
  },
  light: {
    accent: '#38BDF8',
    foreground: '#040B15',
    secondary: '#475569',
  },
  mono: {
    accent: 'currentColor',
    foreground: 'currentColor',
    secondary: 'currentColor',
  },
} as const;

export function QuantXLogo({
  className,
  tone = 'dark',
  variant = 'mark',
}: {
  className?: string;
  tone?: QuantXLogoTone;
  variant?: QuantXLogoVariant;
}) {
  const palette = logoPalettes[tone];
  const showName = variant !== 'mark';

  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-flex min-w-0 items-center',
        showName && 'gap-2',
        className
      )}
      data-logo-tone={tone}
      data-logo-variant={variant}
    >
      <svg
        aria-hidden="true"
        data-testid="studio-brand-logo"
        fill="none"
        shapeRendering="geometricPrecision"
        style={{ flexShrink: 0, height: 30, width: 30 }}
        viewBox="0 0 32 32"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M18.3 24.13A11 11 0 1 1 24.13 18.3"
          stroke={palette.accent}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2.6"
        />
        <path
          d="M16.6 16.6 27 27"
          stroke={palette.foreground}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2.6"
        />
        <path
          d="M19.5 27 27 19.5"
          stroke={palette.accent}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2.6"
        />
      </svg>

      {showName && (
        <span
          className="hidden min-w-0 truncate whitespace-nowrap text-ui-title leading-none md:block"
          data-testid="studio-brand-wordmark"
        >
          <span className="font-bold" style={{ color: palette.foreground }}>
            Quant
          </span>
          <span className="font-bold" style={{ color: palette.accent }}>
            X
          </span>
          {variant === 'studio' && (
            <>
              {' '}
              <span
                className="font-normal tracking-[0.02em]"
                style={{ color: palette.secondary }}
              >
                Studio
              </span>
            </>
          )}
        </span>
      )}
    </span>
  );
}
