"""动画 RSS 源包插件 — 蜜柑/Nyaa/ACG.RIP/Bangumi Moe/动漫花园。

安装后提供：动画类 RSS 订阅源，用于订阅追更。
"""


def register(ctx):
    """注册动画 RSS 源"""
    ctx.logger.info("动画 RSS 源包已加载（内置源通过兼容层注册）")


def unregister():
    """卸载时注销"""
    pass
