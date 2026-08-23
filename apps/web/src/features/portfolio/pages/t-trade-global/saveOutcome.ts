export interface TTradeGlobalSaveResultLike {
  success?: boolean | null;
  code?: string | null;
}

export const T_TRADE_GLOBAL_SAVE_APPLIED_CODE = 'CONFIG_APPLIED';
export const T_TRADE_GLOBAL_SAVE_PENDING_CODE = 'CONFIG_APPLY_PENDING';
export const T_TRADE_GLOBAL_SAVE_COMMAND_PENDING_CODE =
  'CONFIG_SAVE_COMMAND_PENDING';

export function isAppliedTTradeGlobalSave(
  payload: TTradeGlobalSaveResultLike | null | undefined
): boolean {
  return Boolean(
    payload?.success && payload.code === T_TRADE_GLOBAL_SAVE_APPLIED_CODE
  );
}

export function isPendingTTradeGlobalSave(
  payload: TTradeGlobalSaveResultLike | null | undefined
): boolean {
  return payload?.code === T_TRADE_GLOBAL_SAVE_PENDING_CODE;
}

export function isCommandPendingTTradeGlobalSave(
  payload: TTradeGlobalSaveResultLike | null | undefined
): boolean {
  return payload?.code === T_TRADE_GLOBAL_SAVE_COMMAND_PENDING_CODE;
}

export function tTradeGlobalSaveToastTitle(
  payload: TTradeGlobalSaveResultLike | null | undefined
): string {
  if (isAppliedTTradeGlobalSave(payload)) return '全局做 T 设置已更新';
  if (isPendingTTradeGlobalSave(payload)) return '配置已保存，等待应用';
  if (isCommandPendingTTradeGlobalSave(payload)) {
    return '保存请求处理中，结果未知';
  }
  return '设置未保存';
}
