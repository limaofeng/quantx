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

  it('renders the Studio rail with visible labels and a separate bottom utility group', () => {
    render(
      <ActivityBar
        activeMode="WORKSPACE"
        environmentStatus={{ detail: '实盘', label: 'READY', tone: 'ready' }}
        globalActions={[
          {
            active: true,
            icon: BarChart3,
            id: 'nav:/',
            label: '行情工作台',
            shortLabel: '行情',
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
            shortLabel: '设置',
            onSelect: vi.fn(),
          },
        ]}
        variant="studio"
      />
    );

    const activityBar = screen.getByTestId('studio-activity-bar');
    expect(activityBar).toHaveAttribute('data-variant', 'studio');
    expect(activityBar).toHaveStyle({
      background: '#040b15',
      position: 'relative',
      width: '84px',
      zIndex: 20,
    });
    expect(activityBar).not.toHaveClass('border-t');
    expect(activityBar).not.toHaveClass('border-r');
    expect(activityBar.style.boxShadow).toBe('');
    expect(activityBar.style.borderColor).toBe('');
    expect(screen.getByTestId('studio-primary-navigation')).toHaveClass(
      'flex-1',
      'overflow-y-auto'
    );
    expect(screen.getByText('行情')).toBeVisible();
    expect(screen.getByText('设置')).toBeVisible();
    const environmentStatus = screen.getByTestId('studio-environment-status');
    expect(environmentStatus).toHaveTextContent('READY实盘');
    expect(environmentStatus.firstElementChild).toHaveClass(
      'text-emerald-300'
    );
    expect(environmentStatus.firstElementChild?.firstElementChild).toHaveClass(
      'bg-emerald-400'
    );
    expect(screen.queryByTestId('studio-service-logo')).not.toBeInTheDocument();
  });
});
