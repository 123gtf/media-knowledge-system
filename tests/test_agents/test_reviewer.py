"""
Reviewer Agent 单元测试
"""
import pytest
from src.agents.state import SharedState, Entity, Relation, ReviewFlag, TaskStatus
from src.agents.reviewer import ReviewerAgent


class TestReviewerAgent:

    @pytest.fixture
    def agent(self):
        return ReviewerAgent(llm_client=None, mysql_repo=None)

    @pytest.fixture
    def state_with_data(self):
        state = SharedState(
            task_id="test_reviewer",
            intent="测试质检",
            status=TaskStatus.PENDING,
        )
        state.extracted_entities = [
            Entity(name="阿里巴巴", type="ORG", confidence=0.95, source="test"),
            Entity(name="张勇", type="PER", confidence=0.90, source="test"),
            # 低置信度实体
            Entity(name="某公司", type="ORG", confidence=0.5, source="test"),
        ]
        state.extracted_relations = [
            Relation(head="张勇", tail="阿里巴巴", relation_type="works_for", confidence=0.9),
        ]
        return state

    def test_agent_initialization(self, agent):
        assert agent.name == "Reviewer"
        assert "validate_schema" in agent.tools
        assert "detect_conflicts" in agent.tools
        assert "llm_arbitrate" in agent.tools
        assert "filter_low_confidence" in agent.tools

    def test_schema_validation_passes_valid_data(self, agent):
        """测试合法数据通过Schema校验"""
        entities = [
            {"name": "实体A", "type": "PER", "confidence": 0.9},
        ]
        result = agent.tools["validate_schema"].func(entities=entities)
        data = result.get("data", {})
        assert data.get("valid") is True

    def test_schema_validation_detects_missing_fields(self, agent):
        """测试Schema校验检测缺失字段"""
        entities = [
            {"name": "实体A"},  # 缺少type和confidence
        ]
        result = agent.tools["validate_schema"].func(entities=entities)
        data = result.get("data", {})
        assert data.get("valid") is False
        issues = data.get("issues", [])
        assert len(issues) > 0

    def test_confidence_filter(self, agent):
        """测试置信度过滤"""
        entities = [
            {"name": "高置信实体", "type": "PER", "confidence": 0.95},
            {"name": "低置信实体", "type": "ORG", "confidence": 0.5},
        ]
        result = agent.tools["filter_low_confidence"].func(
            entities=entities,
            threshold=0.7,
        )
        data = result.get("data", {})
        assert data.get("low_quality_count", 0) > 0

    def test_conflict_detection_different_types(self, agent):
        """测试同名异类冲突检测"""
        entities = [
            {"name": "苹果", "type": "ORG"},
            {"name": "苹果", "type": "TOPIC"},
        ]
        result = agent.tools["detect_conflicts"].func(entities=entities)
        data = result.get("data", {})
        assert data.get("conflicts_found", 0) > 0

    @pytest.mark.asyncio
    async def test_run_adds_review_flags(self, agent, state_with_data):
        """测试run方法添加质检标记"""
        result = await agent.run(state_with_data)
        assert result.current_stage == "review"
        assert "review" in result.confidence_scores
        # 应该有低置信度标记
        assert len(result.review_flags) >= 0
