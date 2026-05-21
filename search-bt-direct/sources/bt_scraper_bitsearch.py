"""Bitsearch (bitsearch.to) BT 搜索爬虫。

通过 JSON API 搜索种子资源，返回磁力链接。
API: GET /api/v1/search?q=关键词&page=1
需要代理（config.json 的 http_proxy），用 curl_cffi 模拟浏览器指纹。
覆盖欧美影视剧、中文资源，是 Prowlarr 不可达时的核心补充。
"""

import logging
import re
from typing import List, Optional

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
class BitsearchResult:
    """Bitsearch 搜索结果（内部中间格式）。"""
    __slots__ = ("title", "infohash", "size", "seeders", "leechers",
                 "category", "verified", "magnet_url")

    def __init__(self, title: str, infohash: str, size: int,
                 seeders: int, leechers: int, category: int,
                 verified: bool):
        self.title = title
        self.infohash = infohash
        self.size = size
        self.seeders = seeders
        self.leechers = leechers
        self.category = category
        self.verified = verified
        self.magnet_url = f"magnet:?xt=urn:btih:{infohash}&dn={title}"


class BitsearchScraper(ScraperBase):
    """Bitsearch BT 搜索爬虫 — JSON API。

    API 返回字段：id, infohash, title, size, category, subCategory,
    seeders, leechers, downloads, verified, updatedAt。
    category: 1=All, 2=Movies, 3=Music, 4=Games, 5=Software, 6=TV, 7=Anime, 8=Other。
    """

    BASE_URL = "https://bitsearch.to"
    API_PATH = "/api/v1/search"
    SOURCE_NAME = "bitsearch"
    MAX_PAGES = 2  # 最多取 2 页（每页 20 条）

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600)

    def search(self, keyword: str, max_results: int = 40) -> List[BitsearchResult]:
        """搜索 Bitsearch，返回 BitsearchResult 列表。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            all_results: List[BitsearchResult] = []
            for page in range(1, self.MAX_PAGES + 1):
                page_results = self._search_page(keyword, page)
                all_results.extend(page_results)
                if len(page_results) < 20:
                    break  # 不足一页，没有更多了
                if len(all_results) >= max_results:
                    break
                self.random_delay(2.0, 4.0)

            # 去重（按 infohash）
            seen = set()
            deduped = []
            for r in all_results:
                if r.infohash not in seen:
                    seen.add(r.infohash)
                    deduped.append(r)

            self.set_cached(keyword, deduped)
            logger.info("[bitsearch] 搜索 '%s' 获取 %d 条结果", keyword, len(deduped))
            return deduped

        except Exception as e:
            logger.error("[bitsearch] 搜索异常: %s", str(e))
            return []

    def _search_page(self, keyword: str, page: int) -> List[BitsearchResult]:
        """请求单页 API。"""
        url = f"{self.BASE_URL}{self.API_PATH}"
        try:
            resp = self.request_with_backoff(
                url, params={"q": keyword, "page": page}, timeout=15
            )
            if resp.status_code == 429:
                logger.warning("[bitsearch] API 限频 429，跳过")
                return []
            if resp.status_code != 200:
                logger.warning("[bitsearch] API 返回 %d", resp.status_code)
                return []

            data = resp.json()
            if not data.get("success"):
                return []

            items = data.get("results", [])
            return self._parse_items(items)

        except Exception as e:
            logger.error("[bitsearch] API 请求失败: %s", str(e))
            return []

    def _parse_items(self, items: list) -> List[BitsearchResult]:
        """解析 API 返回的条目列表。"""
        results = []
        for item in items:
            try:
                title = item.get("title", "").strip()
                infohash = item.get("infohash", "").strip()
                if not title or not infohash:
                    continue

                results.append(BitsearchResult(
                    title=title,
                    infohash=infohash,
                    size=item.get("size", 0),
                    seeders=item.get("seeders", 0),
                    leechers=item.get("leechers", 0),
                    category=item.get("category", 0),
                    verified=item.get("verified", False),
                ))
            except Exception:
                continue
        return results

    def search_as_search_results(self, keyword: str, max_results: int = 40):
        """搜索并转换为 SearchResult 格式（兼容 Prowlarr 结果）。

        返回 List[SearchResult]，可直接合并到 bt_results。
        延迟导入 SearchResult 避免循环依赖。
        """
        from searcher import SearchResult

        raw = self.search(keyword, max_results)
        results = []
        for r in raw:
            quality = parse_quality(r.title)
            quality_level = get_quality_level(quality)
            results.append(SearchResult(
                title=r.title,
                size_gb=round(r.size / (1024 ** 3), 2) if r.size > 0 else 0,
                indexer=self.SOURCE_NAME,
                seeders=r.seeders,
                leechers=r.leechers,
                download_url=r.magnet_url,
                info_url="",
                quality_tag=quality.display if quality.display else "Unknown",
                quality=quality,
                quality_rank=quality_level.rank,
            ))
        # 按做种数降序
        results.sort(key=lambda x: x.seeders, reverse=True)
        return results
