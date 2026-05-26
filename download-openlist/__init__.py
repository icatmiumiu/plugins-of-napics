"""OpenList/Alist 下载后端插件。

安装后提供：网盘离线下载、云端进度追踪。
"""


def register(ctx):
    """注册 OpenList 下载后端"""
    ctx.logger.info("OpenList 下载后端已加载")


def unregister():
    """卸载时注销"""
    pass
