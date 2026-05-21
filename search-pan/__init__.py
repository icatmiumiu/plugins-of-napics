"""网盘搜索源包 — 9 个网盘搜索源。

包含：pansearch / rrdynb / ddys / pansou / sites / slowread /
      wnsearch / gogopanso / github
"""

import os
import sys

# 将 sources/ 目录加入 import 路径
_sources_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _sources_dir not in sys.path:
    sys.path.insert(0, _sources_dir)


# 源注册清单：(provider_id, 显示名, 模块名, 类名)
_PAN_SOURCES = [
    ("pansearch", "PanSearch", "pan_scraper_pansearch", "PanSearchScraper"),
    ("rrdynb", "人人电影", "pan_scraper_rrdynb", "RrdynbScraper"),
    ("ddys", "低端影视", "pan_scraper_ddys", "DdysScraper"),
    ("pansou", "盘搜", "pan_scraper_pansou", "PanSouClient"),
    ("sites", "多站聚合", "pan_scraper_sites", "MultiSiteScraper"),
    ("slowread", "慢读搜索", "pan_scraper_slowread", "SlowreadScraper"),
    ("wnsearch", "我能搜", "pan_scraper_wnsearch", "WnSearchScraper"),
    ("gogopanso", "狗狗盘搜", "pan_scraper_gogopanso", "GogoPansoScraper"),
    ("github", "GitHub 资源", "pan_scraper_github", "GitHubPanScraper"),
]


def register(ctx):
    """插件注册入口 — 注册所有网盘搜索源。"""
    import importlib
    from provider_models import PanSearchCandidate

    registered = 0
    for provider_id, name, module_name, class_name in _PAN_SOURCES:
        try:
            mod = importlib.import_module(module_name)
            scraper_class = getattr(mod, class_name)

            # 创建搜索函数包装器
            def make_search_fn(cls, pid):
                _instance = None

                def search_fn(keyword: str, max_results: int) -> list:
                    nonlocal _instance
                    if _instance is None:
                        proxy = ctx.get_proxy()
                        _instance = cls(proxy=proxy or None)
                    # 调用 scraper 的搜索方法
                    raw_results = _instance.search(keyword)
                    # 转换为 PanSearchCandidate
                    results = []
                    for r in raw_results[:max_results]:
                        results.append(PanSearchCandidate(
                            title=getattr(r, "title", ""),
                            shareUrl=getattr(r, "share_url", "") or getattr(r, "url", ""),
                            panType=getattr(r, "pan_type", "") or "",
                            sourceProviderId=pid,
                        ))
                    return results

                return search_fn

            ctx.register_pan_search_provider(
                provider_id=provider_id,
                name=name,
                search_fn=make_search_fn(scraper_class, provider_id),
                description=f"网盘搜索源: {name}",
            )
            registered += 1
        except Exception as e:
            ctx.logger.warning(f"注册网盘源 {provider_id} 失败: {e}")

    ctx.logger.info(f"网盘搜索源包已加载: {registered}/{len(_PAN_SOURCES)} 个源注册成功")


def unregister():
    """插件卸载时调用"""
    pass
