"""低端影视 (ddys.io) 爬虫。

ddys 已从 WordPress 站点升级为自建站，提供 JSON API：
- POST /api/search-netdisk {"q": "关键词"} — 网盘资源搜索
- POST /api/search-online {"q": "关键词"} — 在线播放源搜索
- GET /api/hot-movies — 热门影片

网盘搜索返回的 link 字段是 base64 编码的真实网盘链接。
disk_type 字段标识网盘类型（"夸克网盘"/"阿里云盘"/"百度网盘" 等）。
"""

import base64
import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

from scraper_base import ScraperBase
from pan_models import PanResult, PanType, VALID_PAN_DOMAINS

logger = logging.getLogger(__name__)
# disk_type 中文 → PanType 映射
_DISK_TYPE_MAP = {
    "夸克网盘": PanType.QUARK,
    "夸克": PanType.QUARK,
    "阿里云盘": PanType.ALIYUN,
    "阿里": PanType.ALIYUN,
    "百度网盘": PanType.BAIDU,
    "百度": PanType.BAIDU,
    "115网盘": PanType.PAN115,
    "115": PanType.PAN115,
    "PikPak": PanType.PIKPAK,
    "pikpak": PanType.PIKPAK,
}

# 域名 → PanType 降级映射（disk_type 匹配不到时用）
_DOMAIN_TO_PAN_TYPE = {
    "pan.quark.cn": PanType.QUARK,
    "drive.quark.cn": PanType.QUARK,
    "www.alipan.com": PanType.ALIYUN,
    "www.aliyundrive.com": PanType.ALIYUN,
    "pan.baidu.com": PanType.BAIDU,
    "115.com": PanType.PAN115,
    "anxia.com": PanType.PAN115,
    "mypikpak.com": PanType.PIKPAK,
}


class DdysScraper(ScraperBase):
    """低端影视爬虫 — 通过 JSON API 搜索网盘资源。"""

    BASE_URL = "https://ddys.io"
    SOURCE_NAME = "ddys"

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy)
        self._warmed_up = False

    def warm_up(self) -> None:
        """预热：访问首页获取 cookie。"""
        if self._warmed_up:
            return
        try:
            resp = self.request_with_backoff(self.BASE_URL, timeout=10)
            if resp.status_code == 200:
                self._warmed_up = True
                logger.info("[ddys] 预热成功")
        except Exception as e:
            logger.warning("[ddys] 预热失败: %s", str(e))

    def search(self, keyword: str) -> List[PanResult]:
        """搜索网盘资源，返回 PanResult 列表。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        if not self._warmed_up:
            self.warm_up()

        try:
            results = self._search_netdisk(keyword)
            self.set_cached(keyword, results)
            logger.info("[ddys] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[ddys] 搜索异常: %s", str(e))
            return []

    def _search_netdisk(self, keyword: str) -> List[PanResult]:
        """调用 /api/search-netdisk 搜索网盘资源。"""
        url = f"{self.BASE_URL}/api/search-netdisk"
        try:
            resp = self.session.post(
                url,
                json={"q": keyword},
                timeout=15,
                headers={
                    "Referer": f"{self.BASE_URL}/search",
                    "Origin": self.BASE_URL,
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                logger.warning("[ddys] API 返回 %d", resp.status_code)
                return []

            data = resp.json()
            if not data.get("success"):
                msg = data.get("message", "未知错误")
                logger.warning("[ddys] API 返回失败: %s", msg)
                return []

            items = data.get("data", [])
            if not isinstance(items, list):
                logger.warning("[ddys] API 返回数据格式异常")
                return []

            return self._parse_items(items)

        except Exception as e:
            logger.error("[ddys] API 请求失败: %s", str(e))
            return []

    def _parse_items(self, items: list) -> List[PanResult]:
        """解析 API 返回的条目列表。

        每条数据结构：
        {
            "name": "流浪地球1+2 4K蓝光原盘...",
            "link": "aHR0cHM6Ly9wYW4ucXVhcmsuY24vcy8yNjYwNjg0N2UwOTk=",  # base64
            "link_display": "quark.ddys.io/s/••••••",
            "time": "28天前",
            "disk_type": "夸克网盘",
            "password": "",
            "source": "pansou"
        }
        """
        results: List[PanResult] = []
        seen_urls = set()

        for item in items:
            try:
                name = item.get("name", "").strip()
                link_b64 = item.get("link", "")
                disk_type = item.get("disk_type", "")
                password = item.get("password", "") or ""

                if not name or not link_b64:
                    continue

                # 解码 base64 链接
                share_url = self._decode_link(link_b64)
                if not share_url:
                    continue

                # 去重
                if share_url in seen_urls:
                    continue
                seen_urls.add(share_url)

                # 识别网盘类型
                pan_type = self._detect_pan_type(disk_type, share_url)
                if pan_type is None:
                    continue

                results.append(PanResult(
                    title=name,
                    pan_type=pan_type,
                    share_url=share_url,
                    password=password,
                    source=self.SOURCE_NAME,
                ))
            except (ValueError, Exception) as e:
                logger.debug("[ddys] 解析条目失败: %s", str(e))
                continue

        return results

    @staticmethod
    def _decode_link(encoded: str) -> Optional[str]:
        """解码 base64 编码的网盘链接。"""
        if not encoded:
            return None
        try:
            decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore").strip()
            if decoded.startswith("http"):
                # 验证域名在白名单中
                domain = urlparse(decoded).netloc.lower()
                if any(d in domain for d in VALID_PAN_DOMAINS):
                    return decoded
            return None
        except Exception:
            pass

        # 尝试 URL-safe base64
        try:
            decoded = base64.urlsafe_b64decode(encoded).decode("utf-8", errors="ignore").strip()
            if decoded.startswith("http"):
                domain = urlparse(decoded).netloc.lower()
                if any(d in domain for d in VALID_PAN_DOMAINS):
                    return decoded
            return None
        except Exception:
            return None

    @staticmethod
    def _detect_pan_type(disk_type: str, url: str) -> Optional[PanType]:
        """识别网盘类型：优先用 disk_type 字段，降级用 URL 域名。"""
        # 优先匹配 disk_type
        if disk_type:
            for keyword, pt in _DISK_TYPE_MAP.items():
                if keyword in disk_type:
                    return pt

        # 降级：从 URL 域名匹配
        domain = urlparse(url).netloc.lower()
        for d, pt in _DOMAIN_TO_PAN_TYPE.items():
            if d in domain:
                return pt

        return None
