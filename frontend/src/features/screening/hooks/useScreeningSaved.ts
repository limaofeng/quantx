import { useState, useCallback, useMemo } from 'react';
import { useQuery } from 'urql';

import { logger } from '@/core/errors/logger';

import { GetStockScreeningsQuery } from './useScreening';

interface NewScreening {
  name: string;
  description: string;
  criteria: any;
}

interface UseScreeningSavedResult {
  // 已保存的筛选列表
  savedScreenings: any[];
  screeningsLoading: boolean;

  // 创建对话框状态
  isCreateDialogOpen: boolean;
  setIsCreateDialogOpen: (open: boolean) => void;

  // 新筛选表单
  newScreening: NewScreening;
  setNewScreening: (
    screening: NewScreening | ((prev: NewScreening) => NewScreening)
  ) => void;

  // 创建筛选
  handleCreateScreening: () => Promise<void>;
}

/**
 * 已保存筛选 Hook
 * 管理已保存的筛选列表和创建新筛选
 */
export function useScreeningSaved(
  _userId: string = 'demo-user'
): UseScreeningSavedResult {
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [newScreening, setNewScreening] = useState<NewScreening>({
    name: '',
    description: '',
    criteria: {},
  });

  // 查询已保存的筛选
  const [result] = useQuery({
    query: GetStockScreeningsQuery as any,
    pause: true, // 暂时跳过，待后端实现
  });

  const screeningsLoading = result.fetching;

  // Mock 数据
  const savedScreenings = useMemo(
    () => [
      {
        id: '1',
        name: '价值投资组合',
        description: '寻找低估值、高ROE的优质股票',
        isActive: true,
        createdAt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000),
        results: [
          {
            id: '1',
            stockCode: '000001',
            stock: { code: '000001', name: '平安银行' },
            score: 85.2,
            returnPercentage: 12.3,
          },
          {
            id: '2',
            stockCode: '600519',
            stock: { code: '600519', name: '贵州茅台' },
            score: 92.1,
            returnPercentage: 28.4,
          },
        ],
      },
      {
        id: '2',
        name: '成长股筛选',
        description: '高增长潜力的科技和新能源股票',
        isActive: true,
        createdAt: new Date(Date.now() - 15 * 24 * 60 * 60 * 1000),
        results: [
          {
            id: '3',
            stockCode: '300750',
            stock: { code: '300750', name: '宁德时代' },
            score: 78.9,
            returnPercentage: 15.6,
          },
          {
            id: '4',
            stockCode: '002594',
            stock: { code: '002594', name: '比亚迪' },
            score: 73.4,
            returnPercentage: -5.2,
          },
        ],
      },
    ],
    []
  );

  // 创建新筛选
  const handleCreateScreening = useCallback(async () => {
    try {
      // TODO: 接入 GraphQL mutation
      logger.info('创建筛选:', newScreening);
      setIsCreateDialogOpen(false);
      setNewScreening({ name: '', description: '', criteria: {} });
    } catch (error) {
      logger.error('创建筛选失败:', error);
    }
  }, [newScreening]);

  return {
    savedScreenings,
    screeningsLoading,
    isCreateDialogOpen,
    setIsCreateDialogOpen,
    newScreening,
    setNewScreening,
    handleCreateScreening,
  };
}
