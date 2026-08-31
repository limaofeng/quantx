import { Loader2 } from 'lucide-react';
import { Suspense, type ReactNode } from 'react';

import ErrorBoundary from '@/components/ErrorBoundary';
import { Button } from '@/components/ui/button';

export function TTradePanelBoundary({
  children,
  name,
}: {
  children: ReactNode;
  name: string;
}) {
  return (
    <ErrorBoundary
      key={name}
      fallback={
        <div
          className="studio-workspace-surface flex h-full min-h-0 flex-col items-center justify-center gap-3 p-ui-section"
          role="alert"
        >
          <p className="text-ui-body text-rose-300">{name}加载失败</p>
          <p className="text-ui-label text-slate-400">
            可以切换其他面板，或刷新页面后重试。
          </p>
          <Button variant="outline" onClick={() => window.location.reload()}>
            刷新页面
          </Button>
        </div>
      }
    >
      <Suspense
        fallback={
          <div
            className="studio-workspace-surface flex h-full min-h-0 items-center justify-center text-ui-label text-slate-500"
            role="status"
          >
            <Loader2
              className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
            正在加载{name}…
          </div>
        }
      >
        {children}
      </Suspense>
    </ErrorBoundary>
  );
}
