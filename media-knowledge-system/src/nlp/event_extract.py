"""
事件抽取模块

基于触发词检测 + LLM论元标注，识别文本中的事件：
- 事件名称与触发词
- 事件参与方及其角色
- 事件时间与地点
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventExtractor:
    """事件抽取器"""

    # 中文事件触发词库
    TRIGGER_WORDS = {
        "并购": ["收购", "并购", "入股", "控股", "合并", "买下"],
        "融资": ["融资", "募资", "投资", "注资", "天使轮", "A轮", "B轮", "C轮", "IPO", "上市"],
        "合作": ["合作", "战略合作", "联手", "签约", "达成协议", "结盟"],
        "发布": ["发布", "推出", "发布", "上线", "开源", "公测"],
        "人事": ["任命", "离职", "加入", "接任", "辞职", "升任", "出任"],
        "纠纷": ["诉讼", "起诉", "仲裁", "罚款", "调查", "处罚", "违规"],
        "会议": ["召开", "举办", "峰会", "论坛", "大会", "发布会"],
        "突破": ["突破", "首次", "刷新", "打破", "创新", "里程碑"],
    }

    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        # 编译触发词正则
        all_triggers = set()
        for words in self.TRIGGER_WORDS.values():
            all_triggers.update(words)
        self._trigger_pattern = re.compile("|".join(all_triggers))

    def extract(
        self,
        text: str,
        entities: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        从文本中抽取事件

        Args:
            text: 输入文本
            entities: 已识别的实体（用于论元标注）

        Returns:
            [{"name": "事件名", "trigger": "收购", "event_type": "并购",
              "participants": [{"entity": "X", "role": "收购方"}],
              "location": "北京", "time": "2024-01-15", "confidence": 0.85}]
        """
        # Step 1: 触发词检测
        triggers = self._detect_triggers(text)
        if not triggers:
            return []

        # Step 2: 如果有LLM，使用LLM做论元标注
        if self.llm_client:
            return self._extract_with_llm(text, triggers, entities)
        else:
            return self._extract_rule(text, triggers, entities)

    def _detect_triggers(self, text: str) -> List[Dict[str, Any]]:
        """检测事件触发词"""
        triggers = []
        for match in self._trigger_pattern.finditer(text):
            trigger_word = match.group()
            event_type = self._classify_trigger(trigger_word)
            triggers.append({
                "word": trigger_word,
                "type": event_type,
                "position": match.start(),
                "context": text[max(0, match.start() - 50):match.end() + 50],
            })
        return triggers

    def _classify_trigger(self, word: str) -> str:
        """对触发词分类"""
        for event_type, words in self.TRIGGER_WORDS.items():
            if word in words:
                return event_type
        return "其他"

    def _extract_with_llm(
        self,
        text: str,
        triggers: List[Dict],
        entities: Optional[List[Dict]],
    ) -> List[Dict]:
        """使用LLM进行论元标注"""
        entity_names = [e.get("text", e.get("name", "")) for e in (entities or [])]

        prompt = f"""从以下文本中识别事件及其参与方。

文本：
{text[:1500]}

检测到的触发词：{json.dumps([t['word'] for t in triggers], ensure_ascii=False)}
已识别实体：{json.dumps(entity_names, ensure_ascii=False)}

请只输出JSON：
{{{{
  "events": [
    {{{{
      "name": "事件名称",
      "trigger": "触发词",
      "event_type": "事件类型",
      "participants": [{{"entity": "参与方", "role": "主体/客体/地点/时间"}}],
      "location": "地点实体",
      "time": "时间表达",
      "confidence": 0.85
    }}}}
  ]
}}}}"""

        try:
            response = self.llm_client.call(prompt)
            result = json.loads(response) if isinstance(response, str) else response
            return result.get("events", [])
        except Exception as e:
            logger.warning(f"LLM事件抽取失败: {e}")
            return self._extract_rule(text, triggers, entities)

    def _extract_rule(
        self,
        text: str,
        triggers: List[Dict],
        entities: Optional[List[Dict]],
    ) -> List[Dict]:
        """规则事件抽取"""
        events = []
        entity_names = [e.get("text", e.get("name", "")) for e in (entities or [])]

        for trigger in triggers:
            context = trigger["context"]
            participants = []

            # 找出上下文中出现的实体
            for ename in entity_names:
                if ename in context:
                    participants.append({"entity": ename, "role": "参与方"})

            # 尝试找时间和地点
            location = None
            event_time = None
            if entities:
                for e in entities:
                    ename = e.get("text", e.get("name", ""))
                    etype = e.get("type", "")
                    if ename in context:
                        if etype == "LOC" and not location:
                            location = ename
                        elif etype == "TIME" and not event_time:
                            event_time = ename

            events.append({
                "name": f"{trigger['type']}事件: {trigger['word']}",
                "trigger": trigger["word"],
                "event_type": trigger["type"],
                "participants": participants,
                "location": location,
                "time": event_time,
                "confidence": 0.65,
            })

        return events
