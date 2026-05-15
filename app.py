import streamlit as st
import pandas as pd
import pdfplumber
import re
import os
import shutil
import easyocr
import numpy as np
from datetime import datetime
from PIL import Image

# --- 初始化 OCR 引擎 ---
@st.cache_resource
def load_ocr():
    # 默认加载简中和英文模型
    return easyocr.Reader(['ch_sim', 'en'])

reader = load_ocr()

# --- 配置与常量 ---
SAVE_DIR = "collected_invoices"
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

# --- 核心识别逻辑 ---

def extract_text(file):
    """从 PDF 或图片中提取文本"""
    text = ""
    file_extension = os.path.splitext(file.name)[1].lower()
    
    if file_extension == ".pdf":
        try:
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
            
            # 如果 PDF 提取不到文字（可能是扫描件 PDF），尝试 OCR
            if not text.strip():
                with pdfplumber.open(file) as pdf:
                    for page in pdf.pages:
                        img = page.to_image(resolution=300).original
                        # 转换为 numpy 数组供 easyocr 使用
                        results = reader.readtext(np.array(img))
                        text += " ".join([res[1] for res in results])
        except Exception as e:
            st.error(f"解析 PDF 出错: {e}")
    elif file_extension in [".jpg", ".jpeg", ".png", ".bmp"]:
        try:
            image = Image.open(file)
            results = reader.readtext(np.array(image))
            text = " ".join([res[1] for res in results])
        except Exception as e:
            st.error(f"解析图片出错: {e}")
    
    return text

def parse_invoice_data(text):
    """从文本中解析发票关键信息"""
    data = {
        "发票号码": "未识别",
        "公司名称": "个人/通用",
        "合计金额": 0.0,
        "开票日期": "未识别"
    }
    
    if not text:
        return data

    # 1. 识别发票号码
    no_match = re.search(r"(?:发票号码|No|NO)[:：]?\s*(\d{8,20})", text, re.I)
    if no_match:
        data["发票号码"] = no_match.group(1)
    
    # 2. 识别公司名称 (购买方)
    # 匹配规则：通常在“名称”后面，避开常见的发票标题字样
    name_patterns = [
        r"名称[:：]?\s*([^\n\d]{4,50})",
        r"购买方[:：]?\s*([^\n\d]{4,50})",
        r"客户[:：]?\s*([^\n\d]{4,50})"
    ]
    for pattern in name_patterns:
        name_match = re.search(pattern, text)
        if name_match:
            candidate = name_match.group(1).strip()
            if "发票" not in candidate and "公司" in candidate or len(candidate) > 4:
                data["公司名称"] = candidate
                break

    # 3. 识别金额
    # 优先匹配带有人民币符号或特定关键词的数字
    amount_patterns = [
        r"(?:价税合计|小写|Total|TOTAL)[^0-9¥$]*[¥￥$]?\s*([\d\.,]+)",
        r"[¥￥$]\s*([\d\.,]+)"
    ]
    for pattern in amount_patterns:
        amount_match = re.search(pattern, text, re.I)
        if amount_match:
            raw_amt = amount_match.group(1).replace(",", "")
            try:
                data["合计金额"] = float(raw_amt)
                break
            except:
                continue
    
    # 4. 识别日期
    date_match = re.search(r"(\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*日?)", text)
    if date_match:
        data["开票日期"] = re.sub(r'[\s年月日]', '-', date_match.group(1)).strip('-')
        
    return data

def organize_file(uploaded_file, company_name):
    """按公司名称自动归集文件"""
    safe_name = re.sub(r'[\\/:*?"<>|]', '', company_name)
    folder_path = os.path.join(SAVE_DIR, safe_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    save_path = os.path.join(folder_path, uploaded_file.name)
    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return save_path

# --- UI 界面 ---

st.set_page_config(page_title="智能发票归集专家", layout="wide", page_icon="🧾")

# 自定义 CSS 样式
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

st.title("🧾 智能发票归集与报销助手")
st.info("💡 提示：支持电子 PDF、扫描件 PDF 以及图片发票 (JPG/PNG)。系统将自动识别并归类。")

with st.expander("📁 上传设置与说明", expanded=True):
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded_files = st.file_uploader("支持批量上传", accept_multiple_files=True, type=["pdf", "jpg", "jpeg", "png"])
    with col2:
        st.write("**归集规则：**")
        st.write("1. 自动提取公司名作为文件夹名")
        st.write("2. 自动汇总至 Excel")
        st.write("3. 支持手动在线校对")

if uploaded_files:
    data_list = []
    
    # 使用状态栏显示处理进度
    progress_text = "正在识别发票，请稍候..."
    my_bar = st.progress(0, text=progress_text)
    
    for i, file in enumerate(uploaded_files):
        # 1. 提取文字
        raw_text = extract_text(file)
        # 2. 解析
        info = parse_invoice_data(raw_text)
        # 3. 归集
        path = organize_file(file, info["公司名称"])
        
        info["文件名"] = file.name
        info["存储路径"] = path
        data_list.append(info)
        
        my_bar.progress((i + 1) / len(uploaded_files), text=f"已完成 {i+1}/{len(uploaded_files)}")
    
    my_bar.empty()
    
    # 数据展示
    df = pd.DataFrame(data_list)
    
    st.write("### 📊 识别结果汇总")
    edited_df = st.data_editor(
        df, 
        column_config={
            "合计金额": st.column_config.NumberColumn("合计金额 (¥)", format="%.2f"),
            "存储路径": st.column_config.LinkColumn("查看文件")
        },
        use_container_width=True,
        num_rows="dynamic"
    )
    
    # 底部统计栏
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("发票总数", f"{len(edited_df)} 张")
    with c2:
        total = edited_df["合计金额"].sum()
        st.metric("总金额汇总", f"¥ {total:,.2f}")
    with c3:
        # 导出按钮
        excel_name = f"发票报销汇总_{datetime.now().strftime('%Y%m%d')}.xlsx"
        edited_df.to_excel(excel_name, index=False)
        with open(excel_name, "rb") as f:
            st.download_button("📥 导出最终 Excel", f, excel_name, "application/vnd.ms-excel", use_container_width=True)

    st.success(f"✅ 处理完毕！所有文件已按公司名称归集至 `{SAVE_DIR}` 目录下。")

# 侧边栏
with st.sidebar:
    st.header("系统管理")
    st.write("当前保存路径：", os.path.abspath(SAVE_DIR))
    if st.button("🗑️ 清空所有已归集文件", use_container_width=True):
        if os.path.exists(SAVE_DIR):
            shutil.rmtree(SAVE_DIR)
            os.makedirs(SAVE_DIR)
            st.rerun()
    st.divider()
    st.markdown("### 关于本工具")
    st.caption("由 AI 驱动的发票自动化解决方案。支持本地部署与云端运行。")
