import { describe, expect, it } from 'vitest';

import {
  isNormalWebSocketClose,
  webSocketCloseDetails,
} from '@/core/graphql/websocket-close';

describe('GraphQL WebSocket close classification', () => {
  it('treats only protocol normal closure as expected lifecycle noise', () => {
    expect(
      isNormalWebSocketClose({ code: 1000, reason: 'Normal Closure' })
    ).toBe(true);
    expect(isNormalWebSocketClose({ code: 1006, reason: '' })).toBe(false);
    expect(isNormalWebSocketClose(undefined)).toBe(false);
  });

  it('does not trust malformed close event fields', () => {
    expect(webSocketCloseDetails({ code: '1000', reason: 123 })).toStrictEqual({
      code: undefined,
      reason: undefined,
    });
  });
});
