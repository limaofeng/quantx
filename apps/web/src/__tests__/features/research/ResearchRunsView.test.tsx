import { render, screen } from '@testing-library/react';

import {
  ResearchRunsView,
  type ResearchRunListItem,
} from '@/features/research/pages/ResearchCenterPage';

const smokeRun: ResearchRunListItem = {
  artifactErrors: [],
  completedAt: '2026-07-29T13:16:53Z',
  elapsedSeconds: 11.28,
  eventCount: 3,
  hasMetrics: true,
  key: 'opaque-smoke-key',
  runId: '20260729-211642-718ab13b',
  startedAt: '2026-07-29T13:16:42Z',
  status: 'success',
  studyId: 'volume-shock',
  version: 'smoke-v1',
};

describe('ResearchRunsView', () => {
  it('renders an explicit empty state', () => {
    render(<ResearchRunsView fetching={false} runs={[]} total={0} />);

    expect(screen.getByText('还没有研究结果')).toBeInTheDocument();
  });

  it('shows the real smoke run as a small-sample result with a detail link', () => {
    render(<ResearchRunsView fetching={false} runs={[smokeRun]} total={1} />);

    expect(screen.getAllByText('异常放量 × 价格位置').length).toBeGreaterThan(
      0
    );
    expect(screen.getAllByText('小样本验证').length).toBeGreaterThan(0);
    const links = screen.getAllByRole('link', {
      name: /查看.*20260729-211642-718ab13b.*研究结果/,
    });
    expect(links[0]).toHaveAttribute(
      'href',
      '/research/volume-shock/smoke-v1/runs/20260729-211642-718ab13b?key=opaque-smoke-key'
    );
  });
});
