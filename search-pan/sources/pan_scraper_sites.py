"""通用网盘搜索站爬虫 — 凌风云/盘搜搜/小白盘/趣盘搜等。

这些站点模式高度相似：GET 搜索 → HTML 结果列表 → 提取标题+链接+网盘类型。
用一个模板类 + 站点配置字典覆盖，避免为每个站写独立爬虫。
"""

import re
import logging
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, quote

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
    r"(?:提取码|密码|访问码|提取密码)\s*[:：]?\s*([a-zA-Z0-9]{4,8})",
)

# 百度网盘短链正则（这些站大多返回百度盘链接）
_BAIDU_SHORT_RE = re.compile(r"https?://pan\.baidu\.com/s/[a-zA-Z0-9_-]+")


class SiteConfig:
    """单个搜索站的配置。"""

    def __init__(
        self,
        name: str,
        base_url: str,
        search_path: str,
        keyword_param: str = "q",
        extra_params: Optional[Dict[str, str]] = None,
        result_selector: str = "",
        title_selector: str = "",
        link_selector: str = "",
        encoding: str = "utf-8",
    ):
        self.name = name
        self.base_url = base_url
        self.search_path = search_path
        self.keyword_param = keyword_param
        self.extra_params = extra_params or {}
        # CSS 选择器（用于结构化解析）
        self.result_selector = result_selector
        self.title_selector = title_selector
        self.link_selector = link_selector
        self.encoding = encoding


# ── 站点配置 ──

SITE_CONFIGS: Dict[str, SiteConfig] = {
    "lingfengyun": SiteConfig(
        name="凌风云",
        base_url="https://www.lingfengyun.com",
        search_path="/search/",
        keyword_param="q",
        result_selector=".search-item, .result-item, .list-item, .item",
        title_selector=".item-title, .title, h3, h4, a",
        link_selector="a[href*='pan.baidu.com'], a[href*='quark'], a[href*='alipan']",
    ),
    "pansoso": SiteConfig(
        name="盘搜搜",
        base_url="https://www.pansoso.com",
        search_path="/search",
        keyword_param="q",
        result_selector=".search-item, .result-item, .item, .resource-item",
        title_selector=".item-title, .title, h3, a",
        link_selector="a[href*='pan.baidu.com'], a[href*='quark'], a[href*='alipan']",
    ),
    "xiaobaipan": SiteConfig(
        name="小白盘",
        base_url="https://www.xiaobaipan.com",
        search_path="/search",
        keyword_param="q",
        result_selector=".search-item, .result-item, .item",
        title_selector=".item-title, .title, h3, a",
        link_selector="a[href*='pan.baidu.com'], a[href*='quark'], a[href*='alipan']",
    ),
    "qupansou": SiteConfig(
        name="趣盘搜",
        base_url="https://www.qupansou.com",
        search_path="/search",
        keyword_param="q",
        result_selector=".search-item, .result-item, .item",
        title_selector=".item-title, .title, h3, a",
        link_selector="a[href*='pan.baidu.com'], a[href*='quark'], a[href*='alipan']",
    ),
}


