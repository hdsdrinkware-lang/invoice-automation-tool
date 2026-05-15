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
        "购买方名称": "未识别",
        "销售方名称": "未识别",
        "项目名称": "未识别",
        "合计金额": 0.0,
        "开票日期": "未识别"
    }
    
    if not text:
        return data

    # 1. 识别发票号码
    no_match = re.search(r"(?:发票号码|No|NO)[:：]?\s*(\d{8,20})", text, re.I)
    if no_match:
        data["发票号码"] = no_match.group(1)
    
    # 2. 识别 购买方 和 销售方
    # 在中文发票中，购买方通常出现在文本的前半部，销售方出现在后半部
    # 我们先尝试定位“购买方”和“销售方”关键词
    
    # 购买方正则
    buyer_match = re.search(r"购买方[:：]?\s*名称[:：]?\s*([^\n\d]{4,50})", text)
    if not buyer_match:
        buyer_match = re.search(r"购买方[:：]?\s*([^\n\d]{4,50})", text)
    if buyer_match:
        data["购买方名称"] = buyer_match.group(1).strip()
    else:
        # 如果没找到显式标记，寻找第一个出现的可能的公司名
        first_name = re.search(r"名称[:：]?\s*([^\n\d]{4,50})", text)
        if first_name:
            data["购买方名称"] = first_name.group(1).strip()

    # 销售方正则
    seller_match = re.search(r"销售方[:：]?\s*名称[:：]?\s*([^\n\d]{4,50})", text)
    if not seller_match:
        # 寻找第二个出现的“名称”或出现在文本后半段的名称
        names = re.findall(r"名称[:：]?\s*([^\n\d]{4,50})", text)
        if len(names) >= 2:
            data["销售方名称"] = names[-1].strip()
        else:
            seller_match = re.search(r"销售方[:：]?\s*([^\n\d]{4,50})", text)
            if seller_match:
                data["销售方名称"] = seller_match.group(1).strip()

    # 3. 识别项目名称 (货物或服务名称)
    # 寻找表格表头后的内容
    item_match = re.search(r"(?:项目名称|货物或应税劳务、服务名称)[:：]?\s*([^\n]*)", text)
    if not item_match:
        # 尝试匹配一些常见的项目后缀
        item_match = re.search(r"([^\n*]*(?:服务费|材料|软件|咨询|费|劳务|技术)[^\n]*)", text)
    
    if item_match:
        data["项目名称"] = item_match.group(1).strip().replace("*", "")

    # 4. 识别金额
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
    
    # 5. 识别日期
    date_match = re.search(r"(\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*日?)", text)
    if date_match:
        data["开票日期"] = re.sub(r'[\s年月日]', '-', date_match.group(1)).strip('-')
        
    return data

def organize_file(uploaded_file, buyer_name):
    """按购买方名称自动归集文件"""
    safe_name = re.sub(r'[\\/:*?"<>|]', '', buyer_name)
    folder_path = os.path.join(SAVE_DIR, safe_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    save_path = os.path.join(folder_path, uploaded_file.name)
    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return save_path

# --- UI 界面 ---

st.set_page_config(page_title="智能发票归集专家", layout="wide", page_icon="🧾")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

st.title("🧾 智能发票归集与报销助手 V3.0")
st.info("💡 已升级：支持购买方、销售方、项目名称的精准拆分识别。")

with st.expander("📁 上传设置与说明", expanded=True):
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded_files = st.file_uploader("支持批量上传 (PDF/图片)", accept_multiple_files=True, type=["pdf", "jpg", "jpeg", "png"])
    with col2:
        st.write("**识别字段：**")
        st.write("- 购买方 (抬头)")
        st.write("- 销售方 (商家)")
        st.write("- 项目名称 (明细)")
        st.write("- 金额 & 日期")

if uploaded_files:
    data_list = []
    
    progress_text = "正在深度解析发票内容..."
    my_bar = st.progress(0, text=progress_text)
    
    for i, file in enumerate(uploaded_files):
        raw_text = extract_text(file)
        info = parse_invoice_data(raw_text)
        
        # 自动归集 (以购买方为文件夹名)
        path = organize_file(file, info["购买方名称"])
        
        info["文件名"] = file.name
        info["存储路径"] = path
        data_list.append(info)
        
        my_bar.progress((i + 1) / len(uploaded_files), text=f"已完成 {i+1}/{len(uploaded_files)}")
    
    my_bar.empty()
    
    df = pd.DataFrame(data_list)
    
    st.write("### 📊 发票详细信息清单")
    
    # 重新排列列顺序，让购买方、销售方、项目名称更醒目
    column_order = ["发票号码", "购买方名称", "销售方名称", "项目名称", "合计金额", "开票日期", "文件名", "存储路径"]
    df = df[column_order]

    edited_df = st.data_editor(
        df, 
        column_config={
            "购买方名称": st.column_config.TextColumn("购买方 (抬头)"),
            "销售方名称": st.column_config.TextColumn("销售方 (商家)"),
            "项目名称": st.column_config.TextColumn("购进项目/内容"),
            "合计金额": st.column_config.NumberColumn("合计金额 (¥)", format="%.2f"),
            "存储路径": st.column_config.LinkColumn("文件位置")
        },
        use_container_width=True,
        num_rows="dynamic"
    )
    
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("处理数量", f"{len(edited_df)} 张")
    with c2:
        total = edited_df["合计金额"].sum()
        st.metric("报销金额总计", f"¥ {total:,.2f}")
    with c3:
        excel_name = f"发票报销明细表_{datetime.now().strftime('%Y%m%d')}.xlsx"
        edited_df.to_excel(excel_name, index=False)
        with open(excel_name, "rb") as f:
            st.download_button("📥 导出报销明细 Excel", f, excel_name, "application/vnd.ms-excel", use_container_width=True)

    st.success(f"✅ 文件已按“购买方抬头”自动整理至 `{SAVE_DIR}` 目录下。")

with st.sidebar:
    st.header("系统管理")
    if st.button("🗑️ 清空所有已归集文件", use_container_width=True):
        if os.path.exists(SAVE_DIR):
            shutil.rmtree(SAVE_DIR)
            os.makedirs(SAVE_DIR)
            st.rerun()
    st.divider()
    st.caption("Accio Work 自动生成 | V3.0")
