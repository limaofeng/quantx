// API 相关常量

export const API_ENDPOINTS = {
  // GraphQL
  GRAPHQL: '/graphql',

  // WebSocket
  WS_GRAPHQL: '/graphql',

  // REST APIs (如果有)
  HEALTH: '/health',
  VERSION: '/version',
} as const;

export const HTTP_STATUS = {
  OK: 200,
  CREATED: 201,
  NO_CONTENT: 204,
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  INTERNAL_SERVER_ERROR: 500,
} as const;

export const REQUEST_TIMEOUT = {
  DEFAULT: 10000, // 10 seconds
  UPLOAD: 30000, // 30 seconds
  LONG_POLLING: 60000, // 60 seconds
} as const;
