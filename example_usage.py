#!/usr/bin/env python3
"""
PDF翻译工具 - 命令行使用示例

如果你想直接在Python代码中使用翻译功能，可以使用这个示例。
"""

from translator import PDFTranslator

def translate_pdf_example():
    """翻译PDF文件示例"""
    # 创建翻译器
    translator = PDFTranslator()

    # 翻译PDF
    # 参数说明：
    # - input_path: 输入PDF文件路径
    # - output_path: 输出PDF文件路径
    # - source_lang: 源语言代码 ('auto' 表示自动检测)
    # - target_lang: 目标语言代码 ('en', 'zh', 'ja', 'ko', 'fr', 'de', 'es', 'ru', 'ar')

    print("开始翻译PDF...")
    translator.translate_pdf(
        input_path='input.pdf',
        output_path='output.pdf',
        source_lang='auto',  # 自动检测源语言
        target_lang='en'      # 翻译成英文
    )
    print("翻译完成！")

# 语言代码参考
LANGUAGE_CODES = {
    '自动检测': 'auto',
    '英语': 'en',
    '中文': 'zh',
    '日语': 'ja',
    '韩语': 'ko',
    '法语': 'fr',
    '德语': 'de',
    '西班牙语': 'es',
    '俄语': 'ru',
    '阿拉伯语': 'ar',
}

if __name__ == '__main__':
    # 打印支持的语言
    print("支持的语言:")
    for name, code in LANGUAGE_CODES.items():
        print(f"  {name}: {code}")
    print()

    # 示例：如果有input.pdf文件，可以翻译它
    import os
    if os.path.exists('input.pdf'):
        translate_pdf_example()
    else:
        print("使用方法:")
        print("1. 将要翻译的PDF文件命名为 'input.pdf' 放在当前目录")
        print("2. 运行此脚本: python example_usage.py")
        print("3. 翻译结果将保存为 'output.pdf'")
