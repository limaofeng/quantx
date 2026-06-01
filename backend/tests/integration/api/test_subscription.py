"""
GraphQL订阅测试客户端
用于测试股票实时数据订阅功能
"""
import asyncio
import websockets
import json
import time

async def test_subscription():
    """测试股票价格订阅"""
    uri = "ws://localhost:8000/graphql"

    # WebSocket子协议
    subprotocol = "graphql-transport-ws"

    try:
        async with websockets.connect(uri, subprotocols=[subprotocol]) as websocket:
            print("WebSocket连接已建立")

            # 发送连接初始化消息
            init_message = {
                "type": "connection_init"
            }
            await websocket.send(json.dumps(init_message))

            # 接收连接确认
            response = await websocket.recv()
            print(f"连接响应: {response}")

            # 发送订阅消息
            subscription_message = {
                "id": "1",
                "type": "start",
                "payload": {
                    "query": """
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
                    """
                }
            }
            await websocket.send(json.dumps(subscription_message))
            print("订阅消息已发送")

            # 接收订阅数据
            count = 0
            while count < 10:  # 接收10次数据后停止
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    data = json.loads(message)

                    if data.get("type") == "data":
                        price_data = data["payload"]["data"]["stockPrice"]
                        print(f"📈 {price_data['stockName']}({price_data['stockCode']}): "
                              f"¥{price_data['currentPrice']} "
                              f"({price_data['change']:+.2f}, {price_data['changePercent']:+.2f}%)")
                        count += 1
                    else:
                        print(f"收到消息: {data}")

                except asyncio.TimeoutError:
                    print("等待数据超时")
                    break

            print("测试完成")

    except Exception as e:
        print(f"连接错误: {e}")

async def test_kline_subscription():
    """测试K线数据订阅"""
    uri = "ws://localhost:8000/graphql"
    subprotocol = "graphql-transport-ws"

    try:
        async with websockets.connect(uri, subprotocols=[subprotocol]) as websocket:
            print("\n=== K线数据订阅测试 ===")

            # 初始化连接
            await websocket.send(json.dumps({"type": "connection_init"}))
            await websocket.recv()

            # 订阅K线数据
            subscription_message = {
                "id": "2",
                "type": "start",
                "payload": {
                    "query": """
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
                    """
                }
            }
            await websocket.send(json.dumps(subscription_message))

            # 接收K线数据
            count = 0
            while count < 3:  # 接收3次K线数据
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    data = json.loads(message)

                    if data.get("type") == "data":
                        kline = data["payload"]["data"]["stockKline"]
                        print(f"📊 K线数据 {kline['stockCode']} {kline['period']}: "
                              f"开盘{kline['open']} 最高{kline['high']} "
                              f"最低{kline['low']} 收盘{kline['close']} "
                              f"成交量{kline['volume']}")
                        count += 1

                except asyncio.TimeoutError:
                    print("等待K线数据超时")
                    break

    except Exception as e:
        print(f"K线订阅错误: {e}")

if __name__ == "__main__":
    print("开始测试GraphQL订阅功能...")
    print("确保服务器正在运行: http://localhost:8000")

    # 测试股票价格订阅
    asyncio.run(test_subscription())

    # 测试K线数据订阅
    asyncio.run(test_kline_subscription())

    print("\n测试完成！")
