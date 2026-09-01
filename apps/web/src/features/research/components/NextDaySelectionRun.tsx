import {
  ArrowLeft,
  CheckCircle2,
  FlaskConical,
  ShieldAlert,
} from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import type { ResearchRunQuery } from '@/generated/gql/graphql';

import { ResearchStatusBadge } from './ResearchStatusBadge';

type Run = NonNullable<ResearchRunQuery['researchRun']>;
type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function at(root: unknown, ...path: string[]): unknown {
  let current = root;
  for (const segment of path) {
    const item = record(current);
    if (!item) return null;
    current = item[segment];
  }
  return current;
}

function numeric(root: unknown, ...path: string[]): number | null {
  const value = at(root, ...path);
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function text(root: unknown, ...path: string[]): string | null {
  const value = at(root, ...path);
  return typeof value === 'string' ? value : null;
}

function percent(value: number | null) {
  return value == null ? '--' : `${(value * 100).toFixed(2)}%`;
}

function decimal(value: number | null) {
  return value == null ? '--' : value.toFixed(4);
}

const GATES = [
  ['brier_skill_positive', 'Brier Skill > 0'],
  ['ece_within_3pct', 'ECE ≤ 3%'],
  ['top20_lift_ci_lower_positive', 'Top20 提升区间下界 > 0'],
  ['historical_universe_complete', '历史股票池完整'],
] as const;

export function NextDaySelectionRun({ run }: { run: Run }) {
  const metrics = record(run.selectionMetrics);
  const quality = record(run.dataQuality);
  const gates = record(metrics?.gates);
  const activeEligible = gates?.active_eligible === true;
  const historical = record(quality?.historical_universe);
  const ranking20 = record(at(metrics, 'frozen_test', 'ranking', 'top_20'));
  const ranking50 = record(at(metrics, 'frozen_test', 'ranking', 'top_50'));

  return (
    <main className="studio-workspace-surface h-full overflow-y-auto text-slate-200">
      <header className="sticky top-0 z-20 border-b border-white/10 bg-background p-ui-section">
        <div className="studio-content-width mx-auto flex flex-wrap items-center gap-3">
          <Button size="icon" variant="outline" asChild>
            <Link href="/research" aria-label="返回研究中心">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <FlaskConical className="h-4 w-4 text-cyan-300" />
          <div className="min-w-0 flex-1">
            <h1 className="text-ui-title font-semibold">
              次日上涨概率模型证据
            </h1>
            <p className="truncate font-mono text-ui-caption text-slate-500">
              {run.summary.version} / {run.summary.runId}
            </p>
          </div>
          <ResearchStatusBadge status={run.summary.status} />
        </div>
      </header>

      <div className="studio-content-width mx-auto space-y-3 p-ui-section">
        <div className="rounded-md border border-cyan-400/20 bg-cyan-400/[0.06] p-3 text-ui-caption text-cyan-100">
          本页只展示冻结测试与发布门禁证据。训练不会自动登记、晋级模型，也不会生成策略、交易意图或订单。
        </div>

        {run.artifactErrors.length > 0 && (
          <p
            role="alert"
            className="rounded-md border border-rose-400/20 p-3 text-ui-caption text-rose-300"
          >
            {run.artifactErrors.join('；')}
          </p>
        )}
        {!metrics && (
          <p
            role="status"
            className="rounded-md border border-amber-400/20 p-3 text-ui-caption text-amber-200"
          >
            当前运行没有通过安全产物校验，不能作为模型登记证据。
          </p>
        )}

        {metrics && (
          <>
            <section
              className="grid grid-cols-2 gap-2 lg:grid-cols-4"
              aria-label="冻结测试摘要"
            >
              {[
                {
                  label: '模型家族',
                  value: text(metrics, 'selected_family') ?? '--',
                },
                {
                  label: 'Brier Skill',
                  value: decimal(
                    numeric(
                      metrics,
                      'frozen_test',
                      'probability',
                      'brier_skill'
                    )
                  ),
                },
                {
                  label: 'ECE',
                  value: percent(
                    numeric(metrics, 'frozen_test', 'probability', 'ece')
                  ),
                },
                {
                  label: 'Top20 CI 下界',
                  value: percent(
                    numeric(
                      metrics,
                      'frozen_test',
                      'ranking',
                      'top_20',
                      'up_rate_lift_ci_low'
                    )
                  ),
                },
              ].map(item => (
                <article
                  key={item.label}
                  className="rounded-md border border-white/[0.08] bg-white/[0.02] p-3"
                >
                  <p className="text-ui-micro font-bold uppercase tracking-wider text-slate-500">
                    {item.label}
                  </p>
                  <p className="mt-2 font-mono text-ui-heading font-black text-slate-100">
                    {item.value}
                  </p>
                </article>
              ))}
            </section>

            <section className="grid gap-3 lg:grid-cols-2">
              <article className="rounded-md border border-white/[0.08] bg-white/[0.02] p-3">
                <h2 className="text-ui-label font-bold text-slate-200">
                  发布门禁
                </h2>
                <div className="mt-3 grid gap-2">
                  {GATES.map(([key, label]) => {
                    const passed = gates?.[key] === true;
                    const Icon = passed ? CheckCircle2 : ShieldAlert;
                    return (
                      <div
                        key={key}
                        className="flex items-center gap-2 text-ui-caption"
                      >
                        <Icon
                          className={
                            passed
                              ? 'h-4 w-4 text-emerald-300'
                              : 'h-4 w-4 text-amber-300'
                          }
                        />
                        <span
                          className={
                            passed ? 'text-slate-300' : 'text-amber-100'
                          }
                        >
                          {label}
                        </span>
                      </div>
                    );
                  })}
                </div>
                <p className="mt-3 border-t border-white/[0.06] pt-3 text-ui-caption text-slate-400">
                  ACTIVE 资格：{' '}
                  <strong
                    className={
                      activeEligible ? 'text-emerald-300' : 'text-amber-300'
                    }
                  >
                    {activeEligible ? '满足' : '不满足，仅可保持 SHADOW'}
                  </strong>
                </p>
              </article>

              <article className="rounded-md border border-white/[0.08] bg-white/[0.02] p-3">
                <h2 className="text-ui-label font-bold text-slate-200">
                  数据与时间切分
                </h2>
                <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-ui-caption">
                  <dt className="text-slate-500">数据区间</dt>
                  <dd className="font-mono text-slate-300">
                    {text(quality, 'data_start') ?? '--'} →{' '}
                    {text(quality, 'data_end') ?? '--'}
                  </dd>
                  <dt className="text-slate-500">冻结测试</dt>
                  <dd className="font-mono text-slate-300">
                    {text(metrics, 'frozen_test', 'start') ?? '--'} →{' '}
                    {text(metrics, 'frozen_test', 'end') ?? '--'}
                  </dd>
                  <dt className="text-slate-500">样本 / 股票 / 日期</dt>
                  <dd className="font-mono text-slate-300">
                    {numeric(quality, 'sample_count')?.toLocaleString() ?? '--'}{' '}
                    /{' '}
                    {numeric(quality, 'stock_count')?.toLocaleString() ?? '--'}{' '}
                    / {numeric(quality, 'date_count')?.toLocaleString() ?? '--'}
                  </dd>
                  <dt className="text-slate-500">历史股票池覆盖</dt>
                  <dd className="font-mono text-slate-300">
                    {historical?.complete === true ? '完整' : '不完整'} ·{' '}
                    {percent(
                      typeof historical?.coverage === 'number'
                        ? historical.coverage
                        : null
                    )}
                  </dd>
                </dl>
              </article>
            </section>

            <section className="overflow-hidden rounded-md border border-white/[0.08] bg-white/[0.02]">
              <div className="border-b border-white/[0.06] p-3">
                <h2 className="text-ui-label font-bold text-slate-200">
                  验证与冻结排名
                </h2>
                <p className="mt-1 text-ui-caption text-slate-500">
                  Walk-forward 共{' '}
                  {numeric(metrics, 'validation', 'fold_count') ?? '--'} 折；
                  Logistic Brier{' '}
                  {decimal(numeric(metrics, 'validation', 'logistic_brier'))}，
                  LightGBM Brier{' '}
                  {decimal(numeric(metrics, 'validation', 'lightgbm_brier'))}。
                </p>
              </div>
              <table className="w-full text-left text-ui-caption">
                <thead className="bg-white/[0.03] text-slate-500">
                  <tr>
                    <th className="px-3 py-2">分组</th>
                    <th className="px-3 py-2 text-right">上涨率</th>
                    <th className="px-3 py-2 text-right">相对全池提升</th>
                    <th className="px-3 py-2 text-right">平均收益提升</th>
                  </tr>
                </thead>
                <tbody className="font-mono text-slate-300">
                  {[
                    ['Top20', ranking20],
                    ['Top50', ranking50],
                  ].map(([label, row]) => (
                    <tr
                      key={String(label)}
                      className="border-t border-white/[0.05]"
                    >
                      <td className="px-3 py-2">{String(label)}</td>
                      <td className="px-3 py-2 text-right">
                        {percent(numeric(row, 'precision'))}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {percent(numeric(row, 'up_rate_lift'))}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {percent(numeric(row, 'mean_return_lift'))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <p className="pb-2 text-center font-mono text-ui-micro text-slate-600">
              登记标识：{run.summary.key}
            </p>
          </>
        )}
      </div>
    </main>
  );
}
