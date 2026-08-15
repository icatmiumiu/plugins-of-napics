"""网盘 GitHub 源 — 狗狗盘搜/GitHub"""

import os
import sys

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)

# (provider_id, 模块名, 类名, 显示名, 描述)
_SOURCES = [
    ("gogopanso", "pan_scraper_gogopanso", "GogoPansoScraper", "狗狗盘搜", "阿里云盘资源搜索"),
    ("github", "pan_scraper_github", "GitHubPanScraper", "GitHub 网盘仓库", "GitHub 夸克/阿里资源仓库索引"),
]


def register(ctx):
    """注册网盘 GitHub 搜索源"""
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
            ctx.logger.error(f"[search-pan-github] {name} 注册失败: {e}")

    if loaded:
        ctx.logger.info(f"网盘 GitHub 源已加载：{'/'.join(loaded)}")
    else:
        ctx.logger.error("网盘 GitHub 源：没有任何搜索源注册成功")


def unregister():
    """卸载时注销"""
    pass
