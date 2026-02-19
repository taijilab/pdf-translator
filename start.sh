#!/bin/bash

echo "================================"
echo "PDF 翻译工具 - 启动脚本"
echo "================================"
echo ""

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到Python3，请先安装Python 3.7或更高版本"
    exit 1
fi

echo "✓ Python已安装: $(python3 --version)"
echo ""

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
    echo "✓ 虚拟环境创建完成"
    echo ""
fi

# 激活虚拟环境
echo "激活虚拟环境..."
source venv/bin/activate
echo "✓ 虚拟环境已激活"
echo ""

# 安装依赖
echo "检查并安装依赖..."
pip install -q -r requirements.txt
echo "✓ 依赖安装完成"
echo ""

# 启动应用
echo "================================"
echo "启动应用..."
echo "================================"
echo ""
echo "应用将在 http://localhost:5000 启动"
echo "按 Ctrl+C 停止应用"
echo ""

python app.py
