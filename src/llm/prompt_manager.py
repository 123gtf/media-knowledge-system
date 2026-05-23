"""
Prompt 模板管理器

从 config/prompts/ 加载YAML模板，支持变量插值。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PromptManager:
    """Prompt模板管理器"""

    def __init__(self, prompts_dir: Optional[str] = None):
        """
        Args:
            prompts_dir: Prompt模板目录路径，默认 config/prompts/
        """
        if prompts_dir:
            self.prompts_dir = Path(prompts_dir)
        else:
            # 自动查找
            candidates = [
                Path("config/prompts"),
                Path(__file__).parent.parent.parent / "config" / "prompts",
            ]
            self.prompts_dir = None
            for c in candidates:
                if c.exists():
                    self.prompts_dir = c
                    break

        self._templates: Dict[str, Dict[str, str]] = {}
        self._load_all()

    def _load_all(self):
        """加载所有模板文件"""
        if not self.prompts_dir or not self.prompts_dir.exists():
            logger.warning(f"Prompt目录不存在: {self.prompts_dir}")
            self._load_defaults()
            return

        try:
            import yaml
        except ImportError:
            logger.warning("pyyaml未安装，使用默认模板")
            self._load_defaults()
            return

        for yaml_file in self.prompts_dir.glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    template = yaml.safe_load(f)
                    name = yaml_file.stem
                    self._templates[name] = template
                    logger.debug(f"加载模板: {name}")
            except Exception as e:
                logger.warning(f"模板加载失败 [{yaml_file}]: {e}")

        logger.info(f"已加载 {len(self._templates)} 个Prompt模板: {list(self._templates.keys())}")

    def _load_defaults(self):
        """加载内置默认模板"""
        self._templates = {
            "ner": {
                "system": "你是一个高精度的命名实体识别系统。请识别PER/ORG/LOC/TIME/EVENT/TOPIC类型实体。只输出JSON。",
                "user": "文本：\n{text}\n\n输出JSON格式的实体列表。",
            },
            "re": {
                "system": "你是一个关系抽取系统。请抽取实体间的语义关系三元组。只输出JSON。",
                "user": "文本：\n{text}\n\n已识别实体：{entities}\n\n输出JSON格式的关系列表。",
            },
            "summary": {
                "system": "你是一个专业的新闻摘要撰写专家。请用简洁客观的语言总结文本。",
                "user": "请为以下文本生成不超过{max_length}字的摘要：\n\n{text}\n\n输出JSON。",
            },
            "report": {
                "system": "你是一个专业的媒体数据分析师。基于数据撰写分析报告。",
                "user": "请基于以下数据撰写{report_type}报告：\n{data}\n\n使用Markdown格式。",
            },
            "review": {
                "system": "你是一个严格的数据质量审核员。检查数据准确性、完整性和一致性。",
                "user": "请审核以下抽取结果：\n实体：{entities}\n关系：{relations}\n\n输出审核JSON。",
            },
        }

    def get(self, template_name: str) -> Dict[str, str]:
        """
        获取模板

        Args:
            template_name: 模板名称 (ner/re/summary/report/review)

        Returns:
            {"system": "...", "user": "..."}
        """
        if template_name not in self._templates:
            logger.warning(f"模板不存在: {template_name}")
            return {"system": "", "user": "{text}"}
        return self._templates[template_name]

    def render(
        self,
        template_name: str,
        **kwargs,
    ) -> Dict[str, str]:
        """
        渲染模板（变量插值）

        Args:
            template_name: 模板名称
            **kwargs: 模板变量

        Returns:
            {"system": "rendered system prompt", "user": "rendered user prompt"}
        """
        template = self.get(template_name)
        rendered = {}

        for key in ("system", "user"):
            text = template.get(key, "")
            try:
                rendered[key] = text.format(**kwargs)
            except KeyError as e:
                logger.warning(f"模板变量缺失 [{template_name}.{key}]: {e}")
                rendered[key] = text
            except Exception as e:
                logger.error(f"模板渲染失败 [{template_name}.{key}]: {e}")
                rendered[key] = text

        return rendered

    def list_templates(self) -> list:
        """列出所有可用模板"""
        return list(self._templates.keys())
