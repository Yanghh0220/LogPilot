# config.py - 集中管理配置项
#
# 为什么需要这个文件？
# 1. 把散落在各处的配置集中管理，修改时不用到处找
# 2. 支持从环境变量读取，方便部署时覆盖默认值
# 3. 新增配置项时，只需要改这一个文件

import os

# ============================================
# DeepSeek API 配置
# ============================================

# API 地址（默认值：https://api.deepseek.com）
# 如果你用其他兼容 OpenAI 的模型（如 Moonshot、智谱），改这里
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL",
    "https://api.deepseek.com"
)

# 模型名称（默认值：deepseek-chat）
# DeepSeek 可选：deepseek-chat（通用）、deepseek-coder（代码专用）
DEEPSEEK_MODEL = os.getenv(
    "DEEPSEEK_MODEL",
    "deepseek-chat"
)

# 温度参数（默认值：0）
# 0 = 最稳定、可重复；1 = 最有创意、随机
DEEPSEEK_TEMPERATURE = float(os.getenv(
    "DEEPSEEK_TEMPERATURE",
    "0"
))

# API Key（从 .env 文件读取）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")