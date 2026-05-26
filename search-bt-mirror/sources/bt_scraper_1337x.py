"""1337x BT 搜索爬虫 — 综合性公开 BT 站，资源覆盖面广。

搜索接口：GET https://www.1337xx.to/search/关键词/1/
详情页获取磁力链接：GET https://www.1337xx.to/torrent/xxx/
需代理，使用 chrome120 指纹绕过 CF。
"""

import re
import logging
from typing import List, Optional
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
_MAGNET_HASH_RE = re.compile(r"btih:([a-fA-F0-9]{40})", re.IGNORECASE)

# 大小单位转换
_SIZE_UNITS = {"TB": 1024, "GB": 1, "MB": 1 / 1024, "KB": 1 / (1024 * 1024)}


def _parse_size(size_str: str) -> float:
    """解析 1337x 的大小字符串（如 '7.2 GB'）为 GB。"""
    if not size_str:
        return 0.0
    # 1337x 的大小格式可能是 "7.2 GB7.2 GB"（重复），取前半部分
    for unit, factor in _SIZE_UNITS.items():
        if unit in size_str.upper():
            try:
                num_str = size_str.upper().split(unit)[0].strip()
                # 去掉非数字字符（除了小数点）
                num_str = re.sub(r"[^\d.]", "", num_str)
                return round(float(num_str) * factor, 2)
            except (ValueError, IndexError):
                pass
    return 0.0


class X1337xScraper(ScraperBase):
    """1337x BT 搜索爬虫 — HTML 解析，需两步请求（列表+详情取磁力）。"""

    # 主域名用镜像站（主站 1337x.to CF 严格）
    BASE_URL = "https://www.1337xx.to"
    SOURCE_NAME = "1337x"

    def __init__(self, proxy: Optional[str] = None):
        # chrome120 能绕过 1337x 镜像站的 CF
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600, impersonate="chrome120")

    def search_as_search_results(self, keyword: str, max_results: int = 20):
        """搜索并返回 SearchResult 格式。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword, max_results)
            # 标题相关性过滤：1337x 是综合站，搜索结果可能包含大量不相关内容
            if results and keyword:
                results = self._filter_relevant(results, keyword)
            self.set_cached(keyword, results)
            logger.info("[1337x] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[1337x] 搜索异常: %s", str(e))
            return []

    def _filter_relevant(self, results: list, keyword: str) -> list:
        """过滤掉标题与搜索词完全不相关的结果。"""
        from text_processing import normalize
        kw_norm = normalize(keyword).lower()
        if not kw_norm:
            return results
        # 简单子串匹配：标题 normalize 后包含搜索词的任意连续 token
        kw_tokens = kw_norm.split()
        filtered = []
        for r in results:
            title_norm = normalize(r.title).lower()
            # 至少一个搜索词 token 出现在标题中
            if any(t in title_norm for t in kw_tokens if len(t) >= 2):
                filtered.append(r)
        return filtered if filtered else results  # 全部过滤掉时保留原结果

    def _do_search(self, keyword: str, max_results: int) -> list:
        """搜索 1337x，解析 HTML 表格，并发获取磁力链接。"""
        from searcher import SearchResult

        encoded = quote(keyword, safe="")
        url = f"{self.BASE_URL}/search/{encoded}/1/"

        try:
            resp = self.request_with_backoff(url, timeout=15)
            if resp.status_code != 200:
                logger.warning("[1337x] 搜索返回 %d", resp.status_code)
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.table-list tbody tr")
            if not rows:
                return []

            # 解析列表页，收集详情页 URL
            items = []
            for row in rows[:max_results]:
                try:
                    item = self._parse_list_row(row)
                    if item:
                        items.append(item)
                except Exception:
                    continue

            if not items:
                return []

            # 并发获取磁力链接（最多 5 个并发）
            results = []
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {
                    pool.submit(self._fetch_magnet, item["detail_url"]): item
                    for item in items
                }
                for future in as_completed(futures, timeout=20):
                    item = futures[future]
                    try:
                        magnet = future.result(timeout=10)
                        if magnet:
                            quality = parse_quality(item["title"])
                            quality_level = get_quality_level(quality)
                            results.append(SearchResult(
                                title=item["title"],
                                size_gb=item["size_gb"],
                                indexer=self.SOURCE_NAME,
                                seeders=item["seeders"],
                                leechers=item["leechers"],
                                download_url=magnet,
                                info_url=item["detail_url"],
                                quality_tag=quality.display if quality.display else "Unknown",
                                quality=quality,
                                quality_rank=quality_level.rank,
                            ))
                    except Exception:
                        continue

            return results

        except Exception as e:
            logger.error("[1337x] 搜索请求失败: %s", str(e))
            return []

    def _parse_list_row(self, row) -> Optional[dict]:
        """解析搜索结果表格的一行。

        列顺序：名称 | 做种 | 下载 | 日期 | 大小 | 上传者
        """
        cols = row.select("td")
        if len(cols) < 5:
            return None

        # 标题和详情链接（第 1 列，第二个 a 标签）
        name_a = cols[0].select_one("a:nth-of-type(2)")
        if not name_a:
            return None
        title = name_a.get_text(strip=True)
        href = name_a.get("href", "")
        if not title or not href:
            return None

        detail_url = f"{self.BASE_URL}{href}" if href.startswith("/") else href

        # 做种数（第 2 列）
        try:
            seeders = int(cols[1].get_text(strip=True))
        except (ValueError, IndexError):
            seeders = 0

        # 下载数（第 3 列）
        try:
            leechers = int(cols[2].get_text(strip=True))
        except (ValueError, IndexError):
            leechers = 0

        # 大小（第 5 列）
        size_str = cols[4].get_text(strip=True) if len(cols) > 4 else ""
        size_gb = _parse_size(size_str)

        return {
            "title": title,
            "detail_url": detail_url,
            "seeders": seeders,
            "leechers": leechers,
            "size_gb": size_gb,
        }

    def _fetch_magnet(self, detail_url: str) -> Optional[str]:
        """从详情页获取磁力链接。"""
        try:
            resp = self.request_with_backoff(detail_url, timeout=10)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            magnet_a = soup.select_one('a[href^="magnet:"]')
            if magnet_a:
                return magnet_a.get("href", "")
            return None
        except Exception:
            return None
