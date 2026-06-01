"""
布林带状态判断工具
提供准确的布林带超买超卖状态判断
"""

from typing import Dict, Optional
from enum import Enum


class BollingerStatus(str, Enum):
    """布林带状态枚举"""
    OVERBOUGHT = "超买"           # 价格接近或突破上轨
    APPROACH_OVERBOUGHT = "接近超买"  # 价格接近上轨但未达到
    NEUTRAL = "中性"              # 价格在中轨附近
    APPROACH_OVERSOLD = "接近超卖"  # 价格接近下轨但未达到
    OVERSOLD = "超卖"             # 价格接近或跌破下轨
    SQUEEZE = "收窄"              # 布林带收窄，波动率低


def calculate_bollinger_status(
    current_price: float,
    upper_band: float,
    middle_band: float,
    lower_band: float,
    percent_b: float,
    bandwidth: float,
    squeeze_threshold: float = 0.02,
) -> Dict[str, any]:
    """
    计算布林带状态

    Args:
        current_price: 当前价格
        upper_band: 布林带上轨
        middle_band: 布林带中轨
        lower_band: 布林带下轨
        percent_b: 价格在布林带中的位置百分比 (0-1)
        bandwidth: 布林带带宽百分比
        squeeze_threshold: 收窄判断阈值，默认2%

    Returns:
        {
            "status": 状态枚举值,
            "description": 状态描述,
            "emoji": 状态图标,
            "position": 价格位置百分比,
            "is_valid": 当前状态是否有效
        }
    """
    # 验证输入数据
    if not all([upper_band, middle_band, lower_band, percent_b is not None, bandwidth is not None]):
        return {
            "status": BollingerStatus.NEUTRAL,
            "description": "数据不足",
            "emoji": "⚪",
            "position": None,
            "is_valid": False
        }

    # 检查布林带是否收窄
    if bandwidth < squeeze_threshold:
        return {
            "status": BollingerStatus.SQUEEZE,
            "description": f"布林带收窄 (带宽: {bandwidth:.1%})",
            "emoji": "🔒",
            "position": percent_b,
            "is_valid": True
        }

    # 计算价格与上下轨的距离
    distance_to_upper = abs((current_price - upper_band) / (upper_band - lower_band)) if upper_band != lower_band else 0
    distance_to_lower = abs((current_price - lower_band) / (upper_band - lower_band)) if upper_band != lower_band else 0

    # 判断超买/超卖状态
    if percent_b >= 0.8:
        return {
            "status": BollingerStatus.OVERBOUGHT,
            "description": f"🔴 超买 (位置: {percent_b:.1%})",
            "emoji": "🔴",
            "position": percent_b,
            "is_valid": True
        }
    elif percent_b >= 0.7:
        return {
            "status": BollingerStatus.APPROACH_OVERBOUGHT,
            "description": f"🟠 接近超买 (位置: {percent_b:.1%})",
            "emoji": "🟠",
            "position": percent_b,
            "is_valid": True
        }
    elif percent_b <= 0.2:
        return {
            "status": BollingerStatus.OVERSOLD,
            "description": f"🟢 超卖 (位置: {percent_b:.1%})",
            "emoji": "🟢",
            "position": percent_b,
            "is_valid": True
        }
    elif percent_b <= 0.3:
        return {
            "status": BollingerStatus.APPROACH_OVERSOLD,
            "description": f"🟡 接近超卖 (位置: {percent_b:.1%})",
            "emoji": "🟡",
            "position": percent_b,
            "is_valid": True
        }
    elif 0.45 <= percent_b <= 0.55:
        return {
            "status": BollingerStatus.NEUTRAL,
            "description": f"⚪ 中性 (位置: {percent_b:.1%})",
            "emoji": "⚪",
            "position": percent_b,
            "is_valid": True
        }
    elif 0.4 <= percent_b < 0.45:
        return {
            "status": BollingerStatus.NEUTRAL,
            "description": f"🔵 偏弱 (位置: {percent_b:.1%})",
            "emoji": "🔵",
            "position": percent_b,
            "is_valid": True
        }
    else:  # 0.55 < percent_b < 0.7
        return {
            "status": BollingerStatus.NEUTRAL,
            "description": f"🟣 偏强 (位置: {percent_b:.1%})",
            "emoji": "🟣",
            "position": percent_b,
            "is_valid": True
        }


def get_bollinger_signal_summary(bollinger_data: Dict[str, float]) -> str:
    """
    获取布林带信号的简洁描述

    Args:
        bollinger_data: 包含布林带数据的字典
            {
                "current_price": float,
                "upper": float,
                "middle": float,
                "lower": float,
                "percent_b": float,
                "bandwidth": float
            }

    Returns:
        格式化的布林带状态字符串
    """
    current_price = bollinger_data.get("current_price")
    upper = bollinger_data.get("upper")
    middle = bollinger_data.get("middle")
    lower = bollinger_data.get("lower")
    percent_b = bollinger_data.get("percent_b")
    bandwidth = bollinger_data.get("bandwidth")

    if not all([current_price, upper, middle, lower, percent_b is not None, bandwidth is not None]):
        return "BOLL: 数据不足"

    status_result = calculate_bollinger_status(
        current_price=current_price,
        upper_band=upper,
        middle_band=middle,
        lower_band=lower,
        percent_b=percent_b,
        bandwidth=bandwidth
    )

    return f"BOLL: {status_result['description']}"


# 示例使用
if __name__ == "__main__":
    # 长江电力示例数据
    example_data = {
        "current_price": 26.39,
        "upper": 27.71,
        "middle": 27.01,
        "lower": 26.31,
        "percent_b": 0.116,  # (26.39 - 26.31) / (27.71 - 26.31) ≈ 0.057, 修正为实际计算
        "bandwidth": 0.052
    }

    # 重新计算正确的percent_b
    example_data["percent_b"] = (26.39 - 26.31) / (27.71 - 26.31)

    result = calculate_bollinger_status(
        current_price=example_data["current_price"],
        upper_band=example_data["upper"],
        middle_band=example_data["middle"],
        lower_band=example_data["lower"],
        percent_b=example_data["percent_b"],
        bandwidth=example_data["bandwidth"]
    )

    print(f"长江电力布林带状态: {result}")
    print(f"完整描述: {get_bollinger_signal_summary(example_data)}")