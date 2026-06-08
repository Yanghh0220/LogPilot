# tests/conftest.py - pytest 配置文件
#
# 为什么需要这个文件？
# 我们的项目代码在 LogPilot/ 根目录，测试在 tests/ 子目录
# 默认情况下 tests/ 里的代码"看不到"上层的模块
# 这个文件把项目根目录加到 Python 的搜索路径中

import sys
from pathlib import Path

# 获取项目根目录（tests 的上一级）
project_root = Path(__file__).parent.parent
# 把根目录加到搜索路径，这样就能 import log_parser、prompt 等模块
sys.path.insert(0, str(project_root))
