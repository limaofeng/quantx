"""
简单的GraphQL订阅测试客户端
使用requests测试查询功能，展示订阅功能的使用方法
"""

import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def test_basic_queries():
    """测试基本的GraphQL查询功能"""
    url = "http://localhost:8000/graphql"

    print("=== 测试GraphQL查询功能 ===\n")

    # 测试股票列表查询
    query1 = {
        "query": """
        {
            stocks {
                code
                name
                currentPrice
                change
                changePercent
                volume
            }
        }
        """
    }

    response = requests.post(url, json=query1)
    if response.status_code == 200:
        data = response.json()
        print("📈 股票列表查询成功:")
        for stock in data["data"]["stocks"][:3]:  # 显示前3只股票
            print(f"  {stock['name']}({stock['code']}): ¥{stock['currentPrice']} "
                  f"({stock['change']:+.2f}, {stock['changePercent']:+.2f}%)")
    else:
        print(f"查询失败: {response.status_code}")

    print()

    # 测试单只股票查询
    query2 = {
        "query": """
        {
            stock(stockCode: "600519") {
                code
                name
                currentPrice
                change
                changePercent
                marketCap
                peRatio
            }
        }
        """
    }

    response = requests.post(url, json=query2)
    if response.status_code == 200:
        data = response.json()
        stock = data["data"]["stock"]
        if stock:
            print("📊 贵州茅台详细信息:")
            print(f"  代码: {stock['code']}")
            print(f"  名称: {stock['name']}")
            print(f"  价格: ¥{stock['currentPrice']}")
            print(f"  涨跌: {stock['change']:+.2f} ({stock['changePercent']:+.2f}%)")
            print(f"  市值: {stock['marketCap']:.2f}亿")
            print(f"  市盈率: {stock['peRatio']:.2f}")
        else:
            print("股票不存在")
    else:
        print(f"查询失败: {response.status_code}")

    print()

def test_orders_and_positions():
    """测试订单和持仓查询"""
    url = "http://localhost:8000/graphql"

    print("=== 测试订单和持仓查询 ===\n")

    # 测试持仓查询
    query = {
        "query": """
        {
            positions {
                id
                stockCode
                stockName
                quantity
                avgCost
                currentPrice
                marketValue
                profitLoss
                profitLossPercent
            }
        }
        """
    }

    response = requests.post(url, json=query)
    if response.status_code == 200:
        data = response.json()
        print("💼 当前持仓:")
        for position in data["data"]["positions"]:
            profit_color = "🟢" if position['profitLoss'] >= 0 else "🔴"
            print(f"  {profit_color} {position['stockName']}({position['stockCode']}): "
                  f"{position['quantity']}股 "
                  f"成本¥{position['avgCost']} 现价¥{position['currentPrice']} "
                  f"盈亏{position['profitLoss']:+.2f}({position['profitLossPercent']:+.2f}%)")

    print()

def test_trades():
    """测试成交记录查询"""
    url = "http://localhost:8000/graphql"

    print("=== 测试成交记录查询 ===\n")

    # 测试所有成交记录查询
    query1 = {
        "query": """
        {
            trades {
                id
                orderId
                stockCode
                stockName
                side
                price
                quantity
                amount
                fee
                commission
                tradeTime
                venue
            }
        }
        """
    }

    response = requests.post(url, json=query1)
    if response.status_code == 200:
        data = response.json()
        print("💰 所有成交记录:")
        for trade in data["data"]["trades"]:
            side_emoji = "🟢" if trade['side'] == 'buy' else "🔴"
            print(f"  {side_emoji} {trade['stockName']}({trade['stockCode']}): "
                  f"{trade['quantity']}股 @¥{trade['price']} "
                  f"成交额¥{trade['amount']:.2f} 手续费¥{trade['fee']:.2f}")

    print()

    # 测试指定订单的成交记录
    query2 = {
        "query": """
        {
            orderTrades(orderId: 1) {
                id
                price
                quantity
                amount
                fee
                tradeTime
            }
        }
        """
    }

    response = requests.post(url, json=query2)
    if response.status_code == 200:
        data = response.json()
        print("📋 订单1的成交明细:")
        for trade in data["data"]["orderTrades"]:
            print(f"  成交: {trade['quantity']}股 @¥{trade['price']} "
                  f"金额¥{trade['amount']:.2f} 费用¥{trade['fee']:.2f}")

    print()

