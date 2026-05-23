"""
RSS/Atom 订阅源采集器

基于 feedparser 解析RSS/Atom feed，批量提取文章。
支持自定义User-Agent和超时设置。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

import feedparser

from .base import BaseCollector

logger = logging.getLogger(__name__)


class RSSCollector(BaseCollector):
    """RSS订阅源采集器"""

    def __init__(self):
        super().__init__(name="RSS", source_type="rss")

    async def collect(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        从RSS源批量抓取文章

        Args:
            params:
                - rss_url: RSS源URL (必填)
                - limit: 最多抓取条数 (默认20)
                - since: 仅抓取此时间之后的文章 (可选)

        Returns:
            标准化文档列表
        """
        rss_url = params.get("rss_url", "")
        limit = params.get("limit", 20)
        since = params.get("since")

        if not rss_url:
            logger.error("RSS采集缺少 rss_url 参数")
            return []

        try:
            feed = feedparser.parse(rss_url)
        except Exception as e:
            logger.error(f"RSS解析失败 [{rss_url}]: {e}")
            return []

        if feed.bozo:
            logger.warning(f"RSS源格式异常 [{rss_url}]: {feed.bozo_exception}")

        docs = []
        feed_title = feed.feed.get("title", "RSS")
        count = 0

        for entry in feed.entries:
            if count >= limit:
                break

            pub_time = entry.get("published", entry.get("updated", datetime.now().isoformat()))

            # 时间过滤
            if since:
                try:
                    from dateutil.parser import parse as parse_date
                    entry_time = parse_date(pub_time)
                    since_time = parse_date(since)
                    if entry_time < since_time:
                        continue
                except Exception:
                    pass

            link = entry.get("link", "")
            title = entry.get("title", "")
            content = entry.get(
                "summary",
                entry.get("description", entry.get("content", [{}])[0].get("value", "") if entry.get("content") else ""),
            )

            doc = self._standardize_doc(
                title=title,
                content=content,
                url=link,
                publish_time=pub_time,
            )
            doc["source"] = feed_title

            if not self._is_duplicate(doc["id"]):
                docs.append(doc)
                count += 1

        logger.info(f"[RSSCollector] {rss_url} → {len(docs)} 篇")
        return docs
