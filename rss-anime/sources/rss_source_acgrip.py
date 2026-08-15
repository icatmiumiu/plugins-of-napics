"""ACG.RIP RSS 源 — 动画字幕组资源站，中日文覆盖率高。

RSS 接口：
- 按关键词：https://acg.rip/t/关键词.xml
- 全站 RSS：https://acg.rip/.xml

国内可直连，不需要代理。
注意：ACG.RIP 没有做种数信息，使用 .torrent 下载链接。
"""

import re
import logging
import xml.etree.ElementTree as ET
from typing import List, Optional

from rss_source_base import RSSSourceBase, RSSItem, extract_episode, extract_season
from quality_parser import parse_quality

logger = logging.getLogger(__name__)
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


class ACGRipRSSSource(RSSSourceBase):
    """ACG.RIP RSS 源：按关键词订阅。"""

    name = "acgrip"
    display_name = "ACG.RIP"

    BASE_URL = "https://acg.rip"

    def __init__(self):
        self._session = None

    def _get_session(self):
        """懒加载 session（国内直连，不需要代理）。"""
        if self._session is not None:
            return self._session
        import requests
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        return self._session

    def fetch(self, subscription) -> List[RSSItem]:
        """根据订阅信息搜索 ACG.RIP RSS。"""
        keywords = self._build_search_keywords(subscription)
        if not keywords:
            return []

        all_items: List[RSSItem] = []
        seen_urls: set = set()

        for kw in keywords:
            try:
                items = self._fetch_rss(kw)
                for item in items:
                    # ACG.RIP 没有 infohash，用 download_url 去重
                    dedup_key = item.download_url or item.title
                    if dedup_key in seen_urls:
                        continue
                    seen_urls.add(dedup_key)
                    all_items.append(item)
            except Exception as e:
                logger.warning("[acgrip] RSS 搜索失败 '%s': %s", kw, str(e))

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

    def _fetch_rss(self, keyword: str) -> List[RSSItem]:
        """请求 ACG.RIP RSS 并解析。端点：/t/关键词.xml"""
        from urllib.parse import quote
        url = f"{self.BASE_URL}/t/{quote(keyword)}.xml"
        session = self._get_session()

        try:
            resp = session.get(url, timeout=15)
            if resp.status_code != 200:
                logger.warning("[acgrip] RSS 返回 %d", resp.status_code)
                return []

            return self._parse_rss_xml(resp.text)
        except Exception as e:
            logger.error("[acgrip] RSS 请求失败: %s", str(e))
            return []

    def _parse_rss_xml(self, xml_text: str) -> List[RSSItem]:
        """解析 ACG.RIP RSS XML。"""
        items = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("[acgrip] RSS XML 解析失败: %s", str(e))
            return []

        for item_el in root.iter("item"):
            try:
                title = (item_el.findtext("title") or "").strip()
                if not title:
                    continue

                # 下载链接：enclosure（.torrent 文件）
                download_url = ""
                enclosure = item_el.find("enclosure")
                if enclosure is not None:
                    download_url = enclosure.get("url", "")

                # fallback: link 可能是详情页或 .torrent
                if not download_url:
                    link = (item_el.findtext("link") or "").strip()
                    if link.endswith(".torrent"):
                        download_url = link

                if not download_url:
                    continue

                # ACG.RIP 没有 infohash，用 download_url 作为去重 key
                info_hash = ""

                # 大小
                size_gb = 0.0
                if enclosure is not None:
                    length = enclosure.get("length", "0")
                    try:
                        size_gb = round(int(length) / (1024 ** 3), 2)
                    except (ValueError, TypeError):
                        pass
                if size_gb == 0:
                    desc = item_el.findtext("description") or ""
                    size_gb = _parse_size(desc + " " + title)

                pub_date = (item_el.findtext("pubDate") or "").strip()
                quality = parse_quality(title)
                episode = extract_episode(title)
                season = extract_season(title)

                # 详情页
                info_url = (item_el.findtext("link") or "").strip()
                if info_url.endswith(".torrent"):
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
                    indexer="acgrip",
                ))
            except Exception:
                continue

        return items
