import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { IndicatorReportQuery } from '@/generated/gql/graphql';
import { financialToneClass } from '@/shared/utils/financialColors';

import {
  formatResearchRate,
  indicatorGroupLabel,
} from '../indicatorPresentation';

type Report = NonNullable<IndicatorReportQuery['indicatorReport']>;
type Row = Report['rows'][number];

const confidenceConfigSchema = z.object({
  statistics: z.object({ confidence_level: z.number().finite().gt(0).lt(1) }),
});

function JsonDetails({ title, data }: { title: string; data: unknown }) {
  return (
    <details className="rounded-md border border-white/10 p-3">
      <summary className="cursor-pointer text-ui-label font-medium text-slate-300 focus-visible:outline-blue-400">
        {title}
      </summary>
      <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words font-mono text-ui-caption text-slate-400">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}

const definitionsSchema = z.array(
  z.object({
    id: z.string(),
    label: z.string(),
    description: z.string(),
    lookback: z.number(),
    unit: z.string(),
    version: z.string(),
  })
);
const coverageLabels: Record<string, string> = {
  sample_count: '候选股票日',
  valid_count: '指标有效股票日',
  missing_count: '指标缺失股票日',
  stock_count: '股票数',
  date_count: '交易日期数',
  restricted_universe: '限定 / 非全市场股票池',
};

function ReportEvidence({ report }: { report: Report }) {
  const definitions = definitionsSchema.safeParse(report.definitions);
  const coverage = z
    .record(z.union([z.number(), z.boolean(), z.string()]))
    .safeParse(report.coverage);
  return (
    <div className="space-y-3">
      {definitions.success && (
        <section className="rounded-md border border-white/10 p-3">
          <h2 className="mb-2 text-ui-title font-medium">指标定义与计算口径</h2>
          {definitions.data.map(indicator => (
            <div key={indicator.id} className="mb-2">
              <h3 className="text-ui-label text-slate-200">
                {indicator.label}
              </h3>
              <p className="text-ui-label text-slate-300">
                {indicator.description}
              </p>
              <p className="font-mono text-ui-caption text-slate-400">
                {indicator.id} · {indicator.version} · {indicator.lookback} 日 ·{' '}
                {indicator.unit}
              </p>
            </div>
          ))}
        </section>
      )}
      {coverage.success && (
        <section className="rounded-md border border-white/10 p-3">
          <h2 className="mb-2 text-ui-title font-medium">样本覆盖</h2>
          <dl className="grid grid-cols-2 gap-2">
            {Object.entries(coverage.data).map(([key, value]) => (
              <div key={key}>
                <dt className="text-ui-label text-slate-400">
                  {coverageLabels[key] ?? key}
                </dt>
                <dd className="font-mono text-ui-body text-slate-200">
                  {typeof value === 'number'
                    ? value.toLocaleString()
                    : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        <JsonDetails title="本报告条件（AND）" data={report.conditions} />
        <JsonDetails title="股票池及历史边界" data={report.universe} />
        <JsonDetails title="完整样本覆盖与缺失" data={report.coverage} />
        <JsonDetails
          title="分布与分组（相同值不拆分）"
          data={report.distribution}
        />
      </div>
      <JsonDetails
        title="冻结研究配置（不含本地输出目录）"
        data={report.configJson}
      />
      {report.configJson != null && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            const url = URL.createObjectURL(
              new Blob([JSON.stringify(report.configJson, null, 2)], {
                type: 'application/json;charset=utf-8',
              })
            );
            const link = document.createElement('a');
            link.href = url;
            link.download = 'indicator-study-resolved.json';
            link.click();
            URL.revokeObjectURL(url);
          }}
        >
          下载冻结配置
        </Button>
      )}
    </div>
  );
}

function Inference({ row }: { row: Row }) {
  if (row.group === 'baseline')
    return <span className="text-slate-400">基准</span>;
  const noInference = row.ciLow == null || row.ciHigh == null;
  return (
    <span
      className={noInference ? 'text-amber-200' : 'text-slate-400'}
      title={row.inferenceStatus}
    >
      {noInference
        ? '描述统计 / 推断不足'
        : `q=${row.qValue?.toFixed(3) ?? '—'}`}
    </span>
  );
}

export function IndicatorReportView({
  report,
  compact = false,
}: {
  report: Report;
  compact?: boolean;
}) {
  const [horizon, setHorizon] = useState(1);
  const [basis, setBasis] = useState('close');
  const [period, setPeriod] = useState('all');
  const confidenceConfig = confidenceConfigSchema.safeParse(report.configJson);
  const confidenceLabel = confidenceConfig.success
    ? `${Number((confidenceConfig.data.statistics.confidence_level * 100).toFixed(2))}% 区间`
    : '置信区间';
  const groups = [...new Set(report.rows.map(row => row.group))];
  const [chosenGroup, setChosenGroup] = useState('');
  const group = groups.includes(chosenGroup)
    ? chosenGroup
    : groups.includes('joint')
      ? 'joint'
      : (groups.find(item => item !== 'baseline') ?? 'baseline');
  const periods = [...new Set(report.rows.map(row => row.period))];
  const activeHorizon = report.horizons.includes(horizon)
    ? horizon
    : (report.horizons[0] ?? 1);
  const activeBasis = report.returnBases.includes(basis)
    ? basis
    : (report.returnBases[0] ?? 'close');
  const activePeriod = periods.includes(period)
    ? period
    : (periods[0] ?? 'all');
  const rows = useMemo(
    () =>
      report.rows.filter(
        row =>
          row.horizon === activeHorizon &&
          row.returnBasis === activeBasis &&
          row.period === activePeriod
      ),
    [report.rows, activeHorizon, activeBasis, activePeriod]
  );
  const curve = useMemo(
    () =>
      report.rows
        .filter(
          row =>
            row.group === group &&
            row.returnBasis === activeBasis &&
            row.period === activePeriod
        )
        .sort((a, b) => a.horizon - b.horizon),
    [report.rows, group, activeBasis, activePeriod]
  );

  return (
    <div className="space-y-3 text-ui-body text-slate-200">
      <div className="rounded-md border border-blue-400/20 bg-blue-500/5 p-3 text-ui-label text-slate-300">
        这是历史样本的价格表现，不是个股预测概率，也不是满足交易约束、扣除费用后的交易收益。置信区间对应日期配对的上涨比例差。
      </div>
      <div className="flex flex-wrap gap-2 text-ui-caption text-slate-400">
        <span>
          数据 {report.reference.dataStart ?? '—'} →{' '}
          {report.reference.dataEnd ?? '—'}
        </span>
        <span>指标版本 {report.indicatorVersion}</span>
        <span>运行 {report.reference.runId}</span>
      </div>
      {[...report.artifactErrors, ...report.warnings].length > 0 && (
        <div
          role="status"
          className="space-y-1 rounded-md border border-amber-400/20 bg-amber-500/5 p-3 text-ui-label text-amber-200"
        >
          {[...new Set([...report.artifactErrors, ...report.warnings])].map(
            warning => (
              <p key={warning}>{warning}</p>
            )
          )}
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <label className="mb-1 block text-ui-label text-slate-400">
            价格起点
          </label>
          <Select value={activeBasis} onValueChange={setBasis}>
            <SelectTrigger aria-label="价格起点">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {report.returnBases.map(item => (
                <SelectItem key={item} value={item}>
                  {item === 'close'
                    ? '当日收盘 → 第 h 日收盘'
                    : '次日开盘 → 第 h 日收盘'}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-ui-label text-slate-400">
            样本区间
          </label>
          <Select value={activePeriod} onValueChange={setPeriod}>
            <SelectTrigger aria-label="样本区间">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {periods.map(item => (
                <SelectItem key={item} value={item}>
                  {item === 'all'
                    ? '全部历史'
                    : item === 'latest_year'
                      ? '最近一年（稳定性检查）'
                      : `${item} 年`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-ui-label text-slate-400">
            曲线分组
          </label>
          <Select value={group} onValueChange={setChosenGroup}>
            <SelectTrigger aria-label="曲线分组">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {groups.map(item => (
                <SelectItem key={item} value={item}>
                  {indicatorGroupLabel(
                    item,
                    report.conditions,
                    report.definitions
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <div
        className="flex flex-wrap items-center gap-1"
        role="group"
        aria-label="后续交易日"
      >
        <span className="mr-2 text-ui-label text-slate-400">观察周期</span>
        {report.horizons.map(day => (
          <Button
            key={day}
            size="sm"
            variant={day === activeHorizon ? 'default' : 'outline'}
            aria-pressed={day === activeHorizon}
            onClick={() => setHorizon(day)}
          >
            {day} 日
          </Button>
        ))}
      </div>
      <div
        className="h-60 min-w-0 rounded-md border border-white/10 p-2"
        role="img"
        aria-label={`${indicatorGroupLabel(group, report.conditions, report.definitions)}未来各交易日日期等权上涨比例与基准曲线`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={curve}>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="currentColor"
              opacity={0.1}
            />
            <XAxis dataKey="horizon" tick={{ fill: '#94a3b8', fontSize: 11 }} />
            <YAxis
              domain={[0, 1]}
              tickFormatter={value => `${Math.round(Number(value) * 100)}%`}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
              width={45}
            />
            <Tooltip
              formatter={value =>
                formatResearchRate(typeof value === 'number' ? value : null)
              }
              contentStyle={{
                background: 'var(--card)',
                border: '1px solid var(--border)',
              }}
            />
            <Legend />
            <Line
              name="分组（日期等权）"
              type="linear"
              dataKey="dateEqualUpRate"
              stroke="#3b82f6"
              dot={false}
              isAnimationActive={false}
              connectNulls={false}
            />
            <Line
              name="同池基准"
              type="linear"
              dataKey="baselineUpRate"
              stroke="#94a3b8"
              dot={false}
              isAnimationActive={false}
              connectNulls={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="text-ui-caption text-slate-400">
        第 {activeHorizon}{' '}
        个交易日累计价格变化；日期等权避免交易活跃日期主导结果。下表保留样本数和股票日合并统计。
      </p>
      {rows.length === 0 ? (
        <p role="status" className="p-3 text-ui-label text-amber-200">
          该口径暂无完整观察样本。
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>分组</TableHead>
              <TableHead>股票 / 日期 / 样本</TableHead>
              <TableHead>
                上涨比例
                <br />
                日期等权 / 合并
              </TableHead>
              <TableHead>对基准差</TableHead>
              <TableHead>
                {confidenceLabel}
                <br />
                百分点
              </TableHead>
              <TableHead>
                日期等权 / 合并平均 / 中位
                <br />
                价格收益
              </TableHead>
              <TableHead>推断</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(row => (
              <TableRow key={row.group}>
                <TableCell>
                  {indicatorGroupLabel(
                    row.group,
                    report.conditions,
                    report.definitions
                  )}
                </TableCell>
                <TableCell className="font-mono">
                  {row.stockCount} / {row.dateCount} /{' '}
                  {row.sampleCount.toLocaleString()}
                </TableCell>
                <TableCell className="font-mono">
                  {formatResearchRate(row.dateEqualUpRate)} /{' '}
                  {formatResearchRate(row.upRate)}
                </TableCell>
                <TableCell
                  className={`font-mono ${financialToneClass(row.upRateLift ?? 0)}`}
                >
                  {formatResearchRate(row.upRateLift, true, 'pp')}
                </TableCell>
                <TableCell className="font-mono">
                  {formatResearchRate(row.ciLow, true, '')} ~{' '}
                  {formatResearchRate(row.ciHigh, true, '')}
                </TableCell>
                <TableCell className="font-mono">
                  {formatResearchRate(row.dateEqualMeanReturn, true)} /{' '}
                  {formatResearchRate(row.meanReturn, true)} /{' '}
                  {formatResearchRate(row.medianReturn, true)}
                </TableCell>
                <TableCell>
                  <Inference row={row} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <details className="rounded-md border border-white/10 p-3">
        <summary className="cursor-pointer text-ui-label text-slate-300 focus-visible:outline-blue-400">
          价格收益差检验（日期配对）
        </summary>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>分组</TableHead>
              <TableHead>平均收益差</TableHead>
              <TableHead>{confidenceLabel}</TableHead>
              <TableHead>校正后 q 值</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows
              .filter(row => row.group !== 'baseline')
              .map(row => (
                <TableRow key={row.group}>
                  <TableCell>
                    {indicatorGroupLabel(
                      row.group,
                      report.conditions,
                      report.definitions
                    )}
                  </TableCell>
                  <TableCell className="font-mono">
                    {formatResearchRate(row.meanReturnLift, true, 'pp')}
                  </TableCell>
                  <TableCell className="font-mono">
                    {formatResearchRate(row.meanCiLow, true)} ~{' '}
                    {formatResearchRate(row.meanCiHigh, true)}
                  </TableCell>
                  <TableCell className="font-mono">
                    {row.meanQValue?.toFixed(3) ?? '描述统计 / 推断不足'}
                  </TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </details>
      {compact ? (
        <>
          <JsonDetails
            title="报告实际条件（不是页面草稿）"
            data={report.conditions}
          />
          <JsonDetails title="报告实际股票池" data={report.universe} />
        </>
      ) : (
        <ReportEvidence report={report} />
      )}
    </div>
  );
}
