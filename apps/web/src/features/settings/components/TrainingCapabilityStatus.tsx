import { useEffect, useState } from 'react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { TrainerServiceStatus } from '@/features/research/components/training/TrainerServiceStatus';
import { useStockSelectionTrainingCapabilities } from '@/features/research/hooks/useStockSelectionTraining';

const gpuDescriptions: Record<string, string> = {
  GPU_AVAILABLE: 'GPU 已通过资格验证',
  GPU_UNAVAILABLE_BUILD: '当前 LightGBM 构建未启用 GPU（OpenCL）支持',
  GPU_UNAVAILABLE_RUNTIME: 'GPU 驱动、OpenCL 运行时或设备不可用',
  GPU_INSUFFICIENT_MEMORY: '可用显存未满足训练要求',
  GPU_UNQUALIFIED: 'GPU 尚未通过资格验证',
};

export function TrainingCapabilityStatus() {
  const { data, error, fetching, refresh } =
    useStockSelectionTrainingCapabilities();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
      refresh();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const updated = data?.updatedAt ? new Date(data.updatedAt).getTime() : NaN;
  const hasHeartbeat = Number.isFinite(updated);
  const age = now - updated;
  const fresh =
    !error &&
    data?.fresh === true &&
    hasHeartbeat &&
    age >= -5000 &&
    age <= 180000;
  const heartbeat = error
    ? '读取失败'
    : fresh
      ? '心跳正常'
      : hasHeartbeat
        ? '心跳已过期'
        : fetching
          ? '正在读取心跳…'
          : '尚无心跳';

  return (
    <>
      <TrainerServiceStatus />
      <section
        aria-labelledby="training-capability-title"
        className="space-y-ui-group rounded-panel border border-white/10 bg-slate-950/35 p-ui-section"
      >
        <div className="flex flex-wrap items-center justify-between gap-ui-group">
          <h2
            id="training-capability-title"
            className="text-ui-title font-semibold text-slate-100"
          >
            CPU / GPU 训练能力心跳
          </h2>
          <Button
            variant="outline"
            size="sm"
            disabled={fetching}
            onClick={() => {
              setNow(Date.now());
              refresh();
            }}
          >
            刷新训练心跳
          </Button>
        </div>
        <dl className="grid gap-ui-group text-ui-body sm:grid-cols-2 xl:grid-cols-4">
          <div>
            <dt className="text-slate-400">探测心跳</dt>
            <dd className={fresh ? 'text-emerald-300' : 'text-amber-300'}>
              {heartbeat}
            </dd>
          </div>
          <div>
            <dt className="text-slate-400">最近成功探测</dt>
            <dd className="text-slate-200">
              {hasHeartbeat
                ? new Date(updated).toLocaleString('zh-CN', {
                    timeZone: 'Asia/Shanghai',
                    hour12: false,
                  })
                : '无记录'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-400">
              {fresh ? 'CPU 训练' : '上次 CPU 探测'}
            </dt>
            <dd className="text-slate-200">
              {data ? (data.cpuAvailable ? '可用' : '未确认可用') : '未知'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-400">
              {fresh ? 'GPU 训练' : '上次 GPU 探测'}
            </dt>
            <dd className="text-slate-200">
              {data ? (gpuDescriptions[data.gpuStatus] ?? '未知') : '未知'}
            </dd>
          </div>
        </dl>
        <p className="text-ui-caption text-slate-400">
          CPU/GPU 共用每分钟一次的训练环境探测，心跳有效期为 180 秒。GPU
          未通过资格验证不代表心跳中断，也不单独阻断 CPU 训练。
        </p>
        {error && (
          <p role="alert" className="text-ui-body text-amber-300">
            训练能力读取失败，请重试；运行组件的监控结果独立展示。
          </p>
        )}
        <Link
          href="/settings/data/research"
          className="inline-flex text-ui-body text-blue-400 hover:underline"
        >
          管理训练数据与 GPU 资格
        </Link>
      </section>
    </>
  );
}
