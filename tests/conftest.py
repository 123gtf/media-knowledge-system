"""
Pytest 共享 fixtures 配置
"""
import sys
from pathlib import Path

import pytest

# 添加项目根目录到路径（使 from src.xxx 可用）
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def sample_text_zh():
    """中文示例文本"""
    return """
    2024年1月15日，阿里巴巴集团宣布与上海市政府达成战略合作协议，
    将在浦东新区投资50亿元建设人工智能研究院。阿里巴巴CEO张勇表示，
    这一合作将推动长三角地区的数字经济发展。此前，腾讯也在深圳成立了AI实验室。
    """


@pytest.fixture
def sample_entities():
    """示例实体列表"""
    return [
        {"name": "阿里巴巴", "type": "ORG", "confidence": 0.95},
        {"name": "上海市政府", "type": "ORG", "confidence": 0.90},
        {"name": "张勇", "type": "PER", "confidence": 0.95},
        {"name": "浦东新区", "type": "LOC", "confidence": 0.92},
    ]


@pytest.fixture
def sample_state_dict():
    """示例SharedState字典"""
    return {
        "task_id": "test_001",
        "intent": "分析AI行业热点",
        "status": "PENDING",
        "current_stage": "init",
        "raw_documents": [],
        "cleaned_documents": [],
        "extracted_entities": [],
        "extracted_relations": [],
        "extracted_events": [],
        "confidence_scores": {},
        "review_flags": [],
        "correction_count": 0,
        "max_corrections": 3,
        "report": None,
        "report_json": None,
        "knowledge_updates": {},
        "checkpoint_id": None,
        "execution_log": [],
    }
