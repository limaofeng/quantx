import { useQuery, useMutation } from '@tanstack/react-query';
import {
  Search,
  Filter,
  Plus,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Target,
  Activity,
  DollarSign,
  Percent,
  Building2,
  Calendar,
  ChevronRight,
  Star,
  LineChart,
  Shield,
  PieChart,
  Clock,
} from 'lucide-react';
import { useState } from 'react';
import {
  LineChart as RechartsLineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart as RechartsPieChart,
  Pie,
  Cell,
} from 'recharts';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
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

import {
  GET_STOCK_SCREENINGS,
  GET_ALL_STOCK_METRICS,
  SCREEN_STOCKS,
  GET_SCREENING_SUMMARY,
  CREATE_STOCK_SCREENING,
  ADD_STOCK_TO_SCREENING,
} from '@/graphql/queries';
import { queryClient } from '@/lib/queryClient';

interface ScreeningCriteria {
  includeIndustries?: string[];
  excludeIndustries?: string[];
  peMin?: number;
  peMax?: number;
  pbMin?: number;
  pbMax?: number;
  roeMin?: number;
  roeMax?: number;
  revenueGrowthMin?: number;
  revenueGrowthMax?: number;
  rsiMin?: number;
  rsiMax?: number;
  marketCapMin?: number;
  marketCapMax?: number;
  customFormula?: string;
}

