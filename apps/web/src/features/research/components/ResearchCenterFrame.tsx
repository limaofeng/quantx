import type { ReactNode } from 'react';
import { Link, useLocation } from 'wouter';

import {
  StudioPageDescription,
  StudioPageFrame,
  StudioPageHeader,
  StudioPageTitle,
} from '@/components/ui/studio-layout';
import { cn } from '@/utils/cn';

const RESEARCH_CENTER_NAV_ITEMS = [
  { href: '/research', label: '概览' },
  { href: '/research/training', label: '模型训练' },
  { href: '/research/runs', label: '实验运行' },
  { href: '/research/models', label: '模型库' },
] as const;

function isResearchCenterNavActive(href: string, pathname: string) {
  const normalizedPath = pathname.split(/[?#]/)[0].replace(/\/+$/, '') || '/';
  if (href === '/research') return normalizedPath === href;
  return normalizedPath === href || normalizedPath.startsWith(`${href}/`);
}

export interface ResearchCenterFrameProps {
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
  description?: string;
  title: string;
}

export function ResearchCenterFrame({
  actions,
  children,
  className,
  contentClassName,
  description,
  title,
}: ResearchCenterFrameProps) {
  const [location] = useLocation();

  return (
    <StudioPageFrame
      scroll="none"
      data-testid="research-center-frame"
      className={cn('flex flex-col p-0 text-slate-200', className)}
    >
      <StudioPageHeader className="shrink-0 border-b border-white/[0.06] px-ui-page py-ui-panel">
        <div className="min-w-0 flex-1">
          <StudioPageTitle className="truncate">{title}</StudioPageTitle>
          {description && (
            <StudioPageDescription className="max-w-3xl truncate">
              {description}
            </StudioPageDescription>
          )}
        </div>
        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        )}
      </StudioPageHeader>

      <nav
        aria-label="研究中心工作区"
        className="shrink-0 overflow-x-auto border-b border-white/[0.06] bg-[#081221]"
      >
        <div className="studio-content-width mx-auto flex min-w-max items-center gap-1 px-ui-page">
          {RESEARCH_CENTER_NAV_ITEMS.map(item => {
            const active = isResearchCenterNavActive(item.href, location);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? 'page' : undefined}
                data-active={active ? 'true' : 'false'}
                className={cn(
                  'relative flex h-control-large cursor-pointer items-center px-3 text-ui-label font-semibold outline-none transition-colors focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-blue-500',
                  active
                    ? 'text-blue-200 after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:bg-blue-500'
                    : 'text-slate-500 hover:bg-blue-500/[0.06] hover:text-slate-200'
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </div>
      </nav>

      <div
        data-testid="research-center-scroll"
        className={cn(
          'min-h-0 flex-1 overflow-y-auto custom-scrollbar',
          contentClassName
        )}
      >
        <div className="studio-content-width mx-auto space-y-ui-section p-ui-page">
          {children}
        </div>
      </div>
    </StudioPageFrame>
  );
}
