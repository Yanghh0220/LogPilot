# analyzer.py - AI 分析逻辑，负责调用 DeepSeek API
#
# 为什么单独放一个文件？
# 1. 把 AI 调用逻辑和页面逻辑分开
# 2. 以后换模型只改这个文件，不用动页面代码

import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from prompt import SYSTEM_PROMPT, build_user_prompt

# ============================================
# 加载 .env 文件中的 API Key
# ============================================
# load_dotenv() 会读取 .env 文件，把里面的变量加载到环境变量中
load_dotenv()

# ============================================
# 创建 DeepSeek 客户端
# ============================================
# DeepSeek 兼容 OpenAI SDK，所以我们用 OpenAI 的客户端
# 只需要把 base_url 改成 DeepSeek 的地址就行
# api_key 需要显式传入，因为 .env 里用的是 DEEPSEEK_API_KEY
client = OpenAI(
    base_url="https://api.deepseek.com",  # DeepSeek 的 API 地址
    api_key=os.getenv("DEEPSEEK_API_KEY")  # 从环境变量读取 API Key
)

def analyze_log(log_text: str) -> dict:
    """
    调用 DeepSeek API 分析构建日志

    参数:
        log_text: 用户粘贴的构建日志

    返回:
        解析后的字典，包含错误摘要、原因、修复建议等

    异常:
        如果 API 调用失败或返回格式不对，会抛出异常
    """

    # ----------------------------------------
    # 1. 构建提示词
    # ----------------------------------------
    user_prompt = build_user_prompt(log_text)

    # ----------------------------------------
    # 2. 调用 DeepSeek API
    # ----------------------------------------
    # client.chat.completions.create() 是 OpenAI SDK 的标准调用方式
    # model: 使用的模型名称
    # messages: 对话历史，包含系统提示词和用户输入
    # temperature: 控制回复的随机性，0 表示最稳定
    response = client.chat.completions.create(
        model="deepseek-chat",  # DeepSeek 的对话模型
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0  # 设为 0 让结果更稳定、可重复
    )

    # ----------------------------------------
    # 3. 提取 AI 的回复内容
    # ----------------------------------------
    # response.choices[0].message.content 就是 AI 返回的文本
    result_text = response.choices[0].message.content

    # ----------------------------------------
    # 4. 解析 JSON
    # ----------------------------------------
    # AI 返回的是 JSON 字符串，我们需要把它转成 Python 字典
    # 有时候 AI 可能会在 JSON 前后加一些文字，所以先清理一下
    result_text = result_text.strip()

    # 如果 AI 返回的内容被 ```json ``` 包裹，需要去掉
    if result_text.startswith("```json"):
        result_text = result_text[7:]  # 去掉开头的 ```json
    if result_text.startswith("```"):
        result_text = result_text[3:]  # 去掉开头的 ```
    if result_text.endswith("```"):
        result_text = result_text[:-3]  # 去掉结尾的 ```

    result_text = result_text.strip()

    # 把 JSON 字符串转成 Python 字典
    result = json.loads(result_text)

    return result
