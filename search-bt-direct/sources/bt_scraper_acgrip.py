"""ACG.RIP BT 搜索爬虫。

搜索接口：GET https://acg.rip/?term=关键词
HTML 表格解析，提取种子下载链接+大小。
动画字幕组资源站，中日文覆盖率高。
国内可直连，不需要代理。
注意：ACG.RIP 没有做种数信息和磁力链接，使用 .torrent 下载链接。
"""

import re
import logging
from typing import List, Optional

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
# 大小单位转换
_SIZE_UNITS = {"TB": 1024, "GB": 1, "MB": 1 / 1024, "KB": 1 / (1024 * 1024)}


def _parse_size(size_str: str) -> float:
    """解析大小字符串（如 '110.5 GB'）为 GB。"""
    size_str = size_str.strip()
    for unit, factor in _SIZE_UNITS.items():
        if unit in size_str.upper():
            try:
                num_str = size_str.upper().replace(unit, "").strip()
                num = float(num_str)
                return round(num * factor, 2)
            except (ValueError, IndexError):
                return 0
    return 0


class ACGRipScraper(ScraperBase):
    """ACG.RIP BT 搜索爬虫。"""

    BASE_URL = "https://acg.rip"
    SOURCE_NAME = "acgrip"

    def __init__(self, proxy: Optional[str] = None):
        # 国内可直连
        super().__init__(proxy=proxy, use_curl_cffi=False, cache_ttl=600)

    def search_as_search_results(self, keyword: str, max_results: int = 30):
        """搜索并返回 SearchResult 格式。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword, max_results)
            self.set_cached(keyword, results)
            logger.info("[acgrip] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[acgrip] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str, max_results: int) -> list:
        """GET 搜索 ACG.RIP，解析 HTML 表格。

        表格结构（4列）：
        - Col 0: 发布者（含 /user/xxx 链接）
        - Col 1: 标题（含 /t/xxx 链接）
        - Col 2: 下载链接（.torrent 文件）
        - Col 3: 大小
        """
        from searcher import SearchResult

        url = self.BASE_URL
        params = {"term": keyword}

        try:
            resp = self.request_with_backoff(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning("[acgrip] 搜索返回 %d", resp.status_code)
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.select_one("table.post-index")
            if not table:
                return []

            rows = table.select("tr")
            results = []
            for row in rows:
                cols = row.select("td")
                if len(cols) < 4:
                    continue  # 跳过表头或无效行

                try:
                    result = self._parse_row(cols)
                    if result:
                        results.append(result)
                        if len(results) >= max_results:
                            break
                except Exception:
                    continue

            return results

        except Exception as e:
            logger.error("[acgrip] 搜索请求失败: %s", str(e))
            return []

    def _parse_row(self, cols):
        """解析搜索结果表格的一行（4 列）。"""
        from searcher import SearchResult

        # Col 1: 标题（找 /t/xxx 链接）
        title_a = cols[1].select_one("a[href*='/t/']")
        if not title_a:
            return None
        title = title_a.get_text(strip=True)
        if not title:
            return None

        # Col 2: 下载链接（.torrent 文件）
        torrent_a = cols[2].select_one("a[href$='.torrent']")
        if not torrent_a:
            return None
        torrent_href = torrent_a.get("href", "")
        if not torrent_href:
            return None

        # 构造完整下载 URL
        download_url = torrent_href
        if download_url.startswith("/"):
            download_url = f"{self.BASE_URL}{download_url}"

        # Col 3: 大小
        size_str = cols[3].get_text(strip=True)
        size_gb = _parse_size(size_str)

        quality = parse_quality(title)
        quality_level = get_quality_level(quality)

        # ACG.RIP 没有做种数信息，标记为磁力链接源特征（seeders=0, size_gb 保留）
        return SearchResult(
            title=title,
            size_gb=size_gb,
            indexer=self.SOURCE_NAME,
            seeders=0,
            leechers=0,
            download_url=download_url,
            info_url="",
            quality_tag=quality.display if quality.display else "Unknown",
            quality=quality,
            quality_rank=quality_level.rank,
        )
