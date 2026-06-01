import * as React from 'react';

import { cn } from '@/utils/cn';

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<'input'>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          'flex h-10 w-full rounded-xl border-2 border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900/40 px-4 py-2.5 text-sm text-slate-900 dark:text-slate-100 ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-slate-400 dark:placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/10 focus-visible:border-blue-500/80 disabled:cursor-not-allowed disabled:opacity-50 transition-all duration-200',
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
