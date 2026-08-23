import { describe, expect, it } from 'vitest';

import {
  isAppliedTTradeGlobalSave,
  isCommandPendingTTradeGlobalSave,
  isPendingTTradeGlobalSave,
  tTradeGlobalSaveToastTitle,
} from './saveOutcome';

describe('T-trade global save outcomes', () => {
  it('keeps a committed-but-pending save non-applied', () => {
    const pending = { success: false, code: 'CONFIG_APPLY_PENDING' };

    expect(isAppliedTTradeGlobalSave(pending)).toBe(false);
    expect(isPendingTTradeGlobalSave(pending)).toBe(true);
    expect(tTradeGlobalSaveToastTitle(pending)).toBe('配置已保存，等待应用');
  });

  it('keeps a command-pending save outcome unknown and retryable', () => {
    const pending = { success: false, code: 'CONFIG_SAVE_COMMAND_PENDING' };

    expect(isAppliedTTradeGlobalSave(pending)).toBe(false);
    expect(isPendingTTradeGlobalSave(pending)).toBe(false);
    expect(isCommandPendingTTradeGlobalSave(pending)).toBe(true);
    expect(tTradeGlobalSaveToastTitle(pending)).toBe(
      '保存请求处理中，结果未知'
    );
  });

  it('only reports the applied state for a real successful response', () => {
    const applied = { success: true, code: 'CONFIG_APPLIED' };

    expect(isAppliedTTradeGlobalSave(applied)).toBe(true);
    expect(tTradeGlobalSaveToastTitle(applied)).toBe('全局做 T 设置已更新');
    expect(
      tTradeGlobalSaveToastTitle({ success: false, code: 'VALIDATION_FAILED' })
    ).toBe('设置未保存');
  });

  it('fails closed for an unknown pending code', () => {
    const unknown = { success: true, code: 'FUTURE_COMMAND_PENDING' };

    expect(isAppliedTTradeGlobalSave(unknown)).toBe(false);
    expect(isPendingTTradeGlobalSave(unknown)).toBe(false);
    expect(isCommandPendingTTradeGlobalSave(unknown)).toBe(false);
    expect(tTradeGlobalSaveToastTitle(unknown)).toBe('设置未保存');
  });
});
