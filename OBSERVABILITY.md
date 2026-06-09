# LogGazer 可观测性与成本管控架构文档

## 1. 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Streamlit App (app.py)                       │
│                                                                     │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │ 限流检查  │───▶│ 成本熔断检查  │───▶│ trace_analysis() Span   │  │
│  │(RateLimit)│    │(CircuitBreak)│    │  └─ ai_engine.call Span  │  │
│  └──────────┘    └──────────────┘    │     └─ prompt.build Span │  │
│                                       └──────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ObservabilityManager                              │
│                                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────┐ │
│  │  OpenTelemetry│  │  Prometheus  │  │    Cost     │  │  Rate    │ │
│  │   Tracer     │  │   Metrics    │  │  Calculator │  │ Limiter  │ │
│  └──────┬──────┘  └──────┬───────┘  └──────┬──────┘  └────┬─────┘ │
│         │                │                  │              │       │
└─────────┼────────────────┼──────────────────┼──────────────┼───────┘
          │                │                  │              │
          ▼                ▼                  ▼              ▼
   ┌────────────┐   ┌────────────┐    ┌────────────┐  ┌────────────┐
   │  Console/   │   │ Prometheus │    │   Redis    │  │   Redis    │
   │  OTLP/Jaeger│   │  :9090     │    │ (or Memory)│  │ (or Memory)│
   │  Exporter   │   │  /metrics  │    │  成本持久化 │  │  令牌桶    │
   └────────────┘   └────────────┘    └────────────┘  └────────────┘
```

## 2. 数据流

```
用户点击「开始分析」
        │
        ▼
   ┌─────────┐
   │ 限流检查 │ ── 超限 ──▶ st.warning("请求过于频繁")
   └────┬────┘
        │ 通过
        ▼
   ┌──────────┐
   │ 熔断检查  │ ── tripped ──▶ st.error("额度已用尽")
   └────┬─────┘
        │ normal/warning
        ▼
   ┌──────────────────────────────────────────────────┐
   │ loggazer.analysis (Root Span)                     │
   │   platform: "npm"                                 │
   │   cache_status: "miss"                            │
   │                                                    │
   │   ┌────────────────────────────────────────────┐  │
   │   │ ai_engine.call.deepseek (Child Span)       │  │
   │   │   model: "deepseek-chat"                   │  │
   │   │   temperature: 0.2                         │  │
   │   │   prompt_length: 1234                      │  │
   │   │                                            │  │
   │   │   ┌─ usage ──────────────────────────────┐ │  │
   │   │   │ input_tokens: 500                    │ │  │
   │   │   │ output_tokens: 300                   │ │  │
   │   │   │ → CostCalculator.calculate()         │ │  │
   │   │   │ → record_tokens() → Prometheus       │ │  │
   │   │   └──────────────────────────────────────┘ │  │
   │   └────────────────────────────────────────────┘  │
   └──────────────────────────────────────────────────┘
        │
        ▼
   结果返回前端 + 指标暴露到 :9090/metrics
```

## 3. 指标定义

### 3.1 Prometheus 指标

| 指标名称 | 类型 | 标签 | 说明 |
|---------|------|------|------|
| `loggazer_analysis_duration_seconds` | Histogram | `platform`, `cache_status` | 端到端分析耗时 |
| `loggazer_token_consumption_total` | Counter | `model`, `provider`, `status` | Token 消耗总量 |
| `loggazer_analysis_errors_total` | Counter | `error_type` | 错误计数 |
| `loggazer_cache_hit_ratio` | Gauge | - | 缓存命中率 (0-1) |
| `loggazer_active_requests` | Gauge | - | 当前并发请求数 |
| `loggazer_monthly_cost_usd` | Gauge | - | 本月累计成本 (USD) |

### 3.2 标签枚举值

**platform** (11个):
`npm`, `GitHub Actions`, `Jenkins`, `Docker`, `pytest`, `jest`, `cargo`, `pip`, `Gradle`, `Maven`, `other`

**cache_status** (3个):
`hit`, `miss`, `rag`

**error_type** (6个):
`auth`, `rate_limit`, `quota`, `network`, `parse`, `validation`

**model**: `deepseek-chat`, `deepseek-coder`, `claude-sonnet-4-20250514`, `qwen2.5:7b`, `llama3:8b`

**provider**: `deepseek`, `claude`, `ollama`

**status**: `success`, `error`

### 3.3 Cardinality 分析

```
Histogram: 11 platforms × 3 cache_status = 33 时间序列
Counter:   5 models × 3 providers × 2 status = 30 时间序列
Counter:   6 error_type = 6 时间序列
Gauge:     3 (cache_hit_ratio, active_requests, monthly_cost)
───────────────────────────────────────
总计:      72 条时间序列（安全，远低于 10K 阈值）
```

## 4. 采样策略

| 环境 | 采样率 | 配置 |
|------|--------|------|
| 生产环境 | 10% | `TraceIdRatioBased(0.1)` |
| 开发环境 | 100% | `TraceIdRatioBased(1.0)` |

通过 `ObservabilityManager(sampling_rate=0.1)` 配置。

## 5. 成本计算模型

### 5.1 定价表

| 模型 | 输入 ($/1M tokens) | 输出 ($/1M tokens) |
|------|-------------------|-------------------|
| deepseek-chat | $0.14 | $0.28 |
| deepseek-coder | $0.14 | $0.28 |
| deepseek-reasoner | $0.55 | $2.19 |
| claude-sonnet-4-20250514 | $3.00 | $15.00 |
| claude-opus-4-20250514 | $15.00 | $75.00 |
| claude-haiku-4-5-20251001 | $0.80 | $4.00 |
| qwen2.5:7b (本地) | $0.00 | $0.00 |
| llama3:8b (本地) | $0.00 | $0.00 |
| 未知模型 | $15.00 | $75.00 |

### 5.2 计算公式

```
cost = (input_tokens / 1,000,000) × input_per_1m
     + (output_tokens / 1,000,000) × output_per_1m
