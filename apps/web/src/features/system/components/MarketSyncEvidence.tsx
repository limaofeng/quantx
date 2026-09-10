import { useEffect, useState } from 'react';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import { getAccessToken } from '@/core/auth';

const evidenceSchema = z.object({
  counts: z.array(
    z.object({
      coverage_status: z.string(),
      request_status: z.string().nullable(),
      count: z.number(),
    })
  ),
  items: z.array(
    z.object({
      batch_index: z.number(),
      request_id: z.string(),
      coverage_status: z.string(),
      request_status: z.string().nullable(),
      records_saved: z.string().nullable(),
      scope: z.object({
        stock_list: z.array(z.string()),
        periods: z.array(z.string()),
        start_time: z.string(),
        end_time: z.string(),
      }),
      summary: z.object({ reason: z.string().optional() }),
    })
  ),
});

type Evidence = z.infer<typeof evidenceSchema>;
const requestLabels: Record<string, string> = {
  QUEUED: '等待采集',
  CLAIMED: '采集中',
  UPLOADED: '等待入库',
  COMPLETED: '已处理',
  FAILED: '失败',
  BLOCKED: '已阻塞',
};
const reasonLabels: Record<string, string> = {
  DATA_UNAVAILABLE: '源数据不可用',
  SOURCE_INCOMPLETE: '源数据交付不完整',
  XT_DATA_NO_ROWS: '行情源未返回数据',
  PERSISTED_COVERAGE_UNPROVEN: '缺少持久化覆盖证明',
  SOURCE_COVERAGE_MISSING: '缺少目标分区数据',
  SOURCE_COVERAGE_UNVERIFIED: '目标分区覆盖未验证',
};

export function MarketSyncEvidence({
  runId,
  live,
}: {
  runId: string;
  live: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loadedOffset, setLoadedOffset] = useState(0);
  const [data, setData] = useState<Evidence | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const read = async () => {
      let poll = live;
      try {
        const token = getAccessToken();
        const response = await fetch(
          `/market-data-sync/${encodeURIComponent(runId)}/partitions?offset=${offset}&limit=50`,
          {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            signal: controller.signal,
            cache: 'no-store',
          }
        );
        if (!response.ok) throw new Error('同步证据读取失败');
        const result = evidenceSchema.parse(await response.json());
        poll ||= result.counts.some(
          item =>
            item.request_status !== null &&
            !['COMPLETED', 'FAILED', 'BLOCKED'].includes(item.request_status)
        );
        if (!stopped) {
          setData(result);
          setLoadedOffset(offset);
          setError('');
        }
      } catch {
        if (!stopped) setError('同步证据读取失败，稍后重试');
        poll = true;
      } finally {
        if (!stopped && poll) timer = setTimeout(() => void read(), 15000);
      }
    };
    void read();
    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId, offset, live, refresh]);

  if (!error && !data?.counts.length) return null;
  const total = data?.counts.reduce((sum, item) => sum + item.count, 0) ?? 0;
  const pending =
    data?.counts
      .filter(
        item =>
          item.request_status !== null &&
          !['COMPLETED', 'FAILED', 'BLOCKED'].includes(item.request_status)
      )
      .reduce((sum, item) => sum + item.count, 0) ?? 0;
  const verified =
    data?.counts
      .filter(item => item.coverage_status === 'VERIFIED')
      .reduce((sum, item) => sum + item.count, 0) ?? 0;
  return (
    <details className="border-b border-slate-200 p-ui-section text-ui-label dark:border-slate-800">
      <summary className="cursor-pointer font-medium">
        行情同步证据：已登记 {total} 个分区，覆盖合格 {verified}，后台处理中{' '}
        {pending}
      </summary>
      {error && (
        <p role="alert" className="text-amber-600">
          {error}
        </p>
      )}
      <p className="my-2 text-ui-caption text-slate-500">
        后台请求会在任务结束后继续收敛；请求处理完成与覆盖合格分别展示。
      </p>
      <div className="max-h-64 overflow-auto">
        <table className="w-full text-left text-ui-caption">
          <thead>
            <tr>
              <th>分区</th>
              <th>标的 / 周期 / 日期</th>
              <th>请求状态</th>
              <th>覆盖状态</th>
              <th>记录数</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {loadedOffset === offset &&
              data?.items.map(item => (
                <tr
                  key={item.batch_index}
                  title={`${item.request_id} ${item.summary.reason ?? ''}`}
                >
                  <td>{item.batch_index}</td>
                  <td className="font-mono">
                    {item.scope.stock_list.join(', ')} /{' '}
                    {item.scope.periods.join(', ')} / {item.scope.start_time}–
                    {item.scope.end_time}
                  </td>
                  <td>
                    {item.request_status === null
                      ? '请求不可用'
                      : (requestLabels[item.request_status] ??
                        item.request_status)}
                  </td>
                  <td>
                    {item.coverage_status === 'VERIFIED'
                      ? '合格'
                      : item.coverage_status === 'INCOMPLETE'
                        ? '不完整'
                        : '待校验'}
                  </td>
                  <td>{item.records_saved ?? '--'}</td>
                  <td>
                    {item.summary.reason
                      ? (reasonLabels[item.summary.reason] ??
                        item.summary.reason)
                      : '--'}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => setRefresh(value => value + 1)}
        >
          刷新状态
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={offset === 0}
          onClick={() => setOffset(value => Math.max(0, value - 50))}
        >
          上一页
        </Button>
        <span>
          {Math.floor(offset / 50) + 1} / {Math.max(1, Math.ceil(total / 50))}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={offset + 50 >= total}
          onClick={() => setOffset(value => value + 50)}
        >
          下一页
        </Button>
      </div>
    </details>
  );
}
