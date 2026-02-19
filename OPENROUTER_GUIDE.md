# OpenRouter (DeepSeek) API 使用指南

## 简介 ✨

**OpenRouter** 是一个API聚合服务，提供统一的接口访问多个AI模型，包括DeepSeek、OpenAI、Anthropic等。使用OpenRouter可以：
- 统一管理多个AI服务的API密钥
- 获得更好的服务稳定性
- 享受竞争性定价
- 灵活切换不同模型

## 获取OpenRouter API密钥 🔑

### 步骤

1. **访问OpenRouter官网**
   - 打开浏览器访问：https://openrouter.ai/

2. **注册账号**
   - 点击右上角 "Sign In"
   - 使用GitHub账号或邮箱注册

3. **获取API密钥**
   - 登录后点击左侧菜单 "API Keys"
   - 点击 "Create API Key" 按钮
   - 复制生成的API密钥（格式：`sk-or-v1-...`）

4. **充值（可选）**
   - 在 "Billing" 页面充值
   - 最低充值金额通常为 $5

## 定价信息 💰

### DeepSeek via OpenRouter

| 模型 | 输入价格 | 输出价格 | 说明 |
|------|---------|---------|------|
| deepseek/deepseek-chat | $0.14/M tokens | $0.28/M tokens | 最新DeepSeek模型 |
| deepseek/deepseek-coder | $0.14/M tokens | $0.28/M tokens | 代码专用模型 |

**注意：** OpenRouter的价格与DeepSeek官方API相同，但可能因市场调整而变化。

### 费用估算

| 内容类型 | Token估算 | 10页PDF费用 | 50页PDF费用 | 100页PDF费用 |
|---------|----------|------------|------------|-------------|
| 中文文档 | 1字符=1token | ~$0.003 | ~$0.015 | ~$0.03 |
| 英文文档 | 4字符=1token | ~$0.001 | ~$0.005 | ~$0.01 |
| 混合文档 | 混合计算 | ~$0.002 | ~$0.010 | ~$0.02 |

## 使用方法 📝

### 1. 选择API服务

在PDF翻译工具中：

1. 打开 http://localhost:5001
2. 在"翻译服务"下拉菜单中选择：**OpenRouter (DeepSeek)**
3. 输入框会自动显示

### 2. 输入API密钥

1. 将你的OpenRouter API密钥粘贴到输入框
2. 密钥格式：`sk-or-v1-xxxxxxxxxxxxx`
3. 密钥只用于本次翻译，不会被存储

### 3. 开始翻译

1. 上传PDF文件
2. 选择源语言和目标语言
3. 点击"开始翻译"
4. 实时查看进度和费用

## API配置说明 ⚙️

### 模型选择

当前使用的模型：
```python
model = "deepseek/deepseek-chat"
```

### API端点

```
https://openrouter.ai/api/v1/chat/completions
```

### 请求格式（OpenAI兼容）

```json
{
  "model": "deepseek/deepseek-chat",
  "messages": [
    {"role": "system", "content": "你是一个专业的翻译助手。"},
    {"role": "user", "content": "请将以下文本翻译成中文：\n\nHello World"}
  ],
  "temperature": 0.3
}
```

### 响应格式

```json
{
  "id": "gen-xxx",
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "你好世界"
    }
  }],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 5,
    "total_tokens": 25
  }
}
```

## 优势对比 📊

### OpenRouter vs 直接API

| 特性 | OpenRouter | DeepSeek官方 | 智谱AI |
|------|-----------|-------------|--------|
| **统一接口** | ✅ | ❌ | ❌ |
| **多模型支持** | ✅ | ❌ | ❌ |
| **价格** | $0.14/$0.28 | $0.14/$0.28 | ~$0.5 |
| **稳定性** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **中文支持** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **使用门槛** | 低 | 中 | 中 |

### 推荐场景

**使用OpenRouter当你：**
- ✅ 想要统一管理多个AI服务
- ✅ 需要更高的服务稳定性
- ✅ 计划使用多种AI模型
- ✅ 希望灵活切换服务商

**使用DeepSeek官方当你：**
- ✅ 只使用DeepSeek模型
- ✅ 想要直接支持
- ✅ 关注成本优化

## 使用示例 📚

### 示例1：英文翻译成中文

```
源语言：en (英语)
目标语言：zh (中文)
翻译服务：OpenRouter (DeepSeek)

原文：
Hello World! This is a PDF translation tool.

译文：
你好世界！这是一个PDF翻译工具。

费用：~$0.0005 (100 tokens)
```

### 示例2：中文翻译成英文

```
源语言：zh (中文)
目标语言：en (英语)
翻译服务：OpenRouter (DeepSeek)

原文：
这是一个PDF翻译工具，支持多种语言。

译文：
This is a PDF translation tool that supports multiple languages.

费用：~$0.0007 (140 tokens)
```

## 常见问题 ❓

### Q: OpenRouter和DeepSeek官方API有什么区别？

**A:**
- **API接口**：OpenRouter使用统一的OpenAI兼容格式
- **价格**：相同（DeepSeek都是$0.14/$0.28）
- **模型**：完全相同的DeepSeek模型
- **稳定性**：OpenRouter提供更好的负载均衡

### Q: 我可以同时使用多个API吗？

**A:** 可以！OpenRouter允许：
- 单个API密钥访问多个模型
- 灵活切换不同的服务商
- 统一的账单和管理

### Q: 费用如何计算？

**A:**
- 输入tokens × $0.14/1M = 输入费用
- 输出tokens × $0.28/1M = 输出费用
- 总费用 = 输入费用 + 输出费用

**示例：**
- 输入1000 tokens，输出500 tokens
- 费用 = (1000/1M × $0.14) + (500/1M × $0.28)
- 费用 = $0.00014 + $0.00014 = $0.00028

### Q: 如何查看我的使用量？

**A:**
1. 登录OpenRouter
2. 访问 "Usage" 页面
3. 查看实时使用统计
4. 下载详细账单

### Q: 免费额度是多少？

**A:**
- 新用户通常有少量免费额度
- 建议先充值 $5-$10 测试
- 实际费用取决于使用量

### Q: API密钥安全吗？

**A:**
- ✅ API密钥只用于本次翻译
- ✅ 不会被存储到服务器
- ✅ 传输过程使用HTTPS加密
- ✅ 翻译完成后立即丢弃

### Q: 翻译质量如何？

**A:** DeepSeek模型：
- 中文翻译：⭐⭐⭐⭐⭐ (优秀)
- 英文翻译：⭐⭐⭐⭐ (很好)
- 技术文档：⭐⭐⭐⭐⭐ (优秀)
- 文学翻译：⭐⭐⭐⭐ (很好)

## 技术支持 🛠️

### 遇到问题？

**1. API密钥无效**
```
错误：Authentication Fails
解决：检查API密钥是否正确复制
```

**2. 余额不足**
```
错误：Insufficient credits
解决：前往OpenRouter充值
```

**3. 模型不可用**
```
错误：Model not found
解决：检查模型名称是否正确
```

### 联系支持

- OpenRouter文档：https://openrouter.ai/docs
- OpenRouter Discord：https://discord.gg/openrouter
- Email: support@openrouter.ai

## 更新日志

### v2.3.0 (最新)
- ✨ 新增OpenRouter (DeepSeek) API支持
- 🎨 优化API选择界面
- 📝 添加详细使用文档

### v2.2.1
- 修复SSE连接问题
- 优化日志系统
- 改进错误处理

---

**推荐指数：** ⭐⭐⭐⭐⭐

如果你想要统一管理多个AI服务，OpenRouter是最佳选择！
