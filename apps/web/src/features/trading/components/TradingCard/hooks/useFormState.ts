import { useState, useCallback, useMemo } from 'react';

export type ManualOrderType = 'best' | 'limit';

/**
 * 表单基础状态管理
 */
export function useFormState(initialTradeType: 'buy' | 'sell' = 'buy') {
  const [tradeType, setTradeType] = useState<'buy' | 'sell'>(initialTradeType);
  const [orderType, setOrderType] = useState<ManualOrderType>('limit');
  const [quantity, setQuantity] = useState('');
  const [price, setPrice] = useState('');

  const resetForm = useCallback(() => {
    setQuantity('');
    setPrice('');
  }, []);

  return useMemo(
    () => ({
      tradeType,
      setTradeType,
      orderType,
      setOrderType,
      quantity,
      setQuantity,
      price,
      setPrice,
      resetForm,
    }),
    [tradeType, orderType, quantity, price, resetForm]
  );
}
