/**
 * useStrategyLogs - 策略运行日志订阅 Hook
 *
 * 通过 GraphQL Subscription 订阅策略运行实例的实时日志。
 */

import { useCallback, useEffect, useState } from 'react';
import { useSubscription } from 'urql';

import {
  useGraphqlWsStatus,
  type GraphqlWsStatus,
} from '@/core/graphql/ws-status';
import { gql } from '@/generated/gql';

// ===== GraphQL 定义 =====

const STRATEGY_LOGS_SUBSCRIPTION = gql(`
  subscription StrategyLogs($runId: String!, $levels: [String!], $includeHistory: Boolean) {
    strategyLogs(runId: $runId, levels: $levels, includeHistory: $includeHistory) {
      runId
      timestamp
      level
      message
      source
    }
  }
`);

// ===== 类型定义 =====

export type LogLevel = 'DEBUG' | 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR';

export interface StrategyLogEntry {
  runId: string;
  timestamp: string;
  level: LogLevel;
  message: string;
  source: string;
}

export interface UseStrategyLogsOptions {
  /** 日志级别过滤（可选） */
  levels?: LogLevel[];
  /** 是否在订阅开始时获取历史日志（默认 true） */
  includeHistory?: boolean;
  /** 暂停订阅 */
  paused?: boolean;
  /** 最大保留日志数量（默认 100） */
  maxLogs?: number;
}

export interface UseStrategyLogsResult {
  /** 日志列表 */
  logs: StrategyLogEntry[];
  /** 是否已连接 */
  isConnected: boolean;
  /** GraphQL WebSocket 连接状态 */
  wsStatus: GraphqlWsStatus;
  /** 错误信息 */
  error: Error | null;
  /** 清空日志 */
  clearLogs: () => void;
}

// ===== Hook 实现 =====

export function useStrategyLogs(
  runId: string | null | undefined,
  options: UseStrategyLogsOptions = {}
): UseStrategyLogsResult {
  const {
    levels,
    includeHistory = true,
    paused = false,
    maxLogs = 100,
  } = options;

  const [logs, setLogs] = useState<StrategyLogEntry[]>([]);
  const wsStatus = useGraphqlWsStatus();

  const [{ data, error, fetching }] = useSubscription({
    query: STRATEGY_LOGS_SUBSCRIPTION,
    variables: {
      runId: runId ?? '',
      levels: levels ?? null,
      includeHistory,
    },
    pause: paused || !runId,
  });

  // 当收到新日志时更新列表
  useEffect(() => {
    if (data?.strategyLogs) {
      const newLog: StrategyLogEntry = {
        runId: data.strategyLogs.runId,
        timestamp: data.strategyLogs.timestamp,
        level: data.strategyLogs.level as LogLevel,
        message: data.strategyLogs.message,
        source: data.strategyLogs.source,
      };

      setLogs(prev => {
        const updated = [...prev, newLog];
        // 保持最大日志数量
        if (updated.length > maxLogs) {
          return updated.slice(-maxLogs);
        }
        return updated;
      });
    }
  }, [data, maxLogs]);

  // 清空日志
  const clearLogs = useCallback(() => {
    setLogs([]);
  }, []);

  // 当 runId 改变时清空日志
  useEffect(() => {
    setLogs([]);
  }, [runId]);

  return {
    logs,
    isConnected:
      !!runId && !paused && fetching && wsStatus === 'connected' && !error,
    wsStatus,
    error: error ?? null,
    clearLogs,
  };
}
