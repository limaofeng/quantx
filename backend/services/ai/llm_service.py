"""
LLM Service
通用的 LLM 服务，支持多种 AI 服务提供商（基于 OpenAI API）
"""

import json
import logging
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from config.settings import settings

logger = logging.getLogger(__name__)

class LLMService:
    """通用的 LLM 服务类，使用 OpenAI 兼容 API"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        logger=None
    ):
        """
        初始化 LLM 服务

        Args:
            api_key: API Key，如果不提供则从 settings 读取
            api_url: API URL，如果不提供则从 settings 读取
            model: 模型名称，如果不提供则从 settings 读取
            logger: 日志记录器
        """
        self.logger = logger or logging.getLogger(__name__)

        # 从参数或 settings 读取配置
        self.api_key = api_key or settings.llm_api_key
        self.api_url = api_url or settings.llm_api_url
        self.model = model or settings.llm_model

        if not self.api_key:
            raise ValueError("未配置 LLM_API_KEY")

        self.logger.info(
            f"初始化 LLM Service\n"
            f"  API Key: {self.api_key[:10]}...\n"
            f"  API URL: {self.api_url}\n"
            f"  Model: {self.model}"
        )

        # 初始化 OpenAI 客户端
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.api_url,
        )

    async def parse_holidays(self, text: str) -> List[Dict[str, Any]]:
        """
        解析文本中的节假日日期
        返回: [{"date": "2026-01-01", "description": "元旦"}, ...]
        """
        prompt = f"""
        你是一个专业的金融数据解析助手。请从以下上海证券交易所发布的休市公告文本中提取所有的【休市日期】。

        规则：
        1. 仅提取"休市"的日期。
        2. 特别注意：公告中提到的"调休上班"或"补班"日期（通常是周六或周日但需要上班的日期）**绝对不要**包含在内。
        3. 请为每个日期提供简洁的描述（例如：春节、国庆节、元旦）。
        4. 统一使用 YYYY-MM-DD 格式，如果是日期范围（如"1月1日至3日"），请拆分为每一天。

        公告文本：
        ---
        {text}
        ---

        请直接返回 JSON 数组格式，不要包含任何 Markdown 代码块标签或其他描述性文字。
        示例：
        [
          {{"date": "2026-01-01", "description": "元旦"}},
          {{"date": "2026-01-02", "description": "元旦"}}
        ]
        """

        try:
            self.logger.info("正在调用 LLM API 解析节假日数据...")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
            )

            result_text = response.choices[0].message.content.strip()

            # 尝试清理可能出现的 Markdown 块
            if result_text.startswith("```json"):
                result_text = result_text.replace("```json", "").replace("```", "").strip()
            elif result_text.startswith("```"):
                result_text = result_text.replace("```", "").strip()

            holidays = json.loads(result_text)
            self.logger.info(f"成功解析出 {len(holidays)} 个休市日期")
            return holidays

        except Exception as e:
            self.logger.error(f"LLM 解析失败: {e}")
            if 'result_text' in locals():
                self.logger.error(f"待解析的内容: {result_text[:500]}...")
            if 'response' in locals():
                try:
                    self.logger.debug(f"原始响应: {response}")
                except:
                    pass
            return []

    async def chat(self, prompt: str, temperature: float = 0.7) -> str:
        """
        通用对话接口

        Args:
            prompt: 提示词
            temperature: 温度参数（0.0-1.0），控制随机性

        Returns:
            str: LLM 返回的文本
        """
        try:
            self.logger.info("正在调用 LLM API...")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
            )

            result = response.choices[0].message.content.strip()
            self.logger.info(f"LLM 响应成功，长度: {len(result)}")
            return result

        except Exception as e:
            self.logger.error(f"LLM 调用失败: {e}")
            raise
