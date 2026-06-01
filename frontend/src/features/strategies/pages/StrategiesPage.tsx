import { BookOpen, Boxes } from 'lucide-react';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import AvailableStrategies from '../components/AvailableStrategies';
import RunningStrategies from '../components/RunningStrategies';

export default function StrategiesPage() {
  return (
    <div className="min-h-screen space-y-8 pb-20">
      <div className="px-2 pt-6">
        <Tabs defaultValue="instances" className="space-y-8">
          <TabsList className="h-11 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-sm dark:border-white/10 dark:bg-slate-900/60">
            <TabsTrigger
              value="instances"
              className="h-8 rounded-xl px-4 text-[10px] font-black uppercase tracking-widest data-[state=active]:bg-blue-600 data-[state=active]:text-white"
            >
              <Boxes className="mr-2 h-3.5 w-3.5" />
              策略实例
            </TabsTrigger>
            <TabsTrigger
              value="library"
              className="h-8 rounded-xl px-4 text-[10px] font-black uppercase tracking-widest data-[state=active]:bg-blue-600 data-[state=active]:text-white"
            >
              <BookOpen className="mr-2 h-3.5 w-3.5" />
              策略库
            </TabsTrigger>
          </TabsList>

          <TabsContent
            value="instances"
            forceMount
            className="mt-0 data-[state=inactive]:hidden"
          >
            <RunningStrategies />
          </TabsContent>

          <TabsContent
            value="library"
            forceMount
            className="mt-0 space-y-6 data-[state=inactive]:hidden"
          >
            <div className="px-4">
              <h2 className="text-sm font-black uppercase tracking-[0.2em] text-slate-800 dark:text-slate-200">
                策略库
              </h2>
              <p className="mt-2 max-w-2xl text-xs font-medium leading-relaxed text-slate-500">
                策略库只描述可创建的策略定义；实际运行、决策审计与执行跟踪在策略实例中管理。
              </p>
            </div>
            <AvailableStrategies />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
