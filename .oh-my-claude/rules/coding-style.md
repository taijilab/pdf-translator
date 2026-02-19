# Python Flask 编码规范

## 文件组织

```
pdfapp/
├── app.py              # Flask 应用入口
├── translator.py       # PDF翻译核心逻辑
├── templates/          # Jinja2 模板
├── static/             # 静态资源
│   ├── css/
│   └── js/
├── tests/              # 测试目录
└── requirements.txt    # 依赖
```

## 代码风格

### 导入顺序
```python
# 1. 标准库
import os
import json

# 2. 第三方库
from flask import Flask, request
import fitz  # PyMuPDF

# 3. 本地模块
from translator import PDFTranslator
```

### 函数命名
- 函数: `snake_case` (如 `translate_pdf`)
- 类: `PascalCase` (如 `PDFTranslator`)
- 常量: `UPPER_SNAKE_CASE` (如 `MAX_FILE_SIZE`)

### Flask 路由
```python
@app.route('/api/translate', methods=['POST'])
def translate_document():
    """翻译文档 API"""
    pass
```

### 错误处理
```python
try:
    result = process_pdf(file)
except PDFProcessError as e:
    return jsonify({'error': str(e)}), 400
except Exception as e:
    app.logger.error(f'Unexpected error: {e}')
    return jsonify({'error': '处理失败'}), 500
```

## 类型注解 (推荐)

```python
from typing import Optional
from flask import Response

def translate_page(page_num: int, target_lang: str) -> Optional[str]:
    """翻译单页"""
    pass
```

## 测试规范

```python
# tests/test_translator.py
import pytest
from translator import PDFTranslator

def test_translate_simple_text():
    translator = PDFTranslator()
    result = translator.translate("Hello", "zh-CN")
    assert result is not None
```
