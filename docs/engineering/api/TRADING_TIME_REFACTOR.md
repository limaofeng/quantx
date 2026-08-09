# 交易时间职责

交易日历与交易时段查询位于 infrastructure 服务边界，策略只能通过
`StrategyInput.market_context` 接收已经裁定的时间上下文。策略不得读取
系统时钟或自行推断节假日。

回测由注入时钟驱动，实盘由 Engine 构造输入；两者必须调用相同的
`StrategyBase.step`。缺少日历数据时应拒绝或保守降级，不能假设为交易日。
