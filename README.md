# PDF 翻译工具

一个功能强大的PDF文件翻译Web应用，支持多种翻译API，实时进度显示和翻译日志。

## 功能特点

### 翻译服务
- **Google Translate** (免费，无需API密钥)
- **DeepSeek API** (高质量AI翻译，需要API密钥)
- **OpenRouter (DeepSeek)** (统一接口，需要API密钥)
- **智谱AI API** (GLM-4模型，需要API密钥)

### 核心功能
- 支持多种语言之间的翻译
- 支持自动检测源语言
- **实时进度显示** - 显示当前翻译进度百分比
- **翻译日志窗口** - 实时显示翻译过程中的详细信息
- 友好的Web界面，支持拖拽上传
- 尽量保持PDF原有格式
- 支持中文、日文、韩文等多字节字符
- 最大支持50MB的PDF文件

### 实时进度和日志
- **进度条** - 可视化显示翻译进度
- **百分比显示** - 精确显示完成百分比
- **翻译日志** - 实时记录每个步骤
  - 显示当前翻译的页码
  - 记录翻译服务信息
  - 显示错误和警告信息
  - 彩色编码（成功/错误/信息）

## 支持的语言

- 中文（zh）
- 英语（en）
- 日语（ja）
- 韩语（ko）
- 法语（fr）
- 德语（de）
- 西班牙语（es）
- 俄语（ru）
- 阿拉伯语（ar）

## 安装步骤

### 1. 安装Python依赖

确保你已安装Python 3.7或更高版本，然后安装依赖：

```bash
pip install -r requirements.txt
```

### 2. 运行应用

```bash
python app.py
```

应用将在 `http://localhost:5001` 启动

### 3. 使用应用

1. 在浏览器中打开 `http://localhost:5001`
2. 选择翻译服务：
   - **Google Translate**: 直接使用，无需配置
   - **DeepSeek API**: 需要输入API密钥（从 https://platform.deepseek.com 获取）
   - **OpenRouter**: 需要输入API密钥（从 https://openrouter.ai 获取）
   - **智谱AI API**: 需要输入API密钥（从 https://open.bigmodel.cn 获取）
3. 点击或拖拽上传PDF文件
4. 选择源语言（可设置为"自动检测"）
5. 选择目标语言
6. 点击"开始翻译"按钮
7. **实时观察翻译进度和日志**
8. 翻译完成后自动下载翻译后的PDF

## API密钥获取

### DeepSeek API
1. 访问 https://platform.deepseek.com
2. 注册账号并登录
3. 创建API密钥
4. 复制密钥到应用中

### OpenRouter API (推荐)
1. 访问 https://openrouter.ai
2. 使用GitHub或邮箱注册
3. 创建API密钥
4. 复制密钥到应用中
5. 充值（建议先充值$5-10测试）

### 智谱AI API
1. 访问 https://open.bigmodel.cn
2. 注册账号并登录
3. 创建API密钥
4. 复制密钥到应用中

## 项目结构

```
pdfapp/
├── app.py              # Flask应用主文件（包含SSE实时推送）
├── translator.py       # PDF翻译核心逻辑（支持多API）
├── requirements.txt    # Python依赖
├── start.sh           # 快速启动脚本
├── example_usage.py   # 命令行使用示例
├── templates/         # HTML模板
│   └── index.html    # Web界面（含进度和日志显示）
└── static/           # 静态资源
    ├── css/
    │   └── style.css # 样式文件
    └── js/
        └── app.js    # 前端交互（SSE连接）
```

## 技术栈

- **后端**: Python Flask
- **实时通信**: Server-Sent Events (SSE)
- **PDF处理**: PyPDF2, ReportLab
- **翻译服务**:
  - Google Translate (googletrans)
  - DeepSeek API (requests)
  - 智谱AI API (requests)
- **前端**: HTML5, CSS3, JavaScript (原生)

## 使用场景

### 场景1: 快速翻译（免费）
- 选择 Google Translate
- 无需API密钥
- 适合一般文档翻译

