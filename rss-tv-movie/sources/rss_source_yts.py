"""YTS RSS 源 — 电影 BT 站，小体积高质量 YIFY 编码。

RSS 接口：https://yts.mx/rss/ 或 https://yts.am/rss/（全站 RSS）
也支持 JSON API：GET /api/v2/list_movies.json?query_term=xxx

主要用于电影洗版发现新版本。
需代理。
"""

import logging
from typing import List, Optional
from urllib.parse import quote

from rss_source_base import RSSSourceBase, RSSItem, extract_episode, extract_season
from quality_parser import parse_quality

logger = logging.getLogger(__name__)

# YTS 常用 tracker 列表
_YTS_TRACKERS = [
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://glotorrents.pw:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://p4p.arenabg.com:1337",
]


class YTSRSSSource(RSSSourceBase):
    """YTS RSS 源：通过 JSON API 搜索电影种子。"""

    name = "yts"
    display_name = "YTS"

    BASE_URL = "https://yts.am"
    FALLBACK_URL = "https://movies-api.accel.li"
    API_PATH = "/api/v2/list_movies.json"

    def __init__(self, proxy: str = ""):
        self._proxy = proxy
        self._session = None

    def _get_session(self):
        """懒加载 session（优先 curl_cffi）。"""
        if self._session is not None:
            return self._session
        try:
            from curl_cffi import requests as cf_requests
            self._session = cf_requests.Session(impersonate="chrome131")
            return self._session
        except ImportError:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if self._proxy:
                self._session.proxies = {"http": self._proxy, "https": self._proxy}
            return self._session

    def fetch(self, subscription) -> List[RSSItem]:
        """根据订阅信息搜索 YTS API。"""
        keywords = self._build_search_keywords(subscription)
        if not keywords:
            return []

        all_items: List[RSSItem] = []
        seen_hashes: set = set()

        for kw in keywords:
            try:
                items = self._search_api(kw)
                for item in items:
                    if item.info_hash and item.info_hash in seen_hashes:
                        continue
                    if item.info_hash:
                        seen_hashes.add(item.info_hash)
                    all_items.append(item)
                if all_items:
                    break  # 有结果就停止回退
            except Exception as e:
                logger.warning("[yts] 搜索失败 '%s': %s", kw, str(e))

        return all_items

    def _build_search_keywords(self, subscription) -> List[str]:
        """从订阅信息构造搜索词（英文优先，YTS 是英文站）。"""
        keywords = []
        seen = set()

        def _add(kw):
            kw = kw.strip()
            if kw and kw not in seen:
                seen.add(kw)
                keywords.append(kw)

        if subscription.search_keyword:
            _add(subscription.search_keyword)
            return keywords

        aliases = subscription.aliases or {}
        for en in (aliases.get("en") or []):
            _add(en)
        _add(subscription.title)

        return keywords

    def _search_api(self, keyword: str) -> List[RSSItem]:
        """调用 YTS JSON API 搜索电影。"""
        params = {
            "query_term": keyword,
            "limit": 20,
            "sort_by": "seeds",
            "order_by": "desc",
        }
        session = self._get_session()

        for base_url in [self.BASE_URL, self.FALLBACK_URL]:
            url = f"{base_url}{self.API_PATH}"
            try:
                proxy = self._proxy if self._proxy else None
                if hasattr(session, "impersonate"):
                    resp = session.get(url, params=params, timeout=15, proxy=proxy)
                else:
                    resp = session.get(url, params=params, timeout=15)

                if resp.status_code != 200:
                    logger.warning("[yts] %s 返回 %d", base_url, resp.status_code)
                    continue

                data = resp.json()
                if data.get("status") != "ok":
                    continue

                movies = data.get("data", {}).get("movies") or []
                items = []
                for movie in movies:
                    items.extend(self._parse_movie(movie))
                return items

            except Exception as e:
                logger.warning("[yts] %s 请求失败: %s", base_url, str(e)[:80])
                continue

        return []

    def _parse_movie(self, movie: dict) -> List[RSSItem]:
        """解析单部电影的所有种子版本。"""
        title = movie.get("title_long") or movie.get("title", "")
        year = movie.get("year", "")
        torrents = movie.get("torrents") or []
        if not title or not torrents:
            return []

        items = []
        for t in torrents:
            torrent_hash = t.get("hash", "")
            if not torrent_hash:
                continue

            quality_str = t.get("quality", "")
            codec = t.get("video_codec", "")
            torrent_type = t.get("type", "")

            display_title = f"{title} {year} {quality_str}"
            if codec:
                display_title += f" {codec}"
            if torrent_type:
                display_title += f" {torrent_type}"
            display_title += " YTS"

            # 构造磁力链接
            dn = quote(f"{title} [{year}] [{quality_str}] [YTS.MX]")
            trackers = "&".join(f"tr={quote(tr)}" for tr in _YTS_TRACKERS)
            magnet = f"magnet:?xt=urn:btih:{torrent_hash}&dn={dn}&{trackers}"

            size_bytes = t.get("size_bytes", 0)
            size_gb = round(size_bytes / (1024 ** 3), 2) if size_bytes > 0 else 0

            quality = parse_quality(display_title)

            items.append(RSSItem(
                title=display_title,
                download_url=magnet,
                info_url=movie.get("url", ""),
                pub_date="",
                size_gb=size_gb,
                info_hash=torrent_hash.upper(),
                quality_tag=quality.display or "Unknown",
                resolution=quality.resolution,
                episode=None,
                season=None,
                source_name=self.name,
                seeders=t.get("seeds", 0),
                indexer="yts",
            ))

        return items
