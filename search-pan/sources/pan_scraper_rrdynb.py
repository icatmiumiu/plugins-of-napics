"""人人电影网 (rrdynb.com) 爬虫。

搜索路径：GET /plus/search.php?q=关键词
搜索流程：session 预热首页 → GET 搜索 → 解析结果页（dl.item-third-dl）→ 逐条访问详情页 → 提取网盘链接+提取码。
多次搜索后可能触发 Cloudflare 验证，通过请求间延迟 + UA 轮换缓解。
"""

import re
import logging
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scraper_base import ScraperBase
from pan_models import PanResult, PanType, VALID_PAN_DOMAINS

logger = logging.getLogger(__name__)
# 网盘域名 → PanType 映射
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
    r"(?:提取码|密码|访问码|提取密码)\s*[:：]\s*([a-zA-Z0-9]{4,8})",
)

# 网盘链接正则
_PAN_URL_RE = re.compile(
    r"https?://(?:" +
    "|".join(re.escape(d) for d in VALID_PAN_DOMAINS) +
    r")[^\s\"'<>]*",
)

_YEAR_RE = re.compile(r"((?:19|20)\d{2})")

# 非分享链接黑名单（百度网盘下载页、工具页等）
_NON_SHARE_PATHS = ["/download", "/disk/home", "/disk/main", "/s/1nvT6eE1", "/s/1kVQSszd"]


