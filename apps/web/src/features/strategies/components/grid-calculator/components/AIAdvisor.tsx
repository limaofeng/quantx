import { Bot, Sparkles } from 'lucide-react';
import React from 'react';

import { useStudioWorkspaceContext } from '@/components/studio-workspace';
import { Button } from '@/components/ui/button';

import { buildGridAuditPrompt } from '../services/gridAuditPrompt';
import { type GridConfig, type GridResult } from '../types';

interface Props {
  config: GridConfig;
  result: GridResult;
}

const AIAdvisor: React.FC<Props> = ({ config, result }) => {
  const workspace = useStudioWorkspaceContext();

  const handleAnalyze = () => {
    workspace?.openAssistant(buildGridAuditPrompt(config, result));
  };

  return (
    <div className="rounded-panel border border-indigo-400/20 bg-indigo-400/5 p-ui-section">
      <div className="flex items-start gap-ui-section">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-indigo-400/20 bg-indigo-400/10 text-indigo-300">
          <Sparkles className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-ui-body font-semibold text-slate-100">
            AI 策略审计
          </h3>
          <p className="mt-1 text-ui-label leading-5 text-slate-400">
            通过服务端 AI Runtime
            分析当前网格参数，运行过程会持久化并接受统一审计。
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          className="h-control-compact shrink-0 bg-indigo-600 text-white hover:bg-indigo-500"
          disabled={!workspace}
          onClick={handleAnalyze}
        >
          <Bot className="mr-1.5 h-3.5 w-3.5" />在 AI 助手中审计
        </Button>
      </div>
      {!workspace && (
        <p className="mt-2 text-ui-caption text-amber-300">
          AI 策略审计仅在 Studio 工作区中可用。
        </p>
      )}
    </div>
  );
};

export default AIAdvisor;
