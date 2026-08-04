import os
from serpapi import GoogleSearch
from dotenv import load_dotenv

# 加载环境变量（确保 .env 中有 SERPAPI_API_KEY）
load_dotenv()

def main():
    """
    一个最简单的 SerpAPI 调用示例
    """
    # 1. 获取 API Key
    # 优先从环境变量读取，如果没有则提示用户
    api_key = os.getenv("SERPAPI_API_KEY")
    
    if not api_key:
        print("❌ 错误：未找到 SERPAPI_API_KEY 环境变量。")
        print("👉 请在 .env 文件中添加一行：SERPAPI_API_KEY=你的密钥")
        print("   或者直接在代码中设置 api_key 变量（仅用于测试）")
        return

    print(f"✅ 检测到 API Key，准备开始搜索...")

    # 2. 构建搜索参数
    # 更多参数参考：https://serpapi.com/search-api
    params = {
        "engine": "google",         # 搜索引擎：google, bing, baidu 等
        "q": "2025年日本旅游", # 搜索关键词
        "api_key": api_key,         # 你的 API 密钥
        "hl": "zh-cn",              # 界面语言：简体中文
        "gl": "my",                 # 搜索地区：马来西亚    
        "num": 5                    # 返回结果数量
    }

    try:
        # 3. 初始化搜索对象
        search = GoogleSearch(params)
        
        # 4. 获取结果（返回 Python 字典）
        results = search.get_dict()
        
        # 检查是否有错误
        if "error" in results:
            print(f"❌ API 返回错误: {results['error']}")
            return

        # 5. 提取并打印自然搜索结果 (organic_results)
        organic_results = results.get("organic_results", [])

        if organic_results:
            print(f"\n🎉 搜索成功！找到相关结果：\n")
            
            for i, result in enumerate(organic_results, 1):
                print(f"--- 结果 {i} ---")
                print(f"📌 标题: {result.get('title')}")
                print(f"🔗 链接: {result.get('link')}")
                print(f"📝 摘要: {result.get('snippet')}")
                print("-" * 30)
        else:
            print("⚠️ 未找到自然搜索结果，可能是关键词太偏或 API 限制。")
            
    except Exception as e:
        print(f"❌ 发生异常: {e}")

if __name__ == "__main__":
    main()
