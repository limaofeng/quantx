import { render, screen } from '@testing-library/react';

import { RouteSkeleton, type RouteSkeletonVariant } from '@/router/skeletons';

const variants: RouteSkeletonVariant[] = [
  'default',
  'dashboard',
  'studio',
  'table',
  'detail',
  'form',
];

describe('RouteSkeleton', () => {
  it.each(variants)('renders an accessible %s loading state', variant => {
    const { container } = render(<RouteSkeleton variant={variant} />);
    const status = screen.getByRole('status');
    const loadingLabel = screen.getByText('页面加载中');
    const skeletonBlocks = container.querySelectorAll('.skeleton-shimmer');

    expect(status).toHaveAttribute('aria-busy', 'true');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(loadingLabel).toHaveClass('sr-only');
    expect(screen.getByTestId(`route-skeleton-${variant}`)).toBe(status);
    expect(skeletonBlocks.length).toBeGreaterThan(0);
    skeletonBlocks.forEach(block => {
      expect(block).toHaveAttribute('aria-hidden', 'true');
    });
  });

  it('keeps the studio skeleton inside the existing workspace chrome', () => {
    const { container } = render(<RouteSkeleton variant="studio" />);

    expect(container.querySelector('[data-studio-workbench]')).toBeNull();
    expect(screen.getAllByRole('status')).toHaveLength(1);
  });
});