def test_create_order():
    """测试创建订单"""
    url = "http://localhost:8000/graphql"

    print("=== 测试创建订单 ===\n")

    mutation = {
        "query": """
        mutation {
            createOrder(orderInput: {
                stockCode: "000001"
                stockName: "平安银行"
                orderType: BUY
                quantity: 100
                price: 12.50
            }) {
                id
                stockCode
                stockName
                orderType
                quantity
                price
                totalAmount
                status
                createTime
            }
        }
        """
    }

    response = requests.post(url, json=mutation)
    if response.status_code == 200:
        data = response.json()
        order = data["data"]["createOrder"]
        print("📝 订单创建成功:")
        print(f"  订单号: {order['id']}")
        print(f"  股票: {order['stockName']}({order['stockCode']})")
        print(f"  类型: {order['orderType']}")
        print(f"  数量: {order['quantity']}股")
        print(f"  价格: ¥{order['price']}")
        print(f"  总金额: ¥{order['totalAmount']}")
        print(f"  状态: {order['status']}")
    else:
        print(f"创建订单失败: {response.status_code}")
        print(response.text)

    print()

def show_subscription_examples():
    """显示订阅功能的使用示例"""
    print("=== GraphQL订阅功能示例 ===\n")

    print("🔄 要使用实时订阅功能，您可以在GraphiQL界面中测试:")
    print("   访问: http://localhost:8000/graphql")
    print()

    print("📈 股票实时价格订阅示例:")
    print("""
    subscription {
        stockPrice(stockCode: "000001") {
            stockCode
            stockName
            currentPrice
            change
            changePercent
            timestamp
        }
    }
    """)

    print("📊 K线数据订阅示例:")
    print("""
    subscription {
        stockKline(stockCode: "600519", period: "1m") {
            stockCode
            timestamp
            open
            high
            low
            close
            volume
            period
        }
    }
    """)

    print("🔍 市场深度订阅示例:")
    print("""
    subscription {
        marketDepth(stockCode: "000858") {
            stockCode
            timestamp
            bids
            asks
        }
    }
    """)

    print("📈 多股票价格订阅示例:")
    print("""
    subscription {
        multiStockPrices(stockCodes: ["000001", "600519", "000858"]) {
            stockCode
            stockName
            currentPrice
            change
            changePercent
            timestamp
        }
    }
    """)

    print("📋 订单状态变更订阅示例:")
    print("""
    subscription {
        orderUpdates {
            id
            stockCode
            status
            filledQuantity
        }
    }
    """)

    print("💰 成交记录订阅示例:")
    print("""
    subscription {
        tradeUpdates {
            id
            orderId
            stockCode
            side
            price
            quantity
            amount
            tradeTime
        }
    }
    """)

if __name__ == "__main__":
    print("QuantX GraphQL API 功能测试")
    print("=" * 50)

    try:
        # 测试基本查询
        test_basic_queries()

        # 测试订单和持仓
        test_orders_and_positions()

        # 测试成交记录
        test_trades()

        # 测试创建订单
        test_create_order()

        # 显示订阅示例
        show_subscription_examples()

        print("✅ 所有测试完成！")
        print("💡 提示: 访问 http://localhost:8000/graphql 体验完整的GraphQL功能")

    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到服务器，请确保服务器正在运行:")
        print("   activate your QuantX Python environment")
        print("   cd e:\\Workspace\\quantx\\api")
        print("   python main.py")
    except Exception as e:
        print(f"❌ 测试失败: {e}")
