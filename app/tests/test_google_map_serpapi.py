import os
from serpapi import GoogleSearch
from dotenv import load_dotenv
import json

# 加载环境变量
load_dotenv()

def main():
    """
    SerpAPI Google Maps API 调用示例
    文档参考: https://serpapi.com/google-maps-api
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        print("❌ 未找到 SERPAPI_API_KEY，请先配置环境变量")
        return

    print("🗺️ 正在查询 Google Maps...")

    params = {
        # --- 基础参数 ---
        "engine": "google_maps",     # 搜索引擎
        "q": "McDonald's",           # 搜索关键词: 麦当劳
        "type": "search",            # 搜索类型: search (搜索地点)
        
        # --- 地理位置 (可选) ---
        "ll": "@2.8051,101.7060,14z", # 坐标 (纬度,经度,缩放级别) - Kota Warisan 附近
        # "location": "Kuala Lumpur",   # 地点名称 (需配合 z 或 m 参数)
        # "lat": 3.1390,                # 纬度 (需配合 lon 和 z/m)
        # "lon": 101.6869,              # 经度 (需配合 lat 和 z/m)
        # "z": 14,                      # 缩放级别 (3-21)
        # "m": 1000,                    # 地图高度 (米)
        # "nearby": "true",             # 搜索附近 (建议配合 "near me" 关键词)
        
        # --- 本地化 ---
        "hl": "zh-cn",               # 界面语言代码
        "gl": "my",                  # 搜索地区代码 (马来西亚)
        "google_domain": "google.com.my", # Google 域名
        
        "api_key": api_key
    }

    try:
        search = GoogleSearch(params)
        results = search.get_dict()

        if "error" in results:
            print(f"❌ API 错误: {results['error']}")
            return

        # 获取本地结果 (local_results)
        local_results = results.get("local_results", [])
        
        if not local_results:
            print("⚠️ 未找到相关地点信息。")
            return

        print(f"\n🎉 找到 {len(local_results)} 个地点 (McDonald's near Kota Warisan):\n")

        # 只展示前 5 个
        for idx, place in enumerate(local_results[:5], 1):
            title = place.get("title", "未知名称")
            address = place.get("address", "未知地址")
            rating = place.get("rating", "无评分")
            reviews = place.get("reviews", 0)
            price = place.get("price", "未知价格")
            type_ = place.get("type", "未知类型")
            place_id = place.get("place_id", "未知ID")
            
            # 营业状态
            open_state = place.get("open_state", "未知状态")
            
            # 缩略图
            thumbnail = place.get("thumbnail", "")

            print(f"--- 地点 {idx} ---")
            print(f"📍 名称: {title}")
            print(f"🏠 地址: {address}")
            print(f"🆔 ID: {place_id}")
            print(f"⭐ 评分: {rating} ({reviews} 条评论)")
            print(f"💰 价格等级: {price}")
            print(f"🏷️ 类型: {type_}")
            print(f"🕒 状态: {open_state}")
            print("-" * 30)

    except Exception as e:
        print(f"❌ 发生异常: {e}")

if __name__ == "__main__":
    main()
