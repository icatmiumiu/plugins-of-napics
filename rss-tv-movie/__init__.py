"""影视 RSS 源包插件 — EZTV/YTS/Prowlarr RSS。

安装后提供：影视类 RSS 订阅源，用于订阅追更。
"""


def register(ctx):
    """注册影视 RSS 源"""
    ctx.logger.info("影视 RSS 源包已加载（内置源通过兼容层注册）")


def unregister():
    """卸载时注销"""
    pass
