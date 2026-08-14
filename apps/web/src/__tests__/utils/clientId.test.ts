import { afterEach, describe, expect, it, vi } from 'vitest';

import { createClientId } from '@/utils/clientId';

describe('createClientId', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses randomUUID when the page exposes it', () => {
    vi.stubGlobal('crypto', {
      randomUUID: vi.fn(() => '5ca92cf8-8fe9-49f6-91bd-ed1771ff0ba3'),
    });

    expect(createClientId('exit-rule')).toBe(
      '5ca92cf8-8fe9-49f6-91bd-ed1771ff0ba3'
    );
  });

  it('uses getRandomValues when randomUUID is unavailable', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: vi.fn((values: Uint32Array) => {
        values.set([1, 2, 3, 4]);
        return values;
      }),
    });

    expect(createClientId('exit-rule')).toBe(
      'exit-rule-00000001000000020000000300000004'
    );
  });
});
