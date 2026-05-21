"""Bangumi Moe (bangumi.moe) BT 搜索爬虫。

搜索接口：POST https://bangumi.moe/api/torrent/search
JSON API，动画字幕组资源站。
国内可直连，不需要代理。
"""

import re
import logging
from typing import List, Optional

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
_MAGNET_HASH_RE = re.compile(r"btih:([a-fA-F0-9]{40})", re.IGNORECASE)

# Bangumi Moe tracker
_BGM_TRACKERS = [
    "http://open.acgtracker.com:1096/announce",
    "http://t2.popgo.org:7456/annonce",
    "http://share.camoe.cn:8080/announce",
]


class BangumiMoeScraper(ScraperBase):
    """Bangumi Moe BT 搜索爬虫 — JSON API。"""

    BASE_URL = "https://bangumi.moe"
    SOURCE_NAME = "bangumi_moe"

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
            logger.info("[bangumi_moe] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[bangumi_moe] 搜索异常: %s", str(e))
            return []

    @staticmethod
    def _parse_size_str(size_str: str) -> float:
        """解析大小字符串（如 '118.6 GB'）为 GB。"""
        _units = {"TB": 1024, "GB": 1, "MB": 1 / 1024, "KB": 1 / (1024 * 1024)}
        size_str = size_str.strip().upper()
        for unit, factor in _units.items():
            if unit in size_str:
                try:
                    num = float(size_str.replace(unit, "").strip())
                    return round(num * factor, 2)
                except ValueError:
                    return 0
        return 0

    def _do_search(self, keyword: str, max_results: int) -> list:
        """POST 搜索 Bangumi Moe API。"""
        from searcher import SearchResult
        from urllib.parse import quote

        url = f"{self.BASE_URL}/api/v2/torrent/search"
        payload = {"query": keyword}

        try:
            resp = self.request_with_backoff(
                url, method="POST", timeout=15,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.warning("[bangumi_moe] API 返回 %d", resp.status_code)
                return []

            data = resp.json()
            # API 返回 {"torrents": [...], "count": N, "page_count": N}
            torrents = data.get("torrents") or data if isinstance(data, list) else []
            if isinstance(data, dict):
                torrents = data.get("torrents", [])

            results = []
            for item in torrents[:max_results]:
                try:
                    result = self._parse_item(item)
                    if result:
                        results.append(result)
                except Exception:
                    continue

            return results

        except Exception as e:
            logger.error("[bangumi_moe] API 请求失败: %s", str(e))
            return []

    def _parse_item(self, item: dict):
        """解析单条种子信息。"""
        from searcher import SearchResult
        from urllib.parse import quote

        title = item.get("title", "").strip()
        if not title:
            return None

        # 获取 infohash 或磁力链接
        magnet = item.get("magnet", "")
        infohash = item.get("infoHash", "") or item.get("info_hash", "")

        if not magnet and infohash:
            # 构造磁力链接
            dn = quote(title)
            trackers = "&".join(f"tr={quote(tr)}" for tr in _BGM_TRACKERS)
            magnet = f"magnet:?xt=urn:btih:{infohash}&dn={dn}&{trackers}"
        elif not magnet:
            # 尝试从 _id 构造下载链接
            torrent_id = item.get("_id", "")
            if torrent_id:
                magnet = f"{self.BASE_URL}/download/torrent/{torrent_id}/{quote(title)}.torrent"
            else:
                return None

        # 验证有 hash
        if not infohash:
            hash_match = _MAGNET_HASH_RE.search(magnet)
            if hash_match:
                infohash = hash_match.group(1)

        # 大小（可能是字节数或字符串如 "118.6 GB"）
        size_raw = item.get("size", 0)
        if isinstance(size_raw, str):
            # 字符串格式如 "118.6 GB"
            size_gb = self._parse_size_str(size_raw)
        elif isinstance(size_raw, (int, float)) and size_raw > 0:
            size_gb = round(size_raw / (1024 ** 3), 2)
        else:
            size_gb = 0

        # 做种数（Bangumi Moe 可能没有做种数信息）
        seeders = item.get("seeders", 0) or item.get("seed", 0) or 0
        leechers = item.get("leechers", 0) or item.get("leech", 0) or 0

        quality = parse_quality(title)
        quality_level = get_quality_level(quality)

        return SearchResult(
            title=title,
            size_gb=size_gb,
            indexer=self.SOURCE_NAME,
            seeders=seeders,
            leechers=leechers,
            download_url=magnet,
            info_url="",
            quality_tag=quality.display if quality.display else "Unknown",
            quality=quality,
            quality_rank=quality_level.rank,
        )
