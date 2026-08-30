import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Route, Router, Switch } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';

import { ServiceStatusHistoryPanel } from '@/features/settings/components/ServiceStatusHistoryPanel';
import { ServiceStatusPanel } from '@/features/settings/components/ServiceStatusPanel';
import type {
  MonitorHistory,
  MonitorIncidentPage,
  MonitorRange,
  MonitorSummary,
} from '@/features/system/monitor-api';

let routing = memoryLocation({ record: true });
function currentQuery() {
  return new URL(routing.history.at(-1)!, 'http://localhost').search;
}

const api = vi.hoisted(() => ({
  getMonitorSummary: vi.fn(),
  getMonitorHistory: vi.fn(),
  getMonitorIncidents: vi.fn(),
}));
vi.mock('@/features/system/monitor-api', () => api);
const now = new Date().toISOString();
const originalScrollIntoView = Element.prototype.scrollIntoView;
const summary: MonitorSummary = {
  generatedAt: now,
  lastCycleAt: now,
  window: '24h',
  checkIntervalSeconds: 30,
  overallStatus: 'degraded',
  groups: [
    {
      id: 'quantx_runtime',
      name: '运行组件',
      status: 'degraded',
      targetIds: ['qmt-agent', 'engine'],
    },
  ],
  targets: ['qmt-agent', 'engine'].map(id => ({
    id,
    name: id === 'qmt-agent' ? 'QMT Agent' : '策略引擎',
    group: 'quantx_runtime',
    optional: false,
    probeKind: id === 'qmt-agent' ? 'composite' : 'derived',
    status: id === 'qmt-agent' ? 'degraded' : 'healthy',
    checkedAt: now,
    lastSuccessAt: now,
    latencyMs: null,
    reasonCode: id === 'qmt-agent' ? 'XTTRADING_UNAVAILABLE' : null,
    availabilityPct: 95,
    healthyPct: 90,
    coveragePct: 100,
    latencyP50Ms: null,
    latencyP95Ms: null,
    sampleCount: 20,
    activeIncident: id === 'qmt-agent',
  })),
};

function history(targetId: string, range: MonitorRange): MonitorHistory {
  return {
    target: { id: targetId, name: targetId },
    range,
    bucketSeconds: 60,
    points: [
      {
        start: now,
        status: 'healthy',
        sampleCount: 1,
        healthyCount: 1,
        degradedCount: 0,
        unavailableCount: 0,
        unknownCount: 0,
        disabledCount: 0,
        latencyCount: 0,
        latencyMaxMs: null,
        latencyP50Ms: null,
        latencyP95Ms: null,
      },
    ],
  };
}

function pageOf(
  range: MonitorRange,
  targetId: string,
  page: number,
  pageSize: number
): MonitorIncidentPage {
  const total = targetId === 'qmt-agent' ? 45 : 0;
  return {
    range,
    page,
    pageSize,
    total,
    asOf: now,
    incidents: Array.from(
      {
        length: Math.min(pageSize, Math.max(0, total - (page - 1) * pageSize)),
      },
      (_, index) => ({
        id: total - (page - 1) * pageSize - index,
        targetId,
        targetName: 'QMT Agent',
        openedAt: new Date(Date.now() - 3600000).toISOString(),
        resolvedAt: page === 1 && index === 0 ? null : now,
        active: page === 1 && index === 0,
        reasonCode: 'QMT_AGENT_NOT_RECONCILED',
      })
    ),
  };
}

function mount(
  path = '/settings/status/qmt-agent/history?range=24h&page=1&pageSize=20'
) {
  routing = memoryLocation({ path, record: true });
  return render(
    <Router hook={routing.hook}>
      <Switch>
        <Route path="/settings/status/:targetId/history">
          {params => <ServiceStatusHistoryPanel targetId={params.targetId} />}
        </Route>
        <Route path="/settings/status">
          <ServiceStatusPanel />
        </Route>
      </Switch>
    </Router>
  );
}

