"""
GraphStore 单元测试
"""
import pytest
from src.knowledge.graph_store import GraphStore


class TestGraphStore:

    @pytest.fixture
    def store(self):
        """创建纯内存GraphStore"""
        return GraphStore(memory_only=True)

    def test_initialization(self, store):
        assert store.uri == "bolt://localhost:7687"
        assert store.database == "neo4j"
        assert store._memory_only is True

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

    def test_upsert_entity_creates_new(self, store):
        store.upsert_entity("测试公司", "ORG", 0.9)
        assert "测试公司" in store._mem_entities
        assert store._mem_entities["测试公司"]["mention_count"] == 1

    def test_upsert_entity_dedup(self, store):
        store.upsert_entity("阿里巴巴", "ORG", 0.9)
        store.register_alias("阿里", "阿里巴巴")
        store.upsert_entity("阿里", "ORG", 0.9)
        # 应该合并到"阿里巴巴"，不创建新实体
        assert len(store._mem_entities) == 1
        assert store._mem_entities["阿里巴巴"]["mention_count"] == 2

    def test_upsert_relation_dedup(self, store):
        store.upsert_entity("A", "ORG", 0.9)
        store.upsert_entity("B", "ORG", 0.9)
        store.upsert_relation("A", tail_name="B", relation_type="related_to")
        store.upsert_relation("A", tail_name="B", relation_type="related_to")
        assert len(store._mem_relations) == 1

    def test_load_demo_data(self, store):
        store.load_demo_data()
        assert len(store._mem_entities) > 50
        assert len(store._mem_relations) > 30
        assert len(store._aliases) > 40

    def test_find_similar_entities_alias(self, store):
        store.load_demo_data()
        results = store.find_similar_entities("阿里", limit=2)
        names = [r["name"] for r in results]
        assert "阿里巴巴" in names
