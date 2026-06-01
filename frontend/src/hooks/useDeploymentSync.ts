/**
 * useDeploymentSync - 统一的 Deployment 同步管理 Hook
 *
 * 封装了获取部署状态、实时订阅状态变化和触发同步的完整逻辑。
 * 用于替代各页面/组件中重复的 GraphQL 定义和同步逻辑。
 */

import { useQuery, useMutation, useSubscription } from 'urql';

import { gql } from '@/generated/gql';
import { useToast } from '@/hooks/use-toast';

// ===== GraphQL 定义 =====

const GET_DEPLOYMENT_BY_NAME = gql(`
  query GetDeploymentByName($name: String!) {
    getDeploymentByName(name: $name) {
      id
      name
      flowName
      description
      workPoolName
      isScheduleActive
      lastRunTime
      nextRunTime
      status
    }
  }
`);

const RUN_DEPLOYMENT = gql(`
  mutation RunDeployment($deploymentId: String!, $parameters: JSON) {
    runDeployment(deploymentId: $deploymentId, parameters: $parameters) {
      id
      state
    }
  }
`);

const DEPLOYMENT_STATUS_SUBSCRIPTION = gql(`
  subscription DeploymentStatus($name: String!) {
    deploymentStatus(name: $name) {
      id
      name
      flowName
      description
      workPoolName
      isScheduleActive
      lastRunTime
      nextRunTime
      status
    }
  }
`);

// ===== 类型定义 =====

export interface DeploymentStatus {
  id: string;
  name: string;
  flowName: string;
  description: string;
  workPoolName: string;
  isScheduleActive: boolean;
  lastRunTime: string | null;
  nextRunTime: string | null;
  status: string | null;
}

export interface UseDeploymentSyncOptions {
  /** 自定义成功提示 */
  successMessage?: string;
  /** 自定义失败提示 */
  errorMessage?: string;
}

export interface UseDeploymentSyncResult {
  /** 当前部署状态（订阅优先于查询） */
  deployment: DeploymentStatus | undefined;
  /** 是否正在同步 */
  isSyncing: boolean;
  /** 触发同步 */
  triggerSync: (parameters?: Record<string, unknown>) => Promise<string | undefined>;
  /** 初始加载中 */
  isLoading: boolean;
}

// ===== Hook 实现 =====

export function useDeploymentSync(
  deploymentName: string,
  options: UseDeploymentSyncOptions = {}
): UseDeploymentSyncResult {
  const { toast } = useToast();

  const {
    successMessage = '同步已启动',
    errorMessage = '无法连接到 Prefect 管理器',
  } = options;

  // 初始获取部署状态
  const [{ data: queryData, fetching: isLoading }] = useQuery({
    query: GET_DEPLOYMENT_BY_NAME as any,
    variables: { name: deploymentName },
  });

  // 实时订阅部署状态
  const [{ data: subscriptionData }] = useSubscription({
    query: DEPLOYMENT_STATUS_SUBSCRIPTION as any,
    variables: { name: deploymentName },
  });

  // 合并部署状态（订阅优先）
  const deployment: DeploymentStatus | undefined =
    subscriptionData?.deploymentStatus || queryData?.getDeploymentByName;

  // 同步 Mutation
  const [syncResult, runDeployment] = useMutation(RUN_DEPLOYMENT);

  // 派生同步状态
  // 后端 status 字段仅在任务处于活跃状态（Running/Pending/Cancelling）时非空
  const isSyncing =
    syncResult.fetching ||
    ['Running', 'Pending', 'Cancelling', 'Scheduled', 'Late'].includes(
      deployment?.status ?? ''
    );

  // 触发同步函数
  const triggerSync = async (parameters?: Record<string, unknown>) => {
    if (!deployment?.id) {
      toast({
        title: '无法同步',
        description: `未找到 ${deploymentName} 部署`,
        variant: 'destructive',
      });
      return undefined;
    }

    try {
      // 提交同步请求
      const result = await runDeployment({
        deploymentId: deployment.id,
        parameters,
      });

      if (result.error) {
        throw result.error;
      }

      if (result.data?.runDeployment) {
        const runId = result.data.runDeployment.id;
        toast({
          title: successMessage,
          description: `运行 ID: ${runId?.substring(0, 8)}...`,
          variant: 'success',
        });
        return runId;
      }
    } catch (e) {
      toast({
        title: '提交失败',
        description: e instanceof Error ? e.message : errorMessage,
        variant: 'destructive',
      });
    }
    return undefined;
  };

  return {
    deployment,
    isSyncing,
    triggerSync,
    isLoading,
  };
}
