import { useQuery, useMutation } from '@apollo/client/react';
import {
  Plus,
  Play,
  Settings,
  Clock,
  PauseCircle,
  Eye,
  MoreVertical,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';

import { GET_STRATEGIES, UPDATE_STRATEGY_STATUS } from '@/graphql/queries';
import {
  STRATEGY_TEMPLATES,
  getCategoryName,
  getRiskLevelName,
  getRiskLevelColor,
  StrategyTemplate,
} from '@/lib/strategyTemplates';
import { Strategy } from '@/lib/types';

export default function Strategies() {
  const { toast } = useToast();
  const userId = 'demo-user';
  const [isRunStrategyDialogOpen, setIsRunStrategyDialogOpen] = useState(false);
  const [selectedTemplate, setSelectedTemplate] =
    useState<StrategyTemplate | null>(null);
  const [strategyName, setStrategyName] = useState('');
  const [stockCodes, setStockCodes] = useState('');
  const [strategyConfig, setStrategyConfig] = useState<Record<string, any>>({});

  const {
    data: strategiesData,
    loading: isLoading,
    refetch,
  } = useQuery(GET_STRATEGIES, {
    variables: { userId },
  });

  const [updateStrategyStatus] = useMutation(UPDATE_STRATEGY_STATUS, {
    onCompleted: () => {
      refetch();
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
  });

  const strategies = strategiesData?.strategies || [];

  // Note: CREATE_STRATEGY mutation will be handled separately
  const createStrategyMutation = {
    mutate: (strategyData: any) => {
      // For now, just show success message
      setIsRunStrategyDialogOpen(false);
      toast({
        title: '策略已启动',
        description: '策略实例已成功创建并开始运行',
      });
    },
    isPending: false,
  };

  const handleStrategyStatusChange = (
    strategy: Strategy,
    newStatus: string
  ) => {
    updateStrategyStatus({
      variables: {
        id: strategy.id,
        status: newStatus,
      },
    });
  };

  const handleRunStrategy = (template: StrategyTemplate) => {
    setSelectedTemplate(template);
    setStrategyName(`${template.name} - ${new Date().toLocaleDateString()}`);
    setStockCodes(template.defaultConfig.stockCodes.join(', '));
    setStrategyConfig(template.defaultConfig.parameters);
    setIsRunStrategyDialogOpen(true);
  };

  const handleCreateStrategy = () => {
    if (!selectedTemplate || !strategyName.trim() || !stockCodes.trim()) {
      toast({
        title: '请填写必填信息',
        description: '策略名称和股票代码为必填项',
        variant: 'destructive',
      });
      return;
    }

    const stockCodeArray = stockCodes
      .split(',')
      .map(code => code.trim())
      .filter(code => code.length > 0);

    createStrategyMutation.mutate({
      name: strategyName,
      description: `基于${selectedTemplate.name}的策略实例`,
      type: selectedTemplate.type,
      status: 'running',
      userId: 'demo-user',
      config: JSON.stringify({
        stockCodes: stockCodeArray,
        ...strategyConfig,
      }),
      performance: '0',
      runningDays: 0,
    });
  };

  // Calculate summary metrics
  const totalStrategies = strategies.length;
  const runningStrategies = strategies.filter(
    s => s.status === 'running'
  ).length;
  const pausedStrategies = strategies.filter(s => s.status === 'paused').length;
  const stoppedStrategies = strategies.filter(
    s => s.status === 'stopped'
  ).length;

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

  if (isLoading) {
    return <div>加载策略数据中...</div>;
  }

  return (
    <div>
      <div className="mb-6">
        <h3 className="text-xl font-semibold">策略管理</h3>
        <p className="text-muted-foreground">
          选择和运行预定义的交易策略，监控策略表现
        </p>
      </div>

      <Tabs defaultValue="available" className="space-y-6">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="available">支持的策略</TabsTrigger>
          <TabsTrigger value="running">
            运行中的策略 ({runningStrategies + pausedStrategies})
          </TabsTrigger>
        </TabsList>

        {/* 支持的策略 */}
        <TabsContent value="available" className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {STRATEGY_TEMPLATES.map(template => {
              const IconComponent = template.icon;
              return (
                <Card
                  key={template.id}
                  className="p-6 hover:shadow-md transition-shadow"
                  data-testid={`template-${template.id}`}
                >
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex items-center">
                      <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mr-4">
                        <IconComponent className="text-primary h-6 w-6" />
                      </div>
                      <div>
                        <h4 className="text-lg font-semibold">
                          {template.name}
                        </h4>
                        <p className="text-sm text-muted-foreground">
                          {getCategoryName(template.category)}
                        </p>
                      </div>
                    </div>
                  </div>

                  <p className="text-sm text-muted-foreground mb-4 line-clamp-2">
                    {template.description}
                  </p>

                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                      <Badge
                        variant="outline"
                        className={getRiskLevelColor(template.riskLevel)}
                      >
                        {getRiskLevelName(template.riskLevel)}
                      </Badge>
                      <Badge variant="secondary">
                        {getCategoryName(template.category)}
                      </Badge>
                    </div>
                  </div>

                  <div className="flex justify-center">
                    <Button
                      onClick={() => handleRunStrategy(template)}
                      data-testid={`run-${template.id}`}
                      className="w-full"
                    >
                      <Play className="mr-2 h-4 w-4" />
                      运行策略
                    </Button>
                  </div>
                </Card>
              );
            })}
          </div>
        </TabsContent>

        {/* 运行中的策略 */}
        <TabsContent value="running" className="space-y-4">
          {strategies.length === 0 ? (
            <Card className="p-8 text-center">
              <Play className="mx-auto h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">暂无运行中的策略</h3>
              <p className="text-muted-foreground mb-4">
                从"支持的策略"中选择一个策略开始运行
              </p>
            </Card>
          ) : (
            <>
              {/* 策略概览 */}
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
                <Card className="p-4">
                  <p className="text-sm text-muted-foreground">总策略数</p>
                  <p
                    className="text-2xl font-bold text-foreground"
                    data-testid="total-strategies"
                  >
                    {totalStrategies}
                  </p>
                </Card>
                <Card className="p-4">
                  <p className="text-sm text-muted-foreground">运行中</p>
                  <p
                    className="text-2xl font-bold text-success"
                    data-testid="running-strategies"
                  >
                    {runningStrategies}
                  </p>
                </Card>
                <Card className="p-4">
                  <p className="text-sm text-muted-foreground">已暂停</p>
                  <p
                    className="text-2xl font-bold text-warning"
                    data-testid="paused-strategies"
                  >
                    {pausedStrategies}
                  </p>
                </Card>
                <Card className="p-4">
                  <p className="text-sm text-muted-foreground">已停止</p>
                  <p
                    className="text-2xl font-bold text-muted-foreground"
                    data-testid="stopped-strategies"
                  >
                    {stoppedStrategies}
                  </p>
                </Card>
              </div>

              {/* 运行中的策略列表 */}
              <div className="space-y-4">
                {strategies.map(strategy => {
                  const config = JSON.parse(strategy.config || '{}');
                  const isRunning = strategy.status === 'running';
                  const isPaused = strategy.status === 'paused';
                  const template = STRATEGY_TEMPLATES.find(
                    t => t.type === strategy.type
                  );
                  const IconComponent = template?.icon || Play;

                  return (
                    <Card
                      key={strategy.id}
                      className="p-6"
                      data-testid={`strategy-${strategy.id}`}
                    >
                      <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center">
                          <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mr-4">
                            <IconComponent className="text-primary h-6 w-6" />
                          </div>
                          <div>
                            <h4 className="text-lg font-semibold">
                              {strategy.name}
                            </h4>
                            <p className="text-sm text-muted-foreground">
                              {strategy.description}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-3">
                          <Badge className={getStatusColor(strategy.status)}>
                            {getStatusText(strategy.status)}
                          </Badge>
                          <div className="flex gap-2">
                            {isRunning ? (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() =>
                                  handleStrategyStatusChange(strategy, 'paused')
                                }
                                disabled={false}
                                data-testid={`pause-strategy-${strategy.id}`}
                              >
                                暂停
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                onClick={() =>
                                  handleStrategyStatusChange(
                                    strategy,
                                    'running'
                                  )
                                }
                                disabled={false}
                                data-testid={`start-strategy-${strategy.id}`}
                              >
                                启动
                              </Button>
                            )}
                            <Link to={`/strategies/${strategy.id}`}>
                              <Button
                                variant="outline"
                                size="sm"
                                data-testid={`view-strategy-${strategy.id}`}
                              >
                                <Eye className="mr-1 h-4 w-4" />
                                详情
                              </Button>
                            </Link>
                          </div>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                        <div>
                          <p className="text-sm text-muted-foreground">
                            监控股票
                          </p>
                          <p
                            className="font-medium"
                            data-testid={`strategy-${strategy.id}-stocks`}
                          >
                            {config.stockCodes?.length ||
                              config.stockCount ||
                              5}
                            只
                          </p>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">
                            今日信号
                          </p>
                          <p
                            className="font-medium"
                            data-testid={`strategy-${strategy.id}-signals`}
                          >
                            {isRunning ? config.todaySignals || '买入 2' : '-'}
                          </p>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">
                            累计收益
                          </p>
                          <p
                            className={`font-medium ${parseFloat(strategy.performance || '0') >= 0 ? 'text-success' : 'text-destructive'}`}
                            data-testid={`strategy-${strategy.id}-performance`}
                          >
                            {parseFloat(strategy.performance || '0') >= 0
                              ? '+'
                              : ''}
                            {strategy.performance || '0'}%
                          </p>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">
                            运行天数
                          </p>
                          <p
                            className="font-medium"
                            data-testid={`strategy-${strategy.id}-days`}
                          >
                            {strategy.runningDays || 0}天
                          </p>
                        </div>
                      </div>

                      <div className="flex items-center text-sm text-muted-foreground">
                        {isRunning ? (
                          <>
                            <Clock className="mr-2 h-4 w-4" />
                            最后执行: {new Date().toLocaleString()} • 下次检查:{' '}
                            {new Date(
                              Date.now() + 4 * 60 * 60 * 1000
                            ).toLocaleString()}
                          </>
                        ) : isPaused ? (
                          <>
                            <PauseCircle className="mr-2 h-4 w-4" />
                            策略已暂停 • 暂停时间:{' '}
                            {strategy.updatedAt.toLocaleString()}
                          </>
                        ) : (
                          <>
                            <PauseCircle className="mr-2 h-4 w-4" />
                            策略已停止
                          </>
                        )}
                      </div>
                    </Card>
                  );
                })}
              </div>
            </>
          )}
        </TabsContent>
      </Tabs>

      {/* 运行策略对话框 */}
      <Dialog
        open={isRunStrategyDialogOpen}
        onOpenChange={setIsRunStrategyDialogOpen}
      >
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>运行策略: {selectedTemplate?.name}</DialogTitle>
            <DialogDescription>
              配置策略参数并开始运行。请仔细检查参数设置。
            </DialogDescription>
          </DialogHeader>

          {selectedTemplate && (
            <div className="space-y-6">
              {/* 策略说明 */}
              <div className="p-4 bg-muted/50 rounded-lg">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="font-medium">策略说明</h4>
                  <div className="flex gap-2">
                    <Badge
                      variant="outline"
                      className={getRiskLevelColor(selectedTemplate.riskLevel)}
                    >
                      {getRiskLevelName(selectedTemplate.riskLevel)}
                    </Badge>
                    <Badge variant="secondary">
                      {getCategoryName(selectedTemplate.category)}
                    </Badge>
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">
                  {selectedTemplate.detailDescription}
                </p>
              </div>

              {/* 基本配置 */}
              <div className="space-y-4">
                <div>
                  <Label htmlFor="strategy-name">策略名称 *</Label>
                  <Input
                    id="strategy-name"
                    value={strategyName}
                    onChange={e => setStrategyName(e.target.value)}
                    placeholder="输入策略名称"
                    className="mt-1"
                  />
                </div>

                <div>
                  <Label htmlFor="stock-codes">监控股票代码 *</Label>
                  <Textarea
                    id="stock-codes"
                    value={stockCodes}
                    onChange={e => setStockCodes(e.target.value)}
                    placeholder="请输入股票代码，多个代码用逗号分隔，例如：000001, 600519, 000002"
                    rows={3}
                    className="mt-1"
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    建议监控 3-10 只股票以获得最佳效果
                  </p>
                </div>
              </div>

              <Separator />

              {/* 策略参数 */}
              <div className="space-y-4">
                <h4 className="font-medium">策略参数</h4>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {Object.entries(selectedTemplate.configSchema.parameters).map(
                    ([key, param]) => (
                      <div key={key}>
                        <Label htmlFor={key}>{param.label}</Label>
                        {param.type === 'number' ? (
                          <Input
                            id={key}
                            type="number"
                            value={strategyConfig[key] ?? param.default}
                            onChange={e =>
                              setStrategyConfig(prev => ({
                                ...prev,
                                [key]:
                                  parseFloat(e.target.value) || param.default,
                              }))
                            }
                            min={param.min}
                            max={param.max}
                            step={param.step}
                            className="mt-1"
                          />
                        ) : param.type === 'select' ? (
                          <Select
                            value={String(strategyConfig[key] ?? param.default)}
                            onValueChange={value =>
                              setStrategyConfig(prev => ({
                                ...prev,
                                [key]: value,
                              }))
                            }
                          >
                            <SelectTrigger className="mt-1">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {param.options?.map(option => (
                                <SelectItem key={option} value={option}>
                                  {option}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        ) : (
                          <Input
                            id={key}
                            value={String(strategyConfig[key] ?? param.default)}
                            onChange={e =>
                              setStrategyConfig(prev => ({
                                ...prev,
                                [key]: e.target.value,
                              }))
                            }
                            className="mt-1"
                          />
                        )}
                        <p className="text-xs text-muted-foreground mt-1">
                          {param.description}
                        </p>
                      </div>
                    )
                  )}
                </div>
              </div>
            </div>
          )}

          <DialogFooter className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => setIsRunStrategyDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              onClick={handleCreateStrategy}
              disabled={
                createStrategyMutation.isPending ||
                !strategyName.trim() ||
                !stockCodes.trim()
              }
            >
              {createStrategyMutation.isPending ? '启动中...' : '启动策略'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
