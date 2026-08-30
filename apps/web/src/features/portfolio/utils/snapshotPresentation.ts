export type PortfolioSnapshotState =
  'CURRENT' | 'STALE' | 'INCONSISTENT' | 'NO_CACHE';

interface PositionSnapshotLike {
  sequence?: number | string | null;
  reportedAt?: string | null;
  receivedAt?: string | null;
  positionCount?: number | null;
  isComplete?: boolean | null;
  lastError?: string | null;
}

interface ResolvePortfolioSnapshotInput {
  snapshot?: PositionSnapshotLike | null;
  renderedPositionCount: number;
  hasOverview: boolean;
  queryError?: string | null;
}

export interface PortfolioSnapshotPresentation {
  state: PortfolioSnapshotState;
  canTrade: boolean;
  isAuthoritativeEmpty: boolean;
  lastSuccessfulSyncAt?: string;
  message: string;
}

export function resolvePortfolioSnapshotPresentation({
  snapshot,
  renderedPositionCount,
  hasOverview,
  queryError,
}: ResolvePortfolioSnapshotInput): PortfolioSnapshotPresentation {
  if (!hasOverview || !snapshot) {
    return {
      state: 'NO_CACHE',
      canTrade: false,
      isAuthoritativeEmpty: false,
      message: queryError
        ? `账户数据请求失败，且没有可展示的成功快照：${queryError}`
        : '尚无成功同步的账户快照，不能据此判断资金或持仓为零。',
    };
  }

  const lastSuccessfulSyncAt =
    snapshot.receivedAt || snapshot.reportedAt || undefined;
  const snapshotSequence = Number(snapshot.sequence ?? 0);
  const expectedPositionCount = Number(snapshot.positionCount ?? -1);
  if (
    snapshot.isComplete === true &&
    (!Number.isFinite(snapshotSequence) ||
      snapshotSequence <= 0 ||
      !lastSuccessfulSyncAt ||
      expectedPositionCount !== renderedPositionCount)
  ) {
    const identityInvalid =
      !Number.isFinite(snapshotSequence) ||
      snapshotSequence <= 0 ||
      !lastSuccessfulSyncAt;
    return {
      state: 'INCONSISTENT',
      canTrade: false,
      isAuthoritativeEmpty: false,
      lastSuccessfulSyncAt,
      message: identityInvalid
        ? '持仓快照缺少有效序列或成功同步时间；已禁止按真实账户数据处理。'
        : `持仓快照计数为 ${expectedPositionCount}，但读取到 ${renderedPositionCount} 条记录；已禁止按空仓处理。`,
    };
  }

  if (queryError || snapshot.isComplete !== true || snapshot.lastError) {
    const reason = snapshot.lastError || queryError || '实时账户同步当前不可用';
    return {
      state: 'STALE',
      canTrade: false,
      isAuthoritativeEmpty: false,
      lastSuccessfulSyncAt,
      message: `当前不可交易；页面保留最近一次成功同步的数据。${reason}`,
    };
  }

  return {
    state: 'CURRENT',
    canTrade: true,
    isAuthoritativeEmpty:
      expectedPositionCount === 0 && renderedPositionCount === 0,
    lastSuccessfulSyncAt,
    message: '账户数据来自已接受的完整券商快照。',
  };
}
