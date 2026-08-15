"""BT 影视搜索 — YTS/EZTV"""

import os
import sys

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)

# (provider_id, 模块名, 类名, 显示名, 描述)
_SOURCES = [
    ("yts", "bt_scraper_yts", "YTSScraper", "YTS", "高清电影 BT 搜索（公开 API，需代理）"),
    ("eztv", "bt_scraper_eztv", "EZTVScraper", "EZTV", "欧美剧集 BT 搜索（公开站，需代理）"),
]


def register(ctx):
    """注册 BT 影视搜索源"""
    import importlib

    loaded = []
    for provider_id, module_name, class_name, name, desc in _SOURCES:
        try:
            mod = importlib.import_module(module_name)
            ctx.register_scraper_search_provider(
                provider_id=provider_id,
                name=name,
                scraper_class=getattr(mod, class_name),
                enabled=True,
                supports_proxy=True,
                description=desc,
            )
            loaded.append(name)
        except Exception as e:
            ctx.logger.error(f"[search-bt-movie-tv] {name} 注册失败: {e}")

    if loaded:
        ctx.logger.info(f"BT 影视搜索已加载：{'/'.join(loaded)}")
    else:
        ctx.logger.error("BT 影视搜索：没有任何搜索源注册成功")


def unregister():
    """卸载时注销"""
    pass
