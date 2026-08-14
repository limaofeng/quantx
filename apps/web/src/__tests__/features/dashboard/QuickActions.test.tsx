import { render, screen } from '@testing-library/react';

import { QuickActions } from '@/features/dashboard/components/QuickActions';

describe('QuickActions', () => {
  it('exposes the limit-up board assistant as a dashboard shortcut', () => {
    render(<QuickActions />);

    expect(screen.getByTestId('quick-action-limit-up-board')).toHaveAttribute(
      'href',
      '/limit-up-board'
    );
    expect(screen.getByText('打板助手')).toBeVisible();
  });
});
