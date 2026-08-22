import { render, screen } from '@testing-library/react';

import { StatusBar } from '@/components/studio-workbench';

describe('StatusBar', () => {
  it('renders the Studio information band with stable left and right slots', () => {
    render(
      <StatusBar left={<span>行情已连接</span>} right={<span>READY</span>} />
    );

    const statusBar = screen.getByTestId('studio-status-bar');
    expect(statusBar).toHaveClass('h-[38px]', 'bg-[#07111f]');
    expect(screen.getByTestId('studio-status-left')).toHaveTextContent(
      '行情已连接'
    );
    expect(screen.getByTestId('studio-status-right')).toHaveTextContent(
      'READY'
    );
  });

  it('renders the stronger workspace footer treatment only when requested', () => {
    render(
      <StatusBar
        left={<span>行情已连接</span>}
        right={<span>READY</span>}
        variant="workspace"
      />
    );

    const statusBar = screen.getByTestId('studio-status-bar');
    expect(statusBar).toHaveAttribute('data-variant', 'workspace');
    expect(statusBar).toHaveClass('studio-shell-status-bar', 'px-5');
  });
});
