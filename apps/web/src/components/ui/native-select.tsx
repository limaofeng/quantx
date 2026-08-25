import * as React from 'react';

import { cn } from '@/utils/cn';

const NativeSelect = React.forwardRef<
  HTMLSelectElement,
  React.ComponentProps<'select'>
>(({ className, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      'h-control-default w-full cursor-pointer rounded-control border border-input bg-background px-3 text-ui-body text-foreground ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50',
      className
    )}
    {...props}
  />
));
NativeSelect.displayName = 'NativeSelect';

export { NativeSelect };
