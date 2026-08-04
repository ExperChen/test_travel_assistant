import os
import sys
import json
from pathlib import Path
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

# 导入你的多地点路径规划工具
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
from app.tools.multi_route_tool import optimize_multi_location_route, _get_route_edge

# ==========================================
# 架构说明：使用 Google 模型
# ==========================================
def run_travel_agent(user_text: str):
    """
    接收自然语言输入，例如：
    我在首尔玩，我的酒店是Grand Hyatt Seoul, 我要去的景点是Gyeongbokgung Palace, N Seoul Tower, 请帮我规划最优路线
    
    将其转换为结构化的路径规划请求并执行。
    """
    load_dotenv(dotenv_path=project_root / ".env")
    
    api_key = os.getenv("GOOGLE_API_KEY")
    model = os.getenv("GOOGLE_LLM_MODEL", "gemini-2.5-flash")
    if not api_key:
        return '{"error": "GOOGLE_API_KEY not found in .env"}'
        
    llm = ChatGoogleGenerativeAI(
        model=model,
        api_key=api_key,
        temperature=0,
    )
    
    # 1. 使用大模型从自然语言中提取地点列表
    extraction_prompt = f"""
    You are a travel assistant. Extract the hotel and attractions from the following user request.
    User request: {user_text}
    
    Return ONLY a valid JSON object in this format:
    {{
      "locations": ["Hotel Name", "Attraction 1", "Attraction 2", "Hotel Name"],
      "visit_durations": {{"Hotel Name": "N/A", "Attraction 1": "2 hours", "Attraction 2": "1.5 hours"}}
    }}
    
    Rules:
    - The 'locations' list MUST start and end with the hotel name.
    - If no specific duration is mentioned, estimate a reasonable duration (e.g., "2 hours").
    - Do not output any markdown formatting, just pure JSON.
    """
    
    print(f" 接收到自然语言输入: {user_text}\n 正在提取地点信息...")
    extraction_result = llm.invoke(extraction_prompt).content.strip()
    # 移除可能包含的 markdown 标记
    if extraction_result.startswith("```json"):
        extraction_result = extraction_result[7:]
    if extraction_result.endswith("```"):
        extraction_result = extraction_result[:-3]
    extraction_result = extraction_result.strip()
    
    # 2. 解析提取出的 JSON
    try:
        input_data = json.loads(extraction_result)
        locations_arg = input_data.get("locations")
        visit_durations = input_data.get("visit_durations", {})
        if not isinstance(locations_arg, list) or len(locations_arg) < 2:
            return '{"error": "Invalid extracted data: locations must be a list with at least 2 places."}'
    except json.JSONDecodeError:
        return f'{{"error": "Failed to parse LLM extraction: {extraction_result}"}}'
    
    print(f" 成功提取地点列表: {locations_arg}，正在执行底层算法...\n")
    
    # 2. 分离酒店和景点
    hotel = locations_arg[0]
    attractions = locations_arg[1:-1]
    
    # 3. 针对景点执行底层 TSP 算法（获取景点的最优顺序）
    raw_chinese_result_str = optimize_multi_location_route.invoke({"locations": attractions})
    try:
        raw_chinese_result = json.loads(raw_chinese_result_str)
    except json.JSONDecodeError:
        return '{"error": "Failed to calculate attraction routes."}'
        
    if "error" in raw_chinese_result:
        return json.dumps(raw_chinese_result, ensure_ascii=False)
        
    # 解析最优景点顺序
    best_route_str = raw_chinese_result.get("推荐游玩顺序", "")
    best_attractions = [p.strip() for p in best_route_str.split("->")]
    best_details = raw_chinese_result.get("行程明细", [])
    
    # 4. 单独计算 酒店 -> 第一个景点 和 最后一个景点 -> 酒店
    first_attraction = best_attractions[0]
    last_attraction = best_attractions[-1]
    
    # 获取 酒店 -> 第一个景点 的边缘信息
    hotel_to_first = _get_route_edge(hotel, first_attraction)
    # 获取 最后一个景点 -> 酒店 的边缘信息
    last_to_hotel = _get_route_edge(last_attraction, hotel)
    
    # 5. 组合最终的完整明细
    final_details = []
    # 5.1 加入 酒店 -> 第一个景点
    final_details.append({
        "从": hotel,
        "到": first_attraction,
        "交通方式与耗时": hotel_to_first.get("duration_text", "未知"),
        "目标地点游玩时间": visit_durations.get(first_attraction, "未知")
    })
    
    # 5.2 加入 景点之间的行程
    for detail in best_details:
        to_place = detail.get("到")
        detail["目标地点游玩时间"] = visit_durations.get(to_place, "未知")
        final_details.append(detail)
    
    # 5.3 加入 最后一个景点 -> 酒店
    final_details.append({
        "从": last_attraction,
        "到": hotel,
        "交通方式与耗时": last_to_hotel.get("duration_text", "未知"),
        "目标地点游玩时间": "N/A"
    })
    
    # 6. 构建新的完整结果，供大模型翻译
    full_route_str = f"{hotel} -> " + " -> ".join(best_attractions) + f" -> {hotel}"
    combined_result = {
        "推荐游玩顺序": full_route_str,
        "行程明细": final_details,
        "总计在途预估": "包含酒店往返的最优路径"
    }
    
    combined_result_str = json.dumps(combined_result, ensure_ascii=False)
    
    # 7. 已经初始化的 Google 大模型在提取步骤已经创建好了 (llm)
    
    # 8. 把中文结果翻译为全英文 JSON
    print(" 正在将计算结果转换为纯英文 JSON...")
    translation_prompt = f"""
    Here is the raw route calculation result: {combined_result_str}
    CRITICAL INSTRUCTION: 
    1. Translate ALL information (keys and values) into professional English.
    2. Make sure to keep the visit duration ("目标地点游玩时间" -> "Target Location Visit Duration") in the output.
    3. Output ONLY a valid, pure JSON object. Absolutely NO markdown formatting blocks like ```json and NO extra text.
    """
    final_msg = llm.invoke(translation_prompt)
    return final_msg.content

# ==========================================
# 测试入口
# ==========================================
if __name__ == "__main__":
    test_input = "我在首尔玩，我的酒店是Grand Hyatt Seoul, 我要去的景点是Gyeongbokgung Palace, N Seoul Tower, 请帮我规划最优路线"
    result = run_travel_agent(test_input)
    
    print("\n 最终纯净 JSON 输出：")
    print(result)
