import { AlertCircle } from 'lucide-react';

import { Card, CardContent } from '@/components/ui/card';

export default function NotFound() {
  return (
    <div className="studio-workspace-surface flex h-full w-full items-center justify-center p-4">
      <Card className="w-full max-w-md border-white/10 bg-slate-950/60 text-slate-100">
        <CardContent className="pt-6">
          <div className="flex mb-4 gap-2">
            <AlertCircle className="h-8 w-8 text-red-500" />
            <h1 className="text-ui-display font-bold">页面未找到</h1>
          </div>

          <p className="mt-4 text-ui-body text-slate-400">
            当前地址没有对应的 QuantX 页面，请从左侧 Studio 导航重新进入。
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
