"""狗狗盘搜 (gogopanso.com) 爬虫 — 基于 aliyunpanshare 的搜索前端。

数据源：github.com/acoooder/aliyunpanshare，每日更新影视资源。
API 接口：
  - 搜索: GET https://gogopanso.com:3642/search?keyword=xxx
  - 详情: GET https://gogopanso.com:3642/getdetail?sid=xxx
返回 JSON，无反爬，直接 requests 可用。
"""

import logging
from typing import List, Optional
from urllib.parse import urlparse

from scraper_base import ScraperBase
from pan_models import PanResult, PanType

logger = logging.getLogger(__name__)
# gogopanso downtype → PanType
_DOWNTYPE_MAP = {
    "quark": PanType.QUARK,
    "alipan": PanType.ALIYUN,
    "aliyun": PanType.ALIYUN,
    "baidu": PanType.BAIDU,
    "115": PanType.PAN115,
    "pikpak": PanType.PIKPAK,
}

_TARGET_PAN_TYPES = {
    PanType.QUARK, PanType.ALIYUN, PanType.BAIDU,
    PanType.PAN115, PanType.PIKPAK,
}

API_BASE = "https://gogopanso.com:3642"


class GogoPansoScraper(ScraperBase):
    """狗狗盘搜爬虫 — 公开 JSON API，无反爬。"""

    SOURCE_NAME = "gogopanso"

    def search(self, keyword: str) -> List[PanResult]:
        """搜索狗狗盘搜，返回 PanResult 列表。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword)
            self.set_cached(keyword, results)
            logger.info("[gogopanso] 搜索 '%s' 获取 %d 条结果",
                        keyword, len(results))
            return results
        except Exception as e:
            logger.error("[gogopanso] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str) -> List[PanResult]:
        """调用搜索 API。"""
        url = f"{API_BASE}/search"
        self.random_delay(0.5, 1.0)
        resp = self.request_with_backoff(
            url, params={"keyword": keyword}, timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("[gogopanso] 搜索返回 %d", resp.status_code)
            return []

        data = resp.json()
        items = data.get("data", [])
        if not isinstance(items, list):
            return []

        return self._parse_results(items)

    def _parse_results(self, items: list) -> List[PanResult]:
        """解析搜索结果列表。"""
        results = []
        seen_urls = set()

        for item in items:
            name = item.get("name", "")
            downurl = item.get("downurl", "")
            downtype = item.get("downtype", "")

            if not downurl or downurl in seen_urls:
                continue
            seen_urls.add(downurl)

            # 解析网盘类型
            pan_type = _DOWNTYPE_MAP.get(downtype.lower(), PanType.UNKNOWN)
            if pan_type == PanType.UNKNOWN:
                pan_type = self._detect_from_url(downurl)
            if pan_type not in _TARGET_PAN_TYPES:
                continue

            # 清理标题（去掉首字母分类前缀，如 "L流浪地球2" → "流浪地球2"）
            title = self._clean_name(name)

            try:
                results.append(PanResult(
                    title=title,
                    pan_type=pan_type,
                    share_url=downurl,
                    password="",
                    source=self.SOURCE_NAME,
                ))
            except ValueError as e:
                logger.debug("[gogopanso] PanResult 校验失败: %s", str(e))
                continue

        return results

    def _clean_name(self, name: str) -> str:
        """清理标题：去掉首字母分类前缀。

        gogopanso 的标题格式如 "L流浪地球2" "B八千里路云和月2026"
        首字母是拼音首字母，需要去掉。
        """
        if not name:
            return "未知标题"
        # 如果第一个字符是英文字母，第二个是中文，去掉首字母
        if (len(name) >= 2
                and name[0].isascii() and name[0].isalpha()
                and "\u4e00" <= name[1] <= "\u9fff"):
            return name[1:]
        return name

    def _detect_from_url(self, url: str) -> PanType:
        """从 URL 域名推断网盘类型。"""
        domain = urlparse(url).netloc.lower()
        if "quark" in domain:
            return PanType.QUARK
        if "alipan" in domain or "aliyun" in domain:
            return PanType.ALIYUN
        if "baidu" in domain:
            return PanType.BAIDU
        if "115" in domain or "anxia" in domain:
            return PanType.PAN115
        if "pikpak" in domain:
            return PanType.PIKPAK
        return PanType.UNKNOWN
