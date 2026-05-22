"""
Collector Agent 单元测试
"""
import pytest
from src.agents.state import SharedState, TaskStatus
from src.agents.collector import CollectorAgent


class TestCollectorAgent:

    @pytest.fixture
    def agent(self):
        """创建CollectorAgent实例"""
        return CollectorAgent(llm_client=None, cleaner=None)

    @pytest.fixture
    def state(self):
        """创建测试State"""
        return SharedState(
            task_id="test_collector",
            intent="采集科技新闻",
            status=TaskStatus.PENDING,
        )

    def test_agent_initialization(self, agent):
        """测试Agent正确初始化"""
        assert agent.name == "Collector"
        assert agent.role == "采集管理Agent"
        assert "fetch_rss" in agent.tools
        assert "scrape_web" in agent.tools
        assert "clean_article" in agent.tools
        assert "dedup_check" in agent.tools

    def test_tool_schemas(self, agent):
        """测试工具Schema生成"""
        schemas = agent.get_tool_schemas()
        assert len(schemas) == 4
        for schema in schemas:
            assert schema["type"] == "function"
            assert "name" in schema["function"]
            assert "description" in schema["function"]

    def test_rss_tool_returns_valid_structure(self, agent):
        """测试RSS工具返回有效结构"""
        result = agent.tools["fetch_rss"].func(rss_url="http://example.com/feed", limit=5)
        assert result["status"] == "success"
        data = result.get("data", {})
        assert "articles" in data
        assert "count" in data
        assert isinstance(data["articles"], list)

    def test_cleaner_tool(self, agent):
        """测试清洗工具"""
        html = "<html><body><h1>Title</h1><p>Content here</p><script>ads</script></body></html>"
        result = agent.tools["clean_article"].func(raw_html=html)
        assert result["status"] == "success"
        data = result.get("data", {})
        assert "cleaned_text" in data
        assert len(data.get("cleaned_text", "")) > 0

    def test_dedup_tool(self, agent):
        """测试去重工具"""
        result = agent.tools["dedup_check"].func(
            checksum="abc123",
            url="http://example.com/article1",
        )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_run_populates_raw_documents(self, agent, state):
        """测试run方法填充raw_documents"""
        result = await agent.run(state)
        assert isinstance(result, SharedState)
        assert result.current_stage == "collection"

    @pytest.mark.asyncio
    async def test_run_populates_cleaned_documents(self, agent, state):
        """测试run方法也执行清洗"""
        result = await agent.run(state)
        # 清洗后的文档数量应等于原始文档数量
        assert len(result.cleaned_documents) == len(result.raw_documents)
