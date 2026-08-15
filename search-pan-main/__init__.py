"""网盘主力搜索源 — PanSearch/盘搜"""

import os
import sys

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)

# (provider_id, 模块名, 类名, 显示名, 描述)
_SOURCES = [
    ("pansearch", "pan_scraper_pansearch", "PanSearchScraper", "PanSearch", "网盘资源搜索（国内直连）"),
    ("pansou", "pan_scraper_pansou", "PanSouClient", "盘搜", "多插件网盘聚合搜索"),
]


def register(ctx):
    """注册网盘主力搜索源"""
    import importlib
    from plugin_context import _plugin_providers

    loaded = []
    for provider_id, module_name, class_name, name, desc in _SOURCES:
        try:
            mod = importlib.import_module(module_name)
            scraper_cls = getattr(mod, class_name)
            ctx.register_pan_search_provider(
                provider_id=provider_id,
                name=name,
                search_fn=None,
                enabled=True,
                description=desc,
            )
            # 存储 scraper_class 供 pan_search_service 使用
            _plugin_providers[provider_id]["scraper_class"] = scraper_cls
            loaded.append(name)
        except Exception as e:
            ctx.logger.error(f"[search-pan-main] {name} 注册失败: {e}")

    if loaded:
        ctx.logger.info(f"网盘主力搜索源已加载：{'/'.join(loaded)}")
    else:
        ctx.logger.error("网盘主力搜索源：没有任何搜索源注册成功")


def unregister():
    """卸载时注销"""
    pass