export default function StockScreening() {
  const [selectedTab, setSelectedTab] = useState('overview');
  const [screeningCriteria, setScreeningCriteria] = useState<ScreeningCriteria>(
    {}
  );
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [newScreening, setNewScreening] = useState({
    name: '',
    description: '',
    criteria: {},
  });

  const userId = 'demo-user';

  // Fetch user's saved screenings
  const { data: stockScreenings, isLoading: screeningsLoading } = useQuery({
    queryKey: ['/graphql/stockScreenings', userId],
    enabled: false, // Will implement GraphQL integration later
  });

  // Fetch all stock metrics for screening
  const { data: allMetrics, isLoading: metricsLoading } = useQuery({
    queryKey: ['/graphql/allStockMetrics'],
    enabled: false, // Will implement GraphQL integration later
  });

  // Fetch screening summary
  const { data: screeningSummary, isLoading: summaryLoading } = useQuery({
    queryKey: ['/graphql/screeningSummary', userId],
    enabled: false, // Will implement GraphQL integration later
  });

  // Screen stocks based on criteria
  const {
    data: screeningResults,
    isLoading: screeningLoading,
    refetch: runScreening,
  } = useQuery({
    queryKey: ['/graphql/screenStocks', screeningCriteria],
    enabled: false, // Only run when explicitly triggered
  });

  // Mock data for development
  const mockScreeningSummary = {
    totalScreenings: 5,
    activeScreenings: 3,
    totalStocks: 42,
    averageReturn: 8.5,
    bestPerformer: {
      stockCode: '600519',
      returnPercentage: 28.4,
      stock: { code: '600519', name: '贵州茅台' },
    },
    worstPerformer: {
      stockCode: '002594',
      returnPercentage: -5.2,
      stock: { code: '002594', name: '比亚迪' },
    },
  };

  const mockScreenings = [
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
  ];

  const availableIndustries = [
    '金融',
    '消费品',
    '科技',
    '医药',
    '制造业',
    '房地产',
    '能源',
    '材料',
  ];

  const handleRunScreening = () => {
    runScreening();
  };

  const handleCreateScreening = async () => {
    try {
      // Will implement GraphQL mutation later
      console.log('Creating screening:', newScreening);
      setIsCreateDialogOpen(false);
      setNewScreening({ name: '', description: '', criteria: {} });
    } catch (error) {
      console.error('Error creating screening:', error);
    }
  };

  const SummaryCard = ({ title, value, icon: Icon, trend, subtitle }: any) => (
    <Card className="bg-white dark:bg-gray-800">
      <CardContent className="p-6">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
              {title}
            </p>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {value}
            </p>
            {subtitle && (
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                {subtitle}
              </p>
            )}
          </div>
          <div className="h-12 w-12 bg-blue-100 dark:bg-blue-900/20 rounded-lg flex items-center justify-center">
            <Icon className="h-6 w-6 text-blue-600 dark:text-blue-400" />
          </div>
        </div>
        {trend && (
          <div className="mt-4 flex items-center">
            {trend > 0 ? (
              <TrendingUp className="h-4 w-4 text-green-500 mr-1" />
            ) : (
              <TrendingDown className="h-4 w-4 text-red-500 mr-1" />
            )}
            <span
              className={`text-sm font-medium ${trend > 0 ? 'text-green-500' : 'text-red-500'}`}
            >
              {Math.abs(trend)}%
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );

  const FilterSection = ({
    title,
    children,
  }: {
    title: string;
    children: React.ReactNode;
  }) => (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
        {title}
      </h3>
      {children}
    </div>
  );

  return (
    <div className="container mx-auto px-4 py-8 max-w-7xl">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
            股票筛选器
          </h1>
          <p className="text-gray-600 dark:text-gray-400 mt-2">
            基于多维度指标筛选优质投资标的
          </p>
        </div>

        <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
          <DialogTrigger asChild>
            <Button
              className="bg-blue-600 hover:bg-blue-700 text-white"
              data-testid="button-create-screening"
            >
              <Plus className="h-4 w-4 mr-2" />
              新建筛选
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>创建新的股票筛选</DialogTitle>
              <DialogDescription>
                设置筛选名称和描述，然后配置筛选条件
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <Input
                placeholder="筛选名称"
                value={newScreening.name}
                onChange={e =>
                  setNewScreening(prev => ({ ...prev, name: e.target.value }))
                }
                data-testid="input-screening-name"
              />
              <Textarea
                placeholder="筛选描述"
                value={newScreening.description}
                onChange={e =>
                  setNewScreening(prev => ({
                    ...prev,
                    description: e.target.value,
                  }))
                }
                data-testid="input-screening-description"
              />
              <div className="flex justify-end space-x-2">
                <Button
                  variant="outline"
                  onClick={() => setIsCreateDialogOpen(false)}
                >
                  取消
                </Button>
                <Button
                  onClick={handleCreateScreening}
                  data-testid="button-save-screening"
                >
                  创建
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <Tabs
        value={selectedTab}
        onValueChange={setSelectedTab}
        className="space-y-6"
      >
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="overview" data-testid="tab-overview">
            概览
          </TabsTrigger>
          <TabsTrigger value="screening" data-testid="tab-screening">
            实时筛选
          </TabsTrigger>
          <TabsTrigger value="saved" data-testid="tab-saved">
            已保存筛选
          </TabsTrigger>
          <TabsTrigger value="performance" data-testid="tab-performance">
            表现追踪
          </TabsTrigger>
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <SummaryCard
              title="总筛选数"
              value={mockScreeningSummary.totalScreenings}
              icon={Filter}
              subtitle="个筛选策略"
            />
            <SummaryCard
              title="活跃筛选"
              value={mockScreeningSummary.activeScreenings}
              icon={Activity}
              subtitle="正在运行"
            />
            <SummaryCard
              title="跟踪股票"
              value={mockScreeningSummary.totalStocks}
              icon={Target}
              subtitle="只股票"
            />
            <SummaryCard
              title="平均收益"
              value={`${mockScreeningSummary.averageReturn}%`}
              icon={TrendingUp}
              trend={mockScreeningSummary.averageReturn}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center">
                  <Star className="h-5 w-5 mr-2 text-green-500" />
                  最佳表现
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-semibold text-gray-900 dark:text-white">
                      {mockScreeningSummary.bestPerformer.stock.name}
                    </p>
                    <p className="text-sm text-gray-500">
                      {mockScreeningSummary.bestPerformer.stockCode}
                    </p>
                  </div>
                  <Badge className="bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400">
                    +{mockScreeningSummary.bestPerformer.returnPercentage}%
                  </Badge>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center">
                  <TrendingDown className="h-5 w-5 mr-2 text-red-500" />
                  最差表现
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-semibold text-gray-900 dark:text-white">
                      {mockScreeningSummary.worstPerformer.stock.name}
                    </p>
                    <p className="text-sm text-gray-500">
                      {mockScreeningSummary.worstPerformer.stockCode}
                    </p>
                  </div>
                  <Badge variant="destructive">
                    {mockScreeningSummary.worstPerformer.returnPercentage}%
                  </Badge>
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>近期筛选活动</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {mockScreenings.slice(0, 3).map(screening => (
                  <div
                    key={screening.id}
                    className="flex items-center justify-between p-4 border rounded-lg"
                  >
                    <div className="flex items-center space-x-4">
                      <div className="h-10 w-10 bg-blue-100 dark:bg-blue-900/20 rounded-lg flex items-center justify-center">
                        <Filter className="h-5 w-5 text-blue-600 dark:text-blue-400" />
                      </div>
                      <div>
                        <p className="font-semibold text-gray-900 dark:text-white">
                          {screening.name}
                        </p>
                        <p className="text-sm text-gray-500">
                          {screening.results.length} 只股票
                        </p>
                      </div>
                    </div>
                    <div className="text-right">
                      <Badge
                        variant={screening.isActive ? 'default' : 'secondary'}
                      >
                        {screening.isActive ? '运行中' : '已停止'}
                      </Badge>
                      <p className="text-sm text-gray-500 mt-1">
                        {new Date(screening.createdAt).toLocaleDateString()}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Real-time Screening Tab */}
        <TabsContent value="screening" className="space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Filter Panel */}
            <div className="lg:col-span-1">
              <Card className="sticky top-4">
                <CardHeader>
                  <CardTitle className="flex items-center">
                    <Filter className="h-5 w-5 mr-2" />
                    筛选条件
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-6">
                  <FilterSection title="行业筛选">
                    <Select
                      onValueChange={value =>
                        setScreeningCriteria(prev => ({
                          ...prev,
                          includeIndustries: value ? [value] : undefined,
                        }))
                      }
                    >
                      <SelectTrigger data-testid="select-include-industries">
                        <SelectValue placeholder="选择包含行业" />
                      </SelectTrigger>
                      <SelectContent>
                        {availableIndustries.map(industry => (
                          <SelectItem key={industry} value={industry}>
                            {industry}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Select
                      onValueChange={value =>
                        setScreeningCriteria(prev => ({
                          ...prev,
                          excludeIndustries: value ? [value] : undefined,
                        }))
                      }
                    >
                      <SelectTrigger data-testid="select-exclude-industries">
                        <SelectValue placeholder="选择排除行业" />
                      </SelectTrigger>
                      <SelectContent>
                        {availableIndustries.map(industry => (
                          <SelectItem key={industry} value={industry}>
                            {industry}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FilterSection>

                  <Separator />

                  <FilterSection title="基本面指标">
                    <div className="grid grid-cols-2 gap-2">
                      <Input
                        placeholder="PE最小值"
                        type="number"
                        onChange={e =>
                          setScreeningCriteria(prev => ({
                            ...prev,
                            peMin: e.target.value
                              ? parseFloat(e.target.value)
                              : undefined,
                          }))
                        }
                        data-testid="input-pe-min"
                      />
                      <Input
                        placeholder="PE最大值"
                        type="number"
                        onChange={e =>
                          setScreeningCriteria(prev => ({
                            ...prev,
                            peMax: e.target.value
                              ? parseFloat(e.target.value)
                              : undefined,
                          }))
                        }
                        data-testid="input-pe-max"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <Input
                        placeholder="PB最小值"
                        type="number"
                        onChange={e =>
                          setScreeningCriteria(prev => ({
                            ...prev,
                            pbMin: e.target.value
                              ? parseFloat(e.target.value)
                              : undefined,
                          }))
                        }
                        data-testid="input-pb-min"
                      />
                      <Input
                        placeholder="PB最大值"
                        type="number"
                        onChange={e =>
                          setScreeningCriteria(prev => ({
                            ...prev,
                            pbMax: e.target.value
                              ? parseFloat(e.target.value)
                              : undefined,
                          }))
                        }
                        data-testid="input-pb-max"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <Input
                        placeholder="ROE最小值"
                        type="number"
                        onChange={e =>
                          setScreeningCriteria(prev => ({
                            ...prev,
                            roeMin: e.target.value
                              ? parseFloat(e.target.value)
                              : undefined,
                          }))
                        }
                        data-testid="input-roe-min"
                      />
                      <Input
                        placeholder="ROE最大值"
                        type="number"
                        onChange={e =>
                          setScreeningCriteria(prev => ({
                            ...prev,
                            roeMax: e.target.value
                              ? parseFloat(e.target.value)
                              : undefined,
                          }))
                        }
                        data-testid="input-roe-max"
                      />
                    </div>
                  </FilterSection>

                  <Separator />

                  <FilterSection title="技术指标">
                    <div className="grid grid-cols-2 gap-2">
                      <Input
                        placeholder="RSI最小值"
                        type="number"
                        onChange={e =>
                          setScreeningCriteria(prev => ({
                            ...prev,
                            rsiMin: e.target.value
                              ? parseFloat(e.target.value)
                              : undefined,
                          }))
                        }
                        data-testid="input-rsi-min"
                      />
                      <Input
                        placeholder="RSI最大值"
                        type="number"
                        onChange={e =>
                          setScreeningCriteria(prev => ({
                            ...prev,
                            rsiMax: e.target.value
                              ? parseFloat(e.target.value)
                              : undefined,
                          }))
                        }
                        data-testid="input-rsi-max"
                      />
                    </div>
                  </FilterSection>

                  <Button
                    onClick={handleRunScreening}
                    className="w-full bg-blue-600 hover:bg-blue-700"
                    disabled={screeningLoading}
                    data-testid="button-run-screening"
                  >
                    {screeningLoading ? '筛选中...' : '开始筛选'}
                  </Button>
                </CardContent>
              </Card>
            </div>

            {/* Results Panel */}
            <div className="lg:col-span-2">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    <span className="flex items-center">
                      <BarChart3 className="h-5 w-5 mr-2" />
                      筛选结果
                    </span>
                    <Badge variant="outline" data-testid="text-results-count">
                      找到 5 只股票
                    </Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {screeningLoading ? (
                    <div className="flex items-center justify-center h-64">
                      <div className="text-center">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-4"></div>
                        <p className="text-gray-500">正在筛选股票...</p>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {/* Mock results */}
                      {[
                        {
                          code: '000001',
                          name: '平安银行',
                          score: 85.2,
                          pe: 12.5,
                          pb: 1.2,
                          roe: 15.8,
                          industry: '金融',
                        },
                        {
                          code: '600519',
                          name: '贵州茅台',
                          score: 92.1,
                          pe: 28.5,
                          pb: 8.9,
                          roe: 35.2,
                          industry: '消费品',
                        },
                        {
                          code: '300750',
                          name: '宁德时代',
                          score: 78.9,
                          pe: 22.8,
                          pb: 4.5,
                          roe: 28.6,
                          industry: '科技',
                        },
                        {
                          code: '002594',
                          name: '比亚迪',
                          score: 73.4,
                          pe: 18.9,
                          pb: 3.2,
                          roe: 22.1,
                          industry: '科技',
                        },
                        {
                          code: '000858',
                          name: '五粮液',
                          score: 81.7,
                          pe: 15.6,
                          pb: 3.8,
                          roe: 25.2,
                          industry: '消费品',
                        },
                      ].map((stock, index) => (
                        <div
                          key={stock.code}
                          className="p-4 border rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center space-x-4">
                              <div className="text-center">
                                <div className="font-semibold text-lg text-gray-900 dark:text-white">
                                  {stock.score}
                                </div>
                                <div className="text-xs text-gray-500">
                                  评分
                                </div>
                              </div>
                              <div>
                                <h3 className="font-semibold text-gray-900 dark:text-white">
                                  {stock.name}
                                </h3>
                                <p className="text-sm text-gray-500">
                                  {stock.code}
                                </p>
                                <Badge variant="outline" className="mt-1">
                                  {stock.industry}
                                </Badge>
                              </div>
                            </div>
                            <div className="text-right space-y-1">
                              <div className="flex space-x-4 text-sm">
                                <span>PE: {stock.pe}</span>
                                <span>PB: {stock.pb}</span>
                                <span>ROE: {stock.roe}%</span>
                              </div>
                              <Button
                                size="sm"
                                variant="outline"
                                data-testid={`button-add-stock-${stock.code}`}
                              >
                                <Plus className="h-4 w-4 mr-1" />
                                加入跟踪
                              </Button>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        {/* Saved Screenings Tab */}
        <TabsContent value="saved" className="space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {mockScreenings.map(screening => (
              <Card
                key={screening.id}
                className="hover:shadow-lg transition-shadow"
              >
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <div>
                      <CardTitle className="text-lg">
                        {screening.name}
                      </CardTitle>
                      <CardDescription className="mt-1">
                        {screening.description}
                      </CardDescription>
                    </div>
                    <Badge
                      variant={screening.isActive ? 'default' : 'secondary'}
                    >
                      {screening.isActive ? '运行中' : '已停止'}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-gray-500">创建时间</span>
                      <span>
                        {new Date(screening.createdAt).toLocaleDateString()}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-gray-500">跟踪股票</span>
                      <span>{screening.results.length} 只</span>
                    </div>
                    <div className="space-y-2">
                      <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
                        股票列表：
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {screening.results.map(result => (
                          <Badge
                            key={result.id}
                            variant="outline"
                            className="text-xs"
                          >
                            {result.stock.name}
                            <span
                              className={`ml-1 ${result.returnPercentage >= 0 ? 'text-green-600' : 'text-red-600'}`}
                            >
                              {result.returnPercentage >= 0 ? '+' : ''}
                              {result.returnPercentage}%
                            </span>
                          </Badge>
                        ))}
                      </div>
                    </div>
                    <div className="flex space-x-2 pt-2">
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1"
                        data-testid={`button-view-screening-${screening.id}`}
                      >
                        查看详情
                      </Button>
                      <Button
                        size="sm"
                        className="flex-1"
                        data-testid={`button-edit-screening-${screening.id}`}
                      >
                        编辑筛选
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        {/* Performance Tracking Tab */}
        <TabsContent value="performance" className="space-y-6">
          <Alert>
            <Activity className="h-4 w-4" />
            <AlertDescription>
              实时追踪你选中股票的表现，包括收益率分析、风险评估和基准比较。
            </AlertDescription>
          </Alert>

          {/* 表现概览 */}
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            <Card>
              <CardContent className="p-6">
                <div className="flex items-center">
                  <TrendingUp className="h-8 w-8 text-green-600 mr-3" />
                  <div>
                    <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
                      总收益率
                    </p>
                    <p className="text-2xl font-bold text-green-600">+12.8%</p>
                    <p className="text-xs text-gray-500">近30天</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-6">
                <div className="flex items-center">
                  <Shield className="h-8 w-8 text-blue-600 mr-3" />
                  <div>
                    <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
                      夏普比率
                    </p>
                    <p className="text-2xl font-bold text-blue-600">1.85</p>
                    <p className="text-xs text-gray-500">风险调整收益</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-6">
                <div className="flex items-center">
                  <Target className="h-8 w-8 text-orange-600 mr-3" />
                  <div>
                    <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
                      最大回撤
                    </p>
                    <p className="text-2xl font-bold text-orange-600">-4.2%</p>
                    <p className="text-xs text-gray-500">历史最大</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-6">
                <div className="flex items-center">
                  <BarChart3 className="h-8 w-8 text-purple-600 mr-3" />
                  <div>
                    <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
                      超额收益
                    </p>
                    <p className="text-2xl font-bold text-purple-600">+6.3%</p>
                    <p className="text-xs text-gray-500">相对沪深300</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* 收益率曲线 */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center">
                  <LineChart className="h-5 w-5 mr-2 text-blue-600" />
                  收益率表现
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <RechartsLineChart
                      data={[
                        { date: '01-01', portfolio: 0, benchmark: 0 },
                        { date: '01-07', portfolio: 2.1, benchmark: 1.2 },
                        { date: '01-14', portfolio: 4.5, benchmark: 2.8 },
                        { date: '01-21', portfolio: 3.2, benchmark: 2.1 },
                        { date: '01-28', portfolio: 6.8, benchmark: 3.9 },
                        { date: '02-04', portfolio: 8.9, benchmark: 4.7 },
                        { date: '02-11', portfolio: 7.5, benchmark: 4.2 },
                        { date: '02-18', portfolio: 10.2, benchmark: 5.8 },
                        { date: '02-25', portfolio: 12.8, benchmark: 6.5 },
                      ]}
                    >
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
                        }}
                        formatter={(value: number, name: string) => [
                          `${value.toFixed(2)}%`,
                          name === 'portfolio' ? '投资组合' : '沪深300',
                        ]}
                      />
                      <Line
                        type="monotone"
                        dataKey="benchmark"
                        stroke="#9CA3AF"
                        strokeWidth={2}
                        dot={false}
                      />
                      <Line
                        type="monotone"
                        dataKey="portfolio"
                        stroke="#3B82F6"
                        strokeWidth={3}
                        dot={false}
                      />
                    </RechartsLineChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex items-center justify-center mt-4 space-x-6 text-sm">
                  <div className="flex items-center">
                    <div className="w-3 h-3 bg-blue-500 rounded-full mr-2"></div>
                    <span>投资组合</span>
                  </div>
                  <div className="flex items-center">
                    <div className="w-3 h-3 bg-gray-400 rounded-full mr-2"></div>
                    <span>沪深300</span>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* 行业配置 */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center">
                  <PieChart className="h-5 w-5 mr-2 text-green-600" />
                  行业配置分析
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <RechartsPieChart
                      data={[
                        { name: '科技', value: 35, color: '#3B82F6' },
                        { name: '金融', value: 25, color: '#10B981' },
                        { name: '消费品', value: 20, color: '#F59E0B' },
                        { name: '医药', value: 12, color: '#EF4444' },
                        { name: '其他', value: 8, color: '#8B5CF6' },
                      ]}
                    >
                      <Tooltip
                        contentStyle={{
                          backgroundColor: 'hsl(var(--card))',
                          border: '1px solid hsl(var(--border))',
                          borderRadius: '8px',
                        }}
                        formatter={(value: number) => [`${value}%`, '配置比例']}
                      />
                      <Pie
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={40}
                        outerRadius={80}
                      >
                        {[
                          { name: '科技', value: 35, color: '#3B82F6' },
                          { name: '金融', value: 25, color: '#10B981' },
                          { name: '消费品', value: 20, color: '#F59E0B' },
                          { name: '医药', value: 12, color: '#EF4444' },
                          { name: '其他', value: 8, color: '#8B5CF6' },
                        ].map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={entry.color} />
                        ))}
                      </Pie>
                    </RechartsPieChart>
                  </ResponsiveContainer>
                </div>
                <div className="grid grid-cols-2 gap-2 mt-4 text-sm">
                  {[
                    { name: '科技', value: 35, color: '#3B82F6' },
                    { name: '金融', value: 25, color: '#10B981' },
                    { name: '消费品', value: 20, color: '#F59E0B' },
                    { name: '医药', value: 12, color: '#EF4444' },
                    { name: '其他', value: 8, color: '#8B5CF6' },
                  ].map(item => (
                    <div key={item.name} className="flex items-center">
                      <div
                        className="w-3 h-3 rounded-full mr-2"
                        style={{ backgroundColor: item.color }}
                      ></div>
                      <span className="text-gray-600 dark:text-gray-400">
                        {item.name} {item.value}%
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>

          {/* 风险指标 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center">
                <Shield className="h-5 w-5 mr-2 text-red-600" />
                风险分析指标
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                <div className="text-center">
                  <p className="text-2xl font-bold text-blue-600">12.5%</p>
                  <p className="text-sm text-gray-500">年化波动率</p>
                  <Progress value={25} className="mt-2" />
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-green-600">0.85</p>
                  <p className="text-sm text-gray-500">贝塔系数</p>
                  <Progress value={85} className="mt-2" />
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-orange-600">15.2%</p>
                  <p className="text-sm text-gray-500">跟踪误差</p>
                  <Progress value={30} className="mt-2" />
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-purple-600">68.9%</p>
                  <p className="text-sm text-gray-500">胜率</p>
                  <Progress value={69} className="mt-2" />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 持仓明细 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span className="flex items-center">
                  <Building2 className="h-5 w-5 mr-2 text-indigo-600" />
                  持仓明细
                </span>
                <Badge variant="outline">8 只股票</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {[
                  {
                    code: '600519',
                    name: '贵州茅台',
                    weight: 18.5,
                    return: 15.2,
                    sector: '消费品',
                  },
                  {
                    code: '000001',
                    name: '平安银行',
                    weight: 15.2,
                    return: 8.9,
                    sector: '金融',
                  },
                  {
                    code: '300750',
                    name: '宁德时代',
                    weight: 12.8,
                    return: 22.1,
                    sector: '科技',
                  },
                  {
                    code: '002594',
                    name: '比亚迪',
                    weight: 11.3,
                    return: -3.5,
                    sector: '科技',
                  },
                  {
                    code: '000858',
                    name: '五粮液',
                    weight: 9.8,
                    return: 12.7,
                    sector: '消费品',
                  },
                ].map(stock => (
                  <div
                    key={stock.code}
                    className="flex items-center justify-between p-3 border rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50"
                  >
                    <div className="flex items-center space-x-4">
                      <div>
                        <p className="font-semibold">{stock.name}</p>
                        <p className="text-sm text-gray-500">{stock.code}</p>
                      </div>
                      <Badge variant="outline">{stock.sector}</Badge>
                    </div>
                    <div className="text-right">
                      <p className="font-semibold">{stock.weight}%</p>
                      <p
                        className={`text-sm ${stock.return >= 0 ? 'text-green-600' : 'text-red-600'}`}
                      >
                        {stock.return >= 0 ? '+' : ''}
                        {stock.return}%
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* 回测结果 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center">
                <Clock className="h-5 w-5 mr-2 text-yellow-600" />
                历史回测表现
              </CardTitle>
              <CardDescription>基于过去12个月数据的回测分析</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="text-center p-4 border rounded-lg">
                  <p className="text-2xl font-bold text-green-600">+28.9%</p>
                  <p className="text-sm text-gray-500">年化收益率</p>
                </div>
                <div className="text-center p-4 border rounded-lg">
                  <p className="text-2xl font-bold text-blue-600">2.15</p>
                  <p className="text-sm text-gray-500">信息比率</p>
                </div>
                <div className="text-center p-4 border rounded-lg">
                  <p className="text-2xl font-bold text-purple-600">73.2%</p>
                  <p className="text-sm text-gray-500">月度胜率</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
