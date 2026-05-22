"""
动态网页爬虫采集器

基于 httpx + readability-lxml 实现：
- 动态页面抓取
- 正文提取（去除广告/导航/侧栏）
- CSS选择器精准抽取（可选）
- 自动编码检测
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

import httpx

from .base import BaseCollector

logger = logging.getLogger(__name__)


class WebScraper(BaseCollector):
    """网页爬虫采集器"""

    def __init__(self):
        super().__init__(name="WebScraper", source_type="web")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={
                    "User-Agent": "MediaKnowledgeBot/1.0 (research project)",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
        return self._client

    async def collect(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        爬取网页正文

        Args:
            params:
                - url: 目标网页URL (必填)
                - selector: CSS选择器 (可选，用于精准抽取)
                - extract_links: 是否同时提取页面内链接 (默认False)

        Returns:
            标准化文档列表
        """
        url = params.get("url", "")
        selector = params.get("selector", "article")
        extract_links = params.get("extract_links", False)

        if not url:
            logger.error("Web爬虫缺少 url 参数")
            return []

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP错误 [{url}]: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"请求失败 [{url}]: {e}")
            return []

        # 正文提取
        title, content = self._extract_content(html, url, selector)

        if not content or len(content) < 100:
            logger.warning(f"正文过短 [{url}]: {len(content)} 字符")
            return []

        doc = self._standardize_doc(
            title=title,
            content=content,
            url=url,
            publish_time=datetime.now().isoformat(),
        )

        if self._is_duplicate(doc["id"]):
            return []

        logger.info(f"[WebScraper] {url} → {len(content)} 字符")
        return [doc]

    def _extract_content(self, html: str, url: str, selector: str = "article") -> tuple:
        """提取网页标题和正文（优先使用 trafilatura，降级为 HTMLParser）"""
        title = ""
        content = ""

        # 优先使用 trafilatura（生产级正文抽取）
        try:
            import trafilatura
            extracted = trafilatura.extract(
                html,
                include_formatting=False,
                include_links=False,
                include_images=False,
                favor_precision=True,
            )
            if extracted and len(extracted) > 100:
                # 尝试从 trafilatura 元数据获取标题
                metadata = trafilatura.extract_metadata(html)
                if metadata:
                    title = metadata.title or ""
                content = extracted
                if title and content:
                    return title, content
        except ImportError:
            logger.debug("trafilatura 未安装，降级为 HTMLParser")
        except Exception as e:
            logger.debug(f"trafilatura 提取失败: {e}")

        # 降级：readability-lxml
        if not content or len(content) < 100:
            try:
                from readability import Document
                doc = Document(html)
                title = doc.title() or title
                summary_html = doc.summary()
                # 去除 summary HTML 标签
                import re
                content = re.sub(r"<[^>]+>", " ", summary_html)
                content = re.sub(r"\s+", " ", content).strip()
            except ImportError:
                logger.debug("readability-lxml 未安装")
            except Exception as e:
                logger.debug(f"readability 提取失败: {e}")

        # 最终降级：简易 HTMLParser
        if not content or len(content) < 100:
            title, content = self._extract_fallback(html, url)

        return title, content

    def _extract_fallback(self, html: str, url: str) -> tuple:
        """简易 HTMLParser 降级方案"""
        from html.parser import HTMLParser

        class ContentExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.title = ""
                self.text_parts: List[str] = []
                self.in_title = False
                self.skip_tags = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
                self.skip_depth = 0

            def handle_starttag(self, tag, attrs):
                if tag in self.skip_tags:
                    self.skip_depth += 1
                if tag == "title":
                    self.in_title = True

            def handle_endtag(self, tag):
                if tag in self.skip_tags and self.skip_depth > 0:
                    self.skip_depth -= 1
                if tag == "title":
                    self.in_title = False

            def handle_data(self, data):
                if self.in_title:
                    self.title = data.strip()
                elif self.skip_depth == 0:
                    text = data.strip()
                    if text and len(text) > 5:
                        self.text_parts.append(text)

        extractor = ContentExtractor()
        try:
            extractor.feed(html)
        except Exception:
            pass

        title = extractor.title or url.split("/")[-1]
        content = "\n\n".join(extractor.text_parts)
        return title, content

    async def close(self):
        """关闭HTTP客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
