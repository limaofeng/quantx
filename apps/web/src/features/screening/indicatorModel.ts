import type {
  IndicatorCondition,
  IndicatorDefinition,
  ScreeningCriteria,
} from './types';

export const INDICATOR_OPERATOR_LABELS = {
  eq: '等于',
  gte: '大于等于',
  lte: '小于等于',
  gt: '大于',
  lt: '小于',
  between: '区间（含边界）',
} as const;

export function validateIndicatorConditions(
  conditions: IndicatorCondition[]
): string | null {
  if (conditions.length > 64) return '最多支持 64 个指标条件';
  for (const condition of conditions) {
    if (
      !condition.indicatorId ||
      condition.value == null ||
      !Number.isFinite(condition.value)
    )
      return '请填写每个指标条件的有效数值';
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

export function describeIndicatorCondition(
  condition: IndicatorCondition,
  indicators: IndicatorDefinition[]
) {
  const indicator = indicators.find(item => item.id === condition.indicatorId);
  const value = condition.value == null ? '未填写' : condition.value;
  return `${indicator?.label ?? condition.indicatorId} ${INDICATOR_OPERATOR_LABELS[condition.operator]} ${value}${condition.operator === 'between' ? ` ~ ${condition.valueTo ?? '未填写'}` : ''} ${indicator?.unit ?? ''}`.trim();
}

// Ordering is not part of a condition intersection's identity. Preserve zero and
// negative thresholds, and never drop incomplete conditions to find a report.
export function canonicalIndicatorConditions(conditions: IndicatorCondition[]) {
  return conditions
    .map(condition => ({
      indicatorId: condition.indicatorId,
      operator: condition.operator,
      value: condition.value,
      valueTo:
        condition.operator === 'between' ? (condition.valueTo ?? null) : null,
    }))
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}

export function buildIndicatorReportRequests(
  criteria: ScreeningCriteria,
  focusIndicatorId?: string | null
) {
  const conditions = canonicalIndicatorConditions(
    criteria.indicatorConditions ?? []
  );
  const indicatorIds = [
    ...new Set([
      ...conditions.map(item => item.indicatorId),
      ...(focusIndicatorId ? [focusIndicatorId] : []),
    ]),
  ].sort();
  const scope = {
    universe: criteria.universe ?? 'STOCK',
    excludeSt: criteria.excludeST !== false,
    includeIndustries: [...(criteria.includeIndustries ?? [])].sort(),
    excludeIndustries: [...(criteria.excludeIndustries ?? [])].sort(),
  };
  return [
    ...indicatorIds.map(indicatorId => ({
      ...scope,
      requestId: `single:${indicatorId}`,
      kind: 'single',
      indicatorIds: [indicatorId],
      conditions: [] as IndicatorCondition[],
    })),
    ...(!validateIndicatorConditions(conditions) && conditions.length > 0
      ? [
          {
            ...scope,
            requestId: 'joint',
            kind: 'joint',
            indicatorIds: [
              ...new Set(conditions.map(item => item.indicatorId)),
            ].sort(),
            conditions,
          },
        ]
      : []),
  ];
}

// Historical labels become transparent editing shortcuts, not scored signals.
export const INDICATOR_TEMPLATES: Array<{
  label: string;
  conditions: IndicatorCondition[];
}> = [
  {
    label: '超跌条件',
    conditions: [
      { indicatorId: 'price_drop_pct', operator: 'lt', value: -20 },
      { indicatorId: 'rsi12', operator: 'lt', value: 40 },
      { indicatorId: 'boll_percent_b', operator: 'lt', value: 0.25 },
    ],
  },
  {
    label: '强势条件',
    conditions: [
      { indicatorId: 'boll_percent_b', operator: 'gt', value: 0.8 },
      { indicatorId: 'volume_ratio', operator: 'gt', value: 1.2 },
      { indicatorId: 'price_drop_pct', operator: 'gt', value: -10 },
    ],
  },
  {
    label: '量比放大',
    conditions: [{ indicatorId: 'volume_ratio', operator: 'gt', value: 1.5 }],
  },
  {
    label: 'KDJ 金叉',
    conditions: [{ indicatorId: 'kdj_cross_up', operator: 'eq', value: 1 }],
  },
  {
    label: '均线金叉',
    conditions: [{ indicatorId: 'ma_cross_up', operator: 'eq', value: 1 }],
  },
  {
    label: 'RSI 低位',
    conditions: [{ indicatorId: 'rsi12', operator: 'lte', value: 30 }],
  },
  {
    label: 'RSI 高位',
    conditions: [{ indicatorId: 'rsi12', operator: 'gte', value: 70 }],
  },
  {
    label: '布林下轨附近',
    conditions: [{ indicatorId: 'boll_near_lower', operator: 'eq', value: 1 }],
  },
  {
    label: '布林上轨附近',
    conditions: [{ indicatorId: 'boll_near_upper', operator: 'eq', value: 1 }],
  },
];
