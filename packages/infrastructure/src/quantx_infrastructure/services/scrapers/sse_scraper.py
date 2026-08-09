"""
SSE Holiday Announcement Scraper
上交所休市公告抓取器
"""

import logging
import re
from typing import Dict, Optional

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

class SSEScraper:
    """上交所公告抓取器 (使用 Playwright)"""
    
    BASE_URL = "https://www.sse.com.cn"
    # 直接通过该页面抓取休市安排数据
    LIST_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"

    def __init__(self, timeout: int = 30000):
        self.timeout = timeout
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    async def get_latest_holiday_announcement(self) -> Optional[Dict[str, str]]:
        """
        使用 Playwright 直接抓取休市安排页面的数据
        返回: {"title": str, "url": str, "content": str}
        """
        try:
            async with async_playwright() as p:
                logger.info(f"正在启动浏览器抓取: {self.LIST_URL}")
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=self.user_agent)
                page = await context.new_page()
                
                # 设置超时
                page.set_default_timeout(self.timeout)
                
                # 访问页面
                await page.goto(self.LIST_URL, wait_until="networkidle")
                
                
                # 等待页面核心内容渲染
                # 尝试多个可能的关键文本，增加容错
                try:
                    await page.wait_for_selector("text=休市安排", timeout=10000)
                except Exception:
                    logger.warning("未检测到 '休市安排' 文本，尝试继续...")

                # 获取标题（比如：2026年休市安排）
                # 优先使用用户提供的 XPath 获取准确标题
                title = "上交所休市安排"
                try:
                    title_elem = await page.wait_for_selector("xpath=/html/body/div[9]/div/div[2]/div/div[1]/strong", timeout=5000)
                    if title_elem:
                        title = await title_elem.inner_text()
                except Exception:
                    # 备选方案：寻找包含年度字样的 h4 标题
                    h4_elems = await page.query_selector_all("h4")
                    for h4 in h4_elems:
                        text = await h4.inner_text()
                        if "休市安排" in text:
                            title = text.strip()
                            break
                
                # 获取数据内容
                # 不再精确寻找表格，而是抓取整个主内容区域或 body 的 HTML，让 AI 负责过滤噪音并利用 HTML 结构
                content = ""
                # 尽量选取包含主要文字的大容器
                main_selectors = [".sse_colContent", ".article-content", ".allText", "#main-content", "main", "body"]
                for selector in main_selectors:
                    elem = await page.query_selector(selector)
                    if elem:
                        content = await elem.inner_html()
                        if "休市" in content:
                            logger.info(f"已选取宽度选择器 {selector} 提供的原始 HTML 内容")
                            break
                
                # 基本清洗：仅压缩多余空白符，保留 HTML 标签供 AI 解析
                content = re.sub(r'\s+', ' ', content).strip()
                
                await browser.close()
                
                # 验证内容
                if "休市" not in content:
                    logger.warning("提取出的内容不包含关键关键词，抓取可能失败")
                    # 如果有截图，可以提示用户查看
                    return None
                
                logger.info(f"抓取成功: {title}")
                return {
                    "title": title,
                    "url": self.LIST_URL,
                    "content": content
                }
                
        except Exception as e:
            logger.error(f"Playwright 抓取上交所页面失败: {e}")
            return None

    async def fetch_announcement_content(self, url: str) -> Optional[str]:
        """抓取指定 URL 的公告正文"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=self.user_agent)
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle")
                content = await page.evaluate("() => document.body.innerText")
                content = re.sub(r'\s+', ' ', content).strip()
                await browser.close()
                return content
        except Exception as e:
            logger.error(f"抓取页面失败: {url}, error: {e}")
            return None


