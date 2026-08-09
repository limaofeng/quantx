/**
 * GraphQL 核心模块统一导出
 */

export { urqlClient, default as defaultUrqlClient } from './client';

// Mock 控制类型定义
export interface MockControlHeaders {
  'x-mock-enabled': string;
  'x-mock-query': string;
  'x-mock-operation-name': string;
  'x-mock-operation-type': string;
}