class GenericPanSiteScraper(ScraperBase):
    """通用网盘搜索站爬虫。

    一个类覆盖所有模式相似的网盘搜索站，通过 SiteConfig 配置差异。
    """

    def __init__(
        self,
        site_config: SiteConfig,
        proxy: Optional[str] = None,
    ):
        super().__init__(proxy=proxy)
        self.site = site_config
        self.SOURCE_NAME = site_config.name

    def search(self, keyword: str) -> List[PanResult]:
        """搜索指定站点，返回 PanResult 列表。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword)
            self.set_cached(keyword, results)
            logger.info("[%s] 搜索 '%s' 获取 %d 条结果",
                        self.site.name, keyword, len(results))
            return results
        except Exception as e:
            logger.error("[%s] 搜索异常: %s", self.site.name, str(e))
            return []

    def _do_search(self, keyword: str) -> List[PanResult]:
        """执行搜索请求并解析结果。"""
        url = f"{self.site.base_url}{self.site.search_path}"
        params = {self.site.keyword_param: keyword}
        params.update(self.site.extra_params)

        self.random_delay(1.0, 2.0)
        resp = self.request_with_backoff(url, params=params, timeout=10)

        if resp.status_code != 200:
            logger.warning("[%s] 搜索返回 %d", self.site.name, resp.status_code)
            return []

        # 处理编码
        if self.site.encoding != "utf-8":
            resp.encoding = self.site.encoding
        html = resp.text

        # 先尝试结构化解析，失败则降级全文正则
        results = self._parse_structured(html)
        if not results:
            results = self._parse_fallback(html)

        return results

    def _parse_structured(self, html: str) -> List[PanResult]:
        """结构化解析：用 CSS 选择器提取结果卡片。"""
        results = []
        seen_urls = set()
        soup = BeautifulSoup(html, "html.parser")

        # 尝试多个选择器
        cards = []
        if self.site.result_selector:
            for selector in self.site.result_selector.split(","):
                cards = soup.select(selector.strip())
                if cards:
                    break

        if not cards:
            return []

        for card in cards:
            card_html = str(card)
            card_text = card.get_text()

            # 提取标题
            title = self._extract_title(card)

            # 提取网盘链接（从 href 和文本中）
            pan_urls = self._extract_pan_urls(card, card_html)
            if not pan_urls:
                continue

            # 提取提取码
            password = ""
            pw_match = _PASSWORD_RE.search(card_text)
            if pw_match:
                password = pw_match.group(1)

            for url in pan_urls:
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
                        source=self.site.name,
                    ))
                except ValueError:
                    continue

        return results

    def _parse_fallback(self, html: str) -> List[PanResult]:
        """降级解析：全文正则提取网盘链接。"""
        results = []
        seen_urls = set()

        pan_urls = _PAN_URL_RE.findall(html)
        for raw_url in pan_urls:
            url = raw_url.rstrip(".,;:!?\"')")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            pan_type = self._detect_pan_type(url)
            if pan_type not in _TARGET_PAN_TYPES:
                continue

            # 从链接附近提取标题和提取码
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
                    source=self.site.name,
                ))
            except ValueError:
                continue

        return results

    def _extract_title(self, card) -> str:
        """从卡片元素中提取标题。"""
        if self.site.title_selector:
            for selector in self.site.title_selector.split(","):
                el = card.select_one(selector.strip())
                if el:
                    text = el.get_text(strip=True)
                    if len(text) > 2:
                        return text[:200]

        # 降级：取第一段有意义的文本
        for text_node in card.stripped_strings:
            if len(text_node) > 3 and not text_node.startswith("http"):
                return text_node[:200]
        return ""

    def _extract_pan_urls(self, card, card_html: str) -> List[str]:
        """从卡片中提取网盘链接。"""
        urls = set()

        # 从 <a> 标签的 href 提取
        for a in card.select("a[href]"):
            href = a.get("href", "")
            if any(d in href for d in VALID_PAN_DOMAINS):
                urls.add(href.rstrip(".,;:!?\"')"))

        # 从文本中正则提取
        for match in _PAN_URL_RE.findall(card_html):
            urls.add(match.rstrip(".,;:!?\"')"))

        return list(urls)

    def _detect_pan_type(self, url: str) -> PanType:
        """根据 URL 域名检测网盘类型。"""
        domain = urlparse(url).netloc.lower()
        for d, pt in _DOMAIN_TO_PAN_TYPE.items():
            if d in domain:
                return pt
        return PanType.UNKNOWN


class MultiSiteScraper(ScraperBase):
    """多站点聚合爬虫 — 并发搜索多个网盘搜索站，合并去重。

    作为一个"源"注册到 PanSearchService，内部管理多个 GenericPanSiteScraper。
    """

    SOURCE_NAME = "sites"

    def __init__(
        self,
        site_names: Optional[List[str]] = None,
        proxy: Optional[str] = None,
    ):
        super().__init__(proxy=proxy)
        self.scrapers: List[GenericPanSiteScraper] = []

        names = site_names or list(SITE_CONFIGS.keys())
        for name in names:
            if name in SITE_CONFIGS:
                self.scrapers.append(
                    GenericPanSiteScraper(SITE_CONFIGS[name], proxy=proxy)
                )

    def search(self, keyword: str) -> List[PanResult]:
        """依次搜索所有站点，合并去重。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        all_results: List[PanResult] = []
        seen_urls = set()

        for scraper in self.scrapers:
            try:
                results = scraper.search(keyword)
                for r in results:
                    if r.share_url not in seen_urls:
                        seen_urls.add(r.share_url)
                        all_results.append(r)
            except Exception as e:
                logger.warning("[sites] %s 搜索失败: %s",
                               scraper.site.name, str(e))

        self.set_cached(keyword, all_results)
        logger.info("[sites] 搜索 '%s' 共获取 %d 条结果", keyword, len(all_results))
        return all_results
