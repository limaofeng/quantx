import type {
  FactorCondition,
  FactorDefinition,
  ScreeningCriteria,
} from './types';

export const FACTOR_OPERATOR_LABELS = {
  eq: '等于',
  gte: '大于等于',
  lte: '小于等于',
  gt: '大于',
  lt: '小于',
  between: '区间（含边界）',
} as const;

export function validateFactorConditions(
  conditions: FactorCondition[]
): string | null {
  if (conditions.length > 64) return '最多支持 64 个因子条件';
  for (const condition of conditions) {
    if (
      !condition.factorId ||
      condition.value == null ||
      !Number.isFinite(condition.value)
    )
      return '请填写每个因子条件的有效数值';
    if (
      condition.operator === 'between' &&
      (condition.valueTo == null ||
        !Number.isFinite(condition.valueTo) ||
        condition.valueTo < condition.value)
    )
      return '区间上限必须大于或等于下限';
  }
  return null;
}

export function describeFactorCondition(
  condition: FactorCondition,
  factors: FactorDefinition[]
) {
  const factor = factors.find(item => item.id === condition.factorId);
  const value = condition.value == null ? '未填写' : condition.value;
  return `${factor?.label ?? condition.factorId} ${FACTOR_OPERATOR_LABELS[condition.operator]} ${value}${condition.operator === 'between' ? ` ~ ${condition.valueTo ?? '未填写'}` : ''} ${factor?.unit ?? ''}`.trim();
}

// Ordering is not part of a condition intersection's identity. Preserve zero and
// negative thresholds, and never drop incomplete conditions to find a report.
export function canonicalFactorConditions(conditions: FactorCondition[]) {
  return conditions
    .map(condition => ({
      factorId: condition.factorId,
      operator: condition.operator,
      value: condition.value,
      valueTo:
        condition.operator === 'between' ? (condition.valueTo ?? null) : null,
    }))
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}

export function buildFactorReportRequests(
  criteria: ScreeningCriteria,
  focusFactorId?: string | null
) {
  const conditions = canonicalFactorConditions(criteria.factorConditions ?? []);
  const factorIds = [
    ...new Set([
      ...conditions.map(item => item.factorId),
      ...(focusFactorId ? [focusFactorId] : []),
    ]),
  ].sort();
  const scope = {
    universe: criteria.universe ?? 'STOCK',
    excludeSt: criteria.excludeST !== false,
    includeIndustries: [...(criteria.includeIndustries ?? [])].sort(),
    excludeIndustries: [...(criteria.excludeIndustries ?? [])].sort(),
  };
  return [
    ...factorIds.map(factorId => ({
      ...scope,
      requestId: `single:${factorId}`,
      kind: 'single',
      factorIds: [factorId],
      conditions: [] as FactorCondition[],
    })),
    ...(!validateFactorConditions(conditions) && conditions.length > 0
      ? [
          {
            ...scope,
            requestId: 'joint',
            kind: 'joint',
            factorIds: [
              ...new Set(conditions.map(item => item.factorId)),
            ].sort(),
            conditions,
          },
        ]
      : []),
  ];
}

// Historical labels become transparent editing shortcuts, not scored signals.
export const FACTOR_TEMPLATES: Array<{
  label: string;
  conditions: FactorCondition[];
}> = [
  {
    label: '超跌条件',
    conditions: [
      { factorId: 'price_drop_pct', operator: 'lt', value: -20 },
      { factorId: 'rsi12', operator: 'lt', value: 40 },
      { factorId: 'boll_percent_b', operator: 'lt', value: 0.25 },
    ],
  },
  {
    label: '强势条件',
    conditions: [
      { factorId: 'boll_percent_b', operator: 'gt', value: 0.8 },
      { factorId: 'volume_ratio', operator: 'gt', value: 1.2 },
      { factorId: 'price_drop_pct', operator: 'gt', value: -10 },
    ],
  },
  {
    label: '量比放大',
    conditions: [{ factorId: 'volume_ratio', operator: 'gt', value: 1.5 }],
  },
  {
    label: 'KDJ 金叉',
    conditions: [{ factorId: 'kdj_cross_up', operator: 'eq', value: 1 }],
  },
  {
    label: '均线金叉',
    conditions: [{ factorId: 'ma_cross_up', operator: 'eq', value: 1 }],
  },
  {
    label: 'RSI 低位',
    conditions: [{ factorId: 'rsi12', operator: 'lte', value: 30 }],
  },
  {
    label: 'RSI 高位',
    conditions: [{ factorId: 'rsi12', operator: 'gte', value: 70 }],
  },
  {
    label: '布林下轨附近',
    conditions: [{ factorId: 'boll_near_lower', operator: 'eq', value: 1 }],
  },
  {
    label: '布林上轨附近',
    conditions: [{ factorId: 'boll_near_upper', operator: 'eq', value: 1 }],
  },
];