### 场景2: 高质量翻译（推荐）
- 选择 OpenRouter (DeepSeek)
- 输入API密钥
- 统一管理，稳定性好
- 适合专业文档、技术文档

### 场景3: 直连DeepSeek
- 选择 DeepSeek API
- 输入API密钥
- 官方接口，速度快

### 场景4: 国产AI翻译
- 选择 智谱AI API
- 输入API密钥
- 适合中文相关翻译

## 界面功能说明

### API选择区
- 下拉选择翻译服务
- 自动显示/隐藏API密钥输入框

### 文件上传区
- 支持点击选择文件
- 支持拖拽上传
- 显示文件名和大小

### 进度显示区
- **进度头部**: 显示标题和百分比
- **进度条**: 可视化进度展示
- **状态文本**: 当前状态描述
- **翻译日志窗口**:
  - 时间戳
  - 彩色编码的日志条目
  - 自动滚动到最新日志

## 注意事项

1. **API密钥安全**: 您的API密钥仅用于本次翻译，不会被存储
2. **翻译质量**:
   - Google Translate: 免费但质量一般
   - DeepSeek/智谱AI: 需要付费但质量更高
3. **格式保持**: 尽量保持原有格式，但复杂的布局可能需要手动调整
4. **文件大小**: 最大支持50MB的PDF文件
5. **处理时间**: 翻译时间取决于PDF的大小、页数和选择的API
6. **网络连接**: 需要稳定的网络连接

## 常见问题

### 翻译失败怎么办？
- 检查网络连接
- 确认PDF文件没有损坏
- 如果使用付费API，检查API密钥是否正确
- 查看翻译日志窗口获取详细错误信息

### API密钥在哪里输入？
- 在"翻译服务"下拉菜单中选择非Google的API
- 会自动显示API密钥输入框
- 输入密钥后即可使用

### 如何查看翻译进度？
- 点击"开始翻译"后
- 进度区域会自动展开
- 实时显示进度条和百分比
- 翻译日志窗口显示详细步骤

### 中文字符显示问题？
应用会自动尝试检测并使用系统中可用的中文字体。如果没有可用的中文字体，中文可能无法正确显示。

## 进阶使用

### 编程方式使用

如果你想直接在Python代码中使用，参考 `example_usage.py` 文件：

```python
from translator import PDFTranslator

# 使用Google翻译
translator = PDFTranslator(api_type='google')
translator.translate_pdf('input.pdf', 'output.pdf')

# 使用DeepSeek翻译
translator = PDFTranslator(
    api_type='deepseek',
    api_key='your-deepseek-key'
)
translator.translate_pdf('input.pdf', 'output.pdf')

# 使用OpenRouter (DeepSeek)翻译
translator = PDFTranslator(
    api_type='openrouter',
    api_key='sk-or-v1-xxxxx'
)
translator.translate_pdf('input.pdf', 'output.pdf')

# 使用智谱AI翻译
translator = PDFTranslator(
    api_type='zhipu',
    api_key='your-zhipu-key'
)
translator.translate_pdf('input.pdf', 'output.pdf')
```

## 许可证

MIT License

## 更新日志

### v2.3.0 (最新)
- ✨ 新增OpenRouter (DeepSeek) API支持
- 🎨 优化API选择界面
- 🐛 修复JSON格式问题
- 📝 添加OpenRouter使用文档
- ⚡ 改进错误处理和日志系统

### v2.2.1
- 🐛 修复SSE连接中断问题
- ⚡ 优化日志数量（减少90%）
- 🔧 改进前端重连机制

### v2.2.0
- ✨ 新增取消翻译功能
- ✨ Token统计和费用估算
- ✨ 详细翻译日志

### v2.1.0
- ✨ 多种翻译API支持
- ✨ 实时进度显示
- ✨ 翻译日志窗口

### v2.0.0
- 🎉 初始版本
- 支持Google Translate翻译
- 基本的PDF翻译功能
