"""
Analyzer Agent 单元测试
"""
import pytest
from src.agents.state import SharedState, Document, TaskStatus
from src.agents.analyzer import AnalyzerAgent


class TestAnalyzerAgent:

    @pytest.fixture
    def agent(self):
        return AnalyzerAgent(llm_client=None, small_model_ner=None)

    @pytest.fixture
    def state_with_docs(self, sample_text_zh):
        state = SharedState(
            task_id="test_analyzer",
            intent="分析文本",
            status=TaskStatus.PENDING,
        )
        state.cleaned_documents.append(Document(
            id="doc_001",
            title="测试文档",
            content=sample_text_zh,
            url="http://example.com/1",
            source="test",
        ))
        return state

    def test_agent_initialization(self, agent):
        assert agent.name == "Analyzer"
        assert "extract_entities" in agent.tools
        assert "extract_relations" in agent.tools
        assert "extract_events" in agent.tools
        assert "summarize" in agent.tools

    def test_entity_extraction_rule_based(self, agent):
        """测试基于规则的实体识别"""
        text = "阿里巴巴集团与上海市政府达成合作。2024年1月15日，张勇CEO在北京宣布。"
        result = agent.tools["extract_entities"].func(text=text)
        assert result["status"] == "success"
        data = result.get("data", {})
        entities = data.get("entities", [])
        assert len(entities) > 0
        # 验证实体结构
        for e in entities:
            assert "name" in e
            assert "type" in e
            assert "confidence" in e

    def test_relation_extraction_no_llm(self, agent):
        """测试无LLM时的关系抽取"""
        entities = [
            {"name": "阿里巴巴", "type": "ORG"},
            {"name": "上海", "type": "LOC"},
        ]
        result = agent.tools["extract_relations"].func(
            text="阿里巴巴在上海设立总部。",
            entities=entities,
        )
        assert result["status"] == "success"

    def test_summarize_extractive(self, agent):
        """测试抽取式摘要"""
        text = "第一段内容。第二段内容。第三段内容。第四段内容。第五段内容。"
        result = agent.tools["summarize"].func(text=text, max_length=50)
        assert result["status"] == "success"
        data = result.get("data", {})
        assert "summary" in data
        assert len(data.get("summary", "")) <= 50

    @pytest.mark.asyncio
    async def test_run_extracts_entities(self, agent, state_with_docs):
        """测试run方法提取实体"""
        result = await agent.run(state_with_docs)
        assert result.current_stage == "analysis"
        assert "analysis_ner" in result.confidence_scores
