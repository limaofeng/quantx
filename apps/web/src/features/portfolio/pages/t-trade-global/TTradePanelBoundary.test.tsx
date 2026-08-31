import { act, fireEvent, render, screen } from '@testing-library/react';
import { lazy, useState, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TTradePanelBoundary } from './TTradePanelBoundary';

vi.mock('@/shared/utils/error-handler', () => ({
  handleError: () => ({ id: 'panel-error' }),
}));

function Workspace({
  children,
  panelName,
}: {
  children: ReactNode;
  panelName: string;
}) {
  const [draft, setDraft] = useState('10000');
  return (
    <>
      <nav aria-label="做 T 工作区">实时监控 / 回放测试</nav>
      <input
        aria-label="参数草稿"
        value={draft}
        onChange={event => setDraft(event.target.value)}
      />
      <TTradePanelBoundary name={panelName}>{children}</TTradePanelBoundary>
    </>
  );
}

afterEach(() => vi.restoreAllMocks());

describe('TTradePanelBoundary', () => {
  it('keeps navigation and parent drafts mounted while a panel loads', async () => {
    let finish!: (module: { default: () => ReactNode }) => void;
    const Panel = lazy(
      () =>
        new Promise<{ default: () => ReactNode }>(resolve => {
          finish = resolve;
        })
    );
    render(
      <Workspace panelName="回放测试">
        <Panel />
      </Workspace>
    );

    fireEvent.change(screen.getByRole('textbox', { name: '参数草稿' }), {
      target: { value: '12000' },
    });
    expect(screen.getByRole('status')).toHaveTextContent('正在加载回放测试');
    expect(
      screen.getByRole('navigation', { name: '做 T 工作区' })
    ).toBeVisible();

    await act(async () => finish({ default: () => <h2>历史回放测试</h2> }));
    expect(screen.getByRole('heading', { name: '历史回放测试' })).toBeVisible();
    expect(screen.getByRole('textbox', { name: '参数草稿' })).toHaveValue(
      '12000'
    );
  });

  it('contains a failed panel and allows switching to another panel', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const BrokenPanel = lazy(() =>
      Promise.reject(new Error('chunk unavailable'))
    );
    const view = render(
      <Workspace panelName="诊断">
        <BrokenPanel />
      </Workspace>
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('诊断加载失败');
    expect(
      screen.getByRole('navigation', { name: '做 T 工作区' })
    ).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: '参数草稿' }), {
      target: { value: '9000' },
    });

    view.rerender(
      <Workspace panelName="总览">
        <h2>实时作战表</h2>
      </Workspace>
    );
    expect(screen.getByRole('heading', { name: '实时作战表' })).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '参数草稿' })).toHaveValue(
      '9000'
    );
  });
});
