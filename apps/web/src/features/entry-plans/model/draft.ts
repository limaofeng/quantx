import { z } from 'zod';

import type { EntryPlanDraft, EntryPlanSaveAction } from './types';

const instrumentCodePattern = /^\d{6}\.(SH|SZ|BJ)$/;

export const entryPlanDraftSchema = z
  .object({
    planId: z.string().optional(),
    configVersion: z.coerce.number().int().positive().optional(),
    instrumentCode: z
      .string()
      .trim()
      .toUpperCase()
      .regex(instrumentCodePattern, '请选择证券主数据中的 A 股标的'),
    instrumentName: z.string().trim().min(1, '请选择股票'),
    bucket: z.enum(['core', 'swing']),
    targetMode: z.enum([
      'TARGET_POSITION_PCT',
      'INCREMENTAL_AMOUNT_CNY',
      'ADDITIONAL_VOLUME',
    ]),
    targetPositionPct: z.coerce.number().min(0).max(100),
    incrementalAmountCny: z.coerce.number().min(0),
    additionalVolume: z.coerce.number().int().min(0),
    maxTotalAmountCny: z.coerce.number().positive('累计预算必须大于 0'),
    maxPositionPct: z.coerce
      .number()
      .positive('仓位上限必须大于 0')
      .max(100, '仓位上限不能超过 100%'),
    maxBuyPrice: z.coerce.number().positive('最高可买价必须大于 0'),
    strategy: z.enum([
      'TREND_PULLBACK_CONFIRMATION',
      'PRICE_LADDER',
      'MANUAL_TRIGGER',
    ]),
    priceLadderLevels: z.array(
      z.object({
        levelId: z.string().trim().min(1, '档位编号不能为空'),
        triggerPrice: z.coerce.number().positive('触发价必须大于 0'),
        trancheMode: z.enum(['AMOUNT', 'VOLUME']),
        trancheAmountCny: z.coerce.number().min(0),
        trancheVolume: z.coerce.number().int().min(0),
      })
    ),
    preset: z.enum(['CONSERVATIVE', 'BALANCED', 'ACTIVE']),
    trancheCount: z.coerce.number().int().min(1).max(20),
    maxSingleIntentAmountCny: z.coerce.number().positive('单笔上限必须大于 0'),
    maxDailyFilledAmountCny: z.coerce.number().positive('单日上限必须大于 0'),
    minIntervalMinutes: z.coerce.number().int().min(0).max(1440),
    cashBufferPct: z.coerce.number().min(0).max(100),
    executionScenario: z.enum(['PAPER_AUTO', 'LIVE_MANUAL', 'LIVE_AUTO']),
    exitProtectionEnabled: z.boolean(),
    exitStopPrice: z.coerce.number().min(0),
    exitGrossTakeProfitPct: z.coerce.number().min(0).max(100),
    exitTrailingArmProfitPct: z.coerce.number().min(0).max(100),
    exitTrailingDrawdownPct: z.coerce.number().min(0).max(100),
    exitMaxHoldingDays: z.coerce.number().int().min(0).max(10000),
    fastEmaPeriod: z.coerce.number().int().min(2).max(250),
    slowEmaPeriod: z.coerce.number().int().min(3).max(500),
    pullbackPct: z.coerce.number().min(0).max(30),
    reboundPct: z.coerce.number().min(0).max(30),
  })
  .superRefine((draft, context) => {
    const targetValue = {
      ADDITIONAL_VOLUME: draft.additionalVolume,
      INCREMENTAL_AMOUNT_CNY: draft.incrementalAmountCny,
      TARGET_POSITION_PCT: draft.targetPositionPct,
    }[draft.targetMode];

    if (targetValue <= 0) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: '当前目标方式的目标值必须大于 0',
        path: [
          draft.targetMode === 'TARGET_POSITION_PCT'
            ? 'targetPositionPct'
            : draft.targetMode === 'INCREMENTAL_AMOUNT_CNY'
              ? 'incrementalAmountCny'
              : 'additionalVolume',
        ],
      });
    }

    if (draft.maxSingleIntentAmountCny > draft.maxTotalAmountCny) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: '单笔上限不能超过累计预算',
        path: ['maxSingleIntentAmountCny'],
      });
    }

    if (draft.maxDailyFilledAmountCny > draft.maxTotalAmountCny) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: '单日上限不能超过累计预算',
        path: ['maxDailyFilledAmountCny'],
      });
    }

    if (draft.fastEmaPeriod >= draft.slowEmaPeriod) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: '快线周期必须小于慢线周期',
        path: ['fastEmaPeriod'],
      });
    }

    if (
      draft.executionScenario === 'LIVE_AUTO' &&
      draft.bucket === 'swing' &&
      !draft.exitProtectionEnabled
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: '活跃仓实盘自动托管必须配置成交后卖出保护',
        path: ['exitProtectionEnabled'],
      });
    }

    if (draft.strategy === 'PRICE_LADDER') {
      if (draft.priceLadderLevels.length === 0) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: '价格阶梯至少需要一个可见档位',
          path: ['priceLadderLevels'],
        });
      }
      const ids = new Set<string>();
      const prices = new Set<number>();
      draft.priceLadderLevels.forEach((level, index) => {
        if (ids.has(level.levelId) || prices.has(level.triggerPrice)) {
          context.addIssue({
            code: z.ZodIssueCode.custom,
            message: '每个价格档位必须有不同的价格',
            path: ['priceLadderLevels', index, 'triggerPrice'],
          });
        }
        ids.add(level.levelId);
        prices.add(level.triggerPrice);
        if (level.triggerPrice > draft.maxBuyPrice) {
          context.addIssue({
            code: z.ZodIssueCode.custom,
            message: '档位触发价不能高于最高可买价',
            path: ['priceLadderLevels', index, 'triggerPrice'],
          });
        }
        if (level.trancheMode === 'AMOUNT' && level.trancheAmountCny <= 0) {
          context.addIssue({
            code: z.ZodIssueCode.custom,
            message: '金额档位的本档预算必须大于 0',
            path: ['priceLadderLevels', index, 'trancheAmountCny'],
          });
        }
        if (level.trancheMode === 'VOLUME' && level.trancheVolume <= 0) {
          context.addIssue({
            code: z.ZodIssueCode.custom,
            message: '股数档位的本档股数必须大于 0',
            path: ['priceLadderLevels', index, 'trancheVolume'],
          });
        }
        if (
          level.trancheMode === 'AMOUNT' &&
          level.trancheAmountCny > draft.maxSingleIntentAmountCny
        ) {
          context.addIssue({
            code: z.ZodIssueCode.custom,
            message: '档位金额不能超过单笔金额上限',
            path: ['priceLadderLevels', index, 'trancheAmountCny'],
          });
        }
      });
      const ladderBudget = draft.priceLadderLevels.reduce(
        (total, level) =>
          total + (level.trancheMode === 'AMOUNT' ? level.trancheAmountCny : 0),
        0
      );
      if (ladderBudget > draft.maxTotalAmountCny) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: '所有价格档位预算合计不能超过累计预算',
          path: ['priceLadderLevels'],
        });
      }
    }

    if (draft.exitProtectionEnabled) {
      const hasSimpleProtection =
        draft.exitStopPrice > 0 ||
        draft.exitGrossTakeProfitPct > 0 ||
        draft.exitMaxHoldingDays > 0;
      const hasTrailingProtection =
        draft.exitTrailingArmProfitPct > 0 && draft.exitTrailingDrawdownPct > 0;
      if (!hasSimpleProtection && !hasTrailingProtection) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: '启用成交后保护时，至少配置一项保护规则',
          path: ['exitProtectionEnabled'],
        });
      }
      if (
        draft.exitTrailingArmProfitPct > 0 !==
        draft.exitTrailingDrawdownPct > 0
      ) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: '追踪保护的启动收益和回撤幅度必须同时填写',
          path: ['exitTrailingDrawdownPct'],
        });
      }
    }
  });

