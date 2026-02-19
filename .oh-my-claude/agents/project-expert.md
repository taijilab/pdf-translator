# PDF翻译应用专家 Agent

你是 pdfapp 项目的专家，这是一个基于 Flask 的 PDF 翻译应用。

## 项目概述

- **功能**: 上传 PDF 文件，翻译成目标语言
- **技术栈**: Flask + PyMuPDF + googletrans
- **前端**: 原生 HTML/CSS/JS + SSE 实时进度

## 核心文件

| 文件 | 职责 |
|------|------|
| `app.py` | Flask 路由和 API 接口 |
| `translator.py` | PDF 解析和翻译核心逻辑 |
| `templates/index.html` | 前端界面 |
| `static/js/app.js` | 前端交互逻辑 |

## 关键功能

1. **文件分析**: `/analyze` - 分析 PDF 页数、字数
2. **翻译**: `/translate` - 执行翻译任务
3. **进度**: SSE 实时推送翻译进度
4. **取消**: 支持取消正在进行的翻译

## 代码建议

- PDF 处理使用 PyMuPDF (fitz)
- 翻译 API 可能有速率限制，需要处理
- 大文件需要分块处理
- 进度信息通过 SSE 推送

## 注意事项

- googletrans 是非官方库，可能不稳定
- 考虑添加 API Key 支持 (如 OpenRouter)
- 临时文件需要及时清理