def _is_non_share_url(url: str) -> bool:
    """检测是否为非分享链接（工具页/下载页/通用短链）。"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    # 黑名单路径
    if any(path == p or path.startswith(p + "/") for p in _NON_SHARE_PATHS):
        return True
    # 百度网盘分享链接格式：/s/ 后跟较长的 ID
    if "pan.baidu.com" in parsed.netloc and "/s/" in path:
        share_id = path.split("/s/")[-1]
        if len(share_id) < 10:
            return True
    return False


class RrdynbScraper(ScraperBase):
    """人人电影网爬虫。

    搜索结果在 dl.item-third-dl 容器中，详情页链接格式 /movie/年/月日/id.html。
    详情页直接包含网盘链接（夸克/阿里/百度/迅雷等）。
    """

    BASE_URL = "https://www.rrdynb.com"
    SEARCH_PATH = "/plus/search.php"
    SOURCE_NAME = "rrdynb"
    MAX_DETAIL_PAGES = 5

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, use_curl_cffi=True)
        self._warmed_up = False

    def warm_up(self) -> None:
        """预热：访问首页获取 cookie。"""
        if self._warmed_up:
            return
        try:
            resp = self.request_with_backoff(self.BASE_URL, timeout=10)
            if resp.status_code == 200:
                self._warmed_up = True
                logger.info("[rrdynb] 预热成功")
        except Exception as e:
            logger.warning("[rrdynb] 预热失败: %s", str(e))

    def search(self, keyword: str) -> List[PanResult]:
        """搜索并返回 PanResult 列表。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        if not self._warmed_up:
            self.warm_up()

        try:
            search_items = self._do_search(keyword)
            if not search_items:
                logger.info("[rrdynb] 搜索 '%s' 无结果", keyword)
                return []

            all_results: List[PanResult] = []
            for item in search_items[:self.MAX_DETAIL_PAGES]:
                self.random_delay(1.5, 3.0)
                detail_results = self._parse_detail_page(
                    item["url"], item["title"]
                )
                all_results.extend(detail_results)

            self.set_cached(keyword, all_results)
            logger.info("[rrdynb] 搜索 '%s' 获取 %d 条结果", keyword, len(all_results))
            return all_results

        except Exception as e:
            logger.error("[rrdynb] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str) -> List[dict]:
        """GET /plus/search.php?q=关键词"""
        url = f"{self.BASE_URL}{self.SEARCH_PATH}"
        try:
            resp = self.request_with_backoff(
                url, params={"q": keyword, "pagesize": 10}, timeout=15
            )
            if resp.status_code != 200:
                logger.warning("[rrdynb] 搜索返回 %d", resp.status_code)
                return []

            # 检测 Cloudflare 拦截
            if self.is_cf_blocked(resp):
                logger.warning("[rrdynb] 搜索被 Cloudflare 拦截")
                return []

            return self._parse_search_page(resp.text)

        except Exception as e:
            logger.error("[rrdynb] 搜索请求失败: %s", str(e))
            return []

    def _parse_search_page(self, html: str) -> List[dict]:
        """解析搜索结果页。

        rrdynb 搜索结果结构：
        <dl class="item-third-dl">
          <div class="item-third">
            <a href="/movie/2023/0414/34378.html" title="...">
        """
        soup = BeautifulSoup(html, "html.parser")
        items = []

        # 主选择器：dl.item-third-dl 内的链接
        for dl in soup.select("dl.item-third-dl"):
            for a in dl.select("a[href]"):
                href = a.get("href", "")
                title = a.get("title", "") or a.get_text(strip=True)
                if not title or len(title) < 3 or not href:
                    continue
                # 只要详情页链接（/movie/ 或 .html 结尾）
                if not any(p in href for p in ["/movie/", "/tv/", "/detail/"]):
                    if not href.endswith(".html"):
                        continue
                full_url = urljoin(self.BASE_URL, href)
                # 去掉 font 标签中的高亮标记，提取纯文本
                title = re.sub(r"<[^>]+>", "", title).strip()
                if title:
                    items.append({
                        "title": title,
                        "url": full_url,
                    })

        # 降级：如果主选择器没匹配到，用宽泛选择器
        if not items:
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                title = a.get("title", "") or a.get_text(strip=True)
                if not title or len(title) < 4:
                    continue
                if not any(p in href for p in ["/movie/", "/tv/", "/detail/"]):
                    continue
                if href in ("#", "/", self.BASE_URL, self.BASE_URL + "/"):
                    continue
                full_url = urljoin(self.BASE_URL, href)
                title = re.sub(r"<[^>]+>", "", title).strip()
                if title:
                    items.append({"title": title, "url": full_url})

        # 去重
        seen = set()
        deduped = []
        for item in items:
            if item["url"] not in seen:
                seen.add(item["url"])
                deduped.append(item)
        return deduped

    def _parse_detail_page(self, url: str, page_title: str) -> List[PanResult]:
        """解析详情页，提取网盘链接+提取码。"""
        try:
            resp = self.request_with_backoff(url, timeout=15)
            if resp.status_code != 200:
                return []

            # 检测 Cloudflare 拦截
            if self.is_cf_blocked(resp):
                logger.warning("[rrdynb] 详情页被 Cloudflare 拦截: %s", url)
                return []

            return self._extract_pan_links(resp.text, page_title)
        except Exception as e:
            logger.warning("[rrdynb] 详情页解析失败 %s: %s", url, str(e))
            return []

    def _extract_pan_links(self, html: str, page_title: str) -> List[PanResult]:
        """从详情页提取所有网盘链接和提取码。"""
        results: List[PanResult] = []
        seen_urls = set()

        # 优先从 a 标签提取（更准确）
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href.startswith("http"):
                continue
            if _is_non_share_url(href):
                continue
            pan_type = self._detect_pan_type(href)
            if pan_type is None:
                continue
            url = href.rstrip(".,;:!?\"')")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            password = self._find_nearby_password(html, href)
            try:
                results.append(PanResult(
                    title=page_title,
                    pan_type=pan_type,
                    share_url=url,
                    password=password,
                    source=self.SOURCE_NAME,
                ))
            except ValueError:
                continue

        # 补充：正则匹配 HTML 中的明文链接（a 标签可能遗漏）
        pan_urls = _PAN_URL_RE.findall(html)
        for raw_url in pan_urls:
            # 过滤非分享链接（如 pan.baidu.com/download）
            if _is_non_share_url(raw_url):
                continue
            url = raw_url.rstrip(".,;:!?\"')")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            pan_type = self._detect_pan_type(url)
            if pan_type is None:
                continue
            password = self._find_nearby_password(html, raw_url)
            try:
                results.append(PanResult(
                    title=page_title,
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

    def _find_nearby_password(self, html: str, url: str, window: int = 200) -> str:
        """在 URL 附近文本中搜索提取码。"""
        idx = html.find(url)
        if idx < 0:
            return ""
        start = max(0, idx - window)
        end = min(len(html), idx + len(url) + window)
        context = html[start:end]
        match = _PASSWORD_RE.search(context)
        return match.group(1) if match else ""
