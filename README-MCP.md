# 🔌 LogGazer MCP Server — AI 原生 IDE 集成

LogGazer MCP Server 将日志分析能力暴露给任何支持 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 的客户端（Claude Desktop、VS Code、Cursor 等）。

## 快速开始

### 1. 安装依赖

```bash
pip install mcp>=1.0.0
```

### 2. 配置 Claude Desktop

将 `claude_desktop_config.json` 合并到你的 Claude Desktop 配置文件中：

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "loggazer": {
      "command": "python",
      "args": ["-m", "mcp_server", "--transport", "stdio"],
      "env": {
        "DEEPSEEK_API_KEY": "sk-your-deepseek-api-key",
        "LOGGAZER_API_URL": "http://localhost:8000"
      }
    }
  }
}
```

### 3. 启动后端（可选，用于 HTTP 模式）

```bash
# 终端 1: 启动 LogGazer API
python -m api.main

# 终端 2: Claude Desktop 会自动启动 MCP Server（stdio 模式）
```

## 可用能力

### Tool: `analyze_log`

分析 CI/CD 构建失败日志并返回结构化报告。

**参数**:
- `log_text` (string, required): 完整的构建失败日志
- `platform_hint` (string, optional): 平台提示（如 `npm`、`docker`、`pytest`）

**返回**: JSON 格式的 AnalysisResult，包含:
- `error_summary`: 错误摘要（≤50 字符）
- `severity`: 严重程度（low/medium/high/critical）
- `root_causes`: 根因分析（含概率分布）
- `fix_suggestions`: 修复建议（含可执行命令和安全等级）
- `debug_commands`: 排查命令
- `prevention`: 预防建议

**示例对话**:
```
用户: 我的 npm install 在 CI 中失败了，日志是：
      npm ERR! code ERESOLVE
      npm ERR! ERESOLVE could not resolve
      ...

Claude: [调用 analyze_log 工具]
       分析结果：
       - 严重程度: MEDIUM 🟡
       - 根因: 依赖版本冲突 (85%)
       - 修复: npm install --legacy-peer-deps 🟢 Safe
```

### Resource: `loggazer://error-patterns/{platform}`

获取特定平台的高频错误模式。

**支持平台**: `npm`, `Docker`, `pytest`, `GitHub Actions`, `pip`, `Jenkins`

每个模式包含: 错误模式、频率、根因解释、修复模板。

### Prompt: `loggazer://prompts/ci-troubleshooting`

预构建的 CI/CD 故障排除提示模板，将 AI 配置为结构化的调试助手。

## 传输模式

### stdio 模式（默认，本地 Claude Desktop）

```bash
python -m mcp_server --transport stdio
```

- 零网络开销，进程内通信
- 适用于 Claude Desktop 本地使用

### SSE 模式（远程/云部署）

```bash
python -m mcp_server --transport sse --host 0.0.0.0 --port 9000
```

- HTTP Server-Sent Events 传输
- 适用于远程访问、Docker 部署、云端实例
- 需配置 `LOGGAZER_API_URL` 指向后端 API

## 架构

```
Claude Desktop / MCP Client
        │
        │ MCP Protocol (stdio/sse)
        ▼
  mcp_server.py (FastMCP)
        │
        ├── 直接调用 analyzer.analyze_log()（同进程，零延迟）
        │   或
        └── HTTP POST /v1/analyze（跨进程，通过 LogGazer API）
                │
                ▼
        FastAPI Backend (api/main.py)
                │
                ▼
        analyzer.py → ai_engine.py → DeepSeek/Claude API
```

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `DEEPSEEK_API_KEY` | 是* | - | DeepSeek API Key |
| `CLAUDE_API_KEY` | 是* | - | Claude API Key（使用 Claude 时） |
| `LOGGAZER_API_URL` | 否 | `http://localhost:8000` | LogGazer 后端 API 地址 |
| `LOGGAZER_API_KEY` | 否 | - | 云端模式 API Key |

\* 至少配置一个 AI Provider。

## 安全

- MCP Server 不执行任何 AI 生成的命令
- 危险命令（`rm -rf /`、`mkfs` 等）在 analyze_log 层被拦截
- `safety_level` 字段标注每个命令的风险等级（safe/review/dangerous）

## 故障排除

**MCP Server 启动失败**:
```bash
pip install mcp>=1.0.0
```

**Claude Desktop 找不到工具**:
1. 检查 `claude_desktop_config.json` 路径是否正确
2. 确认 Python 路径在 `PATH` 中
3. 查看 Claude Desktop 开发者日志

**分析返回错误**:
1. 确认 `DEEPSEEK_API_KEY` 或 `CLAUDE_API_KEY` 已配置
2. 检查 LogGazer API 是否运行: `curl http://localhost:8000/v1/health`
