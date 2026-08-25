import * as React from 'react';

import { cn } from '@/utils/cn';

const StudioPageFrame = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & {
    scroll?: 'none' | 'page';
  }
>(({ className, scroll = 'page', ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      'studio-workspace-surface h-full min-h-0 min-w-0 p-ui-page text-ui-body',
      scroll === 'page'
        ? 'overflow-y-auto custom-scrollbar'
        : 'overflow-hidden',
      className
    )}
    {...props}
  />
));
StudioPageFrame.displayName = 'StudioPageFrame';

const StudioPageStack = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('studio-content-width mx-auto space-y-ui-panel', className)}
    {...props}
  />
));
StudioPageStack.displayName = 'StudioPageStack';

const StudioPageHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <header
    ref={ref}
    className={cn(
      'flex min-h-control-large flex-wrap items-center justify-between gap-2',
      className
    )}
    {...props}
  />
));
StudioPageHeader.displayName = 'StudioPageHeader';

const StudioPageTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h1
    ref={ref}
    className={cn('text-ui-page-title font-semibold text-slate-100', className)}
    {...props}
  />
));
StudioPageTitle.displayName = 'StudioPageTitle';

const StudioPageDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn('mt-1 text-ui-caption text-slate-500', className)}
    {...props}
  />
));
StudioPageDescription.displayName = 'StudioPageDescription';

const StudioToolbar = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    role="toolbar"
    className={cn(
      'flex min-h-control-default flex-wrap items-center gap-2',
      className
    )}
    {...props}
  />
));
StudioToolbar.displayName = 'StudioToolbar';

const StudioPanel = React.forwardRef<
  HTMLElement,
  React.HTMLAttributes<HTMLElement>
>(({ className, ...props }, ref) => (
  <section
    ref={ref}
    className={cn(
      'rounded-panel border border-white/[0.06] bg-[#0b1120]/80',
      className
    )}
    {...props}
  />
));
StudioPanel.displayName = 'StudioPanel';

const StudioPanelHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      'flex min-h-control-large flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-ui-panel',
      className
    )}
    {...props}
  />
));
StudioPanelHeader.displayName = 'StudioPanelHeader';

const StudioPanelTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h2
    ref={ref}
    className={cn('text-ui-title font-semibold text-slate-100', className)}
    {...props}
  />
));
StudioPanelTitle.displayName = 'StudioPanelTitle';

const StudioPanelDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn('text-ui-caption text-slate-500', className)}
    {...props}
  />
));
StudioPanelDescription.displayName = 'StudioPanelDescription';

const StudioPanelContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('p-ui-panel', className)} {...props} />
));
StudioPanelContent.displayName = 'StudioPanelContent';

export {
  StudioPageDescription,
  StudioPageFrame,
  StudioPageHeader,
  StudioPageStack,
  StudioPageTitle,
  StudioPanel,
  StudioPanelContent,
  StudioPanelDescription,
  StudioPanelHeader,
  StudioPanelTitle,
  StudioToolbar,
};
