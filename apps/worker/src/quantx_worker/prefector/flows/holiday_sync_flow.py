"""
Holiday Sync Prefect Flow
自动化休市同步工作流
"""

from datetime import datetime

from prefect import flow, get_run_logger, task
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.ai.llm_service import LLMService
from quantx_infrastructure.services.holiday_service import HolidayService
from quantx_infrastructure.services.scrapers.sse_scraper import SSEScraper


@task(name="同步上交所休市公告")
async def sync_sse_holidays_task():
    logger = get_run_logger()

    # 初始化服务
    scraper = SSEScraper()
    parser = LLMService(logger=logger)
    holiday_service = HolidayService()
    
    logger.info("开始执行自动化休市安排同步...")
    
    # 1. 抓取公告
    announcement = await scraper.get_latest_holiday_announcement()
    if not announcement:
        logger.error("无法获取最新公告")
        raise ValueError("无法获取最新公告")
    
    content = announcement["content"]
    title = announcement["title"]
    logger.info(f"成功抓取公告标题: {title}")
    logger.info(f"成功抓取公告内容 (长度: {len(content)} 字符):\n{content}")
    
    # 从标题中提取年份 (例如 "2026年休市安排" -> 2026)
    import re
    year_match = re.search(r'(\d{4})', title)
    detected_year = int(year_match.group(1)) if year_match else time_utils.now().year
    if year_match:
        logger.info(f"从标题中检测到年份: {detected_year}")
    
    # 2. 调用 AI 解析 (将检测到的年份作为上下文，或者直接把标题也传进去)
    # 组合标题和内容，让 AI 知道具体年份
    full_text = f"标题: {title}\n正文: {content}"
    raw_holidays = await parser.parse_holidays(full_text)
    
    if not raw_holidays:
        logger.error("AI 解析未提取到节假日信息")
        raise ValueError("AI 解析未提取到节假日信息")
    
    logger.info(f"AI 解析出的原始数据 (共 {len(raw_holidays)} 条):\n{raw_holidays}")
    
    # 3. 数据整理与持久化
    # 优先使用从标题中提取的年份，如果 AI 解析出的第一个日期年份不匹配，则进行修正
    first_date_str = raw_holidays[0]["date"]
    parsed_year = int(first_date_str.split("-")[0])
    
    # 如果 AI 猜错了年份（比如猜成了 2015），强制纠正为标题中的年份
    year = detected_year
    if parsed_year != detected_year:
        logger.warning(f"AI 解析年份 ({parsed_year}) 与标题年份 ({detected_year}) 不符，将强制修正")
    
    # 格式化数据并转换日期对象，同时确保年份正确
    formatted_list = []
    for h in raw_holidays:
        # 如果 AI 返回的日期年份不对，将其替换为正确的年份
        date_parts = h["date"].split("-")
        corrected_date_str = f"{year}-{date_parts[1]}-{date_parts[2]}"
        
        formatted_list.append({
            "date": datetime.strptime(corrected_date_str, "%Y-%m-%d").date(),
            "description": h["description"]
        })
    market = "SH"
    
    try:
        # 执行保存
        await holiday_service.bulk_save_holidays(
            market=market,
            year=year,
            holidays_data=formatted_list
        )
        
        summary = {
            "success": True,
            "message": f"成功同步 {year} 年节假日",
            "count": len(formatted_list),
            "year": year,
            "announcement_title": announcement["title"]
        }
        logger.info(f"同步任务完成: {summary}")
        return summary
        
    except Exception as e:
        logger.error(f"保存节假日数据失败: {e}")
        raise

@flow(
    name="节假日休市安排同步", 
    description="从上交所官方渠道抓取并解析节假日休市安排"
)
async def holiday_sync_flow():
    """节假日休市安排同步 Flow"""
    return await sync_sse_holidays_task()

if __name__ == "__main__":
    import asyncio
    asyncio.run(holiday_sync_flow())
