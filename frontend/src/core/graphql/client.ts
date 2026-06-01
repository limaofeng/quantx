import {
  createClient as createWSClient,
  type SubscribePayload,
} from 'graphql-ws';
import {
  Client,
  cacheExchange,
  fetchExchange,
  subscriptionExchange,
} from 'urql';

import { logger } from '@/core/errors/logger';
import { env } from '@/shared/utils/env';

import { setGraphqlWsStatus } from './ws-status';

export function resolveGraphqlWsUrl(
  graphqlHttpUrl: string,
  explicitWsUrl?: string
) {
  if (explicitWsUrl?.trim()) return explicitWsUrl.trim();

  const fallbackHttpUrl = graphqlHttpUrl || '/graphql';
  if (typeof window === 'undefined') {
    if (fallbackHttpUrl.startsWith('http://')) {
      return fallbackHttpUrl.replace(/^http:\/\//, 'ws://');
    }
    if (fallbackHttpUrl.startsWith('https://')) {
      return fallbackHttpUrl.replace(/^https:\/\//, 'wss://');
    }
    return 'ws://localhost:8080/graphql';
  }

  const resolvedHttpUrl = new URL(fallbackHttpUrl, window.location.origin);
  resolvedHttpUrl.protocol =
    resolvedHttpUrl.protocol === 'https:' ? 'wss:' : 'ws:';
  return resolvedHttpUrl.toString();
}

// WebSocket 客户端设置（支持自动重连）
const wsClient = createWSClient({
  url: resolveGraphqlWsUrl(env.VITE_GRAPHQL_HTTP_URL, env.VITE_GRAPHQL_WS_URL),
  connectionParams: () => {
    // 可以在此添加认证 Token
    return {};
  },
  // 自动重连配置
  retryAttempts: Infinity, // 无限重试
  retryWait: async retries => {
    // 指数退避：1s, 2s, 4s, 8s... 最多 30s
    const delay = Math.min(1000 * Math.pow(2, retries), 30000);
    setGraphqlWsStatus('reconnecting');
    logger.info(
      `WebSocket reconnecting in ${delay / 1000}s (attempt ${retries + 1})`
    );
    await new Promise(resolve => setTimeout(resolve, delay));
  },
  on: {
    connecting: () => {
      setGraphqlWsStatus('connecting');
    },
    connected: () => {
      setGraphqlWsStatus('connected');
      logger.info('WebSocket connected');
    },
    closed: (event: unknown) => {
      setGraphqlWsStatus('closed');
      const closeEvent = event as { code?: number; reason?: string };
      logger.warn('WebSocket closed:', {
        code: closeEvent.code,
        reason: closeEvent.reason,
      });
    },
    error: (error: unknown) => {
      setGraphqlWsStatus('error');
      logger.error(
        'WebSocket connection error:',
        error as Record<string, unknown>
      );
    },
  },
});

// 创建 URQL 客户端

export const urqlClient = new Client({
  url: env.VITE_GRAPHQL_HTTP_URL || '/graphql',
  exchanges: [
    cacheExchange,
    fetchExchange,
    subscriptionExchange({
      forwardSubscription(request) {
        const input = {
          ...request,
          query: request.query || '',
        };
        return {
          subscribe: sink => ({
            unsubscribe: wsClient.subscribe(input as SubscribePayload, sink),
          }),
        };
      },
    }),
  ],
});

export const apolloClient = urqlClient; // 兼容性导出，待后期完全替换
export default urqlClient;
