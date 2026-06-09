# config.py - 集中管理配置项
#
# 为什么需要这个文件？
# 1. 把散落在各处的配置集中管理，修改时不用到处找
# 2. 支持从环境变量读取，方便部署时覆盖默认值
# 3. 新增配置项时，只需要改这一个文件
#
# 配置读取优先级：
#   1. Streamlit Secrets（云端部署时使用，在 Streamlit Cloud 控制台配置）
#   2. 环境变量 / .env 文件（本地开发时使用）

import os


def _get_secret(key: str, default=None):
    """
    统一的配置读取函数：优先 Streamlit Secrets，回退到环境变量

    为什么需要这个？
    - 本地开发用 .env 文件（os.getenv）
    - Streamlit Cloud 部署用 Secrets（st.secrets）
    - 一个函数搞定两种场景，业务代码不需要关心配置来源
    """
    try:
        import streamlit as st
        # st.secrets 的行为类似字典，key 不存在时抛出 KeyError
        value = st.secrets[key]
        # Streamlit Secrets 的值可能是 TOML 类型，统一转字符串
        return str(value) if value is not None else default
    except (KeyError, FileNotFoundError, TypeError):
        # KeyError: key 不存在于 secrets 中
        # FileNotFoundError: 没有 .streamlit/secrets.toml（本地开发正常情况）
        # TypeError: st.secrets 还未初始化
        return os.getenv(key, default)


# ============================================
# DeepSeek API 配置
# ============================================

# API 地址（默认值：https://api.deepseek.com）
# 如果你用其他兼容 OpenAI 的模型（如 Moonshot、智谱），改这里
DEEPSEEK_BASE_URL = _get_secret(
    "DEEPSEEK_BASE_URL",
    "https://api.deepseek.com"
)

# 模型名称（默认值：deepseek-chat）
# DeepSeek 可选：deepseek-chat（通用）、deepseek-coder（代码专用）
DEEPSEEK_MODEL = _get_secret(
    "DEEPSEEK_MODEL",
    "deepseek-chat"
)

# 温度参数（默认值：0）
# 0 = 最稳定、可重复；1 = 最有创意、随机
DEEPSEEK_TEMPERATURE = float(_get_secret(
    "DEEPSEEK_TEMPERATURE",
    "0"
))

# API Key
# 本地开发从 .env 读取 DEEPSEEK_API_KEY
# Streamlit Cloud 从 Secrets 读取 API_KEY 或 DEEPSEEK_API_KEY
DEEPSEEK_API_KEY = _get_secret("DEEPSEEK_API_KEY") or _get_secret("API_KEY")

# ============================================
# AI 提供商选择
# ============================================
# 可选值："openai"（兼容 DeepSeek/Moonshot/智谱）或 "claude"
AI_PROVIDER = _get_secret("AI_PROVIDER", "openai")

# AI 温度参数（默认 0.2，比 0 更稳定但不完全死板）
AI_TEMPERATURE = float(_get_secret("AI_TEMPERATURE", "0.2"))

# ============================================
# Claude API 配置
# ============================================
CLAUDE_API_KEY = _get_secret("CLAUDE_API_KEY")
CLAUDE_MODEL = _get_secret("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# ============================================
# 语义缓存配置
# ============================================

# 缓存总开关（默认开启）
CACHE_ENABLED = _get_secret("CACHE_ENABLED", "true").lower() == "true"

# 语义相似度阈值：>= 此值直接返回缓存结果
CACHE_SIMILARITY_HIGH = float(_get_secret("CACHE_SIMILARITY_HIGH", "0.92"))

# 语义相似度阈值：>= 此值且 < HIGH 时注入 RAG 上下文
CACHE_SIMILARITY_LOW = float(_get_secret("CACHE_SIMILARITY_LOW", "0.80"))

# 缓存 TTL（小时），默认 30 天
CACHE_TTL_HOURS = int(_get_secret("CACHE_TTL_HOURS", "720"))

# Qdrant 存储路径，空字符串 = 内存模式
CACHE_QDRANT_PATH = _get_secret("CACHE_QDRANT_PATH", "")

# Embedding 模型名称
CACHE_EMBEDDING_MODEL = _get_secret("CACHE_EMBEDDING_MODEL", "all-MiniLM-L6-v2")