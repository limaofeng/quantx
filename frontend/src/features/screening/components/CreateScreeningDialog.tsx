import { Plus } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

interface CreateScreeningDialogProps {
  isOpen: boolean;
  setIsOpen: (open: boolean) => void;
  newScreening: {
    name: string;
    description: string;
    criteria: any;
  };
  setNewScreening: (screening: any) => void;
  onCreateScreening: () => Promise<void>;
}

export function CreateScreeningDialog({
  isOpen,
  setIsOpen,
  newScreening,
  setNewScreening,
  onCreateScreening,
}: CreateScreeningDialogProps) {
  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
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
            <Button variant="outline" onClick={() => setIsOpen(false)}>
              取消
            </Button>
            <Button
              onClick={onCreateScreening}
              data-testid="button-save-screening"
            >
              创建
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
