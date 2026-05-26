"""社区插件打包脚本 — 生成 zip 包并更新 index.json 中的 sha256。

用法：
    cd community-plugins
    python build_release.py

输出：
    dist/ 目录下生成各插件的 zip 包
    index.json 中的 sha256 字段自动更新
"""

import hashlib
import json
import os
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(SCRIPT_DIR, "dist")
INDEX_PATH = os.path.join(SCRIPT_DIR, "index.json")

# 需要打包的插件 ID 列表
PLUGIN_IDS = [
    "search-bt-mirror",
    "search-bt-movie-tv",
    "search-bt-anime-jp",
    "search-bt-anime-cn",
    "search-bt-cn",
    "search-pan-main",
    "search-pan-github",
    "search-pan-resource",
    "rss-anime",
    "rss-tv-movie",
    "download-openlist",
]


def build_zip(plugin_id: str) -> str:
    """打包单个插件为 zip，返回 sha256"""
    plugin_dir = os.path.join(SCRIPT_DIR, plugin_id)
    if not os.path.isdir(plugin_dir):
        print(f"  ⚠️  跳过 {plugin_id}（目录不存在）")
        return ""

    os.makedirs(DIST_DIR, exist_ok=True)
    zip_path = os.path.join(DIST_DIR, f"{plugin_id}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(plugin_dir):
            # 跳过 __pycache__
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for file in files:
                if file.endswith(".pyc"):
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.join(plugin_id, os.path.relpath(file_path, plugin_dir))
                zf.write(file_path, arcname)

    # 计算 sha256
    sha256 = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
    size_kb = os.path.getsize(zip_path) / 1024
    print(f"  ✅ {plugin_id}.zip ({size_kb:.1f} KB) sha256={sha256[:16]}...")
    return sha256


def update_index(sha256_map: dict):
    """更新 index.json 中的 sha256 字段"""
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        index = json.load(f)

    for plugin in index["plugins"]:
        pid = plugin["id"]
        if pid in sha256_map and sha256_map[pid]:
            plugin["sha256"] = sha256_map[pid]

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=4)

    print(f"\n📝 index.json 已更新（{len(sha256_map)} 个插件）")


def main():
    print("🔨 开始打包社区插件...\n")

    sha256_map = {}
    for plugin_id in PLUGIN_IDS:
        sha256_map[plugin_id] = build_zip(plugin_id)

    print()
    update_index(sha256_map)

    print(f"\n📦 输出目录: {DIST_DIR}")
    print("🎉 完成！将 dist/ 下的 zip 文件上传到 GitHub Release 即可。")


if __name__ == "__main__":
    main()
