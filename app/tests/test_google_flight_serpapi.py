import os
from serpapi import GoogleSearch
from dotenv import load_dotenv
import json

# 加载环境变量
load_dotenv()

def main():
    """
    SerpAPI Google Flights API 调用示例
    文档参考: https://serpapi.com/google-flights-api
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        print("❌ 未找到 SERPAPI_API_KEY，请先配置环境变量")
        return

    print("✈️ 正在查询 Google Flights...")

    params = {
        # --- 基础搜索 ---
        "engine": "google_flights",
        "departure_id": "PEK",       # 出发机场代码 (北京首都) 或 KGMID (如 /m/0vzm)
        "arrival_id": "HND",         # 到达机场代码 (东京羽田) 或 KGMID
        "outbound_date": "2026-05-01", # 出发日期 (YYYY-MM-DD)
        "return_date": "2026-05-05",   # 返程日期 (YYYY-MM-DD, 往返必填)
        
        # --- 本地化 ---
        "gl": "my",                  # 搜索地区代码 (如 us, uk, fr, cn)
        "hl": "zh-cn",               # 界面语言代码 (如 en, es, fr, zh-cn)
        "currency": "CNY",           # 货币代码 (默认 USD)
        
        # --- 高级参数 ---
        "type": 1,                   # 航班类型: 1=往返(默认), 2=单程, 3=多城市
        "travel_class": 1,           # 舱位等级: 1=经济(默认), 2=优选经济, 3=商务, 4=头等
        "show_hidden": False,        # 是否包含隐藏结果 (默认 False)
        "deep_search": True,         # 启用深度搜索 (可能更慢但结果更好)
        
        # --- 乘客人数 ---
        "adults": 1,                 # 成人人数 (默认 1)
        # "children": 0,             # 儿童人数 (默认 0)
        # "infants_in_seat": 0,      # 占座婴儿 (默认 0)
        # "infants_on_lap": 0,       # 怀抱婴儿 (默认 0)
        
        # --- 排序 ---
        "sort_by": 1,                # 排序: 1=最佳(默认), 2=价格, 3=出发时间, 4=到达时间, 5=时长, 6=排放
        
        # --- 其他高级过滤 (注释备用) ---
        # "exclude_basic": False,    # 排除基础经济舱 (仅 gl=us 且 travel_class=1 时有效)
        # "multi_city_json": "",     # 多城市航班信息 (JSON 字符串, 仅 type=3 时有效)
        
        "api_key": api_key
    }

    try:
        search = GoogleSearch(params)
        results = search.get_dict()

        if "error" in results:
            print(f"❌ API 错误: {results['error']}")
            return

        # 获取最佳航班 (best_flights)
        best_flights = results.get("best_flights", [])
        
        if not best_flights:
            print("⚠️ 未找到最佳航班，尝试查看所有航班...")
            # 有时结果在 other_flights 中
            other_flights = results.get("other_flights", [])
            if other_flights:
                best_flights = other_flights[:3] # 取前3个
            else:
                print("⚠️ 未找到任何航班信息。")
                return

        print(f"\n🎉 找到 {len(best_flights)} 个推荐航班 (北京 PEK -> 东京 HND):\n")

        for idx, flight in enumerate(best_flights, 1):
            flight_token = flight.get("flight_token")
            price = flight.get("price", "未知价格")
            duration = flight.get("total_duration", "未知时长")
            
            # 提取航段信息
            flights_segment = flight.get("flights", [])
            airline_names = []
            for segment in flights_segment:
                airline_names.append(segment.get("airline", "未知航空"))
            
            airline_str = " + ".join(airline_names)

            print(f"--- 航班 {idx} ---")
            print(f"💰 价格: {price} CNY")
            print(f"✈️ 航司: {airline_str}")
            print(f"⏱️ 时长: {duration} 分钟")
            print("-" * 30)

    except Exception as e:
        print(f"❌ 发生异常: {e}")

if __name__ == "__main__":
    main()
