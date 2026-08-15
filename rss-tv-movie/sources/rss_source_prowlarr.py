"""Prowlarr RSS 源：通过 Prowlarr API 搜索，作为订阅框架的第一个可用源。

搜索策略：
- 从订阅的 aliases 构造 Search Group（多词并查、info_hash 去重）
- 剧集追加季号后缀（S01）
- 复用现有 enhanced_search() 的 ProwlarrClient
"""

import re
import logging
from typing import List, Optional

from rss_source_base import RSSSourceBase, RSSItem, extract_episode, extract_season
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
class ProwlarrRSSSource(RSSSourceBase):
    """Prowlarr 搜索源"""

    name = "prowlarr"
    display_name = "Prowlarr"

    def __init__(self, prowlarr_client=None):
        self._client = prowlarr_client

    def set_client(self, client):
        """延迟设置 client（避免循环依赖）"""
        self._client = client

    def fetch(self, subscription) -> List[RSSItem]:
        """根据订阅信息搜索 Prowlarr，返回标准化结果"""
        if not self._client:
            logger.info("[ProwlarrRSS] 客户端未初始化")
            return []

        keywords = self._build_search_group(subscription)
        if not keywords:
            return []

        all_items: List[RSSItem] = []
        seen_hashes: set = set()

        for kw in keywords:
            try:
                results = self._client.search(kw)
            except Exception as e:
                logger.error(f"[ProwlarrRSS] 搜索失败 '{kw}': {e}")
                continue

            for r in results:
                # info_hash 去重（用 download_url 兜底）
                dedup_key = r.download_url or r.title
                if dedup_key in seen_hashes:
                    continue
                seen_hashes.add(dedup_key)

                quality = parse_quality(r.title)
                episode = extract_episode(r.title)
                season = extract_season(r.title)

                all_items.append(RSSItem(
                    title=r.title,
                    download_url=r.download_url,
                    info_url=r.info_url or "",
                    size_gb=r.size_gb,
                    info_hash=dedup_key,
                    quality_tag=quality.display or "Unknown",
                    resolution=quality.resolution,
                    episode=episode,
                    season=season,
                    source_name=self.name,
                    seeders=r.seeders,
                    indexer=r.indexer,
                ))

        return all_items

    def _build_search_group(self, subscription) -> List[str]:
        """从订阅信息构造搜索词组"""
        keywords: List[str] = []
        seen: set = set()

        def _add(kw: str):
            kw = kw.strip()
            if kw and kw not in seen:
                seen.add(kw)
                keywords.append(kw)

        # 自定义搜索词优先
        if subscription.search_keyword:
            _add(subscription.search_keyword)
            return keywords

        # 季号后缀
        season_suffix = ""
        if subscription.type == "tv" and subscription.season:
            season_suffix = f" S{subscription.season:02d}"

        aliases = subscription.aliases or {}
        # 英文名（BT 站英文为主，优先级最高）
        for en in (aliases.get("en") or []):
            _add(en + season_suffix)
        # 日文/原始语言名（Nyaa/Mikan 日文命中率高）
        for jp in (aliases.get("original") or aliases.get("jp") or []):
            _add(jp + season_suffix)
        # 中文名
        for cn in (aliases.get("cn") or []):
            _add(cn + season_suffix)
        # 兜底：标题 + 年份
        _add(subscription.title + season_suffix)
        if subscription.year:
            _add(f"{subscription.title} {subscription.year}" + season_suffix)

        return keywords
