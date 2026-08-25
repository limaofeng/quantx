import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('favicon branding', () => {
  it('uses the current Qx mark instead of the legacy red mark', () => {
    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
    const favicon = readFileSync(
      resolve(process.cwd(), 'public/favicon.svg'),
      'utf8'
    );

    expect(html).toContain(
      '<link rel="icon" href="/favicon.svg" type="image/svg+xml" />'
    );
    expect(html).not.toContain('data:image/svg+xml');
    expect(favicon).toContain('M18.3 24.13A11 11 0 1 1 24.13 18.3');
    expect(favicon).toContain('M16.6 16.6 27 27');
    expect(favicon).toContain('M19.5 27 27 19.5');
    expect(favicon).toContain('#38BDF8');
    expect(favicon).not.toContain('#ef4444');
  });
});
