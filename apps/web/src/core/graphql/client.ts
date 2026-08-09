import { authExchange } from '@urql/exchange-auth';
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

import {
  getAccessToken,
  refreshWebSession,
  subscribeSession,
  willAccessTokenExpireSoon,
} from '@/core/auth';
import { logger } from '@/core/errors/logger';
import { env } from '@/shared/utils/env';

import {
  isNormalWebSocketClose,
  webSocketCloseDetails,
} from './websocket-close';
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

  const pageOrigin =
    window.location.origin || new URL(window.location.href).origin;
  const resolvedHttpUrl = new URL(fallbackHttpUrl, pageOrigin);
  resolvedHttpUrl.protocol =
    resolvedHttpUrl.protocol === 'https:' ? 'wss:' : 'ws:';
  return resolvedHttpUrl.toString();
}

interface RestartableSocket {
  readyState: number;
  close: (code?: number, reason?: string) => void;
}

let activeSocket: RestartableSocket | null = null;

function createGraphqlWsClient() {
  return createWSClient({
    url: resolveGraphqlWsUrl(
      env.VITE_GRAPHQL_HTTP_URL,
      env.VITE_GRAPHQL_WS_URL
    ),
    lazy: true,
    connectionParams: () => {
      const accessToken = getAccessToken();
      return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
    },
    retryAttempts: Infinity,
    retryWait: async retries => {
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
      connected: socket => {
        activeSocket = socket as RestartableSocket;
        setGraphqlWsStatus('connected');
        logger.info('WebSocket connected');
      },
      closed: (event: unknown) => {
        activeSocket = null;
        setGraphqlWsStatus('closed');
        const details = webSocketCloseDetails(event);
        if (isNormalWebSocketClose(event)) {
          logger.info('WebSocket closed normally', details);
        } else {
          logger.warn('WebSocket closed unexpectedly', details);
        }
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
}

let wsClient = createGraphqlWsClient();
let previousAccessToken = getAccessToken();

subscribeSession(() => {
  const nextAccessToken = getAccessToken();
  if (nextAccessToken === previousAccessToken) return;
  previousAccessToken = nextAccessToken;

  if (!nextAccessToken) {
    void wsClient.dispose();
    activeSocket = null;
    wsClient = createGraphqlWsClient();
    return;
  }

  if (activeSocket?.readyState === WebSocket.OPEN) {
    activeSocket.close(4205, 'Access token rotated');
  }
});

// 创建 URQL 客户端

export const urqlClient = new Client({
  url: env.VITE_GRAPHQL_HTTP_URL || '/graphql',
  // Strawberry's public GraphQL endpoint is intentionally POST-only.
  // URQL 5 defaults short queries to GET unless this is disabled.
  preferGetMethod: false,
  fetchOptions: { credentials: 'same-origin' },
  exchanges: [
    cacheExchange,
    authExchange(async utils => ({
      addAuthToOperation(operation) {
        const accessToken = getAccessToken();
        return accessToken
          ? utils.appendHeaders(operation, {
              Authorization: `Bearer ${accessToken}`,
            })
          : operation;
      },
      willAuthError() {
        return willAccessTokenExpireSoon();
      },
      didAuthError(error) {
        return error.graphQLErrors.some(
          item => item.extensions?.code === 'UNAUTHENTICATED'
        );
      },
      async refreshAuth() {
        await refreshWebSession();
      },
    })),
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
