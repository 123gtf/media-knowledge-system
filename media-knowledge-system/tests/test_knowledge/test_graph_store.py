"""
GraphStore 单元测试
"""
import pytest
from src.knowledge.graph_store import GraphStore
from src.knowledge.entity_linker import EntityLinker


class TestGraphStore:

    @pytest.fixture
    def store(self):
        """创建无连接的GraphStore（使用Mock模式）"""
        return GraphStore(
            uri="bolt://localhost:7687",
            user="neo4j",
            password="password",
        )

    def test_initialization(self, store):
        assert store.uri == "bolt://localhost:7687"
        assert store.database == "neo4j"

    def test_batch_upsert_entities_empty(self, store):
        result = store.batch_upsert_entities([])
        assert result["created"] == 0
        assert result["total"] == 0

    def test_batch_upsert_relations_empty(self, store):
        result = store.batch_upsert_relations([])
        assert result["created"] == 0
        assert result["total"] == 0

    def test_get_hot_entities(self, store):
        result = store.get_hot_entities(limit=10)
        assert isinstance(result, list)


class TestEntityLinker:

    @pytest.fixture
    def linker(self):
        return EntityLinker(
            graph_store=None,
            mysql_repo=None,
            llm_client=None,
        )

    def test_exact_match_returns_none_without_db(self, linker):
        result = linker.link("测试实体", "ORG")
        assert isinstance(result, dict)
        assert "is_new" in result

    def test_name_similarity_identical(self):
        sim = EntityLinker._name_similarity("阿里巴巴", "阿里巴巴")
        assert sim == 1.0

    def test_name_similarity_different(self):
        sim = EntityLinker._name_similarity("阿里巴巴", "腾讯")
        assert sim < 0.5

    def test_name_similarity_substring(self):
        sim = EntityLinker._name_similarity("阿里巴巴集团", "阿里巴巴")
        assert sim > 0.5
