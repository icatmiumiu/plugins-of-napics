"""BT 镜像站搜索 — Bitsearch/1337x/LimeTorrents"""

import os
import sys

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)

# (provider_id, 模块名, 类名, 显示名, 描述)
_SOURCES = [
    ("bitsearch", "bt_scraper_bitsearch", "BitsearchScraper", "Bitsearch", "综合 BT 搜索（有限频保护，需代理）"),
    ("1337x", "bt_scraper_1337x", "X1337xScraper", "1337x", "综合 BT 搜索（镜像站，需代理）"),
    ("limetorrents", "bt_scraper_limetorrents", "LimeTorrentsScraper", "LimeTorrents", "综合 BT 搜索（TLS 兼容性问题，默认禁用）"),
]


def register(ctx):
    """注册 BT 镜像站搜索源"""
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
            ctx.logger.error(f"[search-bt-mirror] {name} 注册失败: {e}")

    if loaded:
        ctx.logger.info(f"BT 镜像站搜索已加载：{'/'.join(loaded)}")
    else:
        ctx.logger.error("BT 镜像站搜索：没有任何搜索源注册成功")


def unregister():
    """卸载时注销"""
    pass
