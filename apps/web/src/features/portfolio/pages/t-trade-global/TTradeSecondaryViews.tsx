import { lazy } from 'react';

// Share lazy component identities between live monitoring and replay without
// importing their editors, charts or logs into the initial monitoring screen.
export const TTradePositionsView = lazy(() =>
  import('./TTradePositionsView').then(module => ({
    default: module.TTradePositionsView,
  }))
);

export const TTradeSignalsView = lazy(() =>
  import('./TTradeSignalsView').then(module => ({
    default: module.TTradeSignalsView,
  }))
);

export const TTradeLiveDecisionAudit = lazy(() =>
  import('./TTradeLiveDecisionAudit').then(module => ({
    default: module.TTradeLiveDecisionAudit,
  }))
);

export const TTradeSignalDiagnosticsPanel = lazy(() =>
  import('./TTradeSignalDiagnostics').then(module => ({
    default: module.TTradeSignalDiagnosticsPanel,
  }))
);

export const TTradeActivityView = lazy(() =>
  import('./TTradeActivityView').then(module => ({
    default: module.TTradeActivityView,
  }))
);

export const TTradeExecutionSettingsPanel = lazy(() =>
  import('./TTradeExecutionSettingsPanel').then(module => ({
    default: module.TTradeExecutionSettingsPanel,
  }))
);

export const TTradeSignalPolicyEditor = lazy(() =>
  import('./TTradeSignalPolicyEditor').then(module => ({
    default: module.TTradeSignalPolicyEditor,
  }))
);

export const TTradeReplaySidebar = lazy(() =>
  import('./TTradeReplaySidebar').then(module => ({
    default: module.TTradeReplaySidebar,
  }))
);

export const TTradeReplayAccountPanel = lazy(() =>
  import('./TTradeReplaySidebar').then(module => ({
    default: module.TTradeReplayAccountPanel,
  }))
);

export const TTradeReplaySettingsEditor = lazy(() =>
  import('./TTradeReplaySettingsPanel').then(module => ({
    default: module.TTradeReplaySettingsEditor,
  }))
);

export const TTradeReplayFrozenSettings = lazy(() =>
  import('./TTradeReplaySettingsPanel').then(module => ({
    default: module.TTradeReplayFrozenSettings,
  }))
);

export const TAssistantPaperPanel = lazy(() =>
  import('./TAssistantPaperPanel').then(module => ({
    default: module.TAssistantPaperPanel,
  }))
);
