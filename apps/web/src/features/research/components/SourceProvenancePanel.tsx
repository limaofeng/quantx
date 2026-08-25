import { Archive, Database, FileCheck2, ShieldCheck } from 'lucide-react';

import type { ResearchSourceProvenance } from '../model';

import { ResearchPanel } from './ResearchSurface';

function formatInteger(value?: number) {
  return value === undefined ? '—' : value.toLocaleString();
}

function formatDate(value?: string) {
  if (!value) return '—';
  if (/^\d{8}$/.test(value)) {
    return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  }
  return value.slice(0, 10);
}

function minValue(values: Array<string | undefined>) {
  return values.filter((value): value is string => Boolean(value)).sort()[0];
}

function maxValue(values: Array<string | undefined>) {
  return values
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);
}

export function SourceProvenancePanel({
  provenance,
}: {
  provenance: ResearchSourceProvenance;
}) {
  const queries = provenance.queries || [];
  const requestedStart =
    minValue(queries.map(query => query.requested_start)) ||
    provenance.campaign?.start_date;
  const requestedEnd =
    maxValue(queries.map(query => query.requested_end)) ||
    provenance.campaign?.end_date;
  const availableStart =
    maxValue(queries.map(query => query.available_start)) || requestedStart;
  const availableEnd =
    minValue(queries.map(query => query.available_end)) || requestedEnd;
  const boundaryTruncated = queries.some(
    query => query.boundary_truncated === true
  );
  const sourceLabel =
    provenance.kind === 'qmt-daily-bar-archive'
      ? 'QMT 日线归档'
      : provenance.kind || '已审计研究数据源';

  return (
    <ResearchPanel
      title="数据来源与可复现证据"
      description="以下摘要来自本次不可变研究产物；本机文件路径和逐 request 明细不会暴露到 Web。"
    >
      <div className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          {
            icon: Archive,
            label: '行情来源',
            value: sourceLabel,
            hint: provenance.archive_format || '结构化研究数据源',
          },
          {
            icon: FileCheck2,
            label: 'QMT requests',
            value: formatInteger(provenance.selected_request_count),
            hint:
              provenance.required_request_count === undefined
                ? '本次实际选中'
                : `完整门槛 ${formatInteger(provenance.required_request_count)}`,
          },
          {
            icon: Database,
            label: '归档源记录',
            value: formatInteger(provenance.selected_source_record_count),
            hint: `${formatInteger(provenance.selected_chunk_count)} 个校验分片`,
          },
          {
            icon: ShieldCheck,
            label: '证券总体校验',
            value:
              provenance.metadata_universe_validated === true
                ? '已通过'
                : provenance.metadata_universe_validated === false
                  ? '未通过'
                  : '未记录',
            hint: `研究输出 ${formatInteger(provenance.emitted_rows)} 行`,
          },
        ].map(item => {
          const Icon = item.icon;
          return (
            <article
              key={item.label}
              className="rounded-md border border-white/[0.06] bg-white/[0.02] p-3"
            >
              <div className="flex items-center gap-2 text-ui-micro font-black uppercase tracking-wider text-slate-600">
                <Icon className="h-3 w-3 text-sky-300" />
                {item.label}
              </div>
              <div className="mt-2 break-words font-mono text-ui-body font-black tabular-nums text-slate-200">
                {item.value}
              </div>
              <div className="mt-1 break-words text-ui-micro text-slate-600">
                {item.hint}
              </div>
            </article>
          );
        })}
      </div>

      <dl className="grid gap-x-6 gap-y-2 border-t border-white/[0.05] px-3 py-3 text-ui-caption sm:grid-cols-2">
        <div className="flex justify-between gap-3">
          <dt className="text-slate-600">请求行情窗口</dt>
          <dd className="font-mono text-slate-400">
            {formatDate(requestedStart)} 至 {formatDate(requestedEnd)}
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-slate-600">归档可用窗口</dt>
          <dd
            className={
              boundaryTruncated
                ? 'font-mono text-amber-300'
                : 'font-mono text-slate-400'
            }
          >
            {formatDate(availableStart)} 至 {formatDate(availableEnd)}
            {boundaryTruncated ? '（边界截断）' : ''}
          </dd>
        </div>
        <div className="min-w-0 sm:col-span-2">
          <dt className="text-slate-600">Archive ledger SHA-256</dt>
          <dd className="mt-1 break-all font-mono text-ui-micro text-slate-500">
            {provenance.ledger_sha256 || '—'}
          </dd>
        </div>
        <div className="min-w-0 sm:col-span-2">
          <dt className="text-slate-600">分片清单 SHA-256</dt>
          <dd className="mt-1 break-all font-mono text-ui-micro text-slate-500">
            {provenance.selected_chunk_manifest_sha256 || '—'}
          </dd>
        </div>
      </dl>
    </ResearchPanel>
  );
}
