# Napics 社区插件源

> ⚠️ 本仓库包含的插件可能涉及版权敏感内容，使用者自行承担法律责任。

## 安装方式

1. 打开 Napics → 插件中心 → 第三方插件源
2. 点击"添加插件源"
3. 输入地址：`https://github.com/icatmiumiu/plugins-of-napics`
4. 在列表中选择需要的插件安装即可

## 插件列表

| 插件 ID | 名称 | 说明 |
|---------|------|------|
| search-bt-direct | BT 直搜源包 | 12 个 BT 直搜源（Bitsearch/Nyaa/蜜柑/磁力熊/YTS 等） |
| search-pan | 网盘搜索 | 9 个网盘源聚合搜索（pansearch/rrdynb 等） |

## 开发

每个插件是一个独立目录，包含：
- `manifest.json` — 插件声明
- `__init__.py` — 注册入口
- `sources/` — 源代码文件

## 许可

MIT License
