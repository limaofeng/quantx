import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';

import { cn } from '@/utils/cn';

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default:
          'border-transparent bg-blue-600 text-white shadow-lg shadow-blue-600/20',
        secondary:
          'border-transparent bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100',
        destructive:
          'border-transparent bg-rose-600 text-white shadow-lg shadow-rose-600/20',
        outline:
          'text-slate-700 dark:text-slate-300 border-slate-300 dark:border-slate-700',
        success:
          'border-transparent bg-emerald-600 text-white shadow-lg shadow-emerald-600/20',
        warning:
          'border-transparent bg-amber-500 text-white shadow-lg shadow-amber-500/20',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge };
