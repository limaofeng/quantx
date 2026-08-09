import { type StrategiesQuery } from '@/generated/gql/graphql';

// 使用生成的 Strategy 类型
export type Strategy = NonNullable<StrategiesQuery['strategies']>[0];
export type StrategyRun = unknown; // 暂时使用 unknown，待具体定义
export type ParameterSchema = unknown;

// 策略参数值类型（支持 JSON Schema 中常见的参数类型，包括数组和嵌套对象）
export type StrategyConfigValue =
  | string
  | number
  | boolean
  | null
  | StrategyConfigValue[]
  | { [key: string]: StrategyConfigValue };

// 策略运行表单数据
export interface StrategyFormData {
  strategyName: string;
  stockCodes: string;
  strategyConfig: Record<string, StrategyConfigValue>;
}
