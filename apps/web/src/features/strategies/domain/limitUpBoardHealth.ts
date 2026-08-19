export type LimitUpBoardHealthTone = 'healthy' | 'warning' | 'error';

export type LimitUpBoardHealthItemTone = LimitUpBoardHealthTone | 'neutral';

export type LimitUpBoardHealthItemId =
  'radar' | 'assistant' | 'projection' | 'entry-gate' | 'coverage' | 'exits';

export interface LimitUpBoardHealthInput {
  marketSessionPhase?: string | null;
  radar: {
    scannerRunning: boolean;
    updating: boolean;
    staleCount: number;
    warnings: readonly string[];
  };
  assistant: {
    enabled: boolean;
    promotionModelMode?: string | null;
    runStatus?: string | null;
    reconcileStatus?: string | null;
    projectionVersion?: string | number | null;
    projectionGeneratedAt?: string | null;
    lastError?: string | null;
    canApprove?: boolean | null;
    killSwitch?: boolean | null;
    blockedReasons?: readonly string[] | null;
    monitoredCount?: number | null;
    pendingSignalCount?: number | null;
    activeExitPlanCount?: number | null;
  };
  exitPlanErrorCount: number;
}

export interface LimitUpBoardHealthItem {
  id: LimitUpBoardHealthItemId;
  label: string;
  value: string;
  detail: string;
  tone: LimitUpBoardHealthItemTone;
}

export interface LimitUpBoardHealthSummary {
  tone: LimitUpBoardHealthTone;
  items: LimitUpBoardHealthItem[];
}

const ACTIVE_MARKET_PHASES = new Set(['call-auction', 'morning', 'afternoon']);

const ERROR_RUN_STATUSES = new Set(['ERROR', 'FAILED']);
const HEALTHY_RUN_STATUSES = new Set(['RUNNING', 'READY']);
const HEALTHY_RECONCILE_STATUSES = new Set([
  'COMPLETED',
  'HEALTHY',
  'OK',
  'READY',
]);

function normalizedStatus(value?: string | null) {
  return String(value || '')
    .trim()
    .toUpperCase();
}

function safeCount(value?: number | null) {
  return Number.isFinite(value) ? Math.max(0, Number(value)) : 0;
}

function hasProjection(value?: string | number | null) {
  const normalized = String(value ?? '').trim();
  return normalized !== '' && normalized !== '0';
}

function deriveOverallTone(items: readonly LimitUpBoardHealthItem[]) {
  if (items.some(item => item.tone === 'error')) return 'error';
  if (items.some(item => item.tone === 'warning')) return 'warning';
  return 'healthy';
}

