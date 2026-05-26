"""YTS.mx BT 搜索爬虫。

官方 JSON API：GET https://yts.mx/api/v2/list_movies.json?query_term=关键词
只有电影（无剧集），英文站，小体积高质量 YIFY 编码。
国内可直连，不需要代理。
"""

import logging
from typing import List, Optional
from urllib.parse import quote

from scraper_base import ScraperBase
from quality_parser import parse_quality, get_quality_level

logger = logging.getLogger(__name__)
# YTS 常用 tracker 列表，用于构造磁力链接
_YTS_TRACKERS = [
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://glotorrents.pw:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://p4p.arenabg.com:1337",
]


class YTSScraper(ScraperBase):
    """YTS.mx 电影 BT 搜索爬虫 — JSON API。"""

    BASE_URL = "https://yts.am"
    FALLBACK_URL = "https://movies-api.accel.li"
    API_PATH = "/api/v2/list_movies.json"
    SOURCE_NAME = "yts"

    def __init__(self, proxy: Optional[str] = None):
        # YTS 需代理；cache 10 分钟
        super().__init__(proxy=proxy, use_curl_cffi=True, cache_ttl=600)

    def search_as_search_results(self, keyword: str, max_results: int = 20):
        """搜索并返回 SearchResult 格式。"""
        from searcher import SearchResult

        cached = self.get_cached(keyword)
        if cached is not None:
            return cached

        try:
            results = self._do_search(keyword, max_results)
            self.set_cached(keyword, results)
            logger.info("[yts] 搜索 '%s' 获取 %d 条结果", keyword, len(results))
            return results
        except Exception as e:
            logger.error("[yts] 搜索异常: %s", str(e))
            return []

    def _do_search(self, keyword: str, max_results: int) -> list:
        """调用 YTS API 搜索电影，主域名失败自动切换备用。"""
        from searcher import SearchResult

        params = {
            "query_term": keyword,
            "limit": min(max_results, 50),
            "sort_by": "seeds",
            "order_by": "desc",
        }

        # 尝试主域名和备用域名
        for base_url in [self.BASE_URL, self.FALLBACK_URL]:
            url = f"{base_url}{self.API_PATH}"
            try:
                resp = self.request_with_backoff(url, params=params, timeout=15)
                if resp.status_code != 200:
                    logger.warning("[yts] %s 返回 %d，尝试下一个", base_url, resp.status_code)
                    continue

                data = resp.json()
                if data.get("status") != "ok":
                    continue

                movies = data.get("data", {}).get("movies") or []
                results = []
                for movie in movies:
                    movie_results = self._parse_movie(movie)
                    results.extend(movie_results)
                    if len(results) >= max_results:
                        break

                return results[:max_results]

            except Exception as e:
                logger.warning("[yts] %s 请求失败: %s", base_url, str(e)[:80])
                continue

        logger.error("[yts] 所有域名均不可用")
        return []

    def _parse_movie(self, movie: dict) -> list:
        """解析单部电影的所有种子版本。

        YTS 每部电影有多个 torrent（720p/1080p/2160p），每个都生成一条结果。
        """
        from searcher import SearchResult

        title = movie.get("title_long") or movie.get("title", "")
        year = movie.get("year", "")
        torrents = movie.get("torrents") or []
        if not title or not torrents:
            return []

        results = []
        for t in torrents:
            torrent_hash = t.get("hash", "")
            if not torrent_hash:
                continue

            quality_str = t.get("quality", "")
            codec = t.get("video_codec", "")
            torrent_type = t.get("type", "")  # "bluray" / "web"

            # 构造显示标题：Movie.Title.Year.Quality.Codec.Source-YTS
            display_title = f"{title} {year} {quality_str}"
            if codec:
                display_title += f" {codec}"
            if torrent_type:
                display_title += f" {torrent_type}"
            display_title += " YTS"

            # 构造磁力链接
            dn = quote(f"{title} [{year}] [{quality_str}] [YTS.MX]")
            trackers = "&".join(f"tr={quote(tr)}" for tr in _YTS_TRACKERS)
            magnet = f"magnet:?xt=urn:btih:{torrent_hash}&dn={dn}&{trackers}"

            size_bytes = t.get("size_bytes", 0)
            size_gb = round(size_bytes / (1024 ** 3), 2) if size_bytes > 0 else 0

            quality = parse_quality(display_title)
            quality_level = get_quality_level(quality)

            results.append(SearchResult(
                title=display_title,
                size_gb=size_gb,
                indexer=self.SOURCE_NAME,
                seeders=t.get("seeds", 0),
                leechers=t.get("peers", 0),
                download_url=magnet,
                info_url=movie.get("url", ""),
                quality_tag=quality.display if quality.display else "Unknown",
                quality=quality,
                quality_rank=quality_level.rank,
            ))

        return results
