import {
  ArrowLeft,
  ArrowLeftRight,
  TrendingUp,
  Search,
  Filter,
  BarChart2,
} from 'lucide-react';
import React, { useState } from 'react';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

import { SyncControlPanel } from '../components/SyncControlPanel';
import { TaskHistory } from '../components/TaskHistory';

// Mock Data
const MOCK_TRANSACTIONS = [
  {
    id: '1',
    date: '2024-05-20 14:30:22',
    code: '600519.SH',
    name: '贵州茅台',
    type: '买入',
    price: '1680.50',
    amount: '100',
    total: '168,050.00',
    status: '成交',
  },
  {
    id: '2',
    date: '2024-05-20 10:15:45',
    code: '300750.SZ',
    name: '宁德时代',
    type: '卖出',
    price: '198.20',
    amount: '500',
    total: '99,100.00',
    status: '成交',
  },
  {
    id: '3',
    date: '2024-05-19 14:55:10',
    code: '601318.SH',
    name: '中国平安',
    type: '买入',
    price: '42.50',
    amount: '2000',
    total: '85,000.00',
    status: '成交',
  },
  {
    id: '4',
    date: '2024-05-19 09:40:33',
    code: '000858.SZ',
    name: '五粮液',
    type: '买入',
    price: '135.80',
    amount: '200',
    total: '27,160.00',
    status: '成交',
  },
  {
    id: '5',
    date: '2024-05-18 11:20:18',
    code: '600036.SH',
    name: '招商银行',
    type: '卖出',
    price: '32.10',
    amount: '1000',
    total: '32,100.00',
    status: '成交',
  },
  {
    id: '6',
    date: '2024-05-18 09:35:05',
    code: '002594.SZ',
    name: '比亚迪',
    type: '买入',
    price: '215.60',
    amount: '300',
    total: '64,680.00',
    status: '成交',
  },
  {
    id: '7',
    date: '2024-05-17 14:45:50',
    code: '600519.SH',
    name: '贵州茅台',
    type: '买入',
    price: '1675.00',
    amount: '100',
    total: '167,500.00',
    status: '成交',
  },
  {
    id: '8',
    date: '2024-05-17 13:10:12',
    code: '300750.SZ',
    name: '宁德时代',
    type: '买入',
    price: '195.50',
    amount: '200',
    total: '39,100.00',
    status: '成交',
  },
];

