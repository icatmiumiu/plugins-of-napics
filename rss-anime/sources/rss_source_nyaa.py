"""Nyaa.si RSS 源 — 日本动画/日剧 BT 站，订阅框架接入。

复用 bt_scraper_nyaa.py 的搜索逻辑，输出 RSSItem 格式。
需代理（config.http_proxy）。
"""

import re
import logging
import xml.etree.ElementTree as ET
from typing import List, Optional

from rss_source_base import RSSSourceBase, RSSItem, extract_episode, extract_season
from quality_parser import parse_quality

logger = logging.getLogger(__name__)
_MAGNET_HASH_RE = re.compile(r"btih:([a-fA-F0-9]{40})", re.IGNORECASE)
_SIZE_UNITS = {"TiB": 1024, "GiB": 1, "MiB": 1 / 1024, "KiB": 1 / (1024 * 1024)}


def _parse_size(size_str: str) -> float:
    """解析大小字符串为 GB。"""
    for unit, factor in _SIZE_UNITS.items():
        if unit in size_str:
            try:
                return round(float(size_str.replace(unit, "").strip()) * factor, 2)
            except ValueError:
                return 0
    return 0.0


class NyaaRSSSource(RSSSourceBase):
    """Nyaa.si RSS 源。"""

    name = "nyaa"
    display_name = "Nyaa"

    BASE_URL = "https://nyaa.si"

    def __init__(self, proxy: str = ""):
        self._proxy = proxy
        self._session = None

    def _get_session(self):
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
        """根据订阅信息搜索 Nyaa RSS。"""
        keywords = self._build_search_keywords(subscription)
        if not keywords:
            return []

        all_items: List[RSSItem] = []
        seen_hashes: set = set()

        for kw in keywords:
            try:
                items = self._fetch_rss(kw)
                for item in items:
                    if item.info_hash and item.info_hash in seen_hashes:
                        continue
                    if item.info_hash:
                        seen_hashes.add(item.info_hash)
                    all_items.append(item)
            except Exception as e:
                logger.warning("[nyaa] RSS 搜索失败 '%s': %s", kw, str(e))

        return all_items

    def _build_search_keywords(self, subscription) -> List[str]:
        """从订阅信息构造搜索词（英文+日文优先，Nyaa 以英文资源为主）。"""
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
        for jp in (aliases.get("original") or aliases.get("jp") or []):
            _add(jp)
        _add(subscription.title)

        return keywords

    def _fetch_rss(self, keyword: str) -> List[RSSItem]:
        """请求 Nyaa RSS 并解析。"""
        url = self.BASE_URL
        params = {"page": "rss", "q": keyword, "c": "0_0", "f": "0"}
        session = self._get_session()

        try:
            proxy = self._proxy if self._proxy else None
            if hasattr(session, "impersonate"):
                resp = session.get(url, params=params, timeout=15, proxy=proxy)
            else:
                resp = session.get(url, params=params, timeout=15)

            if resp.status_code != 200:
                logger.warning("[nyaa] RSS 返回 %d", resp.status_code)
                return []

            return self._parse_rss_xml(resp.text)
        except Exception as e:
            logger.error("[nyaa] RSS 请求失败: %s", str(e))
            return []

    def _parse_rss_xml(self, xml_text: str) -> List[RSSItem]:
        """解析 Nyaa RSS XML。"""
        items = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("[nyaa] RSS XML 解析失败: %s", str(e))
            return []

        # Nyaa RSS namespace
        ns = {"nyaa": "https://nyaa.si/xmlns/nyaa"}

        for item_el in root.iter("item"):
            try:
                title = (item_el.findtext("title") or "").strip()
                if not title:
                    continue

                link = (item_el.findtext("link") or "").strip()
                # Nyaa RSS 的 link 是详情页，磁力在 nyaa:infoHash 或 guid
                guid = (item_el.findtext("guid") or "").strip()
                download_url = ""

                # 尝试从 nyaa:infoHash 构造磁力链接
                info_hash_el = item_el.find("nyaa:infoHash", ns)
                if info_hash_el is not None and info_hash_el.text:
                    info_hash = info_hash_el.text.strip().upper()
                    download_url = f"magnet:?xt=urn:btih:{info_hash}"
                else:
                    # fallback: 从 guid 或 link 提取
                    for candidate in [guid, link]:
                        if candidate.startswith("magnet:"):
                            download_url = candidate
                            break
                        hash_match = _MAGNET_HASH_RE.search(candidate)
                        if hash_match:
                            info_hash = hash_match.group(1).upper()
                            download_url = f"magnet:?xt=urn:btih:{info_hash}"
                            break

                if not download_url:
                    continue

                # 提取 infohash
                info_hash = ""
                hash_match = _MAGNET_HASH_RE.search(download_url)
                if hash_match:
                    info_hash = hash_match.group(1).upper()

                # 大小
                size_gb = 0.0
                size_el = item_el.find("nyaa:size", ns)
                if size_el is not None and size_el.text:
                    size_gb = _parse_size(size_el.text)

                # 做种数
                seeders = 0
                seeders_el = item_el.find("nyaa:seeders", ns)
                if seeders_el is not None and seeders_el.text:
                    try:
                        seeders = int(seeders_el.text)
                    except ValueError:
                        pass

                pub_date = (item_el.findtext("pubDate") or "").strip()
                quality = parse_quality(title)
                episode = extract_episode(title)
                season = extract_season(title)

                items.append(RSSItem(
                    title=title,
                    download_url=download_url,
                    info_url=link,
                    pub_date=pub_date,
                    size_gb=size_gb,
                    info_hash=info_hash,
                    quality_tag=quality.display or "Unknown",
                    resolution=quality.resolution,
                    episode=episode,
                    season=season,
                    source_name=self.name,
                    seeders=seeders,
                    indexer="nyaa",
                ))
            except Exception:
                continue

        return items
