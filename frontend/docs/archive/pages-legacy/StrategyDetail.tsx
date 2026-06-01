import { useQuery, useMutation } from '@apollo/client/react';
import {
  ArrowLeft,
  Play,
  Pause,
  Settings,
  BarChart,
  TrendingUp,
  TrendingDown,
  Clock,
  Target,
  AlertCircle,
} from 'lucide-react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Area,
  AreaChart,
} from 'recharts';
import { useParams, Link } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import XTerminalLog from '@/components/XTerminalLog';
import { useToast } from '@/hooks/use-toast';

import { GET_STRATEGY, UPDATE_STRATEGY_STATUS } from '@/graphql/queries';
import { STRATEGY_TEMPLATES } from '@/lib/strategyTemplates';
import { Strategy } from '@/lib/types';

export default function StrategyDetail() {
  const { strategyId } = useParams();
  const { toast } = useToast();

  const { data: strategyData, loading } = useQuery(GET_STRATEGY, {
    variables: { id: strategyId },
    skip: !strategyId,
  });

  const strategy = strategyData?.strategy;
  const isLoading = loading;

  const [updateStrategyStatus] = useMutation(UPDATE_STRATEGY_STATUS, {
    onCompleted: () => {
      toast({
        title: '策略状态已更新',
        description: '策略状态已成功更新',
      });
    },
    onError: () => {
      toast({
        title: '更新失败',
        description: '请稍后重试',
        variant: 'destructive',
      });
    },
    refetchQueries: [{ query: GET_STRATEGY, variables: { id: strategyId } }],
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div>加载策略详情...</div>
      </div>
    );
  }

  if (!strategy) {
    return (
      <div className="text-center py-16">
        <AlertCircle className="mx-auto h-16 w-16 text-muted-foreground mb-4" />
        <h3 className="text-xl font-semibold mb-2">策略不存在</h3>
        <p className="text-muted-foreground mb-4">无法找到指定的策略</p>
        <Link to="/strategies">
          <Button>返回策略列表</Button>
        </Link>
      </div>
    );
  }

  const template = STRATEGY_TEMPLATES.find(t => t.type === strategy?.type);
  const config = strategy ? JSON.parse(strategy.config || '{}') : {};
  const isRunning = strategy?.status === 'running';
  const isPaused = strategy?.status === 'paused';
  const IconComponent = template?.icon || Play;

  const handleStatusChange = (newStatus: string) => {
    if (!strategy) return;
    updateStrategyStatus({
      variables: {
        strategyId: strategy.id,
        status: newStatus,
      },
    });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running':
        return 'bg-success/10 text-success';
      case 'paused':
        return 'bg-warning/10 text-warning';
      case 'stopped':
        return 'bg-muted text-muted-foreground';
      default:
        return 'bg-muted text-muted-foreground';
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case 'running':
        return '运行中';
      case 'paused':
        return '已暂停';
      case 'stopped':
        return '已停止';
      default:
        return '未知';
    }
  };

  // 模拟的策略信号历史数据
  const mockSignals = [
    {
      date: '2024-12-30',
      stock: '600519',
      signal: '买入',
      price: 1680.5,
      reason: '短期均线上穿长期均线',
    },
    {
      date: '2024-12-29',
      stock: '000001',
      signal: '卖出',
      price: 12.45,
      reason: '达到止盈目标',
    },
    {
      date: '2024-12-28',
      stock: '000002',
      signal: '买入',
      price: 25.8,
      reason: 'RSI指标超卖',
    },
  ];

  // 模拟的收益曲线数据（过去30天）
  const mockPerformanceData = [
    { date: '12-01', value: 0, benchmark: 0 },
    { date: '12-02', value: 0.8, benchmark: 0.3 },
    { date: '12-03', value: 1.2, benchmark: 0.1 },
    { date: '12-04', value: 0.9, benchmark: 0.4 },
    { date: '12-05', value: 1.8, benchmark: 0.6 },
    { date: '12-06', value: 2.1, benchmark: 0.8 },
    { date: '12-07', value: 1.9, benchmark: 0.5 },
    { date: '12-08', value: 2.4, benchmark: 0.9 },
    { date: '12-09', value: 2.0, benchmark: 0.7 },
    { date: '12-10', value: 2.8, benchmark: 1.1 },
    { date: '12-11', value: 2.5, benchmark: 1.0 },
    { date: '12-12', value: 3.2, benchmark: 1.3 },
    { date: '12-13', value: 2.9, benchmark: 1.1 },
    { date: '12-14', value: 3.6, benchmark: 1.5 },
    { date: '12-15', value: 3.3, benchmark: 1.4 },
    { date: '12-16', value: 4.1, benchmark: 1.7 },
    { date: '12-17', value: 3.8, benchmark: 1.6 },
    { date: '12-18', value: 4.5, benchmark: 1.9 },
    { date: '12-19', value: 4.2, benchmark: 1.8 },
    { date: '12-20', value: 3.7, benchmark: 1.5 },
    { date: '12-21', value: 4.0, benchmark: 1.7 },
    { date: '12-22', value: 4.8, benchmark: 2.0 },
    { date: '12-23', value: 4.5, benchmark: 1.9 },
    { date: '12-24', value: 5.2, benchmark: 2.2 },
    { date: '12-25', value: 4.9, benchmark: 2.1 },
    { date: '12-26', value: 5.6, benchmark: 2.4 },
    { date: '12-27', value: 5.3, benchmark: 2.3 },
    { date: '12-28', value: 6.1, benchmark: 2.6 },
    { date: '12-29', value: 5.8, benchmark: 2.5 },
    { date: '12-30', value: 6.5, benchmark: 2.8 },
  ];

  return (
    <div className="space-y-6">
      {/* 头部导航 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <Link to="/strategies">
            <Button variant="outline" size="sm" className="mr-4">
              <ArrowLeft className="mr-2 h-4 w-4" />
              返回
            </Button>
          </Link>
          <div className="flex items-center">
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mr-4">
              <IconComponent className="text-primary h-6 w-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold">{strategy.name}</h1>
              <p className="text-muted-foreground">{strategy.description}</p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Badge className={getStatusColor(strategy.status)}>
            {getStatusText(strategy.status)}
          </Badge>
          {isRunning ? (
            <Button
              variant="outline"
              onClick={() => handleStatusChange('paused')}
            >
              <Pause className="mr-2 h-4 w-4" />
              暂停策略
            </Button>
          ) : (
            <Button onClick={() => handleStatusChange('running')}>
              <Play className="mr-2 h-4 w-4" />
              启动策略
            </Button>
          )}
          <Button variant="outline">
            <Settings className="mr-2 h-4 w-4" />
            配置
          </Button>
        </div>
      </div>

      {/* 策略概览 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">累计收益</p>
              <p
                className={`text-2xl font-bold ${parseFloat(strategy.performance || '0') >= 0 ? 'text-success' : 'text-destructive'}`}
              >
                {parseFloat(strategy.performance || '0') >= 0 ? '+' : ''}
                {strategy.performance || '0'}%
              </p>
            </div>
            <TrendingUp
              className={`h-8 w-8 ${parseFloat(strategy.performance || '0') >= 0 ? 'text-success' : 'text-destructive'}`}
            />
          </div>
        </Card>

        <Card className="p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">运行天数</p>
              <p className="text-2xl font-bold">{strategy.runningDays || 0}</p>
            </div>
            <Clock className="h-8 w-8 text-muted-foreground" />
          </div>
        </Card>

        <Card className="p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">监控股票</p>
              <p className="text-2xl font-bold">
                {config.stockCodes?.length || 5}
              </p>
            </div>
            <Target className="h-8 w-8 text-muted-foreground" />
          </div>
        </Card>

        <Card className="p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">今日信号</p>
              <p className="text-2xl font-bold">{isRunning ? 3 : 0}</p>
            </div>
            <BarChart className="h-8 w-8 text-muted-foreground" />
          </div>
        </Card>
      </div>

      {/* 详细信息标签页 */}
      <Tabs defaultValue="overview" className="space-y-4">
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="logs">运行日志</TabsTrigger>
          <TabsTrigger value="signals">交易信号</TabsTrigger>
          <TabsTrigger value="performance">收益分析</TabsTrigger>
          <TabsTrigger value="config">策略配置</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card className="p-6">
              <h3 className="text-lg font-semibold mb-4">策略说明</h3>
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  {template?.detailDescription || '暂无详细说明'}
                </p>
                <div className="flex items-center text-sm">
                  <span className="font-medium mr-2">策略类型:</span>
                  <span className="text-muted-foreground">
                    {template?.name}
                  </span>
                </div>
                <div className="flex items-center text-sm">
                  <span className="font-medium mr-2">风险等级:</span>
                  <span
                    className={`${template ? getRiskLevelColor(template.riskLevel) : ''}`}
                  >
                    {template ? getRiskLevelName(template.riskLevel) : '未知'}
                  </span>
                </div>
              </div>
            </Card>

            <Card className="p-6">
              <h3 className="text-lg font-semibold mb-4">运行状态</h3>
              <div className="space-y-3">
                {isRunning ? (
                  <>
                    <div className="flex items-center text-sm">
                      <span className="font-medium mr-2">状态:</span>
                      <Badge className="bg-success/10 text-success">
                        运行中
                      </Badge>
                    </div>
                    <div className="flex items-center text-sm">
                      <span className="font-medium mr-2">上次执行:</span>
                      <span className="text-muted-foreground">
                        {new Date().toLocaleString()}
                      </span>
                    </div>
                    <div className="flex items-center text-sm">
                      <span className="font-medium mr-2">下次检查:</span>
                      <span className="text-muted-foreground">
                        {new Date(
                          Date.now() + 4 * 60 * 60 * 1000
                        ).toLocaleString()}
                      </span>
                    </div>
                  </>
                ) : isPaused ? (
                  <>
                    <div className="flex items-center text-sm">
                      <span className="font-medium mr-2">状态:</span>
                      <Badge className="bg-warning/10 text-warning">
                        已暂停
                      </Badge>
                    </div>
                    <div className="flex items-center text-sm">
                      <span className="font-medium mr-2">暂停时间:</span>
                      <span className="text-muted-foreground">
                        {strategy.updatedAt.toLocaleString()}
                      </span>
                    </div>
                  </>
                ) : (
                  <div className="flex items-center text-sm">
                    <span className="font-medium mr-2">状态:</span>
                    <Badge className="bg-muted text-muted-foreground">
                      已停止
                    </Badge>
                  </div>
                )}
              </div>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="logs" className="space-y-4">
          <XTerminalLog strategyId={strategy.id} isRunning={isRunning} />
        </TabsContent>

        <TabsContent value="signals" className="space-y-4">
          <Card className="p-6">
            <h3 className="text-lg font-semibold mb-4">最近交易信号</h3>
            <div className="space-y-3">
              {mockSignals.map((signal, index) => (
                <div
                  key={index}
                  className="flex items-center justify-between p-3 border rounded-lg"
                >
                  <div className="flex items-center">
                    <div className="mr-4">
                      <p className="font-medium">{signal.stock}</p>
                      <p className="text-sm text-muted-foreground">
                        {signal.date}
                      </p>
                    </div>
                    <div>
                      <Badge
                        className={
                          signal.signal === '买入'
                            ? 'bg-success/10 text-success'
                            : 'bg-destructive/10 text-destructive'
                        }
                      >
                        {signal.signal}
                      </Badge>
                      <p className="text-sm text-muted-foreground mt-1">
                        {signal.reason}
                      </p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-medium">¥{signal.price.toFixed(2)}</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="performance" className="space-y-4">
          <Card className="p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">收益曲线</h3>
              <div className="flex items-center space-x-4 text-sm">
                <div className="flex items-center">
                  <div className="w-3 h-3 bg-blue-500 rounded-full mr-2"></div>
                  <span>策略收益</span>
                </div>
                <div className="flex items-center">
                  <div className="w-3 h-3 bg-gray-400 rounded-full mr-2"></div>
                  <span>基准指数</span>
                </div>
              </div>
            </div>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={mockPerformanceData}>
                  <defs>
                    <linearGradient
                      id="strategyGradient"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop offset="5%" stopColor="#3B82F6" stopOpacity={0.8} />
                      <stop
                        offset="95%"
                        stopColor="#3B82F6"
                        stopOpacity={0.1}
                      />
                    </linearGradient>
                    <linearGradient
                      id="benchmarkGradient"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop offset="5%" stopColor="#9CA3AF" stopOpacity={0.6} />
                      <stop
                        offset="95%"
                        stopColor="#9CA3AF"
                        stopOpacity={0.1}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    className="stroke-gray-200 dark:stroke-gray-700"
                  />
                  <XAxis
                    dataKey="date"
                    className="text-gray-600 dark:text-gray-400"
                    tick={{ fontSize: 12 }}
                  />
                  <YAxis
                    className="text-gray-600 dark:text-gray-400"
                    tick={{ fontSize: 12 }}
                    tickFormatter={value => `${value}%`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'hsl(var(--card))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: '8px',
                      fontSize: '14px',
                    }}
                    labelStyle={{ color: 'hsl(var(--foreground))' }}
                    formatter={(value: number, name: string) => [
                      `${value.toFixed(2)}%`,
                      name === 'value' ? '策略收益' : '基准指数',
                    ]}
                    labelFormatter={label => `日期: ${label}`}
                  />
                  <Area
                    type="monotone"
                    dataKey="benchmark"
                    stroke="#9CA3AF"
                    strokeWidth={2}
                    fill="url(#benchmarkGradient)"
                    fillOpacity={0.6}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="#3B82F6"
                    strokeWidth={3}
                    fill="url(#strategyGradient)"
                    fillOpacity={0.8}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* 收益统计 */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6 pt-6 border-t">
              <div className="text-center">
                <p className="text-2xl font-bold text-green-600">
                  +
                  {mockPerformanceData[
                    mockPerformanceData.length - 1
                  ]?.value.toFixed(2)}
                  %
                </p>
                <p className="text-sm text-muted-foreground">总收益率</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-blue-600">
                  +
                  {(
                    (mockPerformanceData[mockPerformanceData.length - 1]
                      ?.value || 0) -
                    (mockPerformanceData[mockPerformanceData.length - 1]
                      ?.benchmark || 0)
                  ).toFixed(2)}
                  %
                </p>
                <p className="text-sm text-muted-foreground">超额收益</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-gray-600">
                  {Math.max(...mockPerformanceData.map(d => d.value)).toFixed(
                    2
                  )}
                  %
                </p>
                <p className="text-sm text-muted-foreground">最大收益</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-orange-600">
                  {(
                    Math.max(...mockPerformanceData.map(d => d.value)) -
                    Math.min(...mockPerformanceData.map(d => d.value))
                  ).toFixed(2)}
                  %
                </p>
                <p className="text-sm text-muted-foreground">最大回撤</p>
              </div>
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="config" className="space-y-4">
          <Card className="p-6">
            <h3 className="text-lg font-semibold mb-4">策略配置</h3>
            <div className="space-y-4">
              <div>
                <label className="font-medium">监控股票</label>
                <p className="text-sm text-muted-foreground mt-1">
                  {config.stockCodes?.join(', ') || '000001, 000002, 600519'}
                </p>
              </div>

              {template?.configSchema.parameters &&
                Object.entries(template.configSchema.parameters).map(
                  ([key, param]) => (
                    <div key={key}>
                      <label className="font-medium">{param.label}</label>
                      <p className="text-sm text-muted-foreground mt-1">
                        {config[key] !== undefined
                          ? String(config[key])
                          : String(param.default)}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {param.description}
                      </p>
                    </div>
                  )
                )}
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function getRiskLevelName(riskLevel: string) {
  switch (riskLevel) {
    case 'low':
      return '低风险';
    case 'medium':
      return '中等风险';
    case 'high':
      return '高风险';
    default:
      return '未知';
  }
}

function getRiskLevelColor(riskLevel: string) {
  switch (riskLevel) {
    case 'low':
      return 'text-success';
    case 'medium':
      return 'text-warning';
    case 'high':
      return 'text-destructive';
    default:
      return 'text-muted-foreground';
  }
}
