# 贡献指南

感谢你对 LogPilot 的关注！以下是参与贡献的流程。

## 本地运行项目

### 1. Fork 并克隆

```bash
# 先在 GitHub 上 Fork 本仓库，然后克隆你的 Fork
git clone https://github.com/你的用户名/LogPilot.git
cd LogPilot
```

### 2. 创建虚拟环境

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API Key
```

### 5. 启动开发服务器

```bash
streamlit run app.py
```

### 6. 运行测试

```bash
pytest tests/ -v
```

---

## Commit 信息规范

请使用以下前缀，让提交历史一目了然：

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat:` | 新功能 | `feat: 支持日志文件上传` |
| `fix:` | 修复 bug | `fix: 修复 Docker 日志识别失败` |
| `docs:` | 文档变更 | `docs: 更新 README 快速开始` |
| `test:` | 测试相关 | `test: 新增 call_ai 异常处理测试` |
| `ci:` | CI/CD 配置 | `ci: 更新 GitHub Actions 配置` |
| `refactor:` | 重构（不改变功能） | `refactor: 提取公共配置到 config.py` |
| `style:` | 代码风格（不影响逻辑） | `style: 统一缩进为 4 空格` |
| `chore:` | 构建/工具变更 | `chore: 更新 .gitignore` |

**示例：**

```bash
git commit -m "feat: 新增 Ollama 本地模型支持"
git commit -m "fix: 修复 pytest 日志中 assertion 拼写错误导致识别失败"
```

---

## 提交 PR 流程

### 1. 创建分支

```bash
# 从 main 分支创建你的工作分支
git checkout -b feat/你的功能名
```

分支命名建议：
- `feat/xxx` — 新功能
- `fix/xxx` — 修复
- `docs/xxx` — 文档

### 2. 开发并测试

```bash
# 写代码...
# 跑测试确认没有破坏已有功能
pytest tests/ -v
```

### 3. 提交并推送

```bash
git add .
git commit -m "feat: 你的功能描述"
git push origin feat/你的功能名
```

### 4. 在 GitHub 上创建 PR

- 目标分支选 `main`
- PR 标题用 commit 规范的格式
- PR 描述说明：做了什么、为什么做、如何测试

### 5. 等待 Review

我会尽快 review 你的 PR，如果有修改建议会直接在 PR 中评论。

---

## 什么贡献是欢迎的？

- 🐛 修复已知 bug
- ✨ 新增 AI 模型支持（如 Ollama、Claude）
- 📝 完善文档或翻译
- 🧪 补充测试用例
- 🎨 改进 UI 样式
- 📦 支持新的日志平台

---

## 有问题？

如有任何问题，欢迎 [提 Issue](https://github.com/Yanghh0220/LogPilot/issues)。
