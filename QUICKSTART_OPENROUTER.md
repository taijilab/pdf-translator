# 🚀 OpenRouter DeepSeek - 快速开始

## 5分钟快速上手 ⏱️

### 第1步：获取OpenRouter API密钥 (2分钟)

1. 访问：https://openrouter.ai
2. 点击 "Sign In" → 使用GitHub登录
3. 点击左侧 "API Keys" → "Create API Key"
4. 复制密钥（格式：`sk-or-v1-xxxxxxxxxxxxx`）
5. 充值 $5-10（Billing → Add Credits）

### 第2步：配置翻译工具 (1分钟)

1. 打开：http://localhost:5001
2. "翻译服务" 选择：**OpenRouter (DeepSeek)**
3. 粘贴API密钥
4. ✅ API密钥框会自动显示

### 第3步：翻译PDF (2分钟)

1. 上传PDF文件
2. 选择源语言和目标语言
3. 点击 "开始翻译"
4. 实时查看进度和费用
5. 翻译完成自动下载

## 为什么选择OpenRouter? 🤔

### ✅ 优势

| 特性 | 说明 |
|------|------|
| 🎯 **统一接口** | 一个密钥访问多个AI模型 |
| 💰 **竞争定价** | $0.14/$0.28 每M tokens |
| 🌍 **全球CDN** | 快速稳定的服务 |
| 📊 **实时统计** | 清晰的使用量跟踪 |
| 🔀 **灵活切换** | 轻松更换不同模型 |

### 📊 价格对比

| API服务 | 输入 | 输出 | 总计(10页PDF) |
|---------|------|------|---------------|
| **Google** | 免费 | 免费 | $0 |
| **OpenRouter** | $0.14/M | $0.28/M | ~$0.003 |
| **DeepSeek** | $0.14/M | $0.28/M | ~$0.003 |
| **智谱AI** | ~$0.5/M | ~$0.5/M | ~$0.01 |

**结论：** OpenRouter = DeepSeek的价格 + 更好的服务！🎉

## 快速参考 📋

### API密钥格式

```
正确: sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
错误: xxx (不完整)
错误: sk-xxx (旧格式)
```

### 模型名称

```
当前使用: deepseek/deepseek-chat
其他可用: deepseek/deepseek-coder (代码专用)
```

### 端点URL

```
生产: https://openrouter.ai/api/v1/chat/completions
```

## 使用技巧 💡

### 技巧1：测试小文件

先用1-2页PDF测试，确保：
- ✅ API密钥正确
- ✅ 账户有余额
- ✅ 翻译质量满意

### 技巧2：查看实时费用

翻译过程中会显示：
```
输入tokens: 1,234
输出tokens: 1,156
预估费用: $0.0048 USD
```

### 技巧3：余额预警

建议余额：
- 小文件(<10页): $5 足够
- 中文件(10-50页): $10
- 大文件(>50页): $20+

### 技巧4：选择合适模型

| 场景 | 推荐模型 |
|------|---------|
| 通用翻译 | deepseek/deepseek-chat |
| 技术文档 | deepseek/deepseek-chat |
| 代码翻译 | deepseek/deepseek-coder |

## 故障排除 🔧

### 问题1：Authentication Fails

**原因：** API密钥无效

**解决：**
1. 检查密钥是否完整复制
2. 确认没有多余空格
3. 重新生成API密钥

### 问题2：Insufficient Credits

**原因：** 余额不足

**解决：**
1. 登录OpenRouter
2. 前往Billing页面
3. 充值$5-10

### 问题3：Model Not Found

**原因：** 模型名称错误

**解决：**
- 当前使用：`deepseek/deepseek-chat`
- 不要修改模型名称

### 问题4：翻译很慢

**原因：** 网络延迟

**解决：**
1. 检查网络连接
2. 使用小文件测试
3. 耐心等待（DeepSeek速度很快）

## 对比测试 🧪

### Google vs OpenRouter

| 文件 | 页数 | Google | OpenRouter |
|------|------|--------|------------|
| 论文 | 10页 | 免费 | $0.003 |
| 书籍 | 50页 | 免费 | $0.015 |
| 报告 | 100页 | 免费 | $0.03 |

**质量对比：**
- Google: ⭐⭐⭐ (一般)
- OpenRouter: ⭐⭐⭐⭐⭐ (优秀)

**推荐：** 重要文档使用OpenRouter！

## 下一步 ➡️

- 📖 详细文档：查看 `OPENROUTER_GUIDE.md`
- 🐛 故障排除：查看 `TROUBLESHOOTING.md`
- 🎯 开始翻译：刷新 http://localhost:5001

## 推荐配置 ⭐

```
翻译服务：OpenRouter (DeepSeek)
源语言：自动检测
目标语言：选择你需要的目标语言
API密钥：sk-or-v1-xxxxxxxxxxxxx
```

**准备好了吗？** 现在就可以开始翻译你的PDF文件了！🚀
