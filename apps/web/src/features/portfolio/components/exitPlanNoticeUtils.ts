export type ExitPlanNoticeTone = 'info' | 'warning';

export interface ExitPlanNotice {
  key: string;
  message: string;
  tone: ExitPlanNoticeTone;
}

export interface ExitPlanNoticeSource {
  capacityError?: string | null;
  capacityStatus?: string | null;
  completionNote?: string | null;
  dataQuality?: string | null;
  lastError?: string | null;
  pendingClientOrderId?: string | null;
  pendingIntentId?: string | null;
}

const knownErrorMessages: Record<string, string> = {
  EXIT_INTENT_AWAITING_APPROVAL: '卖出意图等待人工确认',
  NO_LEGAL_SELL_VOLUME: '当前没有合法可卖数量',
  ORPHANED_EXIT_INTENT_RELEASED: '未确认的卖出意图已安全释放',
  WAITING_FOR_T1_SELLABLE_VOLUME: '等待可卖数量在下一交易日释放',
};

function normalizeCode(value?: string | null) {
  return String(value || '')
    .trim()
    .replace(/[.\s-]+/g, '_')
    .toUpperCase();
}

function readableMessage(value?: string | null) {
  const message = String(value || '').trim();
  if (!message) return '';
  const known = knownErrorMessages[normalizeCode(message)];
  if (known) return known;
  return /^[A-Z0-9_.:-]+$/i.test(message)
    ? '计划暂未执行，请查看事件记录'
    : message;
}

export function buildExitPlanNotices(
  source: ExitPlanNoticeSource
): ExitPlanNotice[] {
  const notices: ExitPlanNotice[] = [];
  const messages = new Set<string>();
  const push = (notice: ExitPlanNotice) => {
    if (!notice.message || messages.has(notice.message)) return;
    messages.add(notice.message);
    notices.push(notice);
  };

  if (source.capacityStatus === 'RECONCILE_REQUIRED') {
    push({
      key: 'capacity-reconcile',
      message: source.capacityError || '持仓认领数量需要重新对账',
      tone: 'warning',
    });
  }

  const qualityCode = normalizeCode(source.dataQuality);
  const errorCode = normalizeCode(source.lastError);
  const marketClosed =
    qualityCode === 'MARKET_CLOSED' || errorCode === 'MARKET_CLOSED';
  const streamNotReady = errorCode === 'MARKET_DATA_STREAM_NOT_READY';
  const marketStale =
    qualityCode === 'MARKET_DATA_STALE' || errorCode === 'MARKET_DATA_STALE';

  if (marketClosed) {
    push({
      key: 'market-closed',
      message: '已收盘，等待下一交易时段',
      tone: 'info',
    });
  } else if (streamNotReady) {
    push({
      key: 'market-stream-not-ready',
      message: '实时行情链路未就绪，自动卖出已暂停',
      tone: 'warning',
    });
  } else if (marketStale) {
    push({
      key: 'market-data-stale',
      message: '实时行情超过 10 秒未更新，自动卖出已暂停',
      tone: 'warning',
    });
  } else if (qualityCode && !['GOOD', 'OK'].includes(qualityCode)) {
    push({
      key: 'market-data-unavailable',
      message: '实时行情状态异常，自动卖出已暂停',
      tone: 'warning',
    });
  }

  if (source.pendingIntentId) {
    push({
      key: 'pending-intent',
      message: '卖出意图等待人工确认',
      tone: 'info',
    });
  }
  if (source.pendingClientOrderId) {
    push({
      key: 'pending-order',
      message: `卖单已提交，等待成交回报：${source.pendingClientOrderId}`,
      tone: 'info',
    });
  }

  if (source.lastError && !marketClosed && !streamNotReady && !marketStale) {
    push({
      key: 'plan-error',
      message: readableMessage(source.lastError),
      tone: 'warning',
    });
  }

  const completionMessage = readableMessage(source.completionNote);
  if (completionMessage) {
    push({
      key: 'completion-note',
      message: completionMessage,
      tone: 'info',
    });
  }

  return notices;
}
