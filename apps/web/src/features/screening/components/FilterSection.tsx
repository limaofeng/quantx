import type React from 'react';

interface FilterSectionProps {
  title: string;
  children: React.ReactNode;
}

export function FilterSection({ title, children }: FilterSectionProps) {
  return (
    <div className="space-y-ui-section">
      <h3 className="text-ui-heading font-semibold text-gray-900 dark:text-white">
        {title}
      </h3>
      {children}
    </div>
  );
}
