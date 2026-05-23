"""
命名实体识别模块

支持：
- HanLP 本地推理（优先）：零API成本，快速
- 规则降级方案：HanLP不可用时自动切换
- BERT NER（可选）：高精度，需GPU

实体类型：PER / ORG / LOC / TIME / EVENT / TOPIC
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class NERExtractor:
    """命名实体识别器"""

    ENTITY_TYPES = ["PER", "ORG", "LOC", "TIME", "EVENT", "TOPIC"]

    def __init__(self, engine: str = "hanlp", model_path: Optional[str] = None):
        """
        Args:
            engine: NER引擎 — "hanlp" / "bert" / "rule"
            model_path: 模型路径（BERT需要）
        """
        self.engine = engine
        self.model_path = model_path
        self._model = None
        self._initialized = False

        if engine == "hanlp":
            self._init_hanlp()

    def _init_hanlp(self):
        """初始化HanLP"""
        try:
            import hanlp
            self._model = hanlp.load(hanlp.pretrained.ner.MSRA_NER_ELECTRA_SMALL_ZH)
            self._initialized = True
            logger.info("HanLP NER模型加载成功")
        except ImportError:
            logger.warning("HanLP未安装，将使用规则NER")
            self.engine = "rule"
            self._initialized = True
        except Exception as e:
            logger.warning(f"HanLP加载失败: {e}，将使用规则NER")
            self.engine = "rule"
            self._initialized = True

    def predict(self, text: str, target_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        提取命名实体

        Args:
            text: 输入文本
            target_types: 目标实体类型（可选，默认全部）

        Returns:
            [{"text": "实体名", "type": "PER", "confidence": 0.95, "start": 0, "end": 3}, ...]
        """
        if self.engine == "hanlp" and self._model:
            return self._predict_hanlp(text, target_types)
        elif self.engine == "bert":
            return self._predict_bert(text, target_types)
        else:
            return self._predict_rule(text, target_types)

    def _predict_hanlp(self, text: str, target_types: Optional[List[str]] = None) -> List[Dict]:
        """HanLP NER预测"""
        try:
            raw_result = self._model(text)
            entities = []

            # HanLP MSRA格式: (entity_name, entity_type, start, end)
            for item in raw_result:
                name = item[0] if isinstance(item, tuple) else item.get("word", "")
                etype = item[1] if isinstance(item, tuple) else item.get("ner", "")
                start = item[2] if isinstance(item, tuple) and len(item) > 2 else 0
                end = item[3] if isinstance(item, tuple) and len(item) > 3 else len(text)

                # 映射HanLP类型到系统类型
                mapped_type = self._map_hanlp_type(etype)
                if mapped_type and (not target_types or mapped_type in target_types):
                    entities.append({
                        "text": name,
                        "type": mapped_type,
                        "confidence": 0.9,
                        "start": start,
                        "end": end,
                    })

            return entities
        except Exception as e:
            logger.warning(f"HanLP预测失败: {e}，降级为规则")
            return self._predict_rule(text, target_types)

    def _predict_bert(self, text: str, target_types: Optional[List[str]] = None) -> List[Dict]:
        """BERT NER预测（占位，需加载模型）"""
        logger.warning("BERT NER未配置，使用规则降级")
        return self._predict_rule(text, target_types)

    def _predict_rule(self, text: str, target_types: Optional[List[str]] = None) -> List[Dict]:
        """基于规则的NER（降级方案）"""
        target = set(target_types) if target_types else set(self.ENTITY_TYPES)
        entities = []

        # ---- 已知实体精确匹配（高置信度）----
        KNOWN = {
            "ORG": [
                "阿里巴巴", "阿里巴巴集团", "腾讯", "腾讯公司", "字节跳动",
                "百度", "百度公司", "OpenAI", "Google", "Google DeepMind",
                "微软", "微软公司", "昆仑万维", "商汤科技", "华为",
                "DeepMind", "Azure",
            ],
            "PER": [
                "Sam Altman", "吴泳铭", "梁汝波", "Demis Hassabis",
                "张勇", "马云", "马化腾", "李彦宏", "任正非",
            ],
            "LOC": [
                "浦东新区", "长三角", "旧金山", "硅谷", "纽约",
                "北京", "上海", "深圳", "广州", "杭州", "成都",
                "武汉", "南京", "天津", "重庆", "苏州", "西安",
                "美国", "英国", "法国", "德国", "日本", "韩国",
                "印度", "俄罗斯", "中国",
            ],
            "EVENT": [
                "GPT-5", "豆包2.0", "文心一言4.0", "Gemini",
            ],
        }

        for etype, names in KNOWN.items():
            if etype not in target:
                continue
            for name in names:
                for m in re.finditer(re.escape(name), text):
                    entities.append({
                        "text": name,
                        "type": etype,
                        "confidence": 0.9,
                        "start": m.start(),
                        "end": m.end(),
                    })

        # ---- 正则规则补充（中等置信度）----
        rules = {
            "TIME": [
                (r"\d{4}年\d{1,2}月\d{1,2}日", 0.9),
                (r"\d{4}-\d{2}-\d{2}", 0.9),
                (r"\d{4}年\d{1,2}月", 0.85),
                (r"\d{1,2}月\d{1,2}日", 0.8),
                (r"\d{2}:\d{2}(:\d{2})?", 0.75),
                (r"(本周|上周|下周|本月|上月)", 0.7),
            ],
            "LOC": [
                (r"(?:浦东|滨海|朝阳|海淀|徐汇)(?:新区|区)", 0.8),
                (r"(?:长三角|珠三角|京津冀|粤港澳)", 0.85),
                (r"[一-鿿]{2}(?:省|市|区|县)", 0.7),
            ],
            "ORG": [
                (r"(?:[一-鿿]{2,6})(?:公司|集团|银行|大学|学院|研究院|研究所|实验室)", 0.8),
                (r"(?:[A-Z][a-z]*\s?)+(?:Inc|Corp|Ltd|AI|Lab)", 0.8),
                (r"(?:上海|北京|深圳|广州|杭州)(?:市政府|市)", 0.85),
            ],
            "TOPIC": [
                (r"(?:人工智能|大语言模型|大模型|深度学习|自动驾驶|量子计算|多模态)", 0.85),
                (r"(?:AI助手|AI市场|AI产业|数字经济|神经网络)", 0.8),
            ],
        }

        for etype, patterns in rules.items():
            if etype not in target:
                continue
            for pattern, confidence in patterns:
                for match in re.finditer(pattern, text):
                    name = match.group()
                    entities.append({
                        "text": name,
                        "type": etype,
                        "confidence": confidence,
                        "start": match.start(),
                        "end": match.end(),
                    })

        # 去重（保留置信度最高的）
        deduped = {}
        for e in entities:
            key = f"{e['text']}::{e['type']}"
            if key not in deduped or e["confidence"] > deduped[key]["confidence"]:
                deduped[key] = e

        result = list(deduped.values())

        # 过滤：移除被同类更长实体完全包含的碎片
        result.sort(key=lambda e: len(e["text"]), reverse=True)
        filtered = []
        for e in result:
            is_fragment = False
            for other in filtered:
                if e["type"] == other["type"] and e["text"] in other["text"] and e["text"] != other["text"]:
                    is_fragment = True
                    break
            if not is_fragment:
                filtered.append(e)

        return filtered

    @staticmethod
    def _map_hanlp_type(hanlp_type: str) -> Optional[str]:
        """映射HanLP实体类型到系统类型"""
        type_map = {
            "PERSON": "PER", "NR": "PER",
            "ORG": "ORG", "ORGANIZATION": "ORG", "NT": "ORG",
            "LOC": "LOC", "LOCATION": "LOC", "GPE": "LOC", "NS": "LOC",
            "TIME": "TIME", "DATE": "TIME", "T": "TIME",
            "EVENT": "EVENT",
            "TOPIC": "TOPIC",
        }
        return type_map.get(hanlp_type.upper())
