# tests/conftest.py - pytest 配置文件
#
# 为什么需要这个文件？
# 我们的项目代码在 LogGazer/ 根目录，测试在 tests/ 子目录
# 默认情况下 tests/ 里的代码"看不到"上层的模块
# 这个文件把项目根目录加到 Python 的搜索路径中

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 获取项目根目录（tests 的上一级）
project_root = Path(__file__).parent.parent
# 把根目录加到搜索路径，这样就能 import log_parser、prompt 等模块
sys.path.insert(0, str(project_root))


# ============================================================
#  全局 Mock：阻止 analyzer.py 模块级 OpenAI 客户端创建失败
# ============================================================
# analyzer.py 在模块级创建 OpenAI() 客户端，需要 API Key
# 测试环境中没有 API Key，所以需要在 import 前 mock 掉
# 这个 fixture 在所有测试之前运行，确保 analyzer 模块能被安全导入

@pytest.fixture(autouse=True, scope="session")
def _mock_openai_client():
    """在测试会话期间 mock OpenAI 客户端，阻止模块级创建失败"""
    mock_client = MagicMock()
    with patch("openai.OpenAI", return_value=mock_client):
        yield mock_client
