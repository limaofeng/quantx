import { render, screen } from '@testing-library/react';

import { ResearchStatusBadge } from '@/features/research/components';

describe('ResearchStatusBadge', () => {
  it('groups failed preflight runs into the failure state', () => {
    render(<ResearchStatusBadge status="failed_preflight" />);

    expect(screen.getByText('失败')).toBeInTheDocument();
  });
});
