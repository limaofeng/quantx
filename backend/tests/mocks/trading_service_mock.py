"""
交易服务Mock实现

用于集成测试，模拟交易服务的行为但不执行真实交易
"""

from typing import Dict, Any, Optional
import asyncio
import uuid
from datetime import datetime

from models.enums import OrderStatus, OrderType, PriceType


class MockTradingService:
    """模拟交易服务"""

    def __init__(self):
        self.mock_account_cash = 100000.0  # 模拟账户资金
        self.mock_orders = {}  # 存储模拟订单
        self.order_counter = 1

    async def get_account_info(self, realtime: bool = False) -> Any:
        """获取模拟账户信息"""
        from types import SimpleNamespace

        return SimpleNamespace(
            cash=self.mock_account_cash,
            total_assets=self.mock_account_cash,
            market_value=0,
            available_cash=self.mock_account_cash
        )

    async def place_order(
        self,
        stock_code: str,
        order_type: OrderType,
        order_volume: int,
        price_type: PriceType,
        price: float,
        order_remark: str = ""
    ) -> Dict[str, Any]:
        """模拟下单操作"""

        # 模拟下单验证
        if order_volume <= 0:
            return {
                "success": False,
                "error": "下单数量必须大于0"
            }

        if price <= 0:
            return {
                "success": False,
                "error": "价格必须大于0"
            }

        # 生成模拟订单ID
        order_id = f"MOCK_{self.order_counter:06d}"
        self.order_counter += 1

        # 创建模拟订单
        mock_order = {
            "order_id": order_id,
            "stock_code": stock_code,
            "order_type": order_type,
            "order_volume": order_volume,
            "price_type": price_type,
            "price": price,
            "order_remark": order_remark,
            "status": OrderStatus.SUBMITTED,
            "created_at": datetime.now(),
            "filled_volume": 0,
            "filled_amount": 0.0
        }

        self.mock_orders[order_id] = mock_order

        # 模拟成功下单
        return {
            "success": True,
            "order_id": order_id,
            "message": f"模拟下单成功: {stock_code}, 数量: {order_volume}, 价格: {price}"
        }

    async def check_order_status(self, order_id: str, timeout_seconds: int = 0) -> Dict[str, Any]:
        """检查模拟订单状态"""

        if order_id not in self.mock_orders:
            return {
                "success": False,
                "error": f"订单 {order_id} 不存在"
            }

        mock_order = self.mock_orders[order_id]

        # 模拟订单处理延时
        if timeout_seconds > 0:
            await asyncio.sleep(min(timeout_seconds, 2))  # 最多等待2秒

        # 模拟订单成交 (90% 概率成交)
        import random
        if random.random() < 0.9:
            # 成交
            mock_order["status"] = OrderStatus.FILLED
            mock_order["filled_volume"] = mock_order["order_volume"]
            mock_order["filled_amount"] = mock_order["order_volume"] * mock_order["price"]
        else:
            # 部分成交或未成交
            if random.random() < 0.3:
                mock_order["status"] = OrderStatus.PARTIAL_FILLED
                mock_order["filled_volume"] = int(mock_order["order_volume"] * 0.5)
                mock_order["filled_amount"] = mock_order["filled_volume"] * mock_order["price"]
            else:
                mock_order["status"] = OrderStatus.SUBMITTED

        return {
            "success": True,
            "order_id": order_id,
            "status": mock_order["status"],
            "filled_volume": mock_order["filled_volume"],
            "filled_amount": mock_order["filled_amount"],
            "original_volume": mock_order["order_volume"]
        }

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """取消模拟订单"""

        if order_id not in self.mock_orders:
            return {
                "success": False,
                "error": f"订单 {order_id} 不存在"
            }

        mock_order = self.mock_orders[order_id]
        if mock_order["status"] in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
            return {
                "success": False,
                "error": f"订单 {order_id} 已完成，无法取消"
            }

        mock_order["status"] = OrderStatus.CANCELLED

        return {
            "success": True,
            "order_id": order_id,
            "message": f"模拟订单 {order_id} 已取消"
        }

    def reset_mock_state(self):
        """重置模拟状态"""
        self.mock_account_cash = 100000.0
        self.mock_orders = {}
        self.order_counter = 1
