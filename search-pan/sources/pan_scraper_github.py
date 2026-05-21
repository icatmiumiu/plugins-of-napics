"""GitHub 网盘资源仓库爬虫 — 从 GitHub 仓库拉取静态资源列表，本地建索引搜索。

支持的仓库：
- Zishuzuinb/QuarkShare: Markdown 表格格式，夸克网盘
- leobba/quark-share: HTML 表格格式，夸克网盘
- acoooder/aliyunpanshare: 同 gogopanso（已有独立爬虫，此处不重复）

设计：启动时（或首次搜索时）拉取所有 md 文件，解析为 (title, url) 列表缓存在内存。
搜索时在本地做关键词匹配，无需每次请求 GitHub。
索引每 30 分钟刷新一次。
"""

import re
import time
import logging
import threading
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote

import requests

from scraper_base import ScraperBase
from pan_models import PanResult, PanType, VALID_PAN_DOMAINS

logger = logging.getLogger(__name__)
# 仓库配置
REPO_CONFIGS = [
    {
        "name": "QuarkShare",
        "repo": "Zishuzuinb/QuarkShare",
        "files": ["全网热门电影.md", "全网热门电视剧.md", "全网热门动漫.md"],
        "format": "markdown",
    },
    {
        "name": "quark-share",
        "repo": "leobba/quark-share",
        "files": ["01影视/①电影.md", "01影视/②动漫.md", "01影视/③综艺.md"],
        "format": "html",
    },
]

# 夸克链接正则
_QUARK_URL_RE = re.compile(r"https?://pan\.quark\.cn/s/[a-zA-Z0-9]+")

# Markdown 表格行正则：| 名称 | 链接 |
_MD_TABLE_RE = re.compile(
    r"\|\s*(.+?)\s*\|\s*(https?://pan\.quark\.cn/s/[a-zA-Z0-9]+)\s*\|"
)

# HTML 表格提取：<td>名称</td> ... <a href="链接">
_HTML_TITLE_RE = re.compile(r"<td[^>]*>([^<]+)</td>")
_HTML_LINK_RE = re.compile(r'href="(https?://pan\.quark\.cn/s/[a-zA-Z0-9]+)"')

# 索引刷新间隔（秒）
INDEX_REFRESH_INTERVAL = 86400  # 24 小时（仓库更新频率约每天一次，无需频繁拉取）


