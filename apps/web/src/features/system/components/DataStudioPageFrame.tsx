import type { ReactNode } from 'react';

import { cn } from '@/utils/cn';

import { DataStudioShell, type DataStudioMode } from './DataStudioShell';

interface DataStudioPageFrameProps {
  activeMode: DataStudioMode;
  children: ReactNode;
  className?: string;
  description?: string;
  statusBarLeft?: ReactNode;
  statusBarRight?: ReactNode;
  title: string;
}

export function DataStudioPageFrame({
  activeMode,
  children,
  className,
  description,
  statusBarLeft,
  statusBarRight,
  title,
}: DataStudioPageFrameProps) {
  return (
    <DataStudioShell
      activeMode={activeMode}
      content={
        <div className="h-full overflow-y-auto bg-[#08101d] p-3 custom-scrollbar">
          <div className={cn('h-full min-h-0', className)}>{children}</div>
        </div>
      }
      showSidebar={false}
      statusBarLeft={
        statusBarLeft || (
          <>
            <span className="inline-flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              数据资源
            </span>
            <span className="text-slate-700">|</span>
            <span>{title}</span>
          </>
        )
      }
      statusBarRight={
        statusBarRight ||
        (description ? <span>{description}</span> : <span>Data Studio</span>)
      }
    />
  );
}
