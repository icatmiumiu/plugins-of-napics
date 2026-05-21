"""PanSou API 客户端 — 网盘搜索聚合引擎。

对接 PanSou 的 /api/search 接口，支持：
- plugins 参数：hunhepan/pansearch/jikepan/qupansou/labi 等 10 个搜索插件
- channels 参数：60+ 个 Telegram 频道（服务端代搜，无需 TG 账号）
- cloud_types 过滤：只返回目标网盘类型
- res=merged_by_type：按网盘类型分组返回

未配置 API 地址时跳过搜索返回空结果。
"""

import logging
from typing import List, Optional
from urllib.parse import urlparse

from scraper_base import ScraperBase
from pan_models import PanResult, PanType, VALID_PAN_DOMAINS

logger = logging.getLogger(__name__)
# PanSou 返回的网盘类型标识 → PanType 映射
_PANSOU_TYPE_MAP = {
    "quark": PanType.QUARK,
    "夸克": PanType.QUARK,
    "aliyun": PanType.ALIYUN,
    "aliyundrive": PanType.ALIYUN,
    "阿里": PanType.ALIYUN,
    "baidu": PanType.BAIDU,
    "百度": PanType.BAIDU,
    "115": PanType.PAN115,
    "pikpak": PanType.PIKPAK,
    "onedrive": PanType.UNKNOWN,
}

# 目标网盘类型（只保留这些类型的结果）
_TARGET_PAN_TYPES = {
    PanType.QUARK, PanType.ALIYUN, PanType.BAIDU,
    PanType.PAN115, PanType.PIKPAK,
}

# 默认启用的插件（覆盖面广、稳定性好的优先）
DEFAULT_PLUGINS = [
    "hunhepan", "pansearch", "jikepan", "qupansou", "labi",
]

# 默认搜索的 TG 频道（影视资源为主的高质量频道）
DEFAULT_CHANNELS = [
    "shareQuark_Movies", "alyp_4K_Movies", "Oscar_4Kmovies",
    "yunpanqk", "alipanshare", "shareAliyun", "alyp_TV",
    "Quark_Share_Channel", "AliyunDrive_Share_Channel",
    "quarkshare", "ucwpzy", "ucquarkxx",
    "PanJClub", "gotopan", "kkxlzy",
]


