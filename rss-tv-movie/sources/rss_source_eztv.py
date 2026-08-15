"""EZTV RSS 源 — 欧美剧集追更首选。

RSS 接口：
- 按 IMDB ID：https://eztv.re/ezrss.xml?imdb_id=0944947
- 按关键词：https://eztv.re/ezrss.xml?search_string=xxx

直搜 API（备用）：
- https://eztv.re/api/get-torrents?imdb_id=0944947&limit=50

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


class EZTVRSSSource(RSSSourceBase):
    """EZTV RSS 源：按 IMDB ID 精准订阅或按关键词搜索。"""

    name = "eztv"
    display_name = "EZTV"

    BASE_URL = "https://eztv.re"

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
        """根据订阅信息搜索 EZTV RSS。优先用 IMDB ID，回退到关键词搜索。"""
        # 优先用 IMDB ID 精准订阅
        imdb_id = getattr(subscription, "imdb_id", "") or ""
        if imdb_id:
            # EZTV 的 imdb_id 参数不带 "tt" 前缀
            imdb_num = imdb_id.replace("tt", "")
            try:
                items = self._fetch_rss_by_imdb(imdb_num)
                if items:
                    return items
            except Exception as e:
                logger.warning("[eztv] IMDB RSS 失败 '%s': %s", imdb_id, str(e))

        # 回退到关键词搜索
        keywords = self._build_search_keywords(subscription)
        if not keywords:
            return []

        all_items: List[RSSItem] = []
        seen_hashes: set = set()

        for kw in keywords:
            try:
                items = self._fetch_rss_by_keyword(kw)
                for item in items:
                    if item.info_hash and item.info_hash in seen_hashes:
                        continue
                    if item.info_hash:
                        seen_hashes.add(item.info_hash)
                    all_items.append(item)
                if all_items:
                    break  # 有结果就停止回退
            except Exception as e:
                logger.warning("[eztv] RSS 搜索失败 '%s': %s", kw, str(e))

        return all_items

    def _build_search_keywords(self, subscription) -> List[str]:
        """从订阅信息构造搜索词（英文优先，EZTV 是英文站）。"""
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
        # EZTV 英文站，英文名优先
        for en in (aliases.get("en") or []):
            _add(en)
        _add(subscription.title)

        return keywords

    def _fetch_rss_by_imdb(self, imdb_num: str) -> List[RSSItem]:
        """按 IMDB ID 拉取 RSS feed。"""
        url = f"{self.BASE_URL}/ezrss.xml"
        return self._do_fetch(url, params={"imdb_id": imdb_num})

    def _fetch_rss_by_keyword(self, keyword: str) -> List[RSSItem]:
        """按关键词拉取 RSS feed。"""
        url = f"{self.BASE_URL}/ezrss.xml"
        return self._do_fetch(url, params={"search_string": keyword})

    def _do_fetch(self, url: str, params: dict) -> List[RSSItem]:
        """执行 RSS 请求并解析。"""
        session = self._get_session()
        try:
            proxy = self._proxy if self._proxy else None
            if hasattr(session, "impersonate"):
                resp = session.get(url, params=params, timeout=15, proxy=proxy)
            else:
                resp = session.get(url, params=params, timeout=15)

            if resp.status_code != 200:
                logger.warning("[eztv] RSS 返回 %d", resp.status_code)
                return []

            return self._parse_rss_xml(resp.text)
        except Exception as e:
            logger.error("[eztv] RSS 请求失败: %s", str(e))
            return []

    def _parse_rss_xml(self, xml_text: str) -> List[RSSItem]:
        """解析 EZTV RSS XML。"""
        items = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("[eztv] RSS XML 解析失败: %s", str(e))
            return []

        # EZTV RSS 使用 torrent 命名空间
        ns = {"torrent": "http://xmlns.ezrss.it/0.1/"}

        for item_el in root.iter("item"):
            try:
                title = (item_el.findtext("title") or "").strip()
                if not title:
                    continue

                # 磁力链接：优先 torrent:magnetURI，回退到 link
                download_url = ""
                magnet_el = item_el.find("torrent:magnetURI", ns)
                if magnet_el is not None and magnet_el.text:
                    download_url = magnet_el.text.strip()
                if not download_url:
                    link = (item_el.findtext("link") or "").strip()
                    if link.startswith("magnet:"):
                        download_url = link
                # 也尝试 enclosure
                if not download_url:
                    enclosure = item_el.find("enclosure")
                    if enclosure is not None:
                        download_url = enclosure.get("url", "")

                if not download_url:
                    continue

                # 提取 infohash
                info_hash = ""
                hash_el = item_el.find("torrent:infoHash", ns)
                if hash_el is not None and hash_el.text:
                    info_hash = hash_el.text.strip().upper()
                if not info_hash:
                    hash_match = _MAGNET_HASH_RE.search(download_url)
                    if hash_match:
                        info_hash = hash_match.group(1).upper()

                # 大小
                size_gb = 0.0
                size_el = item_el.find("torrent:contentLength", ns)
                if size_el is not None and size_el.text:
                    try:
                        size_gb = round(int(size_el.text) / (1024 ** 3), 2)
                    except (ValueError, TypeError):
                        pass
                if size_gb == 0:
                    size_gb = _parse_size(title)

                # 做种数
                seeders = 0
                seeds_el = item_el.find("torrent:seeds", ns)
                if seeds_el is not None and seeds_el.text:
                    try:
                        seeders = int(seeds_el.text)
                    except (ValueError, TypeError):
                        pass

                # 发布时间
                pub_date = (item_el.findtext("pubDate") or "").strip()

                # 质量解析
                quality = parse_quality(title)
                episode = extract_episode(title)
                season = extract_season(title)

                items.append(RSSItem(
                    title=title,
                    download_url=download_url,
                    info_url="",
                    pub_date=pub_date,
                    size_gb=size_gb,
                    info_hash=info_hash,
                    quality_tag=quality.display or "Unknown",
                    resolution=quality.resolution,
                    episode=episode,
                    season=season,
                    source_name=self.name,
                    seeders=seeders,
                    indexer="eztv",
                ))
            except Exception:
                continue

        return items
