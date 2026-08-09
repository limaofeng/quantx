import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface RedeemCashDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onRedeem: (amount: number) => Promise<void>;
}

export function RedeemCashDialog({
  isOpen,
  onClose,
  onRedeem,
}: RedeemCashDialogProps) {
  const [amount, setAmount] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);

  const availableCash = 100000; // 模拟可用现金

  const handleRedeem = async () => {
    const redeemAmount = parseFloat(amount);
    if (isNaN(redeemAmount) || redeemAmount <= 0) {
      return;
    }

    if (redeemAmount > availableCash) {
      return;
    }

    setIsProcessing(true);
    try {
      await onRedeem(redeemAmount);
      setAmount('');
      onClose();
    } finally {
      setIsProcessing(false);
    }
  };

  const quickAmounts = [1000, 5000, 10000, 20000];

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>赎回现金</DialogTitle>
          <DialogDescription>
            从您的投资账户中赎回现金到银行账户
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label htmlFor="amount">赎回金额</Label>
            <Input
              id="amount"
              type="number"
              placeholder="请输入赎回金额"
              value={amount}
              onChange={e => setAmount(e.target.value)}
              data-testid="redeem-amount-input"
            />
            <p className="text-sm text-muted-foreground mt-1">
              可用现金：¥{availableCash.toLocaleString()}
            </p>
          </div>

          <div>
            <Label>快速选择</Label>
            <div className="grid grid-cols-2 gap-2 mt-2">
              {quickAmounts.map(quickAmount => (
                <Button
                  key={quickAmount}
                  variant="outline"
                  size="sm"
                  onClick={() => setAmount(quickAmount.toString())}
                  data-testid={`quick-amount-${quickAmount}`}
                >
                  ¥{quickAmount.toLocaleString()}
                </Button>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            onClick={handleRedeem}
            disabled={
              isProcessing ||
              !amount ||
              isNaN(parseFloat(amount)) ||
              parseFloat(amount) <= 0 ||
              parseFloat(amount) > availableCash
            }
            data-testid="confirm-redeem-button"
          >
            {isProcessing ? '处理中...' : '确认赎回'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
