export type EntryPlanTab = 'PLANS' | 'PENDING' | 'HISTORY';

export type EntryPlanStatus =
  | 'ARMED'
  | 'ACCUMULATING'
  | 'AWAITING_APPROVAL'
  | 'ENTRY_PENDING'
  | 'PAUSED'
  | 'DRAINING'
  | 'COMPLETED'
  | 'EXPIRED'
  | 'CANCELLED'
  | 'ERROR';

export type EntryPlanBucket = 'core' | 'swing';
export type EntryPlanStrategy =
  'TREND_PULLBACK_CONFIRMATION' | 'PRICE_LADDER' | 'MANUAL_TRIGGER';
export type EntryTargetMode =
  'TARGET_POSITION_PCT' | 'INCREMENTAL_AMOUNT_CNY' | 'ADDITIONAL_VOLUME';
export type EntryExecutionScenario = 'PAPER_AUTO' | 'LIVE_MANUAL' | 'LIVE_AUTO';

export interface EntryPriceLadderDraft {
  levelId: string;
  triggerPrice: number;
  trancheMode: 'AMOUNT' | 'VOLUME';
  trancheAmountCny: number;
  trancheVolume: number;
}

export interface EntryPlanDraft {
  planId?: string;
  configVersion?: number;
  instrumentCode: string;
  instrumentName: string;
  bucket: EntryPlanBucket;
  targetMode: EntryTargetMode;
  targetPositionPct: number;
  incrementalAmountCny: number;
  additionalVolume: number;
  maxTotalAmountCny: number;
  maxPositionPct: number;
  maxBuyPrice: number;
  strategy: EntryPlanStrategy;
  priceLadderLevels: EntryPriceLadderDraft[];
  preset: 'CONSERVATIVE' | 'BALANCED' | 'ACTIVE';
  trancheCount: number;
  maxSingleIntentAmountCny: number;
  maxDailyFilledAmountCny: number;
  minIntervalMinutes: number;
  cashBufferPct: number;
  executionScenario: EntryExecutionScenario;
  exitProtectionEnabled: boolean;
  exitStopPrice: number;
  exitGrossTakeProfitPct: number;
  exitTrailingArmProfitPct: number;
  exitTrailingDrawdownPct: number;
  exitMaxHoldingDays: number;
  fastEmaPeriod: number;
  slowEmaPeriod: number;
  pullbackPct: number;
  reboundPct: number;
}

export interface EntrySecurityOption {
  instrumentCode: string;
  instrumentName: string;
  latestPrice: number | null;
  heldVolume: number;
}

export interface EntryPlanView {
  id: string;
  configVersion?: number;
  instrumentCode: string;
  instrumentName: string;
  bucket: EntryPlanBucket;
  status: EntryPlanStatus;
  strategy: EntryPlanStrategy;
  primaryRuleId?: string;
  currentPositionPct: number;
  currentPositionVolume?: number;
  latestPrice?: number | null;
  targetMode?: EntryTargetMode;
  targetPositionPct: number | null;
  incrementalAmountCny?: number;
  additionalVolume?: number;
  filledAmountCny: number;
  maxTotalAmountCny: number;
  maxPositionPct?: number;
  maxSingleIntentAmountCny?: number;
  maxDailyFilledAmountCny?: number;
  dailyRemainingAmountCny: number;
  maxBuyPrice: number;
  executionScenario?: EntryExecutionScenario;
  authorizationLabel: string;
  lastDecision: string;
  nextEvaluationAt: string | null;
  expiresAt: string | null;
  exitProtectionEnabled: boolean;
  hasWorkingOrder: boolean;
  hasPendingApproval: boolean;
  editableDraft?: Partial<EntryPlanDraft>;
}

export interface PendingEntryIntentView {
  id: string;
  planId: string;
  instrumentCode: string;
  instrumentName: string;
  bucket: EntryPlanBucket;
  strategy: EntryPlanStrategy;
  signalAt: string;
  expiresAt: string;
  referencePrice: number;
  currentAskPrice: number;
  expectedAmountCny: number;
  candidateVolume: number;
  riskAction: string;
  planFilledAmountCny: number;
  dailyFilledAmountCny: number;
  cashBufferPct: number;
}

export type EntryPlanEventKind =
  | 'EVALUATED'
  | 'TRIGGERED'
  | 'APPROVAL_REQUIRED'
  | 'APPROVED'
  | 'REJECTED'
  | 'ORDER_SUBMITTED'
  | 'TRADE_FILLED'
  | 'PAUSED'
  | 'RESUMED'
  | 'AUTHORIZATION_CHANGED';

export interface EntryPlanEventView {
  id: string;
  occurredAt: string;
  instrumentCode: string;
  instrumentName: string;
  kind: EntryPlanEventKind;
  title: string;
  description: string;
  amountCny?: number | null;
  volume?: number | null;
  traceId?: string | null;
}

export interface EntryPlanWorkspaceView {
  availableCashCny: number;
  todayFilledAmountCny: number;
  globalAutoEntryPaused: boolean;
  plans: EntryPlanView[];
  pendingIntents: PendingEntryIntentView[];
  events: EntryPlanEventView[];
  dataUpdatedAt: string | null;
  runtimeMessage: string;
  capabilities?: EntryPlanCapabilitiesView;
}

export interface EntryCapabilityFieldView {
  key: string;
  label: string;
  type: string;
  unit?: string | null;
  required?: boolean;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  helpText?: string | null;
  advanced?: boolean;
}

export interface EntryRulePresetView {
  presetId: string;
  label: string;
  summary: string;
  parameters?: Record<string, unknown>;
}

export interface EntryRuleCapabilityView {
  ruleType: string;
  label: string;
  category: string;
  description: string;
  suitableFor: string;
  warning: string;
  fields?: EntryCapabilityFieldView[];
  presets: EntryRulePresetView[];
}

export interface EntryPlanCapabilitiesView {
  version: string;
  targetModes: Array<{
    value: string;
    label: string;
    description: string;
  }>;
  ruleTypes: EntryRuleCapabilityView[];
}

export type EntryPlanSaveAction =
  | 'SAVE_PAUSED'
  | 'START_PAPER'
  | 'START_LIVE_MANUAL'
  | 'PREVIEW_LIVE_AUTHORIZATION';

export interface EntryPlanController {
  searchSecurities(query: string): Promise<EntrySecurityOption[]>;
  saveDraft(draft: EntryPlanDraft, action: EntryPlanSaveAction): Promise<void>;
  refresh(): Promise<void>;
  setGlobalAutoEntryPaused(paused: boolean): Promise<void>;
  pausePlan(planId: string, cancelWorkingOrder: boolean): Promise<void>;
  resumePlan(planId: string): Promise<void>;
  evaluatePlan(planId: string): Promise<void>;
  triggerManualRule(planId: string, ruleId: string): Promise<void>;
  cancelPlan(planId: string, cancelWorkingOrder?: boolean): Promise<void>;
  previewPendingIntent(intentId: string): Promise<void>;
  rejectPendingIntent(intentId: string): Promise<void>;
}