class PanSouClient(ScraperBase):
    """PanSou API 客户端 — 继承 ScraperBase。

    通过 plugins + channels 参数，一次调用覆盖 10 个搜索插件 + TG 频道。
    """

    SOURCE_NAME = "pansou"

    def __init__(
        self,
        api_url: str = "",
        proxy: Optional[str] = None,
        plugins: Optional[List[str]] = None,
        channels: Optional[List[str]] = None,
    ):
        super().__init__(proxy=proxy)
        self.api_url = api_url.rstrip("/") if api_url else ""
        self.plugins = plugins if plugins is not None else DEFAULT_PLUGINS
        self.channels = channels if channels is not None else DEFAULT_CHANNELS

    def search(self, keyword: str) -> List[PanResult]:
        """搜索并返回 PanResult 列表。未配置 API 地址时返回空。"""
        if not self.api_url:
            logger.debug("[pansou] API 地址未配置，跳过")
            return []

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._search_merged(keyword)
            self.set_cached(keyword, results)
            logger.info("[pansou] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[pansou] 搜索异常: %s", str(e))
            return []

    def _search_merged(self, keyword: str) -> List[PanResult]:
        """调用 /api/search，使用 merged_by_type 响应格式。"""
        url = f"{self.api_url}/api/search"
        params = {
            "kw": keyword,
            "res": "merged_by_type",
        }
        # 拼接 plugins 和 channels
        if self.plugins:
            params["plugins"] = ",".join(self.plugins)
        if self.channels:
            params["channels"] = ",".join(self.channels)
        # 只搜目标网盘类型
        params["cloud_types"] = "quark,aliyun,baidu,115,pikpak"

        resp = self.request_with_backoff(url, method="GET", params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning("[pansou] API 返回 %d", resp.status_code)
            # 降级到旧接口
            return self._search_legacy(keyword)

        data = resp.json()
        if data.get("code") != 0:
            logger.warning("[pansou] API 返回错误: %s", data.get("message", ""))
            return self._search_legacy(keyword)

        return self._parse_merged_response(data)

    def _parse_merged_response(self, data: dict) -> List[PanResult]:
        """解析 merged_by_type 格式的响应。

        格式：{"data": {"merged_by_type": {"aliyun": [...], "quark": [...]}}}
        """
        results = []
        seen_urls = set()

        inner = data.get("data", {})
        merged = inner.get("merged_by_type", {})

        for type_key, items in merged.items():
            pan_type = self._resolve_pan_type_from_key(type_key)
            if pan_type not in _TARGET_PAN_TYPES:
                continue

            if not isinstance(items, list):
                continue

            for item in items:
                share_url = item.get("url", "")
                if not share_url or share_url in seen_urls:
                    continue
                seen_urls.add(share_url)

                title = item.get("note", item.get("title", ""))
                password = item.get("password", "")

                # 从 URL 二次确认网盘类型
                url_type = self._resolve_pan_type("", share_url)
                final_type = url_type if url_type != PanType.UNKNOWN else pan_type

                try:
                    results.append(PanResult(
                        title=title or "未知标题",
                        pan_type=final_type,
                        share_url=share_url,
                        password=str(password) if password else "",
                        source=self.SOURCE_NAME,
                    ))
                except ValueError as e:
                    logger.debug("[pansou] PanResult 校验失败: %s", str(e))
                    continue

        return results

    def _search_legacy(self, keyword: str) -> List[PanResult]:
        """降级到旧版接口（兼容不支持新参数的 PanSou 实例）。"""
        try:
            url = f"{self.api_url}/api/search"
            resp = self.request_with_backoff(
                url, method="GET",
                params={"keyword": keyword, "page": 1, "size": 20},
                timeout=10,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            items = data.get("data", data.get("list", data.get("results", [])))
            if not isinstance(items, list):
                return []

            return self._parse_flat_results(items)
        except Exception as e:
            logger.error("[pansou] 旧接口降级失败: %s", str(e))
            return []

    def _parse_flat_results(self, items: list) -> List[PanResult]:
        """解析旧版扁平列表格式的结果。"""
        results = []
        seen_urls = set()

        for item in items:
            title = item.get("title", item.get("name", ""))
            share_url = item.get("url", item.get("share_url", item.get("link", "")))
            pan_type_str = item.get("type", item.get("pan_type", item.get("source", "")))
            password = item.get("password", item.get("pwd", ""))

            if not title or not share_url:
                continue
            if share_url in seen_urls:
                continue
            seen_urls.add(share_url)

            pan_type = self._resolve_pan_type(pan_type_str, share_url)
            if pan_type not in _TARGET_PAN_TYPES:
                continue

            try:
                results.append(PanResult(
                    title=title,
                    pan_type=pan_type,
                    share_url=share_url,
                    password=str(password) if password else "",
                    source=self.SOURCE_NAME,
                ))
            except ValueError as e:
                logger.debug("[pansou] PanResult 校验失败: %s", str(e))
                continue

        return results

    def _resolve_pan_type_from_key(self, key: str) -> PanType:
        """从 merged_by_type 的分组 key 解析网盘类型。"""
        key_lower = key.lower().strip()
        for k, pt in _PANSOU_TYPE_MAP.items():
            if k in key_lower:
                return pt
        return PanType.UNKNOWN

    def _resolve_pan_type(self, type_str: str, url: str) -> PanType:
        """解析网盘类型：先从 type 字段匹配，再从 URL 域名推断。"""
        if type_str:
            type_lower = type_str.lower().strip()
            for key, pt in _PANSOU_TYPE_MAP.items():
                if key in type_lower:
                    return pt

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
