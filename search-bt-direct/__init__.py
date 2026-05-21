"""BT 直搜源包 — 12 个 BT 搜索源。

包含：Bitsearch / 磁力熊 / XL720 / Nyaa / 蜜柑 / YTS /
      LimeTorrents / ACG.RIP / Bangumi Moe / EZTV / 动漫花园 / 1337x
"""

import os
import sys

# 将 sources/ 目录加入 import 路径
_sources_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _sources_dir not in sys.path:
    sys.path.insert(0, _sources_dir)


# 源注册清单：(provider_id, 显示名, 模块名, 类名, 是否需要代理)
_BT_SOURCES = [
    ("bitsearch", "Bitsearch", "bt_scraper_bitsearch", "BitsearchScraper", True),
    ("cilixiong", "磁力熊", "bt_scraper_cilixiong", "CilixiongScraper", False),
    ("xl720", "XL720", "bt_scraper_xl720", "XL720Scraper", False),
    ("nyaa", "Nyaa", "bt_scraper_nyaa", "NyaaScraper", True),
    ("mikan", "蜜柑计划", "bt_scraper_mikan", "MikanScraper", True),
    ("yts", "YTS", "bt_scraper_yts", "YTSScraper", True),
    ("limetorrents", "LimeTorrents", "bt_scraper_limetorrents", "LimeTorrentsScraper", True),
    ("acgrip", "ACG.RIP", "bt_scraper_acgrip", "ACGRipScraper", False),
    ("bangumi_moe", "Bangumi Moe", "bt_scraper_bangumi_moe", "BangumiMoeScraper", False),
    ("eztv", "EZTV", "bt_scraper_eztv", "EZTVScraper", True),
    ("dmhy", "动漫花园", "bt_scraper_dmhy", "DMHYScraper", True),
    ("1337x", "1337x", "bt_scraper_1337x", "X1337xScraper", True),
]


def register(ctx):
    """插件注册入口 — 注册所有 BT 直搜源。"""
    import importlib

    registered = 0
    for provider_id, name, module_name, class_name, needs_proxy in _BT_SOURCES:
        try:
            mod = importlib.import_module(module_name)
            scraper_class = getattr(mod, class_name)
            ctx.register_scraper_search_provider(
                provider_id=provider_id,
                name=name,
                scraper_class=scraper_class,
                supports_proxy=needs_proxy,
                capabilities=["keyword_en", "keyword_cn"],
                description=f"BT 直搜源: {name}",
            )
            registered += 1
        except Exception as e:
            ctx.logger.warning(f"注册 BT 源 {provider_id} 失败: {e}")

    ctx.logger.info(f"BT 直搜源包已加载: {registered}/{len(_BT_SOURCES)} 个源注册成功")


def unregister():
    """插件卸载时调用"""
    pass