export function deriveLimitUpBoardHealth(
  input: LimitUpBoardHealthInput
): LimitUpBoardHealthSummary {
  const marketOpen = ACTIVE_MARKET_PHASES.has(input.marketSessionPhase || '');
  const staleCount = safeCount(input.radar.staleCount);
  const warnings = input.radar.warnings.filter(Boolean);
  const enabled = input.assistant.enabled;
  const runStatus = normalizedStatus(input.assistant.runStatus) || 'UNKNOWN';
  const modelMode =
    normalizedStatus(input.assistant.promotionModelMode) || 'UNKNOWN';
  const reconcileStatus =
    normalizedStatus(input.assistant.reconcileStatus) || 'UNKNOWN';
  const monitoredCount = safeCount(input.assistant.monitoredCount);
  const pendingSignalCount = safeCount(input.assistant.pendingSignalCount);
  const activeExitPlanCount = safeCount(input.assistant.activeExitPlanCount);
  const exitPlanErrorCount = safeCount(input.exitPlanErrorCount);
  const blockedReasons = (input.assistant.blockedReasons || []).filter(Boolean);

  let radarTone: LimitUpBoardHealthItemTone = 'healthy';
  let radarValue = input.radar.scannerRunning ? '实时扫描' : '扫描已停';
  let radarDetail = staleCount ? `${staleCount} 条报价陈旧` : '候选报价新鲜';
  if (marketOpen && !input.radar.scannerRunning && !input.radar.updating) {
    radarTone = 'error';
    radarDetail = '交易时段内雷达未运行';
  } else if (warnings.length > 0) {
    radarTone = 'warning';
    radarValue = `${warnings.length} 项提示`;
    radarDetail = warnings[0];
  } else if (marketOpen && staleCount > 0) {
    radarTone = 'warning';
  } else if (input.radar.updating) {
    radarTone = 'neutral';
    radarValue = '正在更新';
    radarDetail = '正在读取最新候选快照';
  } else if (!marketOpen) {
    radarTone = 'neutral';
    radarValue = staleCount > 0 ? '保留快照' : '非交易时段';
    radarDetail =
      staleCount > 0 ? '休市后的陈旧报价属于预期状态' : '等待下一交易时段';
  }

  let assistantTone: LimitUpBoardHealthItemTone = 'healthy';
  let assistantValue = enabled ? runStatus : '已停用';
  let assistantDetail = enabled
    ? `晋级模型 ${modelMode}`
    : '未运行自动晋级助手';
  if (!enabled) {
    assistantTone = 'neutral';
  } else if (input.assistant.lastError || ERROR_RUN_STATUSES.has(runStatus)) {
    assistantTone = 'error';
    assistantValue = '运行异常';
    assistantDetail = input.assistant.lastError || `运行状态 ${runStatus}`;
  } else if (!HEALTHY_RUN_STATUSES.has(runStatus)) {
    assistantTone = 'warning';
    assistantDetail = `运行状态 ${runStatus}`;
  } else if (modelMode === 'SHADOW') {
    assistantTone = 'neutral';
    assistantValue = '影子观察';
    assistantDetail = '模型仅记录判断，不改变交易资格';
  }

  const projectionReady =
    hasProjection(input.assistant.projectionVersion) &&
    Boolean(input.assistant.projectionGeneratedAt);
  let projectionTone: LimitUpBoardHealthItemTone = 'healthy';
  const projectionValue = projectionReady
    ? `v${String(input.assistant.projectionVersion)}`
    : '等待投影';
  let projectionDetail = `对账 ${reconcileStatus}`;
  if (!enabled) {
    projectionTone = 'neutral';
    projectionDetail = '助手停用时不要求刷新运行投影';
  } else if (
    !projectionReady ||
    !HEALTHY_RECONCILE_STATUSES.has(reconcileStatus)
  ) {
    projectionTone = 'warning';
    projectionDetail = !projectionReady
      ? '尚未生成可用的助手投影'
      : `对账状态 ${reconcileStatus}`;
  }

  let entryTone: LimitUpBoardHealthItemTone = 'healthy';
  let entryValue = input.assistant.canApprove ? '允许确认' : '确认受限';
  let entryDetail = blockedReasons[0] || '当前没有业务阻断项';
  if (pendingSignalCount > 0 && !input.assistant.canApprove) {
    entryTone = 'error';
    entryValue = `${pendingSignalCount} 条待确认受阻`;
    entryDetail = blockedReasons[0] || '存在待确认信号，但当前不能批准';
  } else if (enabled && input.assistant.killSwitch) {
    entryTone = 'error';
    entryValue = '紧急停止';
    entryDetail = blockedReasons[0] || '业务熔断已阻止新确认';
  } else if (!enabled) {
    entryTone = 'neutral';
    entryValue =
      pendingSignalCount > 0 ? `${pendingSignalCount} 条待处理` : '未启用';
    entryDetail = '助手停用，不要求开放买入确认';
  } else if (pendingSignalCount > 0) {
    entryTone = 'warning';
    entryValue = `${pendingSignalCount} 条待确认`;
    entryDetail = '需要在有效期内人工处理';
  } else if (!marketOpen && !input.assistant.canApprove) {
    entryTone = 'neutral';
    entryDetail = '非交易时段关闭买入确认属于预期状态';
  } else if (!input.assistant.canApprove || blockedReasons.length > 0) {
    entryTone = 'warning';
  }

  let coverageTone: LimitUpBoardHealthItemTone = 'healthy';
  let coverageValue = `${monitoredCount} 只监控`;
  let coverageDetail = `待确认 ${pendingSignalCount} · 退出托管 ${activeExitPlanCount}`;
  if (!enabled) {
    coverageTone = 'neutral';
    coverageValue = '监控已停';
  } else if (marketOpen && monitoredCount === 0) {
    coverageTone = 'warning';
    coverageDetail = '交易时段内没有首板监控标的';
  } else if (!marketOpen && monitoredCount === 0) {
    coverageTone = 'neutral';
    coverageDetail = '非交易时段没有监控标的属于正常状态';
  }

  const exitsTone: LimitUpBoardHealthItemTone =
    exitPlanErrorCount > 0 ? 'error' : 'healthy';
  const exitsValue =
    exitPlanErrorCount > 0
      ? `${exitPlanErrorCount} 项异常`
      : `${activeExitPlanCount} 项托管`;
  const exitsDetail =
    exitPlanErrorCount > 0
      ? '存在需要人工处置的退出计划'
      : activeExitPlanCount > 0
        ? '退出计划由 Engine 持续托管'
        : '当前没有退出异常';

  const items: LimitUpBoardHealthItem[] = [
    {
      id: 'radar',
      label: '候选雷达',
      value: radarValue,
      detail: radarDetail,
      tone: radarTone,
    },
    {
      id: 'assistant',
      label: '晋级助手',
      value: assistantValue,
      detail: assistantDetail,
      tone: assistantTone,
    },
    {
      id: 'projection',
      label: '业务投影',
      value: projectionValue,
      detail: projectionDetail,
      tone: projectionTone,
    },
    {
      id: 'entry-gate',
      label: '确认门禁',
      value: entryValue,
      detail: entryDetail,
      tone: entryTone,
    },
    {
      id: 'coverage',
      label: '监控负载',
      value: coverageValue,
      detail: coverageDetail,
      tone: coverageTone,
    },
    {
      id: 'exits',
      label: '退出托管',
      value: exitsValue,
      detail: exitsDetail,
      tone: exitsTone,
    },
  ];

  return { tone: deriveOverallTone(items), items };
}
