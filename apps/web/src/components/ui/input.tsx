import * as React from 'react';

import { cn } from '@/utils/cn';

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<'input'>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          'flex h-control-default w-full rounded-control border border-slate-200 bg-white px-3 text-ui-body text-slate-900 ring-offset-background transition-colors duration-200 file:border-0 file:bg-transparent file:text-ui-body file:font-medium file:text-foreground placeholder:text-slate-400 focus-visible:border-blue-500/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-100 dark:placeholder:text-slate-500',
          'shadow-[inset_0_1px_2px_rgba(0,0,0,0.03)]',
          'hover:border-slate-300 dark:hover:border-slate-600',
          className
        )}
        autoComplete="new-password"
        ref={ref}
        {...props}
      />
    );
  }
);
Input.displayName = 'Input';

export { Input };
