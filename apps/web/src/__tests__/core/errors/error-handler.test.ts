import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ErrorHandler,
  ErrorSeverity,
  ErrorType,
} from '@/core/errors/error-handler';
import { logger } from '@/core/errors/logger';

describe('ErrorHandler', () => {
  const handler = ErrorHandler.getInstance();

  afterEach(() => {
    handler.clearErrorHistory();
    vi.restoreAllMocks();
  });

  it('records a normalized error through the shared logger synchronously', () => {
    const logError = vi.spyOn(logger, 'logError').mockImplementation(() => {});
    vi.spyOn(globalThis.console, 'group').mockImplementation(() => {});
    vi.spyOn(globalThis.console, 'error').mockImplementation(() => {});
    vi.spyOn(globalThis.console, 'groupEnd').mockImplementation(() => {});

    const normalized = handler.handleError(new Error('request failed'), {
      operation: 'load-portfolio',
    });

    expect(logError).toHaveBeenCalledOnce();
    expect(logError).toHaveBeenCalledWith(normalized);
    expect(normalized).toMatchObject({
      type: ErrorType.UNKNOWN,
      severity: ErrorSeverity.MEDIUM,
      message: 'request failed',
      context: { operation: 'load-portfolio' },
    });
  });
});