class GitHubPanScraper(ScraperBase):
    """GitHub 网盘资源仓库爬虫 — 本地索引 + 关键词匹配。"""

    SOURCE_NAME = "github"

    def __init__(self, proxy: Optional[str] = None):
        super().__init__(proxy=proxy, cache_ttl=600)
        self._index: List[Tuple[str, str]] = []  # [(title, url), ...]
        self._index_time: float = 0
        self._index_lock = threading.Lock()
        self._building = False

    def search(self, keyword: str) -> List[PanResult]:
        """搜索 GitHub 资源仓库。首次调用时自动建索引。"""
        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        # 确保索引已建立
        self._ensure_index()

        if not self._index:
            return []

        results = self._match_keyword(keyword)
        self.set_cached(keyword, results)
        logger.info("[github] 搜索 '%s' 匹配 %d 条（索引 %d 条）",
                    keyword, len(results), len(self._index))
        return results

    def _ensure_index(self) -> None:
        """确保索引已建立且未过期。"""
        now = time.time()
        if self._index and (now - self._index_time) < INDEX_REFRESH_INTERVAL:
            return

        with self._index_lock:
            # 双重检查
            if self._index and (time.time() - self._index_time) < INDEX_REFRESH_INTERVAL:
                return
            if self._building:
                return
            self._building = True

        try:
            self._build_index()
        finally:
            self._building = False

    def _build_index(self) -> None:
        """从 GitHub 拉取所有 md 文件，解析建立索引。"""
        logger.info("[github] 开始建立索引...")
        new_index: List[Tuple[str, str]] = []
        seen_urls = set()

        for config in REPO_CONFIGS:
            repo = config["repo"]
            fmt = config["format"]

            for filepath in config["files"]:
                try:
                    encoded_path = quote(filepath, safe="/")
                    url = f"https://raw.githubusercontent.com/{repo}/main/{encoded_path}"
                    resp = requests.get(url, timeout=15, headers={
                        "User-Agent": "Mozilla/5.0",
                    })
                    if resp.status_code != 200:
                        logger.warning("[github] %s/%s 返回 %d",
                                       repo, filepath, resp.status_code)
                        continue

                    content = resp.text
                    if fmt == "markdown":
                        entries = self._parse_markdown(content)
                    else:
                        entries = self._parse_html(content)

                    for title, link in entries:
                        if link not in seen_urls:
                            seen_urls.add(link)
                            new_index.append((title, link))

                    logger.info("[github] %s/%s: %d 条",
                                repo, filepath, len(entries))
                except Exception as e:
                    logger.warning("[github] %s/%s 拉取失败: %s",
                                   repo, filepath, str(e))

        self._index = new_index
        self._index_time = time.time()
        logger.info("[github] 索引建立完成: %d 条资源", len(new_index))

    def _parse_markdown(self, content: str) -> List[Tuple[str, str]]:
        """解析 Markdown 表格格式。"""
        entries = []
        for match in _MD_TABLE_RE.finditer(content):
            title = match.group(1).strip()
            url = match.group(2).strip()
            if title and url and "---" not in title:
                entries.append((title, url))
        return entries

    def _parse_html(self, content: str) -> List[Tuple[str, str]]:
        """解析 HTML 表格格式（用 BeautifulSoup 处理复杂嵌套）。"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
        except ImportError:
            # 降级到正则
            return self._parse_html_regex(content)

        entries = []
        for row in soup.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) < 2:
                continue

            # 找包含夸克链接的 td
            link = ""
            for td in tds:
                a = td.find("a", href=_QUARK_URL_RE)
                if a:
                    link = a["href"]
                    break
            if not link:
                # 也检查 td 文本中的链接
                for td in tds:
                    m = _QUARK_URL_RE.search(td.get_text())
                    if m:
                        link = m.group(0)
                        break
            if not link:
                continue

            # 标题：取第二个 td 的文本（分享名），如果太短取第一个（目录名）
            title = ""
            if len(tds) >= 2:
                t2 = tds[1].get_text(strip=True)
                t1 = tds[0].get_text(strip=True)
                title = t2 if len(t2) > 2 else t1
            elif len(tds) >= 1:
                title = tds[0].get_text(strip=True)

            if title and "目录名称" not in title and "分享名" not in title:
                entries.append((title, link))

        return entries

    def _parse_html_regex(self, content: str) -> List[Tuple[str, str]]:
        """降级正则解析 HTML（无 BeautifulSoup 时）。"""
        entries = []
        rows = re.split(r"</tr>", content, flags=re.IGNORECASE)
        for row in rows:
            links = _HTML_LINK_RE.findall(row)
            if not links:
                continue
            tds = _HTML_TITLE_RE.findall(row)
            title = ""
            if len(tds) >= 2:
                title = tds[1].strip()
            elif len(tds) >= 1:
                title = tds[0].strip()
            for link in links:
                if title and "目录名称" not in title:
                    entries.append((title, link))
        return entries

    def _match_keyword(self, keyword: str) -> List[PanResult]:
        """在本地索引中匹配关键词。"""
        results = []
        seen_urls = set()

        # 中文 2 字滑窗 + 英文完整词
        cn_tokens = set()
        cn_parts = re.findall(r"[\u4e00-\u9fff]+", keyword)
        for part in cn_parts:
            if len(part) <= 2:
                cn_tokens.add(part)
            else:
                for i in range(len(part) - 1):
                    cn_tokens.add(part[i:i + 2])

        en_tokens = {w.lower() for w in re.findall(r"[a-zA-Z]{2,}", keyword)}
        all_tokens = cn_tokens | en_tokens

        if not all_tokens:
            return []

        for title, url in self._index:
            title_lower = title.lower()
            if any(t in title_lower for t in all_tokens):
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                try:
                    results.append(PanResult(
                        title=title,
                        pan_type=PanType.QUARK,
                        share_url=url,
                        password="",
                        source=self.SOURCE_NAME,
                    ))
                except ValueError:
                    continue

        return results
