"""
社交媒体API采集器

通过API接口采集社交媒体数据（微博/Reddit/Twitter等）。
支持按关键词和时间范围检索。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

import httpx

from .base import BaseCollector

logger = logging.getLogger(__name__)


class SocialAPICollector(BaseCollector):
    """社交媒体API采集器"""

    def __init__(self, platform: str = "generic"):
        super().__init__(name=f"SocialAPI:{platform}", source_type="social_media")
        self.platform = platform
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=10,
                headers={
                    "User-Agent": "MediaKnowledgeBot/1.0",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def collect(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        通过API采集社交媒体数据

        Args:
            params:
                - keywords: 搜索关键词列表 (必填)
                - platform: 平台标识 (reddit/weibo/twitter)
                - limit: 最多返回条数 (默认20)
                - since: 起始时间 (可选)
                - until: 结束时间 (可选)

        Returns:
            标准化文档列表
        """
        keywords = params.get("keywords", [])
        limit = params.get("limit", 20)
        platform = params.get("platform", self.platform)

        if not keywords:
            logger.warning(f"[{platform}] 缺少关键词")
            return []

        docs = []

        for keyword in keywords:
            try:
                results = await self._search_platform(platform, keyword, limit)
                docs.extend(results)
            except Exception as e:
                logger.error(f"[{platform}] 搜索 '{keyword}' 失败: {e}")

        # 去重
        seen_ids = set()
        unique_docs = []
        for doc in docs:
            if doc["id"] not in seen_ids:
                seen_ids.add(doc["id"])
                unique_docs.append(doc)

        logger.info(f"[{platform}] {len(keywords)} 关键词 → {len(unique_docs)} 篇")
        return unique_docs[:limit]

    async def _search_platform(self, platform: str, keyword: str, limit: int) -> List[Dict]:
        """平台特定的搜索逻辑"""
        # 各平台API差异较大，此处提供框架，实际需配置API密钥
        if platform == "reddit":
            return await self._search_reddit(keyword, limit)
        else:
            logger.info(f"[{platform}] API未配置，返回空结果")
            return []

    async def _search_reddit(self, keyword: str, limit: int) -> List[Dict]:
        """Reddit搜索（示例）"""
        docs = []
        try:
            client = await self._get_client()
            url = f"https://www.reddit.com/search.json?q={keyword}&limit={limit}"
            response = await client.get(url)
            data = response.json()

            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                doc = self._standardize_doc(
                    title=post_data.get("title", ""),
                    content=post_data.get("selftext", ""),
                    url=f"https://reddit.com{post_data.get('permalink', '')}",
                    publish_time=datetime.fromtimestamp(
                        post_data.get("created_utc", datetime.now().timestamp())
                    ).isoformat(),
                )
                docs.append(doc)
        except Exception as e:
            logger.error(f"Reddit搜索失败: {e}")

        return docs

    async def close(self):
        """关闭HTTP客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
