import { readFileSync } from 'node:fs';

const SOURCE_PATH = '../../../features/portfolio/pages/TTradeGlobalPage.tsx';

function sourceSection(source: string, startToken: string, endToken: string) {
  const start = source.indexOf(startToken);
  expect(
    start,
    `Could not find toolbar section starting with ${startToken}`
  ).toBeGreaterThanOrEqual(0);

  const end = source.indexOf(endToken, start);
  expect(
    end,
    `Could not find toolbar section ending with ${endToken}`
  ).toBeGreaterThan(start);

  return source.slice(start, end);
}

function readToolbarSource() {
  const source = readFileSync(new URL(SOURCE_PATH, import.meta.url), 'utf8');

  return sourceSection(
    source,
    '  const toolbar = (',
    '  const monitorView = ('
  );
}

describe('TTradeGlobalPage toolbar color contract', () => {
  it('uses blue interaction classes for the 实时监控 workspace button while preserving replay cyan', () => {
    const toolbar = readToolbarSource();
    const workspaceModes = sourceSection(
      toolbar,
      "{(['REALTIME', 'REPLAY'] as const).map(mode => {",
      "{workspaceMode === 'REALTIME' && ("
    );

    expect(workspaceModes).toContain(
      "'text-blue-200 after:bg-blue-400 focus-visible:ring-blue-400/70'"
    );
    expect(workspaceModes).not.toMatch(
      /text-red-200 after:bg-red-400|focus-visible:ring-red-500\/60/
    );
    expect(workspaceModes).toContain(
      "'text-cyan-200 after:bg-cyan-400 focus-visible:ring-cyan-400/60'"
    );
  });

  it('uses blue interaction classes for every realtime subview button', () => {
    const source = readFileSync(new URL(SOURCE_PATH, import.meta.url), 'utf8');
    const toolbar = readToolbarSource();
    const realtimeSubviews = sourceSection(
      toolbar,
      '{tTradeModes.map(mode => {',
      '            })}'
    );

    for (const label of [
      '总览',
      '信号',
      '诊断',
      '做T仓位',
      '运行动态',
      '参数',
    ]) {
      expect(source).toContain(`label: '${label}'`);
    }

    expect(realtimeSubviews).toContain('focus-visible:ring-blue-400/70');
    expect(realtimeSubviews).toContain("? 'text-blue-200 after:bg-blue-400'");
    expect(realtimeSubviews).not.toMatch(
      /(?:text|after:bg|focus-visible:ring)-red-/
    );
  });
});
