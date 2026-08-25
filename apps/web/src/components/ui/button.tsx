/* eslint-disable react-refresh/only-export-components */
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';

import { cn } from '@/utils/cn';

const buttonVariants = cva(
  'inline-flex cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-control text-ui-label font-semibold ring-offset-background transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default:
          'bg-[var(--button-primary)] text-white hover:bg-[var(--button-primary-hover)] shadow-sm shadow-[var(--button-primary-shadow)] active:shadow-none',
        destructive:
          'bg-rose-600 text-white hover:bg-rose-500 shadow-sm shadow-rose-600/20 active:shadow-none',
        outline:
          'border border-slate-200 dark:border-slate-700 bg-transparent hover:bg-slate-100 dark:hover:bg-slate-800 hover:border-slate-300 dark:hover:border-slate-600',
        secondary:
          'bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100 hover:bg-slate-200 dark:hover:bg-slate-700',
        ghost:
          'hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-slate-100',
        link: 'text-[var(--button-primary)] underline-offset-4 hover:underline',
        success:
          'bg-emerald-600 text-white hover:bg-emerald-500 shadow-sm shadow-emerald-600/20 active:shadow-none',
      },
      size: {
        default: 'h-control-default px-3',
        sm: 'h-control-compact px-2.5 text-ui-caption',
        lg: 'h-control-large px-4 text-ui-body',
        icon: 'h-control-default w-control-default px-0',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

export interface ButtonProps
  extends
    React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = 'Button';

export { Button, buttonVariants };
