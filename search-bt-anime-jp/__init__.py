"""BT 日系动画搜索 — Nyaa/Bangumi Moe"""

import os
import sys

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)

# (provider_id, 模块名, 类名, 显示名, 描述)
_SOURCES = [
    ("nyaa", "bt_scraper_nyaa", "NyaaScraper", "Nyaa", "日本动画/日剧 BT 搜索（公开站，需代理）"),
    ("bangumi_moe", "bt_scraper_bangumi_moe", "BangumiMoeScraper", "Bangumi Moe", "动画 BT 搜索（萌番组，国内直连）"),
]


def register(ctx):
    """注册 BT 日系动画搜索源"""
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
            ctx.logger.error(f"[search-bt-anime-jp] {name} 注册失败: {e}")

    if loaded:
        ctx.logger.info(f"BT 日系动画搜索已加载：{'/'.join(loaded)}")
    else:
        ctx.logger.error("BT 日系动画搜索：没有任何搜索源注册成功")


def unregister():
    """卸载时注销"""
    pass
