import os
from serpapi import GoogleSearch
from dotenv import load_dotenv
import json

# 加载环境变量
load_dotenv()

def main():
    """
    SerpAPI Google Hotels API 调用示例
    文档参考: https://serpapi.com/google-hotels-api
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        print("❌ 未找到 SERPAPI_API_KEY，请先配置环境变量")
        return

    print("🏨 正在查询 Google Hotels...")

    # 完整参数示例（注释掉了部分不常用的）
    params = {
        # --- 基础参数 ---
        "engine": "google_hotels",
        "q": "Tokyo, Japan",         # 搜索地点/关键词 (Required)
        "gl": "my",                  # 搜索地区代码 (如 us, uk, fr, cn)
        "hl": "zh-cn",               # 界面语言代码 (如 en, es, fr, zh-cn)
        "currency": "CNY",           # 货币代码 (默认 USD)
        
        # --- 日期与人数 ---
        "check_in_date": "2026-05-01", # 入住日期 (Required, YYYY-MM-DD)
        "check_out_date": "2026-05-05", # 退房日期 (Required, YYYY-MM-DD)
        "adults": 2,                 # 成人人数 (默认 2)
        # "children": 0,             # 儿童人数 (默认 0)
        # "children_ages": "5,8",    # 儿童年龄 (如有多个用逗号分隔，数量需匹配 children 参数)
        
        # --- 高级过滤 ---
        "sort_by": 8,                # 排序方式: 3=最低价格, 8=最高评分/相关性, 13=最多评论
        "min_price": 0,              # 最低价格限制
        "max_price": 500,            # 最高价格限制
        
        # "property_types": "17,12", # 物业类型: 17=度假租赁, 12=酒店等 (具体 ID 需查阅文档)
        # "amenities": "35,9",       # 设施过滤: 35=WiFi, 9=泳池等 (具体 ID 需查阅文档)
        # "rating": 8,               # 评分过滤: 7=3.5+, 8=4.0+, 9=4.5+
        
        # --- 酒店特有过滤 ---
        # "brands": "33,67",         # 品牌过滤 (品牌 ID 需从 API 返回结果中获取)
        # "hotel_class": "2,3,4",    # 星级过滤: 2=2星, 3=3星, 4=4星, 5=5星
        # "free_cancellation": True, # 仅显示免费取消
        # "special_offers": True,    # 仅显示有特惠
        # "eco_certified": True,     # 仅显示环保认证
        
        # --- 度假租赁特有过滤 (仅当 vacation_rentals=True 时有效) ---
        # "vacation_rentals": True,  # 搜索度假租赁而非酒店
        # "bedrooms": 2,             # 最少卧室数
        # "bathrooms": 1,            # 最少卫浴数
        
        # --- 分页 ---
        # "next_page_token": "...",  # 下一页 Token (从上一次结果中获取)
        
        "api_key": api_key
    }

    try:
        search = GoogleSearch(params)
        results = search.get_dict()

        if "error" in results:
            print(f"❌ API 错误: {results['error']}")
            return

        # 获取酒店列表 (properties)
        properties = results.get("properties", [])
        
        if not properties:
            print("⚠️ 未找到酒店信息。")
            return

        print(f"\n🎉 找到 {len(properties)} 家推荐酒店 (马来西亚):\n")

        # 只展示前 5 家
        for idx, hotel in enumerate(properties[:5], 1):
            name = hotel.get("name", "未知名称")
            
            # 价格信息通常在 rate_per_night 或 total_rate 中
            rate = hotel.get("rate_per_night", {})
            price = rate.get("lowest", "未知价格")
            
            # 评分
            rating = hotel.get("overall_rating", "无评分")
            reviews = hotel.get("reviews", 0)
            
            # 描述/设施
            description = hotel.get("description", "暂无描述")
            
            # 链接
            link = hotel.get("link", "")

            print(f"--- 酒店 {idx} ---")
            print(f"🏨 名称: {name}")
            print(f"💰 价格: {price} / 晚")
            print(f"⭐ 评分: {rating} ({reviews} 条评论)")
            print(f"📝 描述: {description[:50]}..." if len(description) > 50 else f"📝 描述: {description}")
            print(f"🔗 链接: {link}")
            print("-" * 30)

    except Exception as e:
        print(f"❌ 发生异常: {e}")

if __name__ == "__main__":
    main()
