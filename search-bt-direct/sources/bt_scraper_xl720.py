"""迅雷电影天堂 (xl720.com) BT 搜索爬虫。

搜索流程：GET /search/关键词 → 搜索结果页 → 详情页 /thunder/xxxx.html → 磁力+迅雷链接。
直连无需代理，中文电影/剧集。
"""

import base64
import re
import logging
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
_MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:([a-fA-F0-9]{40})[^\s\"'<>]*")
_THUNDER_RE = re.compile(r"thunder://([A-Za-z0-9+/=]+)")


class XL720Scraper(ScraperBase):
    """迅雷电影天堂 BT 搜索爬虫。"""

    BASE_URL = "https://www.xl720.com"
    SOURCE_NAME = "xl720"
    MAX_DETAIL_PAGES = 2

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600)
        self.max_retries = 1  # XL720 响应慢，只重试 1 次

    def search_as_search_results(self, keyword: str, max_results: int = 20):
        """搜索并返回 SearchResult 格式。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            detail_items = self._do_search(keyword)
            if not detail_items:
                logger.info("[xl720] 搜索 '%s' 无结果", keyword)
                return []

            # 标题相关性过滤：搜索词的连续中文子串必须在标题中出现
            cn_chars = re.sub(r"[^\u4e00-\u9fff]", "", keyword)
            if cn_chars and len(cn_chars) >= 2:
                # 取搜索词中文部分的前 N 个字作为子串匹配
                cn_sub = cn_chars[:min(4, len(cn_chars))]
                detail_items = [item for item in detail_items if cn_sub in item["title"]]
                if not detail_items:
                    logger.info("[xl720] 搜索 '%s' 标题过滤后无结果", keyword)
                    self.set_cached(keyword, [])
                    return []

            all_results: List[SearchResult] = []
            seen_hashes = set()

            for item in detail_items[:self.MAX_DETAIL_PAGES]:
                self.random_delay(0.5, 1.0)
                magnets = self._parse_detail_page(item["url"])
                for magnet_url, infohash, context in magnets:
                    if infohash in seen_hashes:
                        continue
                    seen_hashes.add(infohash)

                    # 标题优先级：dn 参数 > 上下文描述 > 搜索标题
                    dn_match = re.search(r"[?&]dn=([^&]+)", magnet_url)
                    if dn_match:
                        from urllib.parse import unquote
                        dn_title = unquote(dn_match.group(1)).replace("+", " ").strip()
                        title = dn_title if len(dn_title) > 5 else item["title"]
                    elif context and len(context) > 5 and context != item["title"]:
                        # 上下文描述（如"流浪地球2.4K.HDR.杜比视界"）
                        title = context
                    else:
                        title = item["title"]

                    quality = parse_quality(title)
                    quality_level = get_quality_level(quality)
                    all_results.append(SearchResult(
                        title=title,
                        size_gb=0,
                        indexer=self.SOURCE_NAME,
                        seeders=0,
                        leechers=0,
                        download_url=magnet_url,
                        info_url=item["url"],
                        quality_tag=quality.display if quality.display else "Unknown",
                        quality=quality,
                        quality_rank=quality_level.rank,
                    ))
                    if len(all_results) >= max_results:
                        break

                if len(all_results) >= max_results:
                    break

            self.set_cached(keyword, all_results)
            logger.info("[xl720] 搜索 '%s' 获取 %d 条结果", keyword, len(all_results))
            return all_results

        except Exception as e:
            logger.error("[xl720] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str) -> List[dict]:
        """GET 搜索，返回详情页链接列表。"""
        url = f"{self.BASE_URL}/search/{keyword}"
        try:
            resp = self.request_with_backoff(url, timeout=12)
            if resp.status_code != 200:
                logger.warning("[xl720] 搜索返回 %d", resp.status_code)
                return []
            return self._parse_search_page(resp.text)
        except Exception as e:
            logger.error("[xl720] 搜索请求失败: %s", str(e))
            return []

    def _parse_search_page(self, html: str) -> List[dict]:
        """解析搜索结果页。"""
        soup = BeautifulSoup(html, "html.parser")
        items = []
        seen = set()

        for a in soup.select("a[href*='/thunder/']"):
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or not href.endswith(".html"):
                continue
            full_url = urljoin(self.BASE_URL, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            items.append({"title": text.strip(), "url": full_url})

        return items

    def _parse_detail_page(self, url: str) -> List[tuple]:
        """解析详情页，提取磁力链接及其附近的描述文字。
        返回 [(magnet_url, infohash, context_text), ...]。
        """
        try:
            resp = self.request_with_backoff(url, timeout=12)
            if resp.status_code != 200:
                return []

            results = []
            seen_hashes = set()
            html = resp.text

            # 用 BeautifulSoup 解析，提取磁力链接所在元素的上下文文字
            soup = BeautifulSoup(html, "html.parser")
            # 找所有包含磁力链接的 <a> 标签
            for a_tag in soup.find_all("a", href=_MAGNET_RE):
                href = a_tag.get("href", "")
                m = _MAGNET_RE.search(href)
                if not m:
                    continue
                infohash = m.group(1).upper()
                if infohash in seen_hashes:
                    continue
                seen_hashes.add(infohash)
                # 提取上下文：a 标签文字 > 父元素文字 > 空
                context = a_tag.get_text(strip=True)
                if not context or len(context) < 3:
                    parent = a_tag.parent
                    if parent:
                        context = parent.get_text(strip=True)[:100]
                results.append((m.group(0), infohash, context or ""))

            # 补充：正则直接从 HTML 提取（可能有些磁力链接不在 <a> 标签里）
            for match in _MAGNET_RE.finditer(html):
                infohash = match.group(1).upper()
                if infohash not in seen_hashes:
                    seen_hashes.add(infohash)
                    results.append((match.group(0), infohash, ""))

            # 迅雷链接解码
            for match in _THUNDER_RE.finditer(html):
                decoded = self._decode_thunder(match.group(1))
                if decoded:
                    magnet_match = _MAGNET_RE.search(decoded)
                    if magnet_match:
                        infohash = magnet_match.group(1).upper()
                        if infohash not in seen_hashes:
                            seen_hashes.add(infohash)
                            results.append((magnet_match.group(0), infohash, ""))

            return results

        except Exception as e:
            logger.warning("[xl720] 详情页失败 %s: %s", url, str(e))
            return []

    @staticmethod
    def _decode_thunder(encoded: str) -> Optional[str]:
        """解码迅雷链接（base64 → 去掉 AA 前缀和 ZZ 后缀）。"""
        try:
            decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
            # 迅雷格式：AA + 真实链接 + ZZ
            if decoded.startswith("AA") and decoded.endswith("ZZ"):
                return decoded[2:-2]
            return decoded
        except Exception:
            return None
