import { cn } from '@/utils/cn';

describe('semantic Tailwind class merging', () => {
  it.each([
    'ui-micro',
    'ui-caption',
    'ui-label',
    'ui-body',
    'ui-title',
    'ui-heading',
    'ui-page-title',
    'ui-display',
    'ui-display-lg',
    'ui-display-xl',
  ])('merges %s as a size independently of text color', size => {
    expect(cn('text-ui-body text-white', `text-${size} text-slate-100`)).toBe(
      `text-${size} text-slate-100`
    );
    expect(cn(`text-slate-100 text-${size}`, 'text-sm')).toBe(
      'text-slate-100 text-sm'
    );
  });

  it('resolves semantic spacing by axis and preserves unrelated padding', () => {
    expect(cn('px-ui-panel py-ui-table-cell-y', 'py-0')).toBe(
      'px-ui-panel py-0'
    );
    expect(cn('px-3 py-2', 'py-ui-table-multiline-y')).toBe(
      'px-3 py-ui-table-multiline-y'
    );
    expect(cn('p-ui-page px-ui-panel py-ui-table-cell-y', 'p-0')).toBe('p-0');
    expect(cn('gap-ui-section gap-x-ui-panel', 'gap-2')).toBe('gap-2');
    expect(cn('space-y-ui-panel', 'space-y-ui-section')).toBe(
      'space-y-ui-section'
    );
    expect(cn('p-ui-empty', 'p-ui-panel')).toBe('p-ui-panel');
  });

  it('allows both semantic and numeric control or studio height overrides', () => {
    expect(cn('h-8', 'h-control-compact')).toBe('h-control-compact');
    expect(cn('h-control-default', 'h-control-large')).toBe('h-control-large');
    expect(cn('min-h-control-large', 'min-h-0')).toBe('min-h-0');
    expect(cn('h-studio-header', 'h-studio-tab')).toBe('h-studio-tab');
    expect(cn('h-studio-status', 'h-6')).toBe('h-6');
  });

  it('keeps responsive and state overrides scoped to their variants', () => {
    expect(cn('py-ui-table-cell-y md:py-ui-table-cell-y', 'md:py-0')).toBe(
      'py-ui-table-cell-y md:py-0'
    );
    expect(
      cn('hover:text-ui-body hover:text-white', 'hover:text-ui-title')
    ).toBe('hover:text-white hover:text-ui-title');
    expect(cn('text-ui-body leading-6', 'text-ui-title/5')).toBe(
      'text-ui-title/5'
    );
  });
});
