export type ManualOrderAttemptPhaseValue =
  | 'QUEUED'
  | 'DELIVERED'
  | 'AGENT_ACKNOWLEDGED'
  | 'BROKER_ORDER_CREATED'
  | 'REJECTED_BEFORE_BROKER'
  | 'EXPIRED_BEFORE_BROKER'
  | 'CANCELLED_BEFORE_BROKER'
  | 'RECONCILE_REQUIRED';

interface ManualOrderPhasePresentation {
  label: string;
  tone: 'danger' | 'info' | 'success' | 'warning';
}

const PHASE_PRESENTATIONS: Record<
  ManualOrderAttemptPhaseValue,
  ManualOrderPhasePresentation
> = {
  AGENT_ACKNOWLEDGED: {
    label: 'Agent 已回执，等待券商委托',
    tone: 'info',
  },
  BROKER_ORDER_CREATED: {
    label: '券商委托已生成',
    tone: 'success',
  },
  CANCELLED_BEFORE_BROKER: {
    label: '未生成券商委托：已取消',
    tone: 'danger',
  },
  DELIVERED: {
    label: '已投递，等待 Agent 回执',
    tone: 'info',
  },
  EXPIRED_BEFORE_BROKER: {
    label: '未生成券商委托：已过期',
    tone: 'danger',
  },
  QUEUED: {
    label: '已排队，等待下发',
    tone: 'warning',
  },
  RECONCILE_REQUIRED: {
    label: '结果待核对，禁止重复下单',
    tone: 'danger',
  },
  REJECTED_BEFORE_BROKER: {
    label: '未生成券商委托：已拒绝',
    tone: 'danger',
  },
};

const TONE_CLASSES: Record<
  ManualOrderPhasePresentation['tone'],
  { badge: string; panel: string; text: string }
> = {
  danger: {
    badge: 'border-destructive/30 bg-destructive/10 text-destructive',
    panel: 'border-destructive/25 bg-destructive/5',
    text: 'text-destructive',
  },
  info: {
    badge: 'border-blue-400/30 bg-blue-500/10 text-blue-200',
    panel: 'border-blue-400/20 bg-blue-500/5',
    text: 'text-blue-200',
  },
  success: {
    badge: 'border-success/30 bg-success/10 text-success',
    panel: 'border-success/20 bg-success/5',
    text: 'text-success',
  },
  warning: {
    badge: 'border-warning/30 bg-warning/10 text-warning',
    panel: 'border-warning/20 bg-warning/5',
    text: 'text-warning',
  },
};

export function normalizeManualOrderPhase(
  value: unknown
): ManualOrderAttemptPhaseValue {
  const normalized = String(value || '')
    .trim()
    .toUpperCase();
  return normalized in PHASE_PRESENTATIONS
    ? (normalized as ManualOrderAttemptPhaseValue)
    : 'RECONCILE_REQUIRED';
}

export function getManualOrderPhasePresentation(value: unknown) {
  const phase = normalizeManualOrderPhase(value);
  const presentation = PHASE_PRESENTATIONS[phase];
  return {
    ...presentation,
    phase,
    classes: TONE_CLASSES[presentation.tone],
  };
}

export function manualOrderPriceTypeLabel(orderType: string) {
  return orderType === 'FIX_PRICE' ? '限价' : '对手方最优';
}
