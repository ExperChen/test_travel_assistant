import json
import itertools
import re
from serpapi import GoogleSearch
from langchain_core.tools import tool

# 🚀 统一配置加载
from app.config import Config

def _parse_duration_to_seconds(duration_str: str) -> int:
    """内部辅助方法：将 '1 小时 20 分钟' 或 '45 mins' 转换为纯秒数"""
    if not duration_str or duration_str == "未知":
        return 999999
    
    seconds = 0
    hour_match = re.search(r'(\d+)\s*(小时|hour|h|hrs)', duration_str, re.IGNORECASE)
    if hour_match:
        seconds += int(hour_match.group(1)) * 3600
        
    min_match = re.search(r'(\d+)\s*(分钟|分|min|m|mins)', duration_str, re.IGNORECASE)
    if min_match:
        seconds += int(min_match.group(1)) * 60
        
    return seconds if seconds > 0 else 999999

def _parse_distance_to_meters(dist_str: str) -> float:
    """内部辅助方法：解析距离字符串为纯数字（米）"""
    if not dist_str or dist_str == "未知":
        return 999999.0
    
    dist_clean = dist_str.lower().replace(',', '')
    try:
        # 匹配千米
        if 'km' in dist_clean or '公里' in dist_clean:
            num = re.search(r'([\d\.]+)', dist_clean)
            return float(num.group(1)) * 1000 if num else 999999.0
        # 匹配米
        elif 'm' in dist_clean or '米' in dist_clean:
            num = re.search(r'([\d\.]+)', dist_clean)
            return float(num.group(1)) if num else 999999.0
    except Exception:
        pass
    return 999999.0

def _get_route_edge(origin: str, destination: str) -> dict:
    """获取两点之间的路线数据（智能判断：<1km 走步行，否则打车）"""
    api_key = getattr(Config, "SERPAPI_API_KEY", "").strip()
    
    if not api_key:
        return {"error": "未配置 SERPAPI_API_KEY", "duration_seconds": 999999}

    try:
        # 第一步：先查询驾车路线（主要为了获取两地距离）
        params = {
            "engine": "google_maps_directions",
            "start_addr": origin,
            "end_addr": destination,
            "travel_mode": 0,  # 0代表驾车
            "hl": "zh-CN",
            "api_key": api_key
        }
        data = GoogleSearch(params).get_dict()
        
        if "directions" in data and len(data["directions"]) > 0:
            route = data["directions"][0]
            dist_text = route.get("formatted_distance", "未知")
            meters = _parse_distance_to_meters(dist_text)
            
            # 🌟 核心亮点逻辑：如果距离小于 1000 米，重新请求步行路线
            if meters < 1000:
                walk_params = params.copy()
                walk_params["travel_mode"] = 2  # 2代表步行
                walk_data = GoogleSearch(walk_params).get_dict()
                
                if "directions" in walk_data and len(walk_data["directions"]) > 0:
                    walk_route = walk_data["directions"][0]
                    w_duration_text = walk_route.get("formatted_duration", "未知")
                    return {
                        "origin": origin,
                        "destination": destination,
                        "duration_text": f"{w_duration_text} ( 步行, 距离: {dist_text})",
                        "duration_seconds": _parse_duration_to_seconds(w_duration_text)
                    }

            # 如果大于 1000 米，或者没有查到步行，则默认使用驾车
            d_duration_text = route.get("formatted_duration", "未知")
            return {
                "origin": origin,
                "destination": destination,
                "duration_text": f"{d_duration_text} ( 打车, 距离: {dist_text})",
                "duration_seconds": _parse_duration_to_seconds(d_duration_text)
            }
            
        return {"error": "未找到路线", "duration_seconds": 999999}
    except Exception as e:
        return {"error": str(e), "duration_seconds": 999999}

@tool
def optimize_multi_location_route(locations: list[str]) -> str:
    """
    多地点路线优化工具：输入一组地点，穷举并计算出在路上花费时间最短的访问顺序。
    特点：支持短距离（<1km）自动切换为步行模式。
    
    Args:
        locations: 需要访问的地点列表，例如 ["王府井", "天安门", "颐和园"]
    """
    if not locations or len(locations) < 2:
        return json.dumps({"error": "至少需要两个地点才能规划路线"}, ensure_ascii=False)

    # 1. 预处理：获取所有地点两两之间的耗时
    edges = {}
    for origin, dest in itertools.permutations(locations, 2):
        key = f"{origin}->{dest}"
        edges[key] = _get_route_edge(origin, dest)

    best_route = None
    min_total_seconds = float('inf')
    best_details = []

    # 2. 穷举寻找最省时间的顺序
    for perm in itertools.permutations(locations):
        current_seconds = 0
        current_details = []
        is_valid = True

        for i in range(len(perm) - 1):
            start = perm[i]
            end = perm[i+1]
            key = f"{start}->{end}"
            edge_data = edges[key]
            
            if edge_data.get("duration_seconds") == 999999:
                is_valid = False
                break
                
            current_seconds += edge_data["duration_seconds"]
            current_details.append({
                "从": start,
                "到": end,
                "交通方式与耗时": edge_data["duration_text"]
            })

        if is_valid and current_seconds < min_total_seconds:
            min_total_seconds = current_seconds
            best_route = list(perm)
            best_details = current_details

    # 3. 组装返回结果
    if not best_route:
        return json.dumps({"error": "无法计算出有效路线"}, ensure_ascii=False)

    result = {
        "推荐游玩顺序": " -> ".join(best_route),
        "行程明细": best_details,
        "总计在途预估": "最优路径"
    }
    
    return json.dumps(result, ensure_ascii=False, indent=2)