describe('single-service history', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    api.getMonitorSummary.mockResolvedValue(summary);
    api.getMonitorHistory.mockImplementation(
      (id: string, range: MonitorRange) => Promise.resolve(history(id, range))
    );
    api.getMonitorIncidents.mockImplementation(
      (range: MonitorRange, id: string, page: number, pageSize: number) =>
        Promise.resolve(pageOf(range, id, page, pageSize))
    );
  });
  afterEach(() => {
    Element.prototype.scrollIntoView = originalScrollIntoView;
    cleanup();
    vi.resetAllMocks();
    window.history.replaceState(null, '', '/');
  });

  it('opens from maximize and restores the overview selection on return', async () => {
    mount('/settings/status?target=engine&range=7d');
    fireEvent.click(
      await screen.findByRole('button', { name: '查看 策略引擎 完整历史' })
    );
    expect(
      await screen.findByRole('heading', { name: '策略引擎 历史' })
    ).toBeInTheDocument();
    expect(currentQuery()).toContain('range=7d');
    fireEvent.click(screen.getByRole('button', { name: '服务状态' }));
    expect(
      await screen.findByRole('button', { name: '查看 策略引擎 完整历史' })
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '7 天' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  it('loads only one server page, keeps the cutoff, and supports URL/back navigation', async () => {
    mount();
    expect(
      await screen.findByText('共 45 条 · 第 1 / 3 页')
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole('list', { name: '事故历史列表' })).getAllByRole(
        'listitem'
      )
    ).toHaveLength(20);
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
    const historyCalls = api.getMonitorHistory.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(
      await screen.findByText('共 45 条 · 第 2 / 3 页')
    ).toBeInTheDocument();
    expect(currentQuery()).toContain('page=2');
    expect(api.getMonitorIncidents).toHaveBeenLastCalledWith(
      '24h',
      'qmt-agent',
      2,
      20,
      expect.any(AbortSignal),
      now
    );
    expect(api.getMonitorHistory).toHaveBeenCalledTimes(historyCalls);
    expect(screen.getByRole('heading', { name: '事故历史' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: '第 3 页' }));
    expect(
      await screen.findByText('共 45 条 · 第 3 / 3 页')
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole('list', { name: '事故历史列表' })).getAllByRole(
        'listitem'
      )
    ).toHaveLength(5);
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
    act(() =>
      routing.navigate(routing.history[routing.history.length - 2], {
        replace: true,
      })
    );
    await waitFor(() => expect(currentQuery()).toContain('page=2'));
    expect(
      await screen.findByText('共 45 条 · 第 2 / 3 页')
    ).toBeInTheDocument();
  });

  it('resets pagination for range/size/target changes and labels summary statistics honestly', async () => {
    mount('/settings/status/qmt-agent/history?range=24h&page=2&pageSize=20');
    await screen.findByText('共 45 条 · 第 2 / 3 页');
    fireEvent.click(screen.getByRole('button', { name: '1 年' }));
    expect(
      await screen.findByText('共 45 条 · 第 1 / 3 页')
    ).toBeInTheDocument();
    expect(api.getMonitorIncidents).toHaveBeenLastCalledWith(
      '1y',
      'qmt-agent',
      1,
      20,
      expect.any(AbortSignal),
      undefined
    );
    expect(screen.getByLabelText('近24小时统计')).toHaveTextContent(
      '近 24 小时'
    );
    fireEvent.click(screen.getByRole('combobox', { name: '每页事故数量' }));
    fireEvent.click(await screen.findByRole('option', { name: '10 条' }));
    expect(
      await screen.findByText('共 45 条 · 第 1 / 5 页')
    ).toBeInTheDocument();
    expect(currentQuery()).toContain('pageSize=10');
    fireEvent.click(screen.getByRole('button', { name: '切换到 策略引擎' }));
    expect(
      await screen.findByRole('heading', { name: '策略引擎 历史' })
    ).toBeInTheDocument();
    expect(await screen.findByText('当前范围没有事故记录')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '切换到 策略引擎' })).toHaveClass(
      'border-blue-400/60'
    );
    expect(
      screen.getByRole('button', { name: '切换到 策略引擎' })
    ).toHaveAttribute('aria-current', 'page');
    expect(screen.getByText(/当前范围没有独立延迟样本/)).toBeInTheDocument();
  });

  it('defaults malformed query values and clamps pages after retention removes records', async () => {
    const view = mount(
      '/settings/status/qmt-agent/history?range=invalid&page=-4&pageSize=999'
    );
    await screen.findByText('共 45 条 · 第 1 / 3 页');
    expect(api.getMonitorIncidents).toHaveBeenLastCalledWith(
      '24h',
      'qmt-agent',
      1,
      20,
      expect.any(AbortSignal),
      undefined
    );
    view.unmount();
    mount('/settings/status/qmt-agent/history?range=24h&page=99&pageSize=20');
    await waitFor(() => expect(currentQuery()).toContain('page=3'));
    expect(
      await screen.findByText('共 45 条 · 第 3 / 3 页')
    ).toBeInTheDocument();
  });

  it('shows independent failures and can retry the same query', async () => {
    api.getMonitorIncidents.mockRejectedValueOnce(new Error('offline'));
    api.getMonitorHistory.mockRejectedValueOnce(new Error('offline'));
    mount();
    expect(await screen.findByText('事故记录暂时不可访问')).toBeInTheDocument();
    expect(await screen.findByText('历史曲线暂时不可访问')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新历史' }));
    expect(
      await screen.findByText('共 45 条 · 第 1 / 3 页')
    ).toBeInTheDocument();
  });

  it('does not overwrite a switched metric with a late response', async () => {
    let resolveOld!: (value: MonitorIncidentPage) => void;
    api.getMonitorIncidents.mockImplementationOnce(
      () =>
        new Promise(resolve => {
          resolveOld = resolve;
        })
    );
    mount();
    expect(await screen.findByText('正在加载事故记录…')).toBeInTheDocument();
    const signal: AbortSignal = api.getMonitorIncidents.mock.calls[0][4];
    fireEvent.click(screen.getByRole('button', { name: '切换到 策略引擎' }));
    await screen.findByText('当前范围没有事故记录');
    expect(signal.aborted).toBe(true);
    await act(async () => resolveOld(pageOf('24h', 'qmt-agent', 1, 20)));
    expect(
      screen.queryByText('QMT Agent 尚未完成账户对账')
    ).not.toBeInTheDocument();
  });

  it('reports unknown targets and monitor failures without fabricating current state', async () => {
    const view = mount('/settings/status/missing/history');
    expect(await screen.findByText(/未找到此监测指标/)).toBeInTheDocument();
    view.unmount();
    api.getMonitorSummary.mockRejectedValue(new Error('offline'));
    mount();
    expect(await screen.findByText(/Monitor 当前不可访问/)).toBeInTheDocument();
    expect(
      screen.queryByRole('list', { name: '事故历史列表' })
    ).not.toBeInTheDocument();
  });
});
