"""LimeTorrents BT 搜索爬虫。

搜索接口：GET https://www.limetorrents.lol/search/all/关键词/seeds/1/
HTML 解析，提取磁力链接+做种数+大小。
需代理（config.http_proxy），欧美影视覆盖率高。
"""

import re
import logging
from typing import List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
# 大小单位转换
_SIZE_UNITS = {"TB": 1024, "GB": 1, "MB": 1 / 1024, "KB": 1 / (1024 * 1024)}


def _parse_size(size_str: str) -> float:
    """解析大小字符串（如 '3.2 GB'）为 GB。"""
    for unit, factor in _SIZE_UNITS.items():
        if unit in size_str.upper():
            try:
                num = float(re.sub(r'[^\d.]', '', size_str.split(unit[0])[0].strip()))
                return round(num * factor, 2)
            except (ValueError, IndexError):
                return 0
    return 0


class LimeTorrentsScraper(ScraperBase):
    """LimeTorrents BT 搜索爬虫。"""

    BASE_URL = "https://www.limetorrents.lol"
    SOURCE_NAME = "limetorrents"

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600)

    def search_as_search_results(self, keyword: str, max_results: int = 30):
        """搜索并返回 SearchResult 格式。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword, max_results)
            self.set_cached(keyword, results)
            logger.info("[limetorrents] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[limetorrents] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str, max_results: int) -> list:
        """GET 搜索 LimeTorrents，解析 HTML。"""
        from searcher import SearchResult

        # URL 格式：/search/all/关键词/seeds/1/
        encoded_kw = quote(keyword)
        url = f"{self.BASE_URL}/search/all/{encoded_kw}/seeds/1/"

        try:
            resp = self.request_with_backoff(url, timeout=15)
            if resp.status_code != 200:
                logger.warning("[limetorrents] 搜索返回 %d", resp.status_code)
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            # 搜索结果在 table.table2 中
            table = soup.select_one("table.table2")
            if not table:
                return []

            rows = table.select("tr")[1:]  # 跳过表头
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
            logger.error("[limetorrents] 搜索请求失败: %s", str(e))
            return []

    def _parse_row(self, row):
        """解析搜索结果表格的一行。"""
        from searcher import SearchResult

        cols = row.select("td")
        if len(cols) < 5:
            return None

        # 标题和详情链接（第1列）
        title_a = cols[0].select_one("a.cif")
        if not title_a:
            return None
        title = title_a.get_text(strip=True)
        if not title:
            return None

        # 详情页链接 → 从中提取 hash
        detail_href = title_a.get("href", "")

        # 大小（第3列）
        size_str = cols[2].get_text(strip=True) if len(cols) > 2 else ""
        size_gb = _parse_size(size_str)

        # 做种数（第4列）和下载数（第5列）
        try:
            seeders = int(cols[3].get_text(strip=True).replace(",", ""))
        except (ValueError, IndexError):
            seeders = 0
        try:
            leechers = int(cols[4].get_text(strip=True).replace(",", ""))
        except (ValueError, IndexError):
            leechers = 0

        # 尝试从详情页获取磁力链接（或从 itorrents 构造）
        # LimeTorrents 的 hash 通常在详情页 URL 中
        hash_match = re.search(r'/([a-fA-F0-9]{40})\.torrent', detail_href)
        if not hash_match:
            # 尝试从页面中的 torrent 链接提取
            torrent_a = row.select_one("a[href*='.torrent']")
            if torrent_a:
                hash_match = re.search(r'/([a-fA-F0-9]{40})', torrent_a.get("href", ""))

        if not hash_match:
            return None

        torrent_hash = hash_match.group(1).upper()
        magnet = f"magnet:?xt=urn:btih:{torrent_hash}&dn={quote(title)}"

        quality = parse_quality(title)
        quality_level = get_quality_level(quality)

        return SearchResult(
            title=title,
            size_gb=size_gb,
            indexer=self.SOURCE_NAME,
            seeders=seeders,
            leechers=leechers,
            download_url=magnet,
            info_url=f"{self.BASE_URL}{detail_href}" if detail_href.startswith("/") else detail_href,
            quality_tag=quality.display if quality.display else "Unknown",
            quality=quality,
            quality_rank=quality_level.rank,
        )
