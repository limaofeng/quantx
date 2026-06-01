"""
测试布林带状态判断逻辑
"""

def calculate_bollinger_status(
    current_price: float,
    upper_band: float,
    middle_band: float,
    lower_band: float,
    percent_b: float,
    bandwidth: float,
    squeeze_threshold: float = 0.02,
):
    """计算布林带状态"""
    # 验证输入数据
    if not all([upper_band, middle_band, lower_band, percent_b is not None, bandwidth is not None]):
        return {
            "status": "NEUTRAL",
            "description": "数据不足",
            "emoji": "白色",
            "position": None,
            "is_valid": False
        }

    # 检查布林带是否收窄
    if bandwidth < squeeze_threshold:
        return {
            "status": "SQUEEZE",
            "description": f"布林带收窄 (带宽: {bandwidth:.1%})",
            "emoji": "锁定",
            "position": percent_b,
            "is_valid": True
        }

    # 判断超买/超卖状态
    if percent_b >= 0.8:
        return {
            "status": "OVERBOUGHT",
            "description": f"超买 (位置: {percent_b:.1%})",
            "emoji": "红色",
            "position": percent_b,
            "is_valid": True
        }
    elif percent_b >= 0.7:
        return {
            "status": "APPROACH_OVERBOUGHT",
            "description": f"接近超买 (位置: {percent_b:.1%})",
            "emoji": "橙色",
            "position": percent_b,
            "is_valid": True
        }
    elif percent_b <= 0.2:
        return {
            "status": "OVERSOLD",
            "description": f"超卖 (位置: {percent_b:.1%})",
            "emoji": "绿色",
            "position": percent_b,
            "is_valid": True
        }
    elif percent_b <= 0.3:
        return {
            "status": "APPROACH_OVERSOLD",
            "description": f"接近超卖 (位置: {percent_b:.1%})",
            "emoji": "黄色",
            "position": percent_b,
            "is_valid": True
        }
    elif 0.45 <= percent_b <= 0.55:
        return {
            "status": "NEUTRAL",
            "description": f"中性 (位置: {percent_b:.1%})",
            "emoji": "白色",
            "position": percent_b,
            "is_valid": True
        }
    elif 0.4 <= percent_b < 0.45:
        return {
            "status": "NEUTRAL",
            "description": f"偏弱 (位置: {percent_b:.1%})",
            "emoji": "蓝色",
            "position": percent_b,
            "is_valid": True
        }
    else:  # 0.55 < percent_b < 0.7
        return {
            "status": "NEUTRAL",
            "description": f"偏强 (位置: {percent_b:.1%})",
            "emoji": "紫色",
            "position": percent_b,
            "is_valid": True
        }


# 长江电力示例数据
current_price = 26.39
upper_band = 27.71
middle_band = 27.01
lower_band = 26.31
bandwidth = 0.052

# 计算正确的percent_b
percent_b = (current_price - lower_band) / (upper_band - lower_band)

print("=== 长江电力布林带分析 ===")
print(f"当前价格: {current_price}")
print(f"布林带上轨: {upper_band}")
print(f"布林带中轨: {middle_band}")
print(f"布林带下轨: {lower_band}")
print(f"带宽: {bandwidth:.1%}")
print(f"Percent B: {percent_b:.1%}")
print(f"价格距离下轨: {((current_price - lower_band) / (upper_band - lower_band)):.1%}")
print(f"价格距离上轨: {((upper_band - current_price) / (upper_band - lower_band)):.1%}")

result = calculate_bollinger_status(
    current_price=current_price,
    upper_band=upper_band,
    middle_band=middle_band,
    lower_band=lower_band,
    percent_b=percent_b,
    bandwidth=bandwidth
)

print("\n=== 修复后的判断结果 ===")
print(f"状态: {result['status']}")
print(f"描述: {result['description']}")
print(f"位置: {result['position']:.1%}")

# 对比原来的错误判断
print("\n=== 原来的错误判断 vs 修复后 ===")
print(f"原来: 接近超卖 (Position: 5.6%, Bandwidth: 5.2%)")
print(f"修复后: {result['description']}")
is_correct = '接近超卖' in result['description']
print(f"\n结论: 原来{'正确' if is_correct else '错误'}判断")
print(f"分析: Percent B = {percent_b:.1%}, 小于20%阈值，应该是'超卖'而非'接近超卖'")