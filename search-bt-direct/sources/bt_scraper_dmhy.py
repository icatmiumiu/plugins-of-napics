"""动漫花园 (share.dmhy.org) BT 直搜爬虫 — 中文动漫资源最大的公开 BT 站。

通过 RSS 端点搜索：GET https://share.dmhy.org/topics/rss/rss.xml?keyword=xxx
RSS 端点比 HTML 搜索页 CF 保护更轻，且返回标准 XML 易于解析。
需代理（config.http_proxy）。无做种数信息。
"""

import re
import logging
import xml.etree.ElementTree as ET
from typing import List, Optional

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
_MAGNET_HASH_RE = re.compile(r"btih:([a-fA-F0-9]{40})", re.IGNORECASE)

# 大小单位转换
_SIZE_UNITS = {"TB": 1024, "TiB": 1024, "GB": 1, "GiB": 1, "MB": 1 / 1024, "MiB": 1 / 1024}


def _parse_size_from_enclosure(length_str: str) -> float:
    """从 enclosure length（字节数）解析为 GB。"""
    try:
        return round(int(length_str) / (1024 ** 3), 2)
    except (ValueError, TypeError):
        return 0.0


class DMHYScraper(ScraperBase):
    """动漫花园 BT 直搜爬虫 — 通过 RSS 端点搜索。"""

    BASE_URL = "https://share.dmhy.org"
    SOURCE_NAME = "dmhy"

    def __init__(self, proxy: Optional[str] = None):
        # chrome124 兼容动漫花园的 SSL 配置（chrome131/120 可能 TLS 报错）
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600, impersonate="chrome124")

    def search_as_search_results(self, keyword: str, max_results: int = 30):
        """搜索并返回 SearchResult 格式。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword, max_results)
            self.set_cached(keyword, results)
            logger.info("[dmhy] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[dmhy] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str, max_results: int) -> list:
        """通过 RSS 端点搜索动漫花园。"""
        from searcher import SearchResult

        url = f"{self.BASE_URL}/topics/rss/rss.xml"
        params = {"keyword": keyword}

        try:
            resp = self.request_with_backoff(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning("[dmhy] RSS 返回 %d", resp.status_code)
                return []

            return self._parse_rss(resp.text, max_results)
        except Exception as e:
            logger.error("[dmhy] RSS 搜索失败: %s", str(e))
            return []

    def _parse_rss(self, xml_text: str, max_results: int) -> list:
        """解析动漫花园 RSS XML，返回 SearchResult 列表。"""
        from searcher import SearchResult

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("[dmhy] RSS XML 解析失败: %s", str(e))
            return []

        results = []
        for item_el in root.iter("item"):
            if len(results) >= max_results:
                break
            try:
                title = (item_el.findtext("title") or "").strip()
                if not title:
                    continue

                # 下载链接：enclosure（.torrent）或 link 中的磁力
                download_url = ""
                enclosure = item_el.find("enclosure")
                if enclosure is not None:
                    download_url = enclosure.get("url", "")

                if not download_url:
                    link = (item_el.findtext("link") or "").strip()
                    if link.startswith("magnet:"):
                        download_url = link

                if not download_url:
                    continue

                # infohash
                info_hash = ""
                hm = _MAGNET_HASH_RE.search(download_url)
                if hm:
                    info_hash = hm.group(1).upper()

                # 大小
                size_gb = 0.0
                if enclosure is not None:
                    length = enclosure.get("length", "0")
                    size_gb = _parse_size_from_enclosure(length)

                # 动漫花园 RSS 无做种数信息
                seeders = 0

                quality = parse_quality(title)
                quality_level = get_quality_level(quality)

                results.append(SearchResult(
                    title=title,
                    size_gb=size_gb,
                    indexer=self.SOURCE_NAME,
                    seeders=seeders,
                    leechers=0,
                    download_url=download_url,
                    info_url="",
                    quality_tag=quality.display if quality.display else "Unknown",
                    quality=quality,
                    quality_rank=quality_level.rank,
                ))
            except Exception:
                continue

        return results
