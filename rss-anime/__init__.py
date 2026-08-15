"""动画 RSS 源包插件 — 蜜柑/Nyaa/ACG.RIP/Bangumi Moe/动漫花园。"""

import importlib.util
import os
import sys

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
_LOADED_MODULES: set[str] = set()

# (source_id, 模块名, 类名, 显示名, 支持代理)
_SOURCES = [
    ("mikan", "rss_source_mikan", "MikanRSSSource", "蜜柑计划", True),
    ("nyaa", "rss_source_nyaa", "NyaaRSSSource", "Nyaa", True),
    ("acgrip", "rss_source_acgrip", "ACGRipRSSSource", "ACG.RIP", False),
    ("bangumi_moe", "rss_source_bangumi_moe", "BangumiMoeRSSSource", "Bangumi Moe", False),
    ("dmhy", "rss_source_dmhy", "DMHYRSSSource", "动漫花园", True),
]


def _load_source_class(ctx, module_name: str, class_name: str):
    """从插件自身目录加载源类，避免依赖主体或全局模块缓存。"""
    module_key = f"napics_plugin_{ctx.plugin_id.replace('-', '_')}_{module_name}"
    source_path = os.path.join(_SOURCES_DIR, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(module_key, source_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 RSS 源模块: {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_key, None)
        raise
    _LOADED_MODULES.add(module_key)
    return getattr(module, class_name)


def register(ctx):
    """注册动画 RSS 源。"""
    if not callable(getattr(ctx, "register_rss_source_provider", None)):
        raise RuntimeError("当前 Napics 主体不支持 RSS Provider API，请先更新主体镜像")
    loaded = []
    for source_id, module_name, class_name, name, supports_proxy in _SOURCES:
        try:
            source_class = _load_source_class(ctx, module_name, class_name)
            ctx.register_rss_source_provider(
                source_id=source_id,
                name=name,
                source_class=source_class,
                supports_proxy=supports_proxy,
                capabilities=["rss", "download_url", "recommended_anime"],
                description=f"{name} 动画订阅源",
            )
            loaded.append(name)
        except Exception as exc:
            ctx.logger.error(f"[rss-anime] {name} 注册失败: {exc}")

    if loaded:
        ctx.logger.info(f"动画 RSS 源已加载：{'/'.join(loaded)}")
    else:
        ctx.logger.error("动画 RSS 源：没有任何源注册成功")


def unregister():
    """移除本插件动态加载的源模块。"""
    for module_name in tuple(_LOADED_MODULES):
        sys.modules.pop(module_name, None)
    _LOADED_MODULES.clear()
