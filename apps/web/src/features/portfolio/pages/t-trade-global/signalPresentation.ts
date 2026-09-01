import { formatNumber } from './utils';

export function nullableScore(value?: number | null) {
  return value == null || !Number.isFinite(value)
    ? '不可计算'
    : formatNumber(value, 1);
}

export const signalEventTypes = new Set([
  'FSM_TRANSITION',
  'CANDIDATE_LATCHED',
  'CANDIDATE_AWAITING_APPROVAL',
  'CANDIDATE_SUPPRESSED',
  'CANDIDATE_REARMING',
  'CANDIDATE_CLEARED',
  'CANDIDATE_STATE_CHANGED',
  'INTENT_LINKED',
]);

export const signalEventLabels: Readonly<Record<string, string>> = {
  POLICY_CHANGED: '策略配置变更',
  PROFILE_CHANGED: '标的画像变更',
  CONTINUITY_GENERATION_CHANGED: '行情连续性变更',
  FSM_TRANSITION: '形态状态迁移',
  CANDIDATE_LATCHED: '候选已锁存',
  CANDIDATE_AWAITING_APPROVAL: '候选等待确认',
  CANDIDATE_SUPPRESSED: '候选已抑制',
  CANDIDATE_REARMING: '候选等待再武装',
  CANDIDATE_CLEARED: '候选已清除',
  CANDIDATE_STATE_CHANGED: '候选状态变更',
  INTENT_LINKED: '交易意图已关联',
};

export const candidateStatusLabels: Readonly<Record<string, string>> = {
  NONE: '无候选',
  LATCHED: '候选已锁存',
  AWAITING_APPROVAL: '等待人工确认',
  SUPPRESSED: '候选已抑制',
  REARMING: '等待再武装',
};

export const signalPathLabels: Readonly<Record<string, string>> = {
  PULLBACK_REBOUND: '回撤反弹',
  MOMENTUM_ACCELERATION: '早期动量',
};

export const signalPhaseLabels: Readonly<Record<string, string>> = {
  NONE: '暂无主导形态',
  OBSERVING: '观察中',
  PULLBACK_FORMING: '回撤形成',
  LOW_STABILIZING: '低点企稳',
  REBOUND_CONFIRMING: '反弹确认',
  BASELINING: '建立基线',
  MOMENTUM_BUILDING: '动量形成',
  ACCELERATING: '加速确认',
  OVEREXTENDED: '过度延伸',
  CANDIDATE_LATCHED: '候选锁存',
  SUPPRESSED: '已抑制',
  PULLBACK_OBSERVING: '回撤 · 观察',
  PULLBACK_LOW_STABILIZING: '回撤 · 低点企稳',
  PULLBACK_REBOUND_CONFIRMING: '回撤 · 反弹确认',
  PULLBACK_CANDIDATE_LATCHED: '回撤 · 候选锁存',
  PULLBACK_SUPPRESSED: '回撤 · 已抑制',
  MOMENTUM_OBSERVING: '动量 · 观察',
  MOMENTUM_BASELINING: '动量 · 建立基线',
  MOMENTUM_ACCELERATING: '动量 · 加速确认',
  MOMENTUM_OVEREXTENDED: '动量 · 过度延伸',
  MOMENTUM_CANDIDATE_LATCHED: '动量 · 候选锁存',
  MOMENTUM_SUPPRESSED: '动量 · 已抑制',
};

export function signalEventTone(eventType: string) {
  if (eventType === 'CANDIDATE_SUPPRESSED') {
    return 'border-rose-400/20 bg-rose-400/5 text-rose-200';
  }
  if (
    eventType === 'CANDIDATE_AWAITING_APPROVAL' ||
    eventType === 'CANDIDATE_REARMING'
  ) {
    return 'border-amber-400/20 bg-amber-400/5 text-amber-200';
  }
  if (eventType === 'INTENT_LINKED') {
    return 'border-emerald-400/20 bg-emerald-400/5 text-emerald-200';
  }
  return 'border-blue-400/20 bg-blue-400/5 text-blue-200';
}

export function signalCandidateStatusTone(
  candidateStatus: string,
  eventType: string
) {
  if (candidateStatus === 'SUPPRESSED') {
    return 'border-rose-400/20 bg-rose-400/5 text-rose-200';
  }
  if (
    candidateStatus === 'AWAITING_APPROVAL' ||
    candidateStatus === 'REARMING'
  ) {
    return 'border-amber-400/20 bg-amber-400/5 text-amber-200';
  }
  if (candidateStatus === 'NONE') {
    return 'border-white/10 bg-white/[0.03] text-slate-400';
  }
  return signalEventTone(eventType);
}