```

精度：保留 6 位小数 ($0.000001)

### 5.3 Token 估算策略

| 优先级 | 方法 | 精度 | 延迟 |
|--------|------|------|------|
| 1 | API 响应 `usage` 字段 | 精确 | 0 |
| 2 | `len(text) / 4` 估算 | ±20% | 0 |

不引入 Tiktoken 以避免额外依赖和 +50ms 延迟。

## 6. 限流机制

### 6.1 算法：Token Bucket（Redis Lua 脚本）

```lua
-- 原子性操作：检查 → 补充 → 消耗
local tokens = redis.call('hget', key, 'tokens')
local refill = elapsed * rate
tokens = math.min(capacity, tokens + refill)
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end
```

### 6.2 限流规则

| 用户类型 | 限制 | 窗口 |
|---------|------|------|
| 匿名用户 | 5 次/分钟 | 60s |
| 认证用户（预留） | 20 次/分钟 | 60s |

### 6.3 降级方案

Redis 不可用时，自动切换到内存滑动窗口（单进程场景下语义正确）。

## 7. 成本熔断器

### 7.1 状态机

```
                    ┌─────────┐
           < 80%    │         │
        ───────────▶│ normal  │◀──────────┐
                    │         │           │
                    └────┬────┘           │
                         │                │
                    >= 80%                │
                         │                │
                         ▼                │
                    ┌─────────┐     < 100%│
                    │ warning │───────────┘
                    │         │
                    └────┬────┘
                         │
                    >= 100%
                         │
                         ▼
                    ┌─────────┐
                    │ tripped │ ──▶ 强制降级到本地模型
                    └─────────┘
```

### 7.2 降级行为

| 状态 | 行为 |
|------|------|
| `normal` | 正常调用远程 API |
| `warning` | 正常调用 + 日志告警 + 前端提示 |
| `tripped` | 拒绝远程 API 调用，返回本地模型降级提示 |

## 8. 告警规则（Prometheus Alertmanager 格式）

```yaml
groups:
  - name: loggazer_alerts
    rules:
      - alert: HighErrorRate
        expr: rate(loggazer_analysis_errors_total[5m]) > 10
        for: 5m
        labels:
          severity: P2
        annotations:
          summary: "LogGazer 错误率过高"

      - alert: MonthlyCostWarning
        expr: loggazer_monthly_cost_usd > 45
        for: 1m
        labels:
          severity: P1
        annotations:
          summary: "LogGazer 月度成本接近预算上限"

      - alert: CacheHitRatioLow
        expr: loggazer_cache_hit_ratio < 0.5
        for: 10m
        labels:
          severity: P3
        annotations:
          summary: "LogGazer 缓存命中率异常偏低"
```

## 9. Redis 不可用降级矩阵

| 功能 | Redis 可用 | Redis 不可用 | 可接受？ |
|------|-----------|-------------|---------|
| 限流 | Token Bucket（精确） | 内存滑动窗口（单进程） | ✅ |
| 成本统计 | Redis INCRBYFLOAT | 内存字典（重启丢失） | ✅ |
| 熔断器 | Redis 读取月度总额 | 内存读取 | ✅ |

**核心决策**: Redis 不可用时所有功能降级到内存模式，系统继续运行。

## 10. 模块清单

| 文件 | 职责 |
|------|------|
| `cost_calculator.py` | Token 成本计算 + 月度累计 + 定价表 |
| `rate_limiter.py` | Token Bucket 限流器（Redis Lua + 内存降级） |
| `observability.py` | 可观测性中心管理器（Tracer + Metrics + Cost + Limiter） |
| `metrics_server.py` | Prometheus /metrics HTTP Server（独立线程） |
| `ai_engine.py` | AI 调用引擎（已注入 Span + Token 记录 + 熔断检查） |
| `app.py` | Streamlit 前端（已注入限流 + 熔断 + 追踪） |
| `tests/test_observability.py` | 成本计算 + 熔断器 + 集成测试 |
| `tests/test_rate_limiter.py` | 限流器单元测试 + 集成测试 |

## 11. 快速验证

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 运行测试
pytest tests/test_observability.py tests/test_rate_limiter.py -v

# 3. 启动应用
streamlit run app.py

# 4. 检查 Metrics 端点
curl http://localhost:9090/metrics

# 5. 验证指标输出（应包含以下内容）
# loggazer_analysis_duration_seconds_count
# loggazer_token_consumption_total
# loggazer_analysis_errors_total
# loggazer_cache_hit_ratio
# loggazer_active_requests
# loggazer_monthly_cost_usd
```

## 12. 禁止项检查清单

- [x] Streamlit 主线程不启动阻塞式 HTTP Server（metrics_server 使用独立 daemon 线程）
- [x] 不将 API Key / Token 消耗明细写入日志
- [x] 不在每次分析后同步写入磁盘/Redis（使用内存缓冲）
- [x] 不使用全局变量持有 ObservabilityManager 实例（通过依赖注入传递）
- [x] 不引入商业 APM SDK（纯 OpenTelemetry + Prometheus 开源栈）
