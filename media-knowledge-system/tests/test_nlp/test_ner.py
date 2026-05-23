"""
NER 模块单元测试
"""
import pytest
from src.nlp.ner import NERExtractor


class TestNERExtractor:

    @pytest.fixture
    def extractor(self):
        return NERExtractor(engine="rule")

    def test_initialization(self, extractor):
        assert extractor.engine == "rule"
        assert extractor._initialized

    def test_predict_returns_entities(self, extractor, sample_text_zh):
        entities = extractor.predict(sample_text_zh)
        assert isinstance(entities, list)
        for e in entities:
            assert "text" in e
            assert "type" in e
            assert "confidence" in e
            assert 0 <= e["confidence"] <= 1

    def test_predict_filters_by_type(self, extractor, sample_text_zh):
        entities = extractor.predict(sample_text_zh, target_types=["ORG"])
        for e in entities:
            assert e["type"] == "ORG"

    def test_predict_short_text(self, extractor):
        entities = extractor.predict("北京")
        assert isinstance(entities, list)

    def test_predict_empty_text(self, extractor):
        entities = extractor.predict("")
        assert isinstance(entities, list)
        assert len(entities) == 0

    def test_type_mapping(self):
        assert NERExtractor._map_hanlp_type("PERSON") == "PER"
        assert NERExtractor._map_hanlp_type("ORGANIZATION") == "ORG"
        assert NERExtractor._map_hanlp_type("GPE") == "LOC"
        assert NERExtractor._map_hanlp_type("unknown") is None