export function TransactionDataPage() {
  const [, setLocation] = useLocation();
  const [showHistory, setShowHistory] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  const { deployment, isSyncing, triggerSync } = useDeploymentSync(
    'daily-trading-sync',
    { successMessage: '交易数据同步任务已提交' }
  );

  const filteredData = MOCK_TRANSACTIONS.filter(
    item =>
      item.code.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.name.includes(searchTerm)
  );

  return (
    <>
      <div className="flex flex-col gap-6 animate-fade-in -mt-4 pb-10">
        {/* Header Section */}
        <div className="flex flex-col md:flex-row items-baseline justify-between gap-4">
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="icon"
              className="h-10 w-10 rounded-xl bg-white dark:bg-white/5 border border-slate-200 dark:border-white/5 shadow-sm hover:scale-105 active:scale-95 transition-all"
              onClick={() => setLocation('/settings/data')}
            >
              <ArrowLeft className="w-5 h-5 text-slate-600 dark:text-slate-400" />
            </Button>
            <div>
              <h1 className="text-2xl font-black text-slate-900 dark:text-white tracking-tight">
                交易数据
              </h1>
              <p className="text-[10px] font-black text-slate-400 dark:text-slate-500 uppercase tracking-widest mt-0.5 opacity-70">
                TRANSACTION HISTORY & LOGS
              </p>
            </div>
          </div>

          <SyncControlPanel
            deployment={deployment}
            isSyncing={isSyncing}
            defaultFlowName="交易数据同步"
            onShowHistory={() => setShowHistory(true)}
            onSync={triggerSync}
          />
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <Card className="border-slate-200/60 dark:border-slate-800/60 shadow-sm p-5 flex items-center gap-4 bg-gradient-to-br from-indigo-50/50 to-purple-50/50 dark:from-indigo-900/10 dark:to-purple-900/10">
            <div className="p-3 rounded-xl bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
              <ArrowLeftRight className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                总交易笔数
              </p>
              <p className="text-2xl font-black text-slate-900 dark:text-white mt-1">
                12,580
              </p>
            </div>
          </Card>

          <Card className="border-slate-200/60 dark:border-slate-800/60 shadow-sm p-5 flex items-center gap-4">
            <div className="p-3 rounded-xl bg-blue-500/10 text-blue-600 dark:text-blue-400">
              <BarChart2 className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                总成交额
              </p>
              <p className="text-2xl font-black text-slate-900 dark:text-white mt-1">
                28.5亿
              </p>
            </div>
          </Card>

          <Card className="border-slate-200/60 dark:border-slate-800/60 shadow-sm p-5 flex items-center gap-4">
            <div className="p-3 rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <TrendingUp className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                平均收益率
              </p>
              <p className="text-2xl font-black text-slate-900 dark:text-white mt-1">
                +8.5%
              </p>
            </div>
          </Card>
        </div>

        {/* Main Content: Data Table */}
        <Card className="border-slate-200/60 dark:border-slate-800/60 shadow-sm overflow-hidden min-h-[500px] flex flex-col">
          <CardHeader className="px-6 py-5 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-white/[0.01]">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div>
                <CardTitle className="text-lg font-bold tracking-tight">
                  历史交易记录
                </CardTitle>
                <CardDescription className="text-xs">
                  近期买入与卖出交易明细
                </CardDescription>
              </div>

              <div className="flex items-center gap-2 w-full md:w-auto">
                <div className="relative flex-1 md:w-64">
                  <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
                  <Input
                    placeholder="搜索股票代码或名称..."
                    className="pl-9 h-9 text-sm bg-white dark:bg-slate-900/50"
                    value={searchTerm}
                    onChange={e => setSearchTerm(e.target.value)}
                  />
                </div>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-9 w-9 shrink-0"
                >
                  <Filter className="w-4 h-4 text-slate-500" />
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0 flex-1">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="bg-slate-50/50 dark:bg-white/[0.01] text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100 dark:border-slate-800">
                    <th className="px-6 py-4 text-left font-black">
                      时间/股票
                    </th>
                    <th className="px-6 py-4 text-left font-black">类型</th>
                    <th className="px-6 py-4 text-right font-black">
                      成交价格
                    </th>
                    <th className="px-6 py-4 text-right font-black">数量</th>
                    <th className="px-6 py-4 text-right font-black">
                      成交金额
                    </th>
                    <th className="px-6 py-4 text-right font-black">状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {filteredData.map(item => (
                    <tr
                      key={item.id}
                      className="hover:bg-slate-50/80 dark:hover:bg-white/[0.02] transition-colors group"
                    >
                      <td className="px-6 py-5 whitespace-nowrap">
                        <div className="flex flex-col">
                          <span className="font-bold text-sm text-slate-900 dark:text-slate-100">
                            {item.name}
                          </span>
                          <span className="font-mono text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1">
                            {item.code}
                            <span className="w-1 h-1 rounded-full bg-slate-300 dark:bg-slate-600 mx-1"></span>
                            {item.date}
                          </span>
                        </div>
                      </td>
                      <td className="px-6 py-5 whitespace-nowrap">
                        <Badge
                          variant="outline"
                          className={cn(
                            'font-medium border-0',
                            item.type === '买入'
                              ? 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400'
                              : 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400'
                          )}
                        >
                          {item.type}
                        </Badge>
                      </td>
                      <td className="px-6 py-5 whitespace-nowrap text-right">
                        <span className="font-mono text-sm font-bold text-slate-700 dark:text-slate-300">
                          {item.price}
                        </span>
                      </td>
                      <td className="px-6 py-5 whitespace-nowrap text-right">
                        <span className="font-mono text-sm font-bold text-slate-700 dark:text-slate-300">
                          {item.amount}
                        </span>
                      </td>
                      <td className="px-6 py-5 whitespace-nowrap text-right">
                        <span className="font-mono text-sm font-bold text-slate-900 dark:text-slate-100">
                          {item.total}
                        </span>
                      </td>
                      <td className="px-6 py-5 whitespace-nowrap text-right">
                        <Badge className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20">
                          {item.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {filteredData.length === 0 && (
              <div className="flex flex-col items-center justify-center py-20 text-slate-400">
                <div className="p-4 rounded-full bg-slate-50 dark:bg-slate-800/50 mb-4">
                  <ArrowLeftRight className="w-8 h-8 opacity-20" />
                </div>
                <p className="text-xs font-bold uppercase tracking-widest">
                  未找到相关交易
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <TaskHistory
        open={showHistory}
        onOpenChange={setShowHistory}
        deploymentId={deployment?.id}
        deploymentName={deployment?.flowName || '交易数据同步'}
        workPoolName={deployment?.workPoolName}
      />
    </>
  );
}
