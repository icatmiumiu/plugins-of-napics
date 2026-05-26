"""蜜柑计划 (mikanani.me) BT 搜索爬虫。

复用 rss_source_mikan 的 RSS 解析逻辑，将结果转换为 SearchResult 格式。
用于 BT Tab 即时搜索，和 RSS 订阅共用同一个数据源。
需代理（config.http_proxy）。
"""

import logging
from typing import List, Optional

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
class MikanScraper(ScraperBase):
    """蜜柑计划 BT 搜索爬虫 — 通过搜索 RSS 获取结果。"""

    SOURCE_NAME = "mikan"

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600)
        self._rss_source = None

    def _get_rss_source(self):
        """懒加载蜜柑 RSS 源。"""
        if self._rss_source is None:
            from rss_source_mikan import MikanRSSSource
            self._rss_source = MikanRSSSource(proxy=self.proxy or "")
        return self._rss_source

    def search_as_search_results(self, keyword: str, max_results: int = 40):
        """搜索并返回 SearchResult 格式。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            rss = self._get_rss_source()
            rss_items = rss._fetch_rss(keyword)
            if not rss_items:
                logger.info("[mikan] 搜索 '%s' 无结果", keyword)
                return []

            results = []
            seen_hashes = set()

            for item in rss_items[:max_results]:
                if item.info_hash and item.info_hash in seen_hashes:
                    continue
                if item.info_hash:
                    seen_hashes.add(item.info_hash)

                quality = parse_quality(item.title)
                quality_level = get_quality_level(quality)

                results.append(SearchResult(
                    title=item.title,
                    size_gb=item.size_gb,
                    indexer=self.SOURCE_NAME,
                    seeders=item.seeders,
                    leechers=0,
                    download_url=item.download_url,
                    info_url=item.info_url,
                    quality_tag=quality.display if quality.display else "Unknown",
                    quality=quality,
                    quality_rank=quality_level.rank,
                ))

            self.set_cached(keyword, results)
            logger.info("[mikan] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results

        except Exception as e:
            logger.error("[mikan] 搜索异常: %s", str(e))
            return []
