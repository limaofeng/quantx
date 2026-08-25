import { Sparkles, Loader2, AlertCircle, CheckCircle } from 'lucide-react';
import React, { useState } from 'react';

import { Button } from '@/components/ui/button';

import { analyzeStrategyWithGemini } from '../services/geminiService';
import { type GridConfig, type GridResult } from '../types';

interface Props {
  config: GridConfig;
  result: GridResult;
}

interface AnalysisResult {
  risk_score: number;
  summary: string;
  analysis: string;
  suggestions: string[];
}

const AIAdvisor: React.FC<Props> = ({ config, result }) => {
  const [loading, setLoading] = useState(false);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleAnalyze = async () => {
    setLoading(true);
    setError(null);
    try {
      const jsonStr = await analyzeStrategyWithGemini(config, result);
      const cleanJson = jsonStr
        .replace(/```json/g, '')
        .replace(/```/g, '')
        .trim();
      setAnalysis(JSON.parse(cleanJson));
    } catch (_err) {
      setError('获取 AI 分析失败，请检查 API Key 或重试。');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-gradient-to-br from-indigo-50 to-white dark:from-indigo-950/40 dark:to-slate-900 rounded-panel shadow-sm border border-indigo-100 dark:border-indigo-900/50 p-ui-section">
      <div className="flex justify-between items-start mb-4">
        <h3 className="text-indigo-900 dark:text-indigo-300 font-bold flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-indigo-500" />
          AI 策略审计
        </h3>
        {!analysis && !loading && (
          <Button
            onClick={handleAnalyze}
            size="sm"
            className="bg-indigo-600 hover:bg-indigo-700 text-white shadow-sm"
          >
            开始分析
          </Button>
        )}
      </div>

      {loading && (
        <div className="flex flex-col items-center justify-center py-ui-panel text-indigo-400">
          <Loader2 className="w-8 h-8 animate-spin mb-2" />
          <span className="text-ui-body">AI 正在深度分析行情与网格分布...</span>
        </div>
      )}

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-950/20 text-red-700 dark:text-red-400 rounded-lg text-ui-body flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {analysis && (
        <div className="space-y-ui-section animate-in fade-in slide-in-from-bottom-2 duration-500">
          <div className="flex items-center gap-ui-section">
            <div className="bg-white dark:bg-slate-800 p-3 rounded-lg border border-indigo-100 dark:border-indigo-900/30 shadow-sm flex-1">
              <span className="text-ui-caption text-slate-500 uppercase font-bold">
                风险评分
              </span>
              <div className="flex items-end gap-2 mt-1">
                <span
                  className={`text-ui-display font-bold ${analysis.risk_score > 7 ? 'text-red-500' : analysis.risk_score > 4 ? 'text-amber-500' : 'text-green-500'}`}
                >
                  {analysis.risk_score}/10
                </span>
                <span className="text-ui-caption text-slate-400 mb-1">
                  {analysis.risk_score > 7
                    ? '高风险'
                    : analysis.risk_score > 4
                      ? '中等风险'
                      : '保守'}
                </span>
              </div>
            </div>
            <div className="bg-white dark:bg-slate-800 p-3 rounded-lg border border-indigo-100 dark:border-indigo-900/30 shadow-sm flex-[2]">
              <span className="text-ui-caption text-slate-500 uppercase font-bold">
                分析总结
              </span>
              <p className="text-ui-label text-slate-700 dark:text-slate-300 mt-1 font-bold leading-snug">
                {analysis.summary}
              </p>
            </div>
          </div>

          <div className="bg-white dark:bg-slate-800 p-ui-section rounded-lg border border-indigo-100 dark:border-indigo-900/30 shadow-sm">
            <h4 className="text-ui-caption font-bold text-indigo-900 dark:text-indigo-300 uppercase tracking-wide mb-2">
              深度分析
            </h4>
            <p className="text-ui-label text-slate-600 dark:text-slate-400 leading-relaxed">
              {analysis.analysis}
            </p>
          </div>

          <div className="bg-indigo-600/5 p-ui-section rounded-lg border border-indigo-100 dark:border-indigo-900/30">
            <h4 className="text-ui-caption font-bold text-indigo-900 dark:text-indigo-300 uppercase tracking-wide mb-2 flex items-center gap-1">
              <CheckCircle className="w-3 h-3" /> 优化建议
            </h4>
            <ul className="space-y-2">
              {analysis.suggestions?.map((s: string, idx: number) => (
                <li
                  key={idx}
                  className="text-ui-label text-indigo-900/80 dark:text-indigo-300/80 flex items-start gap-2 text-ui-caption"
                >
                  <span className="mt-1.5 w-1 h-1 bg-indigo-400 rounded-full flex-shrink-0"></span>
                  {s}
                </li>
              ))}
            </ul>
          </div>

          <button
            onClick={handleAnalyze}
            className="text-ui-caption text-indigo-400 hover:text-indigo-600 underline mt-2"
          >
            重新分析
          </button>
        </div>
      )}
    </div>
  );
};

export default AIAdvisor;
