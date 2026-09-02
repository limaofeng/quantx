import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Router } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';

import { ResearchCenterFrame } from '@/features/research/components/ResearchCenterFrame';

describe('ResearchCenterFrame navigation', () => {
  it('renders one fixed header/nav shell and one scrolling content region', () => {
    const location = memoryLocation({
      path: '/research/training/runs/run-1',
    });
    const { container } = render(
      <Router hook={location.hook}>
        <ResearchCenterFrame title="训练运行详情" description="详情">
          <p>content</p>
        </ResearchCenterFrame>
      </Router>
    );

    const frame = screen.getByTestId('research-center-frame');
    const scrollRegion = screen.getByTestId('research-center-scroll');
    const nav = screen.getByRole('navigation', { name: '研究中心工作区' });

    expect(frame).toHaveClass('overflow-hidden');
    expect(scrollRegion).toHaveClass('overflow-y-auto');
    expect(screen.getByRole('banner')).toHaveClass('shrink-0');
    expect(nav).toHaveClass('shrink-0');
    expect(within(nav).getAllByRole('link')).toHaveLength(4);
    expect(within(nav).getByRole('link', { name: '概览' })).toHaveAttribute(
      'href',
      '/research'
    );
    expect(within(nav).getByRole('link', { name: '模型训练' })).toHaveAttribute(
      'aria-current',
      'page'
    );
    expect(within(nav).getByRole('link', { name: '实验运行' })).toHaveAttribute(
      'href',
      '/research/runs'
    );
    expect(within(nav).getByRole('link', { name: '模型库' })).toHaveAttribute(
      'href',
      '/research/models'
    );
    expect(
      container.querySelectorAll('[data-testid="research-center-scroll"]')
    ).toHaveLength(1);
  });
});
