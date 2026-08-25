import {
  isConfirmedRegressionCoefficient,
  type RegressionResult,
} from '../model';

import { ResearchEmptyState, ResearchPanel } from './ResearchSurface';

const TERM_LABELS: Record<string, string> = {
  centered_price_position: '中心化价格位置',
  event_return: '事件日收益',
  log_average_amount_20: '20 日平均成交额（对数、中心化）',
  log_amount: '对数成交额',
  log_rvol: '对数 RVOL',
  log_rvol_x_position: 'RVOL × 位置',
  momentum_20: '20 日动量（中心化）',
  momentum_20d: '20 日动量',
  price_position: '价格位置',
  shock_indicator: '异常放量事件',
  shock_position_interaction: '异常放量 × 价格位置',
  volatility_20: '20 日波动率（中心化）',
  volatility_20d: '20 日波动率',
};

function fixed(value: number | null, digits = 4) {
  return value === null ? '—' : value.toFixed(digits);
}

function optionalFixed(value: number | null | undefined, digits = 4) {
  return value === null || value === undefined ? '—' : value.toFixed(digits);
}

export function RegressionTable({ models }: { models: RegressionResult[] }) {
  const rows = models.flatMap(model =>
    model.coefficients.map(coefficient => ({
      ...coefficient,
      dependentVariable: model.dependent_variable,
      horizon: model.horizon,
      nobs: model.nobs,
      returnKind: model.return_kind,
    }))
  );
  const modelWarnings = Array.from(
    new Set(models.flatMap(model => model.warnings))
  );
  const covarianceLabels = Array.from(
    new Set(
      models.map(model =>
        model.covariance === 'two_way_cluster'
          ? '股票/日期双向聚类'
          : model.covariance
      )
    )
  );

  return (
    <ResearchPanel
      title="交互回归"
      description={`日期固定效应；协方差口径：${covarianceLabels.join('、') || '未提供'}。系数仅描述条件相关性。`}
    >
      {rows.length === 0 ? (
        <ResearchEmptyState
          title="样本不足，未估计回归"
          description={
            modelWarnings[0] || '有效样本没有达到当前回归模型的最低估计要求。'
          }
        />
      ) : (
        <div className="max-h-[25rem] overflow-auto">
          <table className="w-full min-w-[900px] text-left text-ui-caption">
            <caption className="sr-only">量价交互回归系数</caption>
            <thead className="sticky top-0 z-10 bg-[#0b1423] text-ui-micro uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-3 py-2.5">周期</th>
                <th className="px-3 py-2.5">收益口径</th>
                <th className="px-3 py-2.5">变量</th>
                <th className="px-3 py-2.5 text-right">系数</th>
                <th className="px-3 py-2.5 text-right">标准误</th>
                <th className="px-3 py-2.5 text-right">p 值</th>
                <th className="px-3 py-2.5 text-right">FDR q</th>
                <th className="px-3 py-2.5 text-right">置信区间</th>
                <th className="px-3 py-2.5">结论</th>
                <th className="px-3 py-2.5 text-right">样本</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.05]">
              {rows.map(row => (
                <tr
                  key={`${row.returnKind}:${row.horizon}:${row.dependentVariable}:${row.term}`}
                  className="transition-colors hover:bg-white/[0.025]"
                >
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono text-slate-400">
                    T+{row.horizon}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-slate-400">
                    {row.returnKind === 'close_response'
                      ? '收盘响应'
                      : '次日开盘'}
                  </td>
                  <td className="px-3 py-2.5 font-semibold text-slate-300">
                    {TERM_LABELS[row.term] || row.term}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono tabular-nums text-slate-200">
                    {fixed(row.estimate)}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono tabular-nums text-slate-400">
                    {fixed(row.std_error)}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono tabular-nums">
                    <span className="text-slate-500">
                      {fixed(row.p_value, 3)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono tabular-nums text-slate-500">
                    {optionalFixed(row.q_value, 3)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-right font-mono tabular-nums text-slate-500">
                    [{fixed(row.ci_low)}, {fixed(row.ci_high)}]
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5">
                    {isConfirmedRegressionCoefficient(row) ? (
                      <span className="font-bold text-emerald-300">已确认</span>
                    ) : (
                      <span className="text-slate-500">
                        {row.q_value === null || row.q_value === undefined
                          ? '未检验'
                          : '未通过 FDR'}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono tabular-nums text-slate-500">
                    {row.nobs.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ResearchPanel>
  );
}
