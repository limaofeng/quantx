import { readFileSync } from 'node:fs';

const WORKSPACE_SURFACE_CLASS = 'studio-workspace-surface';
const HARDCODED_CANVAS_BACKGROUND = /\bbg-\[#[\da-f]{6}\](?:\/[\d.]+)?/i;

function readSource(relativeUrl: string) {
  return readFileSync(new URL(relativeUrl, import.meta.url), 'utf8');
}

function getClassName(source: string, pattern: RegExp, label: string) {
  const match = source.match(pattern);

  expect(match, `${label} root was not found`).not.toBeNull();
  return match?.[1] ?? '';
}

describe('workspace page surface contract', () => {
  it('defines one semantic workspace surface token and utility', () => {
    const source = readSource('../../../index.css');

    expect(source).toMatch(/--studio-workspace-surface:\s*#050b16;/i);
    expect(source).toMatch(
      /\.studio-workspace-surface\s*\{\s*background-color:\s*var\(--studio-workspace-surface\);\s*\}/
    );
  });

  it('keeps the page-width contract independent from spacing tokens', () => {
    const styleSource = readSource('../../../index.css');
    const layoutSource = readSource('../../../components/ui/studio-layout.tsx');

    expect(styleSource).toMatch(
      /\.studio-content-width\s*\{[^}]*max-width:\s*var\(--content-width-page\);[^}]*\}/s
    );
    expect(layoutSource).toContain('studio-content-width');
    expect(layoutSource).not.toContain('max-w-ui-page');
  });

  it('uses the workspace surface token for the screening page canvas', () => {
    const source = readSource(
      '../../../features/screening/pages/StockScreeningPage.tsx'
    );
    const rootClassName = getClassName(
      source,
      /content=\{\s*<div className="([^"]+)"/,
      'StockScreeningPage content'
    );

    expect(rootClassName).toContain(WORKSPACE_SURFACE_CLASS);
    expect(rootClassName).not.toMatch(HARDCODED_CANVAS_BACKGROUND);
  });

  it.each([
    {
      label: 'ScreeningTopBar',
      path: '../../../features/screening/components/ScreeningTopBar.tsx',
      pattern:
        /export function ScreeningTopBar[\s\S]*?return \(\s*<div className="([^"]+)"/,
    },
    {
      label: 'TTradeHealthConsole',
      path: '../../../features/portfolio/pages/t-trade-global/TTradeLiveMonitor.tsx',
      pattern:
        /export function TTradeHealthConsole[\s\S]*?return \(\s*<aside className="([^"]+)"/,
    },
    {
      label: 'TTradeSignalsView',
      path: '../../../features/portfolio/pages/t-trade-global/TTradeSignalsView.tsx',
      pattern:
        /export function TTradeSignalsView[\s\S]*?return \(\s*<div className="([^"]+)"/,
    },
    {
      label: 'TTradeActivityView',
      path: '../../../features/portfolio/pages/t-trade-global/TTradeActivityView.tsx',
      pattern:
        /export function TTradeActivityView[\s\S]*?return \(\s*<div className="([^"]+)"/,
    },
    {
      label: 'LimitUpBoardHealthConsole',
      path: '../../../features/strategies/components/LimitUpBoardHealthConsole.tsx',
      pattern:
        /export function LimitUpBoardHealthConsole[\s\S]*?return \(\s*<aside[\s\S]*?className="([^"]+)"/,
    },
    {
      label: 'LiquidationPage toolbar',
      path: '../../../features/portfolio/pages/LiquidationPage.tsx',
      pattern: /const toolbar = \(\s*<div className="([^"]+)"/,
    },
    {
      label: 'TTradeGlobalPage replay sidebar',
      path: '../../../features/portfolio/pages/TTradeGlobalPage.tsx',
      pattern: /const replaySidebar = \(\s*<aside className="([^"]+)"/,
    },
    {
      label: 'LimitUpBoardPage replay sidebar',
      path: '../../../features/strategies/pages/LimitUpBoardPage.tsx',
      pattern: /const replaySidebar = \(\s*<aside className="([^"]+)"/,
    },
    {
      label: 'route skeleton content header',
      path: '../../../router/skeletons.tsx',
      pattern:
        /function ContentHeaderSkeleton[\s\S]*?return \(\s*<div className="([^"]+)"/,
    },
  ])('keeps the user-facing $label attached to the workspace surface', item => {
    const rootClassName = getClassName(
      readSource(item.path),
      item.pattern,
      item.label
    );

    expect(rootClassName).toContain(WORKSPACE_SURFACE_CLASS);
    expect(rootClassName).not.toMatch(HARDCODED_CANVAS_BACKGROUND);
  });

  it.each(['monitorView', 'positionsView', 'settingsView'])(
    'uses the workspace surface token for TTradeGlobalPage %s',
    viewName => {
      const source = readSource(
        '../../../features/portfolio/pages/TTradeGlobalPage.tsx'
      );
      const rootClassName = getClassName(
        source,
        new RegExp(`const ${viewName} = \\(\\s*<div className="([^"]+)"`),
        `TTradeGlobalPage ${viewName}`
      );

      expect(rootClassName).toContain(WORKSPACE_SURFACE_CLASS);
      expect(rootClassName).not.toMatch(HARDCODED_CANVAS_BACKGROUND);
    }
  );
});
