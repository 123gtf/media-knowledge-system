"""采集器插件"""
from src.data.collectors.base import BaseCollector
from src.data.collectors.rss import RSSCollector
from src.data.collectors.web_scraper import WebScraper
from src.data.collectors.social_api import SocialAPICollector

__all__ = ["BaseCollector", "RSSCollector", "WebScraper", "SocialAPICollector"]
