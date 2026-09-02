import { ArrowLeft } from 'lucide-react';
import { Link, useLocation, useParams } from 'wouter';

import { StudioPageFrame } from '@/components/ui/studio-layout';
import { safeDecodeURIComponent } from '@/router';

import { StockSelectionTrainingRunLifecycle } from '../components';

export default function ResearchTrainingRunPage() {
  const params = useParams<{ runId: string }>();
  const [, navigate] = useLocation();
  const runId = safeDecodeURIComponent(params.runId || '');

  if (!runId) {
    return (
      <StudioPageFrame>
        <div className="studio-content-width mx-auto space-y-3">
          <Link
            href="/research/training"
            className="inline-flex items-center gap-1 text-ui-caption font-semibold text-blue-300 hover:text-blue-200"
          >
            <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
            返回模型训练
          </Link>
          <div
            role="alert"
            className="rounded-panel border border-rose-400/20 bg-rose-400/[0.06] p-ui-panel text-ui-label text-rose-200"
          >
            训练运行标识不可用。
          </div>
        </div>
      </StudioPageFrame>
    );
  }

  return (
    <StudioPageFrame>
      <div className="studio-content-width mx-auto space-y-ui-section pb-ui-section">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Link
            href="/research/training"
            className="inline-flex h-control-compact items-center gap-1 rounded-control px-2 text-ui-caption font-semibold text-slate-400 outline-none transition-colors hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
            返回模型训练
          </Link>
          <Link
            href="/research/runs"
            className="text-ui-caption font-semibold text-blue-300 hover:text-blue-200"
          >
            查看实验运行
          </Link>
        </div>
        <h1 className="text-ui-page-title font-semibold text-slate-100">
          训练运行详情
        </h1>
        <StockSelectionTrainingRunLifecycle
          runId={runId}
          onFinalCreated={nextRunId => {
            navigate(
              `/research/training/runs/${encodeURIComponent(nextRunId)}`
            );
          }}
          onRegistered={() => navigate('/research/models')}
        />
      </div>
    </StudioPageFrame>
  );
}
