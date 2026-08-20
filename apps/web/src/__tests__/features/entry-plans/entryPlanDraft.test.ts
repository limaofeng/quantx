import { describe, expect, it } from 'vitest';

import {
  defaultEntryPlanDraft,
  entryPlanDraftSchema,
  getEntryPlanPrimaryActionLabel,
  getEntryPlanSaveAction,
} from '@/features/entry-plans';

describe('entry plan draft model', () => {
  it('requires explicit hard limits and a selected security', () => {
    const result = entryPlanDraftSchema.safeParse(defaultEntryPlanDraft);

    expect(result.success).toBe(false);
    if (result.success) return;

    expect(result.error.flatten().fieldErrors.instrumentCode).toContain(
      '请选择证券主数据中的 A 股标的'
    );
    expect(result.error.flatten().fieldErrors.maxBuyPrice).toContain(
      '最高可买价必须大于 0'
    );
  });

  it('accepts a bounded simulated plan', () => {
    const result = entryPlanDraftSchema.safeParse({
      ...defaultEntryPlanDraft,
      instrumentCode: '605499.SH',
      instrumentName: '东鹏饮料',
      maxBuyPrice: 128,
    });

    expect(result.success).toBe(true);
  });

  it('rejects unsafe live auto swing plans without exit protection', () => {
    const result = entryPlanDraftSchema.safeParse({
      ...defaultEntryPlanDraft,
      bucket: 'swing',
      executionScenario: 'LIVE_AUTO',
      exitProtectionEnabled: false,
      instrumentCode: '605499.SH',
      instrumentName: '东鹏饮料',
      maxBuyPrice: 128,
    });

    expect(result.success).toBe(false);
    if (result.success) return;
    expect(result.error.flatten().fieldErrors.exitProtectionEnabled).toContain(
      '活跃仓实盘自动托管必须配置成交后卖出保护'
    );
  });

  it('requires visible, bounded and unique price ladder levels', () => {
    const result = entryPlanDraftSchema.safeParse({
      ...defaultEntryPlanDraft,
      instrumentCode: '605499.SH',
      instrumentName: '东鹏饮料',
      maxBuyPrice: 128,
      strategy: 'PRICE_LADDER',
      priceLadderLevels: [
        {
          levelId: 'level-1',
          triggerPrice: 129,
          trancheMode: 'AMOUNT',
          trancheAmountCny: 6000,
          trancheVolume: 0,
        },
        {
          levelId: 'level-2',
          triggerPrice: 129,
          trancheMode: 'VOLUME',
          trancheAmountCny: 0,
          trancheVolume: 0,
        },
      ],
    });

    expect(result.success).toBe(false);
    if (result.success) return;
    const errors = result.error.flatten().fieldErrors.priceLadderLevels ?? [];
    expect(errors).toEqual(
      expect.arrayContaining([
        '档位触发价不能高于最高可买价',
        '档位金额不能超过单笔金额上限',
        '每个价格档位必须有不同的价格',
        '股数档位的本档股数必须大于 0',
      ])
    );
  });

  it('rejects an enabled but empty exit protection template', () => {
    const result = entryPlanDraftSchema.safeParse({
      ...defaultEntryPlanDraft,
      instrumentCode: '605499.SH',
      instrumentName: '东鹏饮料',
      maxBuyPrice: 128,
      exitProtectionEnabled: true,
      exitStopPrice: 0,
      exitGrossTakeProfitPct: 0,
      exitTrailingArmProfitPct: 0,
      exitTrailingDrawdownPct: 0,
      exitMaxHoldingDays: 0,
    });

    expect(result.success).toBe(false);
    if (result.success) return;
    expect(result.error.flatten().fieldErrors.exitProtectionEnabled).toContain(
      '启用成交后保护时，至少配置一项保护规则'
    );
  });

  it('maps each visible action to its real controller action', () => {
    expect(getEntryPlanSaveAction('PAPER_AUTO', true)).toBe('SAVE_PAUSED');
    expect(getEntryPlanSaveAction('PAPER_AUTO', false)).toBe('START_PAPER');
    expect(getEntryPlanSaveAction('LIVE_MANUAL', false)).toBe(
      'START_LIVE_MANUAL'
    );
    expect(getEntryPlanSaveAction('LIVE_AUTO', false)).toBe(
      'PREVIEW_LIVE_AUTHORIZATION'
    );
    expect(getEntryPlanPrimaryActionLabel('PAPER_AUTO')).toBe('保存并启动模拟');
    expect(getEntryPlanPrimaryActionLabel('LIVE_MANUAL')).toBe(
      '保存并开始监控'
    );
    expect(getEntryPlanPrimaryActionLabel('LIVE_AUTO')).toBe('预览授权并启动');
  });
});
