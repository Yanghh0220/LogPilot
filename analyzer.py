# analyzer.py - AI 分析逻辑，负责调用 DeepSeek API
#
# 为什么单独放一个文件？
# 1. 把 AI 调用逻辑和页面逻辑分开
# 2. 以后换模型只改这个文件，不用动页面代码

import json
from openai import OpenAI
from dotenv import load_dotenv
from prompt import SYSTEM_PROMPT, build_user_prompt
from log_parser import parse_log
from models import AnalysisResult
from config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_TEMPERATURE, DEEPSEEK_API_KEY

# ============================================
# 加载 .env 文件中的 API Key
# ============================================
# load_dotenv() 会读取 .env 文件，把里面的变量加载到环境变量中
load_dotenv()

# ============================================
# 创建 DeepSeek 客户端
# ============================================
# DeepSeek 兼容 OpenAI SDK，所以我们用 OpenAI 的客户端
# 配置项从 config.py 读取，方便统一管理
client = OpenAI(
    base_url=DEEPSEEK_BASE_URL,  # 从配置文件读取 API 地址
    api_key=DEEPSEEK_API_KEY      # 从配置文件读取 API Key
)

def analyze_log(log_text: str) -> AnalysisResult:
    """
    调用 DeepSeek API 分析构建日志

    参数:
        log_text: 用户粘贴的构建日志

    返回:
        解析后的字典，包含错误摘要、原因、修复建议等

    异常:
        ValueError: 输入为空
        RuntimeError: API Key 未配置
        ConnectionError: API 调用失败
        json.JSONDecodeError: AI 返回格式错误
    """

    # ----------------------------------------
    # 0. 输入验证
    # ----------------------------------------
    # 如果用户没粘贴日志就点分析，直接报错
    if not log_text or not log_text.strip():
        raise ValueError("日志内容不能为空")

    # ----------------------------------------
    # 1. 检查 API Key
    # ----------------------------------------
    # 如果 API Key 没配置，提前报错，避免调用 API 后才失败
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "未配置 DEEPSEEK_API_KEY，请在 .env 文件中填入你的 API Key"
        )

    # ----------------------------------------
    # 2. 预处理日志 + 构建提示词
    # ----------------------------------------
    # 用 log_parser 预处理：识别平台、提取错误行、智能截断
    parsed = parse_log(log_text)
    user_prompt = build_user_prompt(
        log_text=parsed["truncated_log"],
        platform=parsed["platform"],
        error_lines=parsed["error_lines"],
        is_truncated=parsed["is_truncated"],
    )

    # ----------------------------------------
    # 3. 调用 DeepSeek API
    # ----------------------------------------
    # client.chat.completions.create() 是 OpenAI SDK 的标准调用方式
    # model: 使用的模型名称
    # messages: 对话历史，包含系统提示词和用户输入
    # temperature: 控制回复的随机性，0 表示最稳定
    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,  # 从配置文件读取模型名称
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=DEEPSEEK_TEMPERATURE  # 从配置文件读取温度参数
        )
    except Exception as e:
        # 捕获所有 API 调用异常（网络问题、认证失败、余额不足等）
        raise ConnectionError(f"调用 DeepSeek API 失败: {str(e)}")

    # ----------------------------------------
    # 4. 提取 AI 的回复内容
    # ----------------------------------------
    # response.choices[0].message.content 就是 AI 返回的文本
    result_text = response.choices[0].message.content

    # 检查 AI 是否返回了空内容
    if not result_text:
        raise RuntimeError("AI 返回了空内容，请重试")

    # ----------------------------------------
    # 5. 解析 JSON
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
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        # 如果 AI 返回的不是合法 JSON，给出友好提示
        raise ValueError(
            f"AI 返回的内容无法解析为 JSON，请重试。\n"
            f"原始内容: {result_text[:200]}..."
        )

    return result
