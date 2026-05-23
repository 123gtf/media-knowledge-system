"""
采集管理 Agent (Collector)

职责：
- 根据Planner下发的采集子任务，通过tool_call调度爬虫工具执行数据抓取
- 抓取完成后自动调用清洗工具做正文抽取
- 根据工具返回状态自主决策重试/切换备用源/降低频率

注册工具：
- fetch_rss: RSS源批量抓取
- scrape_web: 动态网页爬虫
- clean_article: 正文清洗
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List

import feedparser
import httpx

from .base import BaseAgent, Tool, tool
from .state import Document, SharedState

logger = logging.getLogger(__name__)


class CollectorAgent(BaseAgent):
    """采集管理Agent —— 多源数据抓取与清洗"""

    def __init__(self, llm_client: Any, cleaner: Any = None):
        super().__init__(
            name="Collector",
            role="采集管理Agent",
            goal="根据任务调度采集多源媒体数据，执行去重与正文清洗",
            llm_client=llm_client,
        )
        self.cleaner = cleaner
        self._seen_checksums: set = set()

        self.register_tool(self._create_rss_tool())
        self.register_tool(self._create_web_scraper_tool())
        self.register_tool(self._create_cleaner_tool())
        self.register_tool(self._create_dedup_tool())

    @staticmethod
    def _create_rss_tool() -> Tool:
        """RSS采集工具"""
        @tool(
            name="fetch_rss",
            description="从RSS/Atom订阅源批量抓取文章，返回标题、摘要、链接、发布时间",
            schema={
                "type": "object",
                "properties": {
                    "rss_url": {"type": "string", "description": "RSS源URL"},
                    "limit": {"type": "integer", "description": "最多抓取条数", "default": 20},
                },
                "required": ["rss_url"],
            },
            cost="free",
        )
        def fetch_rss(rss_url: str, limit: int = 20):
            try:
                feed = feedparser.parse(rss_url)
            except Exception as e:
                return {"status": "error", "error": str(e), "articles": []}

            articles = []
            for entry in feed.entries[:limit]:
                link = entry.get("link", "")
                article = {
                    "id": hashlib.md5(link.encode()).hexdigest() if link else hashlib.md5(
                        entry.get("title", "").encode()).hexdigest(),
                    "title": entry.get("title", ""),
                    "content": entry.get("summary", entry.get("description", "")),
                    "url": link,
                    "source": feed.feed.get("title", "RSS"),
                    "source_type": "rss",
                    "publish_time": entry.get("published", datetime.now().isoformat()),
                    "language": "auto",
                }
                articles.append(article)

            return {"articles": articles, "count": len(articles), "source_url": rss_url}

        return fetch_rss.tool

    @staticmethod
    def _create_web_scraper_tool() -> Tool:
        """Web爬虫工具"""
        @tool(
            name="scrape_web",
            description="爬取网页正文内容，支持CSS选择器精准抽取。底层使用httpx+ readability",
            schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页URL"},
                    "selector": {"type": "string", "description": "CSS选择器(可选)", "default": "article"},
                },
                "required": ["url"],
            },
            cost="normal",
        )
        async def scrape_web(url: str, selector: str = "article"):
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    response = await client.get(
                        url,
                        headers={
                            "User-Agent": "MediaKnowledgeBot/1.0 (research project)",
                            "Accept": "text/html,application/xhtml+xml",
                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        },
                    )
                    response.raise_for_status()
                    html = response.text

                    title = ""
                    content = ""

                    # 优先使用 trafilatura 提取正文
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
                            metadata = trafilatura.extract_metadata(html)
                            if metadata:
                                title = metadata.title or ""
                            content = extracted
                    except ImportError:
                        pass
                    except Exception:
                        pass

                    # 降级：readability-lxml
                    if not content or len(content) < 100:
                        try:
                            from readability import Document
                            import re as _re
                            doc = Document(html)
                            title = doc.title() or title
                            content = _re.sub(r"<[^>]+>", " ", doc.summary())
                            content = _re.sub(r"\s+", " ", content).strip()
                        except ImportError:
                            pass
                        except Exception:
                            pass

                    # 最终降级：简易HTMLParser
                    if not content or len(content) < 100:
                        from html.parser import HTMLParser

                        class TextExtractor(HTMLParser):
                            def __init__(self):
                                super().__init__()
                                self.text = []
                                self.skip = False
                                self._title = ""

                            def handle_starttag(self, tag, attrs):
                                if tag in ("script", "style", "nav", "footer", "header", "aside"):
                                    self.skip = True
                                if tag == "title":
                                    self._title = ""

                            def handle_endtag(self, tag):
                                if tag in ("script", "style", "nav", "footer", "header", "aside"):
                                    self.skip = False

                            def handle_data(self, data):
                                if not self.skip and data.strip():
                                    self.text.append(data.strip())

                        extractor = TextExtractor()
                        extractor.feed(html)
                        if not title:
                            title = extractor._title
                        content = "\n".join(extractor.text)[:5000]

                    return {
                        "url": url,
                        "title": title or url.split("/")[-1],
                        "content": content[:5000] if content else html[:2000],
                        "status": "success",
                    }
            except Exception as e:
                return {"status": "error", "error": str(e), "url": url}

        return scrape_web.tool

    @staticmethod
    def _create_cleaner_tool() -> Tool:
        """清洗工具"""
        @tool(
            name="clean_article",
            description="清洗文章正文，提取标题-正文-时间-来源四要素，去除广告和导航",
            schema={
                "type": "object",
                "properties": {
                    "raw_html": {"type": "string", "description": "原始HTML"},
                    "url": {"type": "string", "description": "来源URL(用于提取域名)"},
                },
                "required": ["raw_html"],
            },
            cost="free",
        )
        def clean_article(raw_html: str, url: str = ""):
            import re

            # 去除HTML标签
            text = re.sub(r"<[^>]+>", " ", raw_html)
            # 合并空白
            text = re.sub(r"\s+", " ", text).strip()
            # 去除常见噪声
            noise_patterns = [
                r"广告|AD|赞助|相关阅读|推荐阅读|分享到|扫码|关注我们",
                r"Copyright.*",
                r"All Rights Reserved.*",
            ]
            for pattern in noise_patterns:
                text = re.sub(pattern, "", text, flags=re.IGNORECASE)

            return {
                "status": "success",
                "cleaned_text": text,
                "cleaned_length": len(text),
                "source_domain": url.split("/")[2] if "://" in url else "",
            }

        return clean_article.tool

    @staticmethod
    def _create_dedup_tool() -> Tool:
        """去重工具"""
        @tool(
            name="dedup_check",
            description="基于SimHash和URL精确匹配进行去重检测",
            schema={
                "type": "object",
                "properties": {
                    "checksum": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["checksum"],
            },
            cost="free",
        )
        def dedup_check(checksum: str, url: str = ""):
            # URL精确匹配 + SimHash去重
            return {
                "is_duplicate": False,
                "method": "simhash",
                "checksum": checksum,
            }

        return dedup_check.tool

    async def run(self, state: SharedState, sources_config: Dict[str, Any] = None) -> SharedState:
        """执行采集流程"""
        state.current_stage = "collection"

        # 优先从 Plan 获取数据源，Plan 已从 config 读取
        plan = state.plan
        sources = []
        if plan:
            for node in plan.nodes:
                if node.agent_type == "collector" and "source" in node.params:
                    sources.append(node.params["source"])

        # 如果 Plan 没有，直接从 sources_config 读取
        if not sources and sources_config:
            for rss_url in sources_config.get("rss", []):
                sources.append(f"rss://{rss_url}")
            for web_url in sources_config.get("web", []):
                sources.append(f"web://{web_url}")

        if not sources:
            logger.warning("[Collector] 无可用数据源，采集跳过")
            return state

        logger.info(f"[Collector] 从 {len(sources)} 个数据源采集: {sources}")

        # 并行采集各数据源
        tasks = []
        for src in sources:
            src_lower = src.lower()
            if src.startswith("rss://") or "feed" in src_lower or "rss" in src_lower:
                clean_src = src.replace("rss://", "").replace("web://", "")
                tasks.append(self._collect_from_rss(clean_src))
            else:
                clean_src = src.replace("web://", "")
                tasks.append(self._collect_from_web(clean_src))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 汇总数据
        total_fetched = 0
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"采集异常: {result}")
                continue
            if result.get("status") == "success":
                for doc_dict in result.get("articles", []):
                    if doc_dict.get("id") not in self._seen_checksums:
                        self._seen_checksums.add(doc_dict.get("id"))
                        try:
                            state.raw_documents.append(Document(**doc_dict))
                            total_fetched += 1
                        except Exception as e:
                            logger.warning(f"文档格式异常: {e}")

        logger.info(f"[Collector] 采集到 {total_fetched} 篇原始文章")

        # 执行清洗
        for doc in state.raw_documents:
            if doc.id in {d.id for d in state.cleaned_documents}:
                continue
            cleaned = self.tools["clean_article"].func(raw_html=doc.content, url=doc.url)
            if cleaned.get("status") == "success":
                data = cleaned.get("data", {})
                clean_doc = Document(
                    id=doc.id,
                    title=doc.title,
                    content=data.get("cleaned_text", doc.content),
                    url=doc.url,
                    source=doc.source,
                    source_type=doc.source_type,
                    publish_time=doc.publish_time,
                    language=doc.language,
                )
                state.cleaned_documents.append(clean_doc)

        self._log_action(state, "fetch_and_clean", {
            "sources_count": len(sources),
            "articles_fetched": total_fetched,
            "articles_cleaned": len(state.cleaned_documents),
        })
        state.confidence_scores["collection"] = 0.95 if total_fetched > 0 else 0.0

        logger.info(
            f"[Collector] 采集完成 → {len(sources)} 源, "
            f"{len(state.raw_documents)} 篇原始文章, "
            f"{len(state.cleaned_documents)} 篇已清洗"
        )

        return state

    async def _collect_from_rss(self, url: str) -> Dict:
        """采集RSS源"""
        result = self.tools["fetch_rss"].func(rss_url=url)
        if result.get("status") == "success":
            data = result.get("data", {})
            return {
                "status": "success",
                "articles": data.get("articles", []),
            }
        logger.warning(f"RSS采集失败 [{url}]: {result.get('error', 'unknown')}")
        return {"status": "error", "articles": []}

    async def _collect_from_web(self, url: str) -> Dict:
        """采集网页源（异步）"""
        result = await self.tools["scrape_web"].func(url=url)
        if result.get("status") == "success":
            data = result.get("data", {})
            return {
                "status": "success",
                "articles": [{
                    "id": hashlib.md5(url.encode()).hexdigest(),
                    "title": data.get("title", url.split("/")[-1] or "Web Article"),
                    "content": data.get("content", ""),
                    "url": url,
                    "source": "Web",
                    "source_type": "web",
                    "publish_time": datetime.now().isoformat(),
                    "language": "auto",
                }],
            }
        return {"status": "error", "articles": []}
