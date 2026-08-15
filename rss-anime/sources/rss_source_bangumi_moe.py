"""Bangumi Moe (bangumi.moe) RSS 源 — 动画字幕组资源站。

RSS 接口：POST https://bangumi.moe/api/v2/torrent/rss（按标签订阅）
也支持搜索 API：POST https://bangumi.moe/api/v2/torrent/search

国内可直连，不需要代理。
"""

import re
import logging
from typing import List, Optional
from urllib.parse import quote

from rss_source_base import RSSSourceBase, RSSItem, extract_episode, extract_season
from quality_parser import parse_quality

logger = logging.getLogger(__name__)
_MAGNET_HASH_RE = re.compile(r"btih:([a-fA-F0-9]{40})", re.IGNORECASE)

_BGM_TRACKERS = [
    "http://open.acgtracker.com:1096/announce",
    "http://t2.popgo.org:7456/annonce",
    "http://share.camoe.cn:8080/announce",
]


class BangumiMoeRSSSource(RSSSourceBase):
    """Bangumi Moe RSS 源：通过搜索 API 获取种子列表。"""

    name = "bangumi_moe"
    display_name = "Bangumi Moe"

    BASE_URL = "https://bangumi.moe"

    def __init__(self):
        self._session = None

    def _get_session(self):
        """懒加载 session（国内直连）。"""
        if self._session is not None:
            return self._session
        import requests
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        })
        return self._session

    def fetch(self, subscription) -> List[RSSItem]:
        """根据订阅信息搜索 Bangumi Moe。"""
        keywords = self._build_search_keywords(subscription)
        if not keywords:
            return []

        all_items: List[RSSItem] = []
        seen_hashes: set = set()

        for kw in keywords:
            try:
                items = self._search(kw)
                for item in items:
                    dedup_key = item.info_hash or item.download_url
                    if dedup_key in seen_hashes:
                        continue
                    seen_hashes.add(dedup_key)
                    all_items.append(item)
            except Exception as e:
                logger.warning("[bangumi_moe] 搜索失败 '%s': %s", kw, str(e))

        return all_items

    def _build_search_keywords(self, subscription) -> List[str]:
        """从订阅信息构造搜索词（中文+日文优先）。"""
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
        for cn in (aliases.get("cn") or []):
            _add(cn)
        for jp in (aliases.get("original") or aliases.get("jp") or []):
            _add(jp)
        _add(subscription.title)

        return keywords

    def _search(self, keyword: str) -> List[RSSItem]:
        """POST 搜索 Bangumi Moe API。"""
        url = f"{self.BASE_URL}/api/v2/torrent/search"
        session = self._get_session()

        try:
            resp = session.post(url, json={"query": keyword}, timeout=15)
            if resp.status_code != 200:
                logger.warning("[bangumi_moe] API 返回 %d", resp.status_code)
                return []

            data = resp.json()
            torrents = data.get("torrents", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

            items = []
            for t in torrents:
                try:
                    item = self._parse_torrent(t)
                    if item:
                        items.append(item)
                except Exception:
                    continue
            return items

        except Exception as e:
            logger.error("[bangumi_moe] API 请求失败: %s", str(e))
            return []

    @staticmethod
    def _parse_size(size_raw) -> float:
        """解析大小为 GB。size_raw 可能是字节数(int)或字符串如 '118.6 GB'。"""
        if isinstance(size_raw, str):
            _units = {"TB": 1024, "GB": 1, "MB": 1/1024, "KB": 1/(1024*1024)}
            size_str = size_raw.strip().upper()
            for unit, factor in _units.items():
                if unit in size_str:
                    try:
                        return round(float(size_str.replace(unit, "").strip()) * factor, 2)
                    except ValueError:
                        return 0
            return 0
        elif isinstance(size_raw, (int, float)) and size_raw > 0:
            return round(size_raw / (1024 ** 3), 2)
        return 0

    def _parse_torrent(self, t: dict) -> Optional[RSSItem]:
        """解析单条种子信息为 RSSItem。"""
        title = t.get("title", "").strip()
        if not title:
            return None

        # 磁力链接
        magnet = t.get("magnet", "")
        infohash = t.get("infoHash", "") or t.get("info_hash", "")

        if not magnet and infohash:
            dn = quote(title)
            trackers = "&".join(f"tr={quote(tr)}" for tr in _BGM_TRACKERS)
            magnet = f"magnet:?xt=urn:btih:{infohash}&dn={dn}&{trackers}"
        elif not magnet:
            torrent_id = t.get("_id", "")
            if torrent_id:
                magnet = f"{self.BASE_URL}/download/torrent/{torrent_id}/{quote(title)}.torrent"
            else:
                return None

        if not infohash:
            hash_match = _MAGNET_HASH_RE.search(magnet)
            if hash_match:
                infohash = hash_match.group(1).upper()

        size_gb = self._parse_size(t.get("size", 0))
        seeders = t.get("seeders", 0) or t.get("seed", 0) or 0
        pub_date = t.get("publish_time", "") or t.get("created_at", "")

        quality = parse_quality(title)
        episode = extract_episode(title)
        season = extract_season(title)

        return RSSItem(
            title=title,
            download_url=magnet,
            info_url="",
            pub_date=pub_date,
            size_gb=size_gb,
            info_hash=infohash.upper() if infohash else "",
            quality_tag=quality.display or "Unknown",
            resolution=quality.resolution,
            episode=episode,
            season=season,
            source_name=self.name,
            seeders=seeders,
            indexer="bangumi_moe",
        )
