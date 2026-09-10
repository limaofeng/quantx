import { Button } from '@/components/ui/button';

import { useStockSelectionTrainerStatus } from '../../hooks/useStockSelectionTrainerStatus';

const reasons: Record<string, string> = {
  TRADING_OR_POST_CLOSE_CRITICAL_WINDOW: '实盘保护时段，任务保持排队',
  OUTSIDE_ALLOWED_TRAINING_WINDOW: '等待允许训练的时段',
  TRAINER_DRAINING: '已暂停领取新任务，已有任务继续收尾',
  HOST_MEMORY_RESERVE: '等待主机空闲内存',
  HOST_DISK_RESERVE: '磁盘剩余空间不足',
  HOST_GPU_MEMORY_BUDGET: '显存占用超过预算',
  HOST_GPU_MEMORY_STATE_UNKNOWN: '尚未确认显存状态',
  HOST_POLICY_MISSING_OR_INVALID: '主机资源策略缺失或无效',
  HOST_RESOURCE_STATE_UNKNOWN: '尚未确认主机资源状态',
  CPU_TRAINING_UNAVAILABLE: '等待有效的 CPU 训练能力探测',
  RUNNING_TRAINING_EXISTS: '等待当前训练完成',
  NO_CLAIMABLE_QUEUED_RUN: '暂无待领取训练',
  CLAIM_INTEGRITY_CONFLICT: '领取状态发生变化，等待下一次调度',
  TRAINER_ADMISSION_UNAVAILABLE: '尚未确认任务领取状态',
  PUBLICATION_RETRY_REQUIRED: '计算已结束，等待制品回传重试',
};
const decisions: Record<string, string> = {
  IDLE: '空闲',
  QUEUED: '排队中',
  RUNNING: '执行中',
  SUCCEEDED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  OWNERSHIP_LOST: '执行归属已变化',
};

export function TrainerServiceStatus() {
  const { data, fetching, error, refresh } = useStockSelectionTrainerStatus();
  const updated = data?.updatedAt ? new Date(data.updatedAt).getTime() : NaN;
  const age = Date.now() - updated;
  const fresh = !error && data?.fresh === true && age >= -5000 && age <= 90000;
  const service = !fresh
    ? fetching && !data
      ? '正在读取'
      : '状态未确认'
    : ({ ALIVE: '服务运行中', OFFLINE: '服务已停止', STALE: '服务心跳过期' }[
        data.service
      ] ?? '状态未确认');
  const reason = fresh ? data.resourceReason : null;
  const dispatchText = (value: NonNullable<typeof data>['training']) => {
    if (!fresh || value.state !== 'FRESH') return '等待新的调度状态';
    if (value.reason)
      return reasons[value.reason] ?? '等待条件恢复，请查看运行日志';
    return decisions[value.status ?? ''] ?? '尚无调度结果';
  };
  return (
    <section
      aria-label="独立训练服务"
      className="space-y-ui-group rounded-panel border border-white/10 bg-slate-950/35 p-ui-section"
    >
      <div className="flex items-center justify-between gap-ui-group">
        <h2 className="text-ui-title font-semibold text-slate-100">
          独立训练服务
        </h2>
        <Button
          size="sm"
          variant="outline"
          disabled={fetching}
          onClick={refresh}
        >
          刷新服务状态
        </Button>
      </div>
      <dl className="grid gap-ui-group text-ui-body sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <dt className="text-slate-400">Trainer</dt>
          <dd
            className={
              fresh && data.service === 'ALIVE'
                ? 'text-emerald-300'
                : 'text-amber-300'
            }
          >
            {service}
          </dd>
        </div>
        <div>
          <dt className="text-slate-400">任务领取</dt>
          <dd className="text-slate-200">
            {!fresh
              ? '未确认'
              : data.admission === 'OPEN'
                ? '允许领取'
                : data.admission === 'DRAINING'
                  ? '已暂停领取新任务'
                  : '未确认'}
          </dd>
        </div>
        <div>
          <dt className="text-slate-400">训练调度</dt>
          <dd className="text-slate-200">
            {data ? dispatchText(data.training) : '等待上报'}
          </dd>
        </div>
        <div>
          <dt className="text-slate-400">数据准备调度</dt>
          <dd className="text-slate-200">
            {data ? dispatchText(data.preparation) : '等待上报'}
          </dd>
        </div>
      </dl>
      {reason && (
        <p role="status" className="text-ui-body text-amber-300">
          {reasons[reason] ?? '当前资源条件不允许启动计算，请查看运行日志'}
        </p>
      )}
      <p className="text-ui-caption text-slate-400">
        服务运行状态与 CPU/GPU
        资格分别上报；资源条件不满足时不启动计算，排队任务会保留。
      </p>
      {error && (
        <p role="alert" className="text-ui-body text-amber-300">
          服务状态读取失败，请重试。
        </p>
      )}
    </section>
  );
}
