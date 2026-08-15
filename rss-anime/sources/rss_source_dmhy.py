"""动漫花园 (share.dmhy.org) RSS 源 — 中文动漫资源最大的公开 BT 站。

RSS 接口：
- 按关键词：https://share.dmhy.org/topics/rss/rss.xml?keyword=xxx
- 按字幕组：?team_id=xxx
- 按分类：?sort_id=2（动画）

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
_SIZE_RE = re.compile(r"(\d+\.?\d*)\s*(GB|MB|TB|GiB|MiB|TiB)", re.IGNORECASE)
_SIZE_UNITS = {"TB": 1024, "TiB": 1024, "GB": 1, "GiB": 1, "MB": 1/1024, "MiB": 1/1024}


def _parse_size(text: str) -> float:
    """从文本中提取大小（GB）。"""
    m = _SIZE_RE.search(text)
    if m:
        num = float(m.group(1))
        unit = m.group(2)
        for k, factor in _SIZE_UNITS.items():
            if k.lower() == unit.lower():
                return round(num * factor, 2)
    return 0.0


class DMHYRSSSource(RSSSourceBase):
    """动漫花园 RSS 源：按关键词搜索，中文动漫全覆盖。"""

    name = "dmhy"
    display_name = "动漫花园"

    BASE_URL = "https://share.dmhy.org"

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
        """根据订阅信息搜索动漫花园 RSS。"""
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
                logger.warning("[dmhy] RSS 搜索失败 '%s': %s", kw, str(e))

        return all_items

    def _build_search_keywords(self, subscription) -> List[str]:
        """从订阅信息构造搜索词（中文+日文优先，动漫花园以中文资源为主）。"""
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

    def _fetch_rss(self, keyword: str) -> List[RSSItem]:
        """请求动漫花园 RSS 并解析。"""
        url = f"{self.BASE_URL}/topics/rss/rss.xml"
        params = {"keyword": keyword}
        session = self._get_session()

        try:
            proxy = self._proxy if self._proxy else None
            if hasattr(session, "impersonate"):
                resp = session.get(url, params=params, timeout=15, proxy=proxy)
            else:
                resp = session.get(url, params=params, timeout=15)

            if resp.status_code != 200:
                logger.warning("[dmhy] RSS 返回 %d", resp.status_code)
                return []

            return self._parse_rss_xml(resp.text)
        except Exception as e:
            logger.error("[dmhy] RSS 请求失败: %s", str(e))
            return []

    def _parse_rss_xml(self, xml_text: str) -> List[RSSItem]:
        """解析动漫花园 RSS XML。"""
        items = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("[dmhy] RSS XML 解析失败: %s", str(e))
            return []

        for item_el in root.iter("item"):
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

                # 提取 infohash
                info_hash = ""
                hash_match = _MAGNET_HASH_RE.search(download_url)
                if hash_match:
                    info_hash = hash_match.group(1).upper()

                # 大小
                size_gb = 0.0
                if enclosure is not None:
                    length = enclosure.get("length", "0")
                    try:
                        size_gb = round(int(length) / (1024 ** 3), 2)
                    except (ValueError, TypeError):
                        pass
                if size_gb == 0:
                    size_gb = _parse_size(title)

                pub_date = (item_el.findtext("pubDate") or "").strip()
                quality = parse_quality(title)
                episode = extract_episode(title)
                season = extract_season(title)

                # 详情页链接
                info_url = (item_el.findtext("link") or "").strip()
                if info_url.startswith("magnet:"):
                    info_url = ""

                items.append(RSSItem(
                    title=title,
                    download_url=download_url,
                    info_url=info_url,
                    pub_date=pub_date,
                    size_gb=size_gb,
                    info_hash=info_hash,
                    quality_tag=quality.display or "Unknown",
                    resolution=quality.resolution,
                    episode=episode,
                    season=season,
                    source_name=self.name,
                    seeders=0,
                    indexer="dmhy",
                ))
            except Exception:
                continue

        return items
