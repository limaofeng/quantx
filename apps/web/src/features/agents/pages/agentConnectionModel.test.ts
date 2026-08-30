import { describe, expect, it } from 'vitest';

import {
  connectionHealth,
  connectionStagePresentation,
  connectionStatusLabel,
  formatBytes,
  formatDuration,
  safeReasonLabel,
} from './agentConnectionModel';

const ready = {
  status: 'READY',
  mode: 'live',
  websocketStatus: 'CONNECTED',
  xtdataStatus: 'CONNECTED',
  xttradingStatus: 'CONNECTED',
  reconciliationStatus: 'READY',
};

describe('QMT Agent connection model', () => {
  it('reports the complete live chain as ready', () => {
    expect(connectionHealth(ready).tone).toBe('ready');
  });

  it('pinpoints XTData without exposing an unknown raw reason', () => {
    expect(
      connectionHealth({
        ...ready,
        xtdataStatus: 'DISCONNECTED',
        xtdataReason: 'XTDATA_UNAVAILABLE',
      })
    ).toMatchObject({
      tone: 'degraded',
      title: 'MiniQMT 行情连接未就绪',
      description: 'MiniQMT 行情连接暂不可用',
    });
    expect(safeReasonLabel('sensitive local detail')).toBe('本地连接暂不可用');
  });

  it('does not require XTTrading in data-only mode', () => {
    expect(
      connectionHealth({
        ...ready,
        mode: 'data-only',
        xttradingStatus: 'DISABLED',
      }).tone
    ).toBe('ready');
  });

  it('presents unavailable capabilities as readable degraded states', () => {
    expect(connectionStagePresentation('TRADING_UNAVAILABLE')).toEqual({
      tone: 'degraded',
      label: '交易未就绪',
    });
    expect(connectionStagePresentation('XTDATA_UNAVAILABLE')).toEqual({
      tone: 'degraded',
      label: '行情未就绪',
    });
  });

  it('does not expose unknown internal status codes in primary labels', () => {
    expect(connectionStatusLabel('BROKER_PRIVATE_FAILURE')).toBe('状态异常');
    expect(connectionStagePresentation('BROKER_PRIVATE_FAILURE').tone).toBe(
      'offline'
    );
  });

  it('formats bounded operational metrics', () => {
    expect(formatDuration(12.4)).toBe('12 秒');
    expect(formatDuration(121)).toBe('2 分钟');
    expect(formatBytes(4096)).toBe('4.0 KiB');
  });
});
