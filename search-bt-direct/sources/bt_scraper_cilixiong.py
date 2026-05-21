"""磁力熊 (cilixiong.org) BT 搜索爬虫。

搜索流程：POST /e/search/index.php → 搜索结果页 → 详情页 /movie/xxxx.html → 磁力链接。
直连无需代理，中文电影为主，作为 Prowlarr 的国产片补充。
"""

import re
import logging
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
_MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:([a-fA-F0-9]{40})[^\s\"'<>]*")


class CilixiongScraper(ScraperBase):
    """磁力熊 BT 搜索爬虫。"""

    BASE_URL = "https://cilixiong.org"
    SEARCH_URL = "https://cilixiong.org/e/search/index.php"
    SOURCE_NAME = "cilixiong"
    MAX_DETAIL_PAGES = 5

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600)

    def search_as_search_results(self, keyword: str, max_results: int = 20):
        """搜索并返回 SearchResult 格式（兼容 Prowlarr 结果）。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            detail_items = self._do_search(keyword)
            if not detail_items:
                logger.info("[cilixiong] 搜索 '%s' 无结果", keyword)
                return []

            # 标题相关性过滤：搜索词的连续中文子串必须在标题中出现
            cn_chars = re.sub(r"[^\u4e00-\u9fff]", "", keyword)
            if cn_chars and len(cn_chars) >= 2:
                cn_sub = cn_chars[:min(4, len(cn_chars))]
                detail_items = [item for item in detail_items if cn_sub in item["title"]]

            all_results: List[SearchResult] = []
            seen_hashes = set()

            for item in detail_items[:self.MAX_DETAIL_PAGES]:
                self.random_delay(2.0, 3.0)
                magnets = self._parse_detail_page(item["url"])
                for magnet_url, infohash, filename in magnets:
                    if infohash in seen_hashes:
                        continue
                    seen_hashes.add(infohash)

                    # 标题优先级：文件名（详情页提取）> dn 参数 > 搜索标题
                    if filename:
                        # 中文搜索标题 + 英文文件名拼接
                        cn_title = re.sub(r"[\d.]+\s*\d{4}$", "", item["title"]).strip()
                        title = f"{cn_title} | {filename}" if cn_title and cn_title not in filename else filename
                    else:
                        dn_match = re.search(r"[?&]dn=([^&]+)", magnet_url)
                        if dn_match:
                            from urllib.parse import unquote
                            dn_title = unquote(dn_match.group(1)).replace("+", " ").strip()
                            title = dn_title if len(dn_title) > 5 else item["title"]
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

            self.set_cached(keyword, all_results)
            logger.info("[cilixiong] 搜索 '%s' 获取 %d 条结果", keyword, len(all_results))
            return all_results

        except Exception as e:
            logger.error("[cilixiong] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str) -> List[dict]:
        """POST 搜索，返回详情页链接列表。"""
        try:
            # 先访问首页拿 cookie
            self.request_with_backoff(self.BASE_URL, timeout=10)
            self.random_delay(1.0, 2.0)

            resp = self.request_with_backoff(
                self.SEARCH_URL,
                method="POST",
                data={
                    "keyboard": keyword,
                    "classid": "1,2",
                    "show": "title",
                    "tempid": "1",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning("[cilixiong] 搜索返回 %d", resp.status_code)
                return []

            return self._parse_search_page(resp.text)

        except Exception as e:
            logger.error("[cilixiong] 搜索请求失败: %s", str(e))
            return []

    def _parse_search_page(self, html: str) -> List[dict]:
        """解析搜索结果页，提取详情页链接。"""
        soup = BeautifulSoup(html, "html.parser")
        items = []
        seen = set()

        for a in soup.select("a[href*='/movie/']"):
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if not text or len(text) < 3 or not href.endswith(".html"):
                continue
            full_url = urljoin(self.BASE_URL, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            # 清理标题（去掉评分和年份后缀粘连）
            title = re.sub(r"(\d\.\d)(\d{4})$", r" \1 \2", text)
            items.append({"title": title.strip(), "url": full_url})

        return items

    def _parse_detail_page(self, url: str) -> List[tuple]:
        """解析详情页，提取磁力链接和文件名。返回 [(magnet_url, infohash, filename), ...]。"""
        try:
            resp = self.request_with_backoff(url, timeout=15)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            seen_hashes = set()

            # 从 <a> 标签提取磁力链接和文件名
            for a_tag in soup.find_all("a", href=_MAGNET_RE):
                href = a_tag.get("href", "")
                m = _MAGNET_RE.search(href)
                if not m:
                    continue
                infohash = m.group(1).upper()
                if infohash in seen_hashes:
                    continue
                seen_hashes.add(infohash)
                # 提取文件名（<a> 标签文字，如 "Project.Hail.Mary.2026.1080p.WEB-DL.mkv[18.6G]"）
                filename = a_tag.get_text(strip=True)
                # 清理：去掉 [大小] 后缀和"详情"等无关文字
                if filename:
                    filename = re.sub(r"\[[\d.]+[GMK]B?\]$", "", filename).strip()
                    if filename in ("详情", "详细", "") or len(filename) < 5:
                        filename = ""
                results.append((m.group(0), infohash, filename))

            # 补充：正则直接提取（可能有些不在 <a> 标签里）
            for match in _MAGNET_RE.finditer(resp.text):
                infohash = match.group(1).upper()
                if infohash not in seen_hashes:
                    seen_hashes.add(infohash)
                    results.append((match.group(0), infohash, ""))

            return results

        except Exception as e:
            logger.warning("[cilixiong] 详情页失败 %s: %s", url, str(e))
            return []
