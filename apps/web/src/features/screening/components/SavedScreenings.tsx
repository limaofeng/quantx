import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { financialToneClass } from '@/shared/utils/financialColors';

interface SavedScreeningResult {
  id: string;
  stock: {
    name: string;
  };
  returnPercentage: number;
}

interface SavedScreening {
  id: string;
  name: string;
  description: string;
  isActive: boolean;
  createdAt: Date | string;
  results: SavedScreeningResult[];
}

interface SavedScreeningsProps {
  screenings: SavedScreening[];
}

export function SavedScreenings({ screenings }: SavedScreeningsProps) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {screenings.map(screening => (
        <Card key={screening.id} className="hover:shadow-lg transition-shadow">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-lg">{screening.name}</CardTitle>
                <CardDescription className="mt-1">
                  {screening.description}
                </CardDescription>
              </div>
              <Badge variant={screening.isActive ? 'default' : 'secondary'}>
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
                        className={`ml-1 ${financialToneClass(result.returnPercentage)}`}
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
  );
}
