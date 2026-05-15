#!/bin/bash
# 自动进入脚本所在目录
cd "$(dirname "$0")"

echo "------------------------------------------------"
echo "正在启动发票自动化管理工具..."
echo "------------------------------------------------"

# 1. 检查 Python 环境
if ! command -v python3 &> /dev/null
then
    echo "错误: 未检测到 Python3。请先安装 Python (https://www.python.org/)"
    exit
fi

# 2. 创建并激活虚拟环境 (避免污染系统环境)
if [ ! -d "venv" ]; then
    echo "正在初始化环境 (仅第一次运行需要)..."
    python3 -m venv venv
fi

source venv/bin/activate

# 3. 安装依赖库
echo "正在检查依赖库..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 4. 运行 Streamlit
echo "启动成功！正在打开浏览器..."
streamlit run app.py
