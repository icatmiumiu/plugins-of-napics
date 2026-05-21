"""我能搜 (wnsearch.top) 爬虫 — 夸克/百度/迅雷/UC 网盘搜索引擎。

影视资源更新快，支持夸克/百度/迅雷/UC 网盘。
前端 JS 渲染，需要逆向 API 或解析 HTML。
"""

import re
import logging
from typing import List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from pan_models import PanResult, PanType, VALID_PAN_DOMAINS

logger = logging.getLogger(__name__)
# 目标网盘类型
_TARGET_PAN_TYPES = {
    PanType.QUARK, PanType.ALIYUN, PanType.BAIDU,
    PanType.PAN115, PanType.PIKPAK,
}

# 网盘链接正则
_PAN_URL_RE = re.compile(
    r"https?://(?:" +
    "|".join(re.escape(d) for d in VALID_PAN_DOMAINS) +
    r")[^\s\"'<>\\]*",
)

# 提取码正则
_PASSWORD_RE = re.compile(
    r"(?:提取码|密码|访问码)\s*[:：]?\s*([a-zA-Z0-9]{4,8})",
)


class WnSearchScraper(ScraperBase):
    """我能搜爬虫 — wnsearch.top。"""

    BASE_URL = "https://www.wnsearch.top"
    SOURCE_NAME = "wnsearch"

    def search(self, keyword: str) -> List[PanResult]:
        """搜索我能搜，返回 PanResult 列表。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword)
            self.set_cached(keyword, results)
            logger.info("[wnsearch] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[wnsearch] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str) -> List[PanResult]:
        """尝试多种方式获取搜索结果。"""
        # 方式1：尝试 API 接口
        results = self._try_api_search(keyword)
        if results:
            return results

        # 方式2：HTML 搜索页
        results = self._try_html_search(keyword)
        return results

    def _try_api_search(self, keyword: str) -> List[PanResult]:
        """尝试调用我能搜的 API 接口。"""
        api_paths = [
            "/api/search",
            "/search/api",
            "/api/v1/search",
            "/api/list",
        ]

        for path in api_paths:
            try:
                url = f"{self.BASE_URL}{path}"
                self.random_delay(0.5, 1.0)
                resp = self.request_with_backoff(
                    url,
                    params={"q": keyword, "keyword": keyword, "kw": keyword},
                    timeout=8,
                )
                if resp.status_code != 200:
                    continue

                try:
                    data = resp.json()
                    return self._parse_api_response(data)
                except ValueError:
                    continue
            except Exception:
                continue

        return []

    def _parse_api_response(self, data: dict) -> List[PanResult]:
        """解析 API JSON 响应。"""
        results = []
        seen_urls = set()

        items = (
            data.get("data", {}).get("list", [])
            or data.get("data", {}).get("results", [])
            or data.get("results", [])
            or data.get("list", [])
            or data.get("data", [])
        )
        if not isinstance(items, list):
            return []

        for item in items:
            title = item.get("title", item.get("name", ""))
            share_url = item.get("url", item.get("share_url", item.get("link", "")))
            password = item.get("password", item.get("pwd", ""))
            pan_type_str = item.get("type", item.get("pan_type", ""))

            if not share_url or share_url in seen_urls:
                continue
            seen_urls.add(share_url)

            pan_type = self._detect_pan_type(share_url, pan_type_str)
            if pan_type not in _TARGET_PAN_TYPES:
                continue

            try:
                results.append(PanResult(
                    title=title or "未知标题",
                    pan_type=pan_type,
                    share_url=share_url,
                    password=str(password) if password else "",
                    source=self.SOURCE_NAME,
                ))
            except ValueError:
                continue

        return results

    def _try_html_search(self, keyword: str) -> List[PanResult]:
        """请求搜索页面，解析 HTML。"""
        try:
            # 我能搜的搜索路径可能是 /search/关键词 或 /search?q=关键词
            search_urls = [
                (f"{self.BASE_URL}/search", {"q": keyword}),
                (f"{self.BASE_URL}/search/{keyword}", {}),
                (f"{self.BASE_URL}/list/all", {"q": keyword}),
            ]

            for url, params in search_urls:
                self.random_delay(1.0, 2.0)
                try:
                    resp = self.request_with_backoff(url, params=params, timeout=10)
                    if resp.status_code == 200 and len(resp.text) > 500:
                        results = self._parse_html(resp.text)
                        if results:
                            return results
                except Exception:
                    continue

            return []
        except Exception as e:
            logger.warning("[wnsearch] HTML 搜索失败: %s", str(e))
            return []

    def _parse_html(self, html: str) -> List[PanResult]:
        """解析搜索结果 HTML。"""
        results = []
        seen_urls = set()
        soup = BeautifulSoup(html, "html.parser")

        # 尝试结构化解析
        selectors = [
            "[class*='result']", "[class*='item']", "[class*='card']",
            "[class*='resource']", "article", ".list-group-item",
        ]
        cards = []
        for sel in selectors:
            cards = soup.select(sel)
            if cards:
                break

        for card in cards:
            card_html = str(card)
            card_text = card.get_text()

            pan_urls = _PAN_URL_RE.findall(card_html)
            if not pan_urls:
                # 检查是否有跳转链接（很多站用中间页跳转）
                for a in card.select("a[href]"):
                    href = a.get("href", "")
                    if any(d in href for d in VALID_PAN_DOMAINS):
                        pan_urls.append(href)
            if not pan_urls:
                continue

            # 提取标题
            title = ""
            for tag in ["h2", "h3", "h4", ".title", "[class*='title']", "a"]:
                el = card.select_one(tag)
                if el:
                    text = el.get_text(strip=True)
                    if len(text) > 2:
                        title = text[:200]
                        break

            # 提取码
            password = ""
            pw_match = _PASSWORD_RE.search(card_text)
            if pw_match:
                password = pw_match.group(1)

            for raw_url in pan_urls:
                url = raw_url.rstrip(".,;:!?\"')")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                pan_type = self._detect_pan_type(url)
                if pan_type not in _TARGET_PAN_TYPES:
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

        # 降级全文正则
        if not results:
            results = self._fallback_extract(html)

        return results

    def _fallback_extract(self, html: str) -> List[PanResult]:
        """全文正则提取网盘链接。"""
        results = []
        seen = set()

        for raw_url in _PAN_URL_RE.findall(html):
            url = raw_url.rstrip(".,;:!?\"')")
            if url in seen:
                continue
            seen.add(url)

            pan_type = self._detect_pan_type(url)
            if pan_type not in _TARGET_PAN_TYPES:
                continue

            idx = html.find(raw_url)
            context = html[max(0, idx - 300):idx + len(raw_url) + 200] if idx >= 0 else ""

            password = ""
            pw_match = _PASSWORD_RE.search(context)
            if pw_match:
                password = pw_match.group(1)

            title = ""
            cn_match = re.search(
                r'[\u4e00-\u9fff][\u4e00-\u9fff\w\s·：:（）()]{2,50}', context
            )
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

    def _detect_pan_type(self, url: str, type_str: str = "") -> PanType:
        """根据 URL 域名或类型字符串检测网盘类型。"""
        if type_str:
            t = type_str.lower()
            if "quark" in t or "夸克" in t:
                return PanType.QUARK
            if "aliyun" in t or "阿里" in t or "alipan" in t:
                return PanType.ALIYUN
            if "baidu" in t or "百度" in t:
                return PanType.BAIDU
            if "115" in t:
                return PanType.PAN115
            if "pikpak" in t:
                return PanType.PIKPAK

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
