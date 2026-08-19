import { render, screen } from '@testing-library/react';
import { BarChart3, Settings, TrendingUp } from 'lucide-react';
import { vi } from 'vitest';

import { ActivityBar } from '@/components/studio-workbench';

describe('ActivityBar', () => {
  it('keeps the brand at the top and utility actions anchored at the bottom', () => {
    render(
      <ActivityBar
        activeMode="WORKSPACE"
        globalActions={[
          {
            icon: BarChart3,
            id: 'nav:market',
            label: '行情',
            onSelect: vi.fn(),
          },
        ]}
        modes={[]}
        onModeChange={vi.fn()}
        theme={{ icon: TrendingUp, name: 'red', title: 'QuantX Studio' }}
        utilityActions={[
          {
            icon: Settings,
            id: 'nav:/settings',
            label: '系统设置',
            onSelect: vi.fn(),
          },
        ]}
      />
    );

    const activityBar = screen.getByTestId('studio-activity-bar');
    const brand = screen.getByTestId('studio-service-logo');
    const primaryAction = screen.getByRole('button', { name: '行情' });
    const settings = screen.getByRole('button', { name: '系统设置' });
    const utilityBar = screen.getByTestId('studio-utility-bar');

    expect(activityBar).toContainElement(brand);
    expect(utilityBar).not.toContainElement(brand);
    expect(utilityBar).toContainElement(settings);
    expect(utilityBar).toHaveClass('mt-auto');
    expect(
      brand.compareDocumentPosition(primaryAction) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
});
