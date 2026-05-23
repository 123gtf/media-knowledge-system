"""
采集器抽象基类

所有采集器必须实现：
- collect(params) -> List[Dict]: 执行采集，返回标准化文档列表
- validate(raw_data) -> bool: 校验原始数据格式
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """采集器抽象基类"""

    def __init__(self, name: str, source_type: str = "generic"):
        self.name = name
        self.source_type = source_type
        self._collected_ids: set = set()

    @abstractmethod
    async def collect(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        执行采集

        Args:
            params: 采集参数（URL、关键词、时间范围等）

        Returns:
            标准化文档列表，每个文档包含:
            - id, title, content, url, source, source_type, publish_time
        """
        ...

    def validate(self, raw_data: Any) -> bool:
        """校验原始数据"""
        if not raw_data:
            return False
        return True

    def _generate_doc_id(self, url: str, content: str = "") -> str:
        """生成文档唯一ID"""
        material = url or content
        return hashlib.md5(material.encode()).hexdigest()

    def _is_duplicate(self, doc_id: str) -> bool:
        """检查是否已采集过"""
        if doc_id in self._collected_ids:
            return True
        self._collected_ids.add(doc_id)
        return False

    def _standardize_doc(
        self,
        title: str,
        content: str,
        url: str = "",
        publish_time: Optional[str] = None,
        language: str = "zh",
    ) -> Dict[str, Any]:
        """标准化文档格式"""
        return {
            "id": self._generate_doc_id(url, title + content[:100]),
            "title": title,
            "content": content,
            "url": url,
            "source": self.name,
            "source_type": self.source_type,
            "publish_time": publish_time or datetime.now().isoformat(),
            "fetch_time": datetime.now().isoformat(),
            "language": language,
        }

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.name}>"
