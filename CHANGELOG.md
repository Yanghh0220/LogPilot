# Changelog

本文件记录 LogPilot 的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2025-07

### Added

- **AI 日志分析引擎**：基于 DeepSeek 大模型，自动分析 CI/CD 构建失败日志
- **10+ 平台自动识别**：支持 GitHub Actions、Jenkins、Docker、npm、pip、cargo、pytest、jest、Gradle、Maven
- **结构化分析报告**：包含错误摘要、根因分析（含概率百分比）、修复建议、排查命令、严重程度、预防建议
- **Top 3 修复建议**：每条建议附带可直接复制执行的命令
- **一键复制功能**：所有命令支持一键复制到终端
- **内置示例日志**：3 种常见构建失败场景（npm 依赖冲突、Docker 构建失败、Python 测试失败），零门槛体验
- **智能日志截断**：自动保留头部环境信息和尾部错误信息，中间部分省略，避免 token 浪费
- **日志统计分析**：自动统计 error / warning / fatal 数量，动态生成严重程度提示
- **多服务商支持**：支持 DeepSeek、OpenAI、Moonshot、智谱等所有兼容 OpenAI 接口的 AI 服务商
- **自定义异常体系**：AuthError / RateLimitError / QuotaError，精准区分不同错误类型
- **指数退避重试**：网络问题自动重试 3 次（1s → 2s → 4s），认证/余额问题直接报错不重试
- **响应式 Web UI**：基于 Streamlit 构建，自定义 CSS 样式，支持宽屏和移动端
- **单元测试覆盖**：65 个测试用例，覆盖日志解析、Prompt 构建、AI 引擎三大模块
- **GitHub Actions CI**：自动运行 flake8 代码检查 + pytest 单元测试 + 必要文件检查
- **集中配置管理**：通过 config.py 统一管理所有环境变量，支持 .env 文件加载
- **类型安全**：使用 TypedDict 定义数据结构，IDE 自动补全和类型检查
