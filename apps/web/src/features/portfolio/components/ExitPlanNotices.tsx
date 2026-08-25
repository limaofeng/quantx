import { AlertTriangle, Clock3 } from 'lucide-react';

import { cn } from '@/utils/cn';

import {
  buildExitPlanNotices,
  type ExitPlanNoticeSource,
} from './exitPlanNoticeUtils';

export function ExitPlanNotices({ plan }: { plan: ExitPlanNoticeSource }) {
  const notices = buildExitPlanNotices(plan);
  if (notices.length === 0) return null;

  return (
    <div aria-live="polite" className="mt-2 grid gap-1" role="status">
      {notices.map(notice => {
        const Icon = notice.tone === 'info' ? Clock3 : AlertTriangle;
        return (
          <div
            className={cn(
              'flex items-start gap-1.5 text-ui-caption font-bold',
              notice.tone === 'info' ? 'text-sky-300' : 'text-amber-200'
            )}
            key={notice.key}
          >
            <Icon aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="break-words">{notice.message}</span>
          </div>
        );
      })}
    </div>
  );
}
