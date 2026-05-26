"""Nyaa.si BT 搜索爬虫。

搜索接口：GET /?f=0&c=0_0&q=关键词
HTML 表格解析，提取磁力链接+做种数+大小。
需代理（config.http_proxy），日本动画/日剧覆盖率极高。
绕过 Prowlarr 直连，不受 tun 模式限制。
"""

import re
import logging
from typing import List, Optional

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
_MAGNET_HASH_RE = re.compile(r"btih:([a-fA-F0-9]{40})")

# 大小单位转换
_SIZE_UNITS = {"TiB": 1024, "GiB": 1, "MiB": 1 / 1024, "KiB": 1 / (1024 * 1024)}


def _parse_size(size_str: str) -> float:
    """解析 Nyaa 的大小字符串（如 '3.2 GiB'）为 GB。"""
    for unit, factor in _SIZE_UNITS.items():
        if unit in size_str:
            try:
                num = float(size_str.replace(unit, "").strip())
                return round(num * factor, 2)
            except ValueError:
                return 0
    return 0


class NyaaScraper(ScraperBase):
    """Nyaa.si BT 搜索爬虫。"""

    BASE_URL = "https://nyaa.si"
    SOURCE_NAME = "nyaa"

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600)

    def search_as_search_results(self, keyword: str, max_results: int = 40):
        """搜索并返回 SearchResult 格式。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword, max_results)
            self.set_cached(keyword, results)
            logger.info("[nyaa] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[nyaa] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str, max_results: int) -> list:
        """GET 搜索 Nyaa，解析 HTML 表格。"""
        from searcher import SearchResult

        url = self.BASE_URL
        params = {"f": 0, "c": "0_0", "q": keyword, "s": "seeders", "o": "desc"}

        try:
            resp = self.request_with_backoff(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning("[nyaa] 搜索返回 %d", resp.status_code)
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.torrent-list tbody tr")
            if not rows:
                return []

            results = []
            for row in rows[:max_results]:
                try:
                    result = self._parse_row(row)
                    if result:
                        results.append(result)
                except Exception:
                    continue

            return results

        except Exception as e:
            logger.error("[nyaa] 搜索请求失败: %s", str(e))
            return []

    def _parse_row(self, row):
        """解析 Nyaa 搜索结果表格的一行。

        列顺序：分类 | 标题 | 链接(种子+磁力) | 大小 | 日期 | 做种 | 下载数
        """
        from searcher import SearchResult

        cols = row.select("td")
        if len(cols) < 7:
            return None

        # 标题（第2列，取非 comments 的 a 标签）
        title_a = cols[1].select_one("a:not(.comments)")
        if not title_a:
            return None
        title = title_a.get_text(strip=True)
        if not title:
            return None

        # 磁力链接（第3列）
        magnet_a = cols[2].select_one("a[href^='magnet:']")
        if not magnet_a:
            return None
        magnet_url = magnet_a.get("href", "")

        # 提取 infohash
        hash_match = _MAGNET_HASH_RE.search(magnet_url)
        if not hash_match:
            return None

        # 大小（第4列）
        size_str = cols[3].get_text(strip=True)
        size_gb = _parse_size(size_str)

        # 做种数（第6列）和下载数（第7列）
        try:
            seeders = int(cols[5].get_text(strip=True))
        except (ValueError, IndexError):
            seeders = 0
        try:
            leechers = int(cols[6].get_text(strip=True))
        except (ValueError, IndexError):
            leechers = 0

        quality = parse_quality(title)
        quality_level = get_quality_level(quality)

        return SearchResult(
            title=title,
            size_gb=size_gb,
            indexer=self.SOURCE_NAME,
            seeders=seeders,
            leechers=leechers,
            download_url=magnet_url,
            info_url="",
            quality_tag=quality.display if quality.display else "Unknown",
            quality=quality,
            quality_rank=quality_level.rank,
        )
