import { ArrowLeft } from 'lucide-react';
import { Link, useLocation, useSearch } from 'wouter';

import { StudioPageFrame } from '@/components/ui/studio-layout';

import { StockSelectionTrainingWizard } from '../components';

export default function ResearchTrainingNewPage() {
  const initialDatasetVersion =
    new URLSearchParams(useSearch()).get('dataset') ?? undefined;
  const [, navigate] = useLocation();

  return (
    <StudioPageFrame>
      <div className="studio-content-width mx-auto space-y-ui-section pb-ui-section">
        <nav
          aria-label="当前位置"
          className="flex items-center gap-2 text-ui-caption text-slate-500"
        >
          <Link
            href="/research"
            className="cursor-pointer outline-none hover:text-blue-300 focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            研究中心
          </Link>
          <span aria-hidden="true">/</span>
          <Link
            href="/research/training"
            className="cursor-pointer outline-none hover:text-blue-300 focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            模型训练
          </Link>
          <span aria-hidden="true">/</span>
          <span className="text-slate-300">新建训练</span>
        </nav>
        <Link
          href="/research/training"
          className="inline-flex h-control-compact items-center gap-1 rounded-control px-2 text-ui-caption font-semibold text-slate-400 outline-none transition-colors hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500"
        >
          <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
          返回模型训练
        </Link>
        <StockSelectionTrainingWizard
          initialDatasetVersion={initialDatasetVersion}
          onCreated={runId => {
            navigate(`/research/training/runs/${encodeURIComponent(runId)}`);
          }}
        />
      </div>
    </StudioPageFrame>
  );
}
