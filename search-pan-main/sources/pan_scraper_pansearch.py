"""PanSearch (pansearch.me) 爬虫 — 国内可直连的网盘聚合搜索站。

无需绕过 Cloudflare，直接 requests 即可。
支持按网盘类型搜索：quark/aliyun/baidu 等。
"""

import re
import logging
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from pan_models import PanResult, PanType, VALID_PAN_DOMAINS

logger = logging.getLogger(__name__)
# 网盘域名 → PanType
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

# 提取码正则
_PASSWORD_RE = re.compile(
    r"(?:提取码|密码|访问码)\s*[:：]\s*([a-zA-Z0-9]{4,8})",
)

# 网盘链接正则
_PAN_URL_RE = re.compile(
    r"https?://(?:" +
    "|".join(re.escape(d) for d in VALID_PAN_DOMAINS) +
    r")[^\s\"'<>\\]+",
)

# pansearch 支持的网盘类型参数
_PAN_SEARCH_TYPES = ["quark", "aliyundrive", "baidu"]


class PanSearchScraper(ScraperBase):
    """PanSearch 爬虫 — 国内可直连，无需反爬处理。"""

    BASE_URL = "https://www.pansearch.me"
    SOURCE_NAME = "pansearch"

    def search(self, keyword: str) -> List[PanResult]:
        """搜索所有支持的网盘类型，合并返回。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        all_results: List[PanResult] = []
        seen_urls = set()

        for pan_type_param in _PAN_SEARCH_TYPES:
            self.random_delay(0.5, 1.0)  # pansearch 不需要太长延迟
            try:
                results = self._search_by_type(keyword, pan_type_param)
                for r in results:
                    if r.share_url not in seen_urls:
                        seen_urls.add(r.share_url)
                        all_results.append(r)
            except Exception as e:
                logger.warning("[pansearch] %s 搜索失败: %s", pan_type_param, str(e))

        self.set_cached(keyword, all_results)
        logger.info("[pansearch] 搜索 '%s' 获取 %d 条结果", keyword, len(all_results))
        return all_results

    def _search_by_type(self, keyword: str, pan_type: str) -> List[PanResult]:
        """按网盘类型搜索。"""
        url = f"{self.BASE_URL}/search"
        params = {"keyword": keyword, "pan": pan_type}

        resp = self.request_with_backoff(url, params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning("[pansearch] 搜索返回 %d", resp.status_code)
            return []

        return self._parse_results(resp.text)

    def _parse_results(self, html: str) -> List[PanResult]:
        """解析搜索结果页，提取网盘链接和标题。"""
        results: List[PanResult] = []
        soup = BeautifulSoup(html, "html.parser")

        # pansearch 的结果通常在卡片或列表中
        # 策略：找所有包含网盘链接的区块
        # 先尝试结构化解析
        for card in soup.select("[class*='result'], [class*='item'], [class*='card'], article"):
            card_html = str(card)
            card_text = card.get_text()

            # 提取网盘链接
            pan_urls = _PAN_URL_RE.findall(card_html)
            if not pan_urls:
                continue

            # 提取标题（优先 h2/h3/h4，其次第一行文本）
            title_el = card.select_one("h2, h3, h4, .title, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                # 取第一段有意义的文本
                for text_node in card.stripped_strings:
                    if len(text_node) > 3 and not text_node.startswith("http"):
                        title = text_node[:100]
                        break

            # 提取提取码
            password = ""
            pw_match = _PASSWORD_RE.search(card_text)
            if pw_match:
                password = pw_match.group(1)

            for raw_url in pan_urls:
                url = raw_url.rstrip(".,;:!?\"')")
                pan_type = self._detect_pan_type(url)
                if pan_type is None:
                    continue

                try:
                    results.append(PanResult(
                        title=title or "未知标题",
                        pan_type=pan_type,
                        share_url=url,
                        password=password,
                        source=self.SOURCE_NAME,
                    ))
                except ValueError:
                    continue

        # 如果结构化解析没结果，降级为全文正则提取
        if not results:
            results = self._fallback_extract(html)

        return results

    def _fallback_extract(self, html: str) -> List[PanResult]:
        """降级：全文正则提取网盘链接。"""
        results = []
        seen = set()

        pan_urls = _PAN_URL_RE.findall(html)
        for raw_url in pan_urls:
            url = raw_url.rstrip(".,;:!?\"')")
            if url in seen:
                continue
            seen.add(url)

            pan_type = self._detect_pan_type(url)
            if pan_type is None:
                continue

            # 在链接附近找标题和提取码
            idx = html.find(raw_url)
            context = html[max(0, idx - 300):idx + len(raw_url) + 200] if idx >= 0 else ""

            # 提取码
            password = ""
            pw_match = _PASSWORD_RE.search(context)
            if pw_match:
                password = pw_match.group(1)

            # 标题：从上下文中提取中文文本
            title = ""
            cn_match = re.search(r'[\u4e00-\u9fff][\u4e00-\u9fff\w\s·：:（）()]{2,50}', context)
            if cn_match:
                title = cn_match.group(0).strip()

            try:
                results.append(PanResult(
                    title=title or "未知标题",
                    pan_type=pan_type,
                    share_url=url,
                    password=password,
                    source=self.SOURCE_NAME,
                ))
            except ValueError:
                continue

        return results

    def _detect_pan_type(self, url: str) -> Optional[PanType]:
        """根据 URL 域名检测网盘类型。"""
        domain = urlparse(url).netloc.lower()
        for d, pt in _DOMAIN_TO_PAN_TYPE.items():
            if d in domain:
                return pt
        return None
