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

PLUGIN_SOURCE_FILES = {
    "search-bt-mirror": [
        "bt_scraper_bitsearch.py",
        "bt_scraper_1337x.py",
        "bt_scraper_limetorrents.py",
    ],
    "search-bt-movie-tv": [
        "bt_scraper_yts.py",
        "bt_scraper_eztv.py",
    ],
    "search-bt-anime-jp": [
        "bt_scraper_nyaa.py",
        "bt_scraper_bangumi_moe.py",
    ],
    "search-bt-anime-cn": [
        "bt_scraper_mikan.py",
        "bt_scraper_acgrip.py",
        "bt_scraper_dmhy.py",
    ],
    "search-bt-cn": [
        "bt_scraper_cilixiong.py",
        "bt_scraper_xl720.py",
    ],
    "search-pan-main": [
        "pan_scraper_pansearch.py",
        "pan_scraper_pansou.py",
    ],
    "search-pan-github": [
        "pan_scraper_gogopanso.py",
        "pan_scraper_github.py",
    ],
    "search-pan-resource": [
        "pan_scraper_rrdynb.py",
        "pan_scraper_ddys.py",
    ],
    "rss-anime": [
        "rss_source_mikan.py",
        "rss_source_nyaa.py",
        "rss_source_acgrip.py",
        "rss_source_bangumi_moe.py",
        "rss_source_dmhy.py",
    ],
    "rss-tv-movie": [
        "rss_source_eztv.py",
        "rss_source_yts.py",
        "rss_source_prowlarr.py",
    ],
}


def build_zip(plugin_id: str) -> str:
    """打包单个插件为 zip，返回 sha256。"""
    plugin_dir = os.path.join(SCRIPT_DIR, plugin_id)
    if not os.path.isdir(plugin_dir):
        print(f"  ⚠️  跳过 {plugin_id}（目录不存在）")
        return ""

    os.makedirs(DIST_DIR, exist_ok=True)
    zip_path = os.path.join(DIST_DIR, f"{plugin_id}.zip")

    written = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(plugin_dir):
            dirs[:] = [directory for directory in dirs if directory != "__pycache__"]
            for file in files:
                if file.endswith(".pyc"):
                    continue
                file_path = os.path.join(root, file)
                rel = os.path.relpath(file_path, plugin_dir)
                arcname = os.path.join(plugin_id, rel)
                written.add(arcname)
                zf.write(file_path, arcname)

    expected_sources = PLUGIN_SOURCE_FILES.get(plugin_id, [])
    missing_sources = [
        os.path.join(plugin_id, "sources", filename)
        for filename in expected_sources
        if os.path.join(plugin_id, "sources", filename) not in written
    ]
    if missing_sources:
        missing_text = ", ".join(missing_sources)
        raise RuntimeError(
            f"{plugin_id} 打包结果缺少预期源文件: {missing_text}"
        )

    # 计算 sha256
    with open(zip_path, "rb") as zip_file:
        sha256 = hashlib.sha256(zip_file.read()).hexdigest()
    size_kb = os.path.getsize(zip_path) / 1024
    print(f"  ✅ {plugin_id}.zip ({size_kb:.1f} KB) sha256={sha256[:16]}...")
    return sha256


def update_index(sha256_map: dict):
    """更新 index.json 中的 sha256 字段。"""
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
