import { describe, expect, it } from 'vitest';

import { canDeleteReplay, replayStatusAfterDelete } from './replayWorkspace';

describe('replay workspace deletion policy', () => {
  it.each(['COMPLETED', 'ERROR', 'FAILED', 'CANCELLED', 'STOPPED'])(
    'allows terminal status %s',
    status => {
      expect(canDeleteReplay(status)).toBe(true);
    }
  );

  it.each(['PENDING', 'STARTING', 'RUNNING', ''])(
    'blocks status %s',
    status => {
      expect(canDeleteReplay(status)).toBe(false);
    }
  );

  it('selects the next history record after deleting the active replay', () => {
    expect(replayStatusAfterDelete(['run-1', 'run-2'], 'run-1', 'run-1')).toBe(
      'run-2'
    );
  });

  it('keeps the current selection when another replay is deleted', () => {
    expect(replayStatusAfterDelete(['run-1', 'run-2'], 'run-2', 'run-1')).toBe(
      'run-1'
    );
  });
});