export const defaultEntryPlanDraft: EntryPlanDraft = {
  instrumentCode: '',
  instrumentName: '',
  bucket: 'core',
  targetMode: 'TARGET_POSITION_PCT',
  targetPositionPct: 20,
  incrementalAmountCny: 20000,
  additionalVolume: 100,
  maxTotalAmountCny: 20000,
  maxPositionPct: 25,
  maxBuyPrice: 0,
  strategy: 'TREND_PULLBACK_CONFIRMATION',
  priceLadderLevels: [],
  preset: 'BALANCED',
  trancheCount: 4,
  maxSingleIntentAmountCny: 5000,
  maxDailyFilledAmountCny: 10000,
  minIntervalMinutes: 30,
  cashBufferPct: 20,
  executionScenario: 'PAPER_AUTO',
  exitProtectionEnabled: true,
  exitStopPrice: 0,
  exitGrossTakeProfitPct: 10,
  exitTrailingArmProfitPct: 8,
  exitTrailingDrawdownPct: 3,
  exitMaxHoldingDays: 0,
  fastEmaPeriod: 10,
  slowEmaPeriod: 30,
  pullbackPct: 2,
  reboundPct: 0.8,
};

export function getEntryPlanSaveAction(
  executionScenario: EntryPlanDraft['executionScenario'],
  paused: boolean
): EntryPlanSaveAction {
  if (paused) return 'SAVE_PAUSED';
  if (executionScenario === 'PAPER_AUTO') return 'START_PAPER';
  if (executionScenario === 'LIVE_MANUAL') return 'START_LIVE_MANUAL';
  return 'PREVIEW_LIVE_AUTHORIZATION';
}

export function getEntryPlanPrimaryActionLabel(
  executionScenario: EntryPlanDraft['executionScenario']
) {
  if (executionScenario === 'PAPER_AUTO') return '保存并启动模拟';
  if (executionScenario === 'LIVE_MANUAL') return '保存并开始监控';
  return '预览授权并启动';
}

export function formatEntryCurrency(value: number) {
  return new Intl.NumberFormat('zh-CN', {
    currency: 'CNY',
    maximumFractionDigits: 0,
    style: 'currency',
  }).format(value);
}

export function formatEntryDateTime(value: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN', { hour12: false });
}
