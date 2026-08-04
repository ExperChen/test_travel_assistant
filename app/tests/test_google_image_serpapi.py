import os
from dotenv import load_dotenv
from serpapi import GoogleSearch


# 加载环境变量（从同目录的 .env 读取 SERPAPI_API_KEY）
load_dotenv()


def main():
    """
    最简单的 SerpAPI Google Images API 调用示例
    文档参考: https://serpapi.com/google-images-api
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        print("❌ 未找到 SERPAPI_API_KEY，请先配置环境变量")
        return

    print("🖼️ 正在查询 Google Images...")

    params = {
        "engine": "google_images",  # 使用 Google Images 引擎
        "q": "Kota Warisan McDonald's",  # 搜索关键词（你可以改成任何想搜的内容）
        "hl": "zh-cn",  # 界面语言
        "gl": "my",  # 国家/地区（马来西亚）
        "num": 10,  # 返回图片数量（通常 10/20/30...）
        # "safe": "active",  # 安全搜索（可选）
        # "ijn": 0,  # 图片分页（第 0 页开始；下一页通常为 1、2...）
        "api_key": api_key,
    }

    try:
        search = GoogleSearch(params)
        results = search.get_dict()

        if "error" in results:
            print(f"❌ API 错误: {results['error']}")
            return

        images = results.get("images_results", [])
        if not images:
            print("⚠️ 未找到图片结果。")
            return

        print(f"\n🎉 找到 {len(images)} 张图片：\n")

        # 只展示前 5 张
        for idx, img in enumerate(images[:5], 1):
            title = img.get("title", "无标题")
            original = img.get("original", "")
            thumbnail = img.get("thumbnail", "")
            source = img.get("source", "")

            print(f"--- 图片 {idx} ---")
            print(f"📝 标题: {title}")
            print(f"🔗 原图: {original}")
            print(f"🖼️ 缩略图: {thumbnail}")
            print(f"🏷️ 来源: {source}")
            print("-" * 30)
    except Exception as e:
        print(f"❌ 发生异常: {e}")


if __name__ == "__main__":
    main()
