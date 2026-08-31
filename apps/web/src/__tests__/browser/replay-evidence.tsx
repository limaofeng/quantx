import * as React from 'react';
import { createRoot } from 'react-dom/client';
import { cacheExchange, createClient, fetchExchange, Provider } from 'urql';

import { Button } from '@/components/ui/button';
import { useTTradeReplayEvidence } from '@/features/portfolio/hooks/useTTradeReplayEvidence';
import { TTradeReplayDecisionAudit } from '@/features/portfolio/pages/t-trade-global/TTradeReplayDecisionAudit';
import { TTradeReplaySignals } from '@/features/portfolio/pages/t-trade-global/TTradeReplaySignals';

import { fixtureFetch, type FixtureRequest } from './replay-evidence-fixture';

import '@/index.css';

const names = new Map([
  ['600000.SH', '样例股票甲'],
  ['000001.SZ', '样例股票乙'],
]);

export function EvidenceWorkspace() {
  const [version, setVersion] = React.useState('A');
  const [view, setView] = React.useState('SIGNALS');
  const [density, setDensity] = React.useState('standard');
  const controller = useTTradeReplayEvidence({
    runId: 'browser-fixture',
    backtestId: `sample-version-${version}`,
    activeView: view,
    includeDiagnostics: false,
  });
  React.useEffect(() => {
    // Only this isolated document changes. Do not read/write user preferences.
    document.documentElement.dataset.uiDensity = density;
  }, [density]);
  return (
    <div className="flex min-h-0 flex-1">
      <aside className="flex w-72 shrink-0 flex-col gap-3 border-r border-white/10 p-3">
        <h1 className="text-ui-heading">只读界面验收样例</h1>
        <p className="text-ui-body text-amber-100">
          非业务数据；不连接账户/API，不运行策略或回放。
        </p>
        <p className="text-ui-caption text-slate-400">
          复用正式页面与分页 Hook；传输仅使用内存样例。A 有 125 条信号，B 有 17
          条，另各有 1 条上下文事件。
        </p>
        <nav aria-label="样例版本" className="flex gap-2">
          {['A', 'B'].map(item => (
            <Button
              key={item}
              variant="outline"
              size="sm"
              aria-pressed={version === item}
              onClick={() => setVersion(item)}
            >
              版本 {item}
            </Button>
          ))}
        </nav>
        <div aria-label="样例界面密度" className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            aria-pressed={density === 'standard'}
            onClick={() => setDensity('standard')}
          >
            标准密度
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-pressed={density === 'compact'}
            onClick={() => setDensity('compact')}
          >
            紧凑密度
          </Button>
        </div>
      </aside>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <nav
          aria-label="证据页面"
          className="flex shrink-0 gap-2 border-b border-white/10 p-3"
        >
          <Button
            variant="outline"
            size="sm"
            aria-pressed={view === 'SIGNALS'}
            onClick={() => setView('SIGNALS')}
          >
            信号
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-pressed={view === 'AUDIT'}
            onClick={() => setView('AUDIT')}
          >
            决策审计
          </Button>
        </nav>
        <div className="min-h-0 flex-1">
          {view === 'SIGNALS' ? (
            <TTradeReplaySignals
              controller={controller}
              hasReplay
              instrumentNames={names}
              onViewAudit={eventKey => {
                controller.setAuditFilters({ eventKey });
                setView('AUDIT');
              }}
            />
          ) : (
            <TTradeReplayDecisionAudit
              controller={controller}
              hasReplay
              instrumentNames={names}
              onViewSignal={eventKey => {
                controller.focusSignalEvent(eventKey);
                setView('SIGNALS');
              }}
            />
          )}
        </div>
      </main>
    </div>
  );
}

export function BrowserFixture() {
  const [lastRequest, setLastRequest] = React.useState<FixtureRequest>();
  const [client] = React.useState(() =>
    createClient({
      url: '/__browser_fixture_only__/graphql',
      preferGetMethod: false,
      exchanges: [cacheExchange, fetchExchange],
      fetch: fixtureFetch(setLastRequest),
    })
  );
  return (
    <Provider value={client}>
      <div className="flex h-screen flex-col bg-[#050B16] text-slate-200">
        <EvidenceWorkspace />
        <output
          aria-label="最近样例查询"
          className="shrink-0 break-all border-t border-white/10 p-2 font-mono text-ui-caption text-slate-400"
        >
          {JSON.stringify(lastRequest)}
        </output>
      </div>
    </Provider>
  );
}

if (!import.meta.env.DEV)
  throw new Error('Browser acceptance fixtures are development-only');
const root = document.getElementById('root');
if (!root) throw new Error('Browser acceptance root is missing');
createRoot(root).render(<BrowserFixture />);
