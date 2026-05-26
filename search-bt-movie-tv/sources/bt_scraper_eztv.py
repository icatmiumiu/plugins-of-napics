"""EZTV BT 直搜爬虫 — 欧美剧集搜索。

JSON API：GET https://eztv.re/api/get-torrents?imdb_id=xxx&limit=50
也支持关键词搜索（通过 RSS 端点回退）。
需代理（config.http_proxy）。
"""

import re
import logging
from typing import List, Optional
from urllib.parse import quote

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
_MAGNET_HASH_RE = re.compile(r"btih:([a-fA-F0-9]{40})", re.IGNORECASE)

# EZTV 常用 tracker
_EZTV_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://open.demonii.com:1337/announce",
]


class EZTVScraper(ScraperBase):
    """EZTV BT 直搜爬虫 — JSON API。"""

    BASE_URL = "https://eztv.re"
    API_PATH = "/api/get-torrents"
    SOURCE_NAME = "eztv"

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
            logger.info("[eztv] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[eztv] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str, max_results: int) -> list:
        """调用 EZTV JSON API 搜索。"""
        from searcher import SearchResult

        # EZTV API 按关键词搜索（search_string 参数）
        params = {
            "limit": min(max_results, 100),
        }
        # 如果 keyword 看起来像 IMDB ID（纯数字或 tt 开头），用 imdb_id 参数
        imdb_match = re.match(r"^(?:tt)?(\d{7,})$", keyword.strip())
        if imdb_match:
            params["imdb_id"] = imdb_match.group(1)
        else:
            # EZTV API 不支持 search_string 参数，用 RSS 端点搜索
            return self._search_via_rss(keyword, max_results)

        url = f"{self.BASE_URL}{self.API_PATH}"
        try:
            resp = self.request_with_backoff(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning("[eztv] API 返回 %d", resp.status_code)
                return []

            data = resp.json()
            torrents = data.get("torrents") or []
            return self._parse_torrents(torrents, max_results)
        except Exception as e:
            logger.error("[eztv] API 请求失败: %s", str(e))
            return []

    def _search_via_rss(self, keyword: str, max_results: int) -> list:
        """通过 RSS 端点搜索（EZTV API 不支持关键词搜索时的回退）。"""
        from searcher import SearchResult
        import xml.etree.ElementTree as ET

        url = f"{self.BASE_URL}/ezrss.xml"
        params = {"search_string": keyword}

        try:
            resp = self.request_with_backoff(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning("[eztv] RSS 返回 %d", resp.status_code)
                return []

            root = ET.fromstring(resp.text)
            ns = {"torrent": "http://xmlns.ezrss.it/0.1/"}
            results = []

            for item_el in root.iter("item"):
                if len(results) >= max_results:
                    break
                try:
                    title = (item_el.findtext("title") or "").strip()
                    if not title:
                        continue

                    # 磁力链接
                    download_url = ""
                    magnet_el = item_el.find("torrent:magnetURI", ns)
                    if magnet_el is not None and magnet_el.text:
                        download_url = magnet_el.text.strip()
                    if not download_url:
                        link = (item_el.findtext("link") or "").strip()
                        if link.startswith("magnet:"):
                            download_url = link
                    if not download_url:
                        continue

                    # infohash
                    info_hash = ""
                    hash_el = item_el.find("torrent:infoHash", ns)
                    if hash_el is not None and hash_el.text:
                        info_hash = hash_el.text.strip().upper()
                    if not info_hash:
                        hm = _MAGNET_HASH_RE.search(download_url)
                        if hm:
                            info_hash = hm.group(1).upper()

                    # 大小
                    size_gb = 0.0
                    size_el = item_el.find("torrent:contentLength", ns)
                    if size_el is not None and size_el.text:
                        try:
                            size_gb = round(int(size_el.text) / (1024 ** 3), 2)
                        except (ValueError, TypeError):
                            pass

                    # 做种数
                    seeders = 0
                    seeds_el = item_el.find("torrent:seeds", ns)
                    if seeds_el is not None and seeds_el.text:
                        try:
                            seeders = int(seeds_el.text)
                        except (ValueError, TypeError):
                            pass

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
        except Exception as e:
            logger.error("[eztv] RSS 搜索失败: %s", str(e))
            return []

    def _parse_torrents(self, torrents: list, max_results: int) -> list:
        """解析 EZTV JSON API 返回的种子列表。"""
        from searcher import SearchResult

        results = []
        for t in torrents[:max_results]:
            try:
                title = t.get("title", "").strip()
                if not title:
                    continue

                # 磁力链接
                magnet_url = t.get("magnet_url", "")
                torrent_url = t.get("torrent_url", "")
                download_url = magnet_url or torrent_url
                if not download_url:
                    # 用 hash 构造磁力链接
                    info_hash = t.get("hash", "")
                    if info_hash:
                        trackers = "&".join(f"tr={tr}" for tr in _EZTV_TRACKERS)
                        download_url = f"magnet:?xt=urn:btih:{info_hash}&{trackers}"
                if not download_url:
                    continue

                size_bytes = t.get("size_bytes", 0) or 0
                size_gb = round(int(size_bytes) / (1024 ** 3), 2) if size_bytes else 0.0

                seeders = t.get("seeds", 0) or 0
                leechers = t.get("peers", 0) or 0

                quality = parse_quality(title)
                quality_level = get_quality_level(quality)

                results.append(SearchResult(
                    title=title,
                    size_gb=size_gb,
                    indexer=self.SOURCE_NAME,
                    seeders=seeders,
                    leechers=leechers,
                    download_url=download_url,
                    info_url="",
                    quality_tag=quality.display if quality.display else "Unknown",
                    quality=quality,
                    quality_rank=quality_level.rank,
                ))
            except Exception:
                continue

        return results
