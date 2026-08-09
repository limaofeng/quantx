import { describe, expect, it } from 'vitest';

import { safeInternalPath } from '@/features/auth';

describe('safeInternalPath', () => {
  it('keeps internal return paths', () => {
    expect(safeInternalPath('/t-trade?tab=monitor#active')).toBe(
      '/t-trade?tab=monitor#active'
    );
  });

  it('rejects external, protocol-relative, and login loops', () => {
    expect(safeInternalPath('https://evil.test')).toBe('/');
    expect(safeInternalPath('//evil.test/path')).toBe('/');
    expect(safeInternalPath('/login?next=/login')).toBe('/');
  });
});
