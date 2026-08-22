import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { QuantXLogo } from '@/components/studio-workspace/QuantXLogo';

describe('QuantXLogo', () => {
  it.each([
    ['quantx', 'QuantX'],
    ['studio', 'QuantX Studio'],
  ] as const)('renders the %s name variant', (variant, expectedName) => {
    render(<QuantXLogo variant={variant} />);

    expect(screen.getByTestId('studio-brand-wordmark')).toHaveTextContent(
      expectedName
    );
    expect(screen.getByTestId('studio-brand-logo')).toBeInTheDocument();
  });

  it('renders the mark without a name', () => {
    render(<QuantXLogo variant="mark" />);

    expect(screen.getByTestId('studio-brand-logo')).toBeInTheDocument();
    expect(
      screen.queryByTestId('studio-brand-wordmark')
    ).not.toBeInTheDocument();
  });

  it('keeps the vector artwork flat', () => {
    render(<QuantXLogo variant="studio" />);

    const mark = screen.getByTestId('studio-brand-logo');
    expect(mark.querySelector('linearGradient')).toBeNull();
    expect(mark.querySelector('radialGradient')).toBeNull();
    expect(mark.querySelector('filter')).toBeNull();
    expect(mark.style.filter).toBe('');
  });

  it('supports a one-color mark', () => {
    render(<QuantXLogo tone="mono" variant="studio" />);

    const mark = screen.getByTestId('studio-brand-logo');
    expect(mark.querySelectorAll('[stroke]')).not.toHaveLength(0);
    for (const stroke of mark.querySelectorAll('[stroke]')) {
      expect(stroke).toHaveAttribute('stroke', 'currentColor');
    }
  });
});
