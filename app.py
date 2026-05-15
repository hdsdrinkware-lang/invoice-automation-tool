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
    return easyocr.Reader(['ch_sim', 'en'])

reader = load_ocr()

# --- 配置与常量 ---
SAVE_DIR = "collected_invoices"
LOG_FILE = "processed_log.csv"

if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

if 'processed_nos' not in st.session_state:
    if os.path.exists(LOG_FILE):
        try:
            log_df = pd.read_csv(LOG_FILE)
            st.session_state.processed_nos = set(log_df['发票号码'].astype(str).tolist())
        except:
            st.session_state.processed_nos = set()
    else:
        st.session_state.processed_nos = set()

# --- 核心识别逻辑 ---

def extract_text(file):
    text = ""
    file_extension = os.path.splitext(file.name)[1].lower()
    if file_extension == ".pdf":
        try:
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    # 针对全电发票，有时表格内容和表头会混在一起，提取时保留布局
                    text += page.extract_text(layout=True) or ""
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
    """深度解析发票关键信息 (针对全电发票和普通发票优化)"""
    data = {
        "查重": "OK",
        "发票号码": "未识别",
        "发票类型": "增值税发票",
        "开票日期": "未识别",
        "购买方名称": "未识别",
        "购买方税号": "未识别",
        "销售方名称": "未识别",
        "销售方税号": "未识别",
        "销售方银行账号": "未识别",
        "商品内容": "未识别",
        "不含税金额": 0.0,
        "税额": 0.0,
        "价税合计": 0.0
    }
    
    if not text: return data

    # 1. 发票类型
    if "电子发票" in text: data["发票类型"] = "电子发票"
    if "专用发票" in text: data["发票类型"] = "增值税专用发票"
    elif "普通发票" in text: data["发票类型"] = "增值税普通发票"

    # 2. 发票号码 (全电发票号码通常较长)
    # 匹配规律：出现在“发票号码：”后，或者是一串 20 位的数字
    no_match = re.search(r"发票号码[:：]?\s*(\d{20}|\d{12}|\d{10}|\d{8})", text)
    if not no_match:
        # 兜底：直接找独立的长数字
        nos = re.findall(r"\b\d{20}\b", text)
        if nos: data["发票号码"] = nos[0]
    else:
        data["发票号码"] = no_match.group(1)
        
    if data["发票号码"] in st.session_state.processed_nos:
        data["查重"] = "⚠️ 重复"

    # 3. 日期
    date_match = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    if date_match:
        data["开票日期"] = re.sub(r'[\s年月日]', '-', date_match.group(1)).strip('-')

    # 4. 购买方 & 销售方 信息
    # 全电发票特征：名称和税号通常成对出现
    names = re.findall(r"名称[:：]?\s*([^\n\d*]{4,50})", text)
    tax_ids = re.findall(r"[A-Z0-9]{15,20}", text)
    
    # 尝试根据上下文区分
    if "购 买 方" in text or "购买方" in text:
        # 寻找“购买方”后的第一个名称和税号
        buyer_section = text.split("购买方")[1] if "购买方" in text else text
        buyer_name = re.search(r"名称[:：]?\s*([^\n\d*]{4,50})", buyer_section)
        if buyer_name: data["购买方名称"] = buyer_name.group(1).strip()
        buyer_tax = re.search(r"代码[:：/纳税人识别号]?\s*([A-Z0-9]{18})", buyer_section)
        if buyer_tax: data["购买方税号"] = buyer_tax.group(1).strip()

    if "销 售 方" in text or "销售方" in text:
        seller_section = text.split("销售方")[1] if "销售方" in text else text
        seller_name = re.search(r"名称[:：]?\s*([^\n\d*]{4,50})", seller_section)
        if seller_name: data["销售方名称"] = seller_name.group(1).strip()
        seller_tax = re.search(r"代码[:：/纳税人识别号]?\s*([A-Z0-9]{18})", seller_section)
        if seller_tax: data["销售方税号"] = seller_tax.group(1).strip()
    
    # 兜底：如果没定位到 section，用提取到的列表
    if data["购买方名称"] == "未识别" and len(names) > 0: data["购买方名称"] = names[0].strip()
    if data["销售方名称"] == "未识别" and len(names) > 1: data["销售方名称"] = names[1].strip()
    if data["购买方税号"] == "未识别" and len(tax_ids) > 0: data["购买方税号"] = tax_ids[0]
    if data["销售方税号"] == "未识别" and len(tax_ids) > 1: data["销售方税号"] = tax_ids[1]

    # 5. 银行账号 (销售方)
    bank_match = re.search(r"银行账号[:：]?\s*(\d{10,25})", text)
    if bank_match:
        data["销售方银行账号"] = bank_match.group(1)

    # 6. 商品内容
    # 提取项目名称列下的内容，通常在“项目名称”和“规格型号”之间
    item_match = re.search(r"(?:\*([^*]+)\*([^*]+))", text)
    if item_match:
        data["商品内容"] = f"*{item_match.group(1)}*{item_match.group(2)}".strip()
    else:
        # 兜底：找常见的服务费等字样
        items = re.findall(r"(\*[^*]+\*[^*]+)", text)
        if items: data["商品内容"] = items[0]

    # 7. 金额、税额、合计
    # 价税合计 (小写)
    total_match = re.search(r"(?:价税合计（小写）|小写)[^0-9¥￥]*[¥￥]?\s*([\d\.]+)", text)
    if total_match:
        data["价税合计"] = float(total_match.group(1))

    # 税额
    tax_match = re.search(r"税\s*额[^0-9¥￥]*[¥￥]?\s*([\d\.]+)", text)
    if tax_match:
        data["税额"] = float(tax_match.group(1))
    elif "免税" in text or "***" in text:
        data["税额"] = 0.0

    # 不含税金额 (合计金额)
    subtotal_match = re.search(r"合\s*计[^0-9¥￥]*[¥￥]?\s*([\d\.]+)", text)
    if subtotal_match:
        data["不含税金额"] = float(subtotal_match.group(1))
    else:
        data["不含税金额"] = data["价税合计"] - data["税额"]

    return data

def organize_file(uploaded_file, info):
    buyer = re.sub(r'[\\/:*?"<>|]', '', info["购买方名称"])
    date = info["开票日期"] if info["开票日期"] != "未识别" else "0000-00-00"
    amt = str(info["价税合计"])
    inv_no = info["发票号码"]
    
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    new_filename = f"{date}_{buyer}_{amt}_{inv_no}{ext}"
    
    folder_path = os.path.join(SAVE_DIR, buyer)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    save_path = os.path.join(folder_path, new_filename)
    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return save_path, new_filename

# --- UI ---
st.set_page_config(page_title="精准发票助手 V5.0", layout="wide", page_icon="🎯")

st.title("🎯 精准发票自动识别与归集 V5.0")
st.markdown("针对全电发票及复杂报销场景深度优化，支持识别：**发票号码、税号、税额、银行账号、商品明细**等。")

uploaded_files = st.file_uploader("批量上传发票 (PDF/图片)", accept_multiple_files=True, type=["pdf", "jpg", "jpeg", "png"])

if uploaded_files:
    data_list = []
    my_bar = st.progress(0, text="正在深度扫描发票字段...")
    
    for i, file in enumerate(uploaded_files):
        raw_text = extract_text(file)
        info = parse_invoice_data(raw_text)
        
        path, new_name = organize_file(file, info)
        info["文件名"] = file.name
        info["新文件名"] = new_name
        data_list.append(info)
        
        if info["发票号码"] != "未识别":
            st.session_state.processed_nos.add(info["发票号码"])
        my_bar.progress((i + 1) / len(uploaded_files))
    
    my_bar.empty()
    df = pd.DataFrame(data_list)
    
    # 持久化记录
    df[["发票号码", "购买方名称", "价税合计", "开票日期"]].to_csv(LOG_FILE, mode='a', header=not os.path.exists(LOG_FILE), index=False)

    st.write("### 🔍 识别结果详细预览")
    
    # 调整列顺序
    cols = ["查重", "发票号码", "发票类型", "开票日期", "购买方名称", "购买方税号", "销售方名称", "销售方税号", "商品内容", "不含税金额", "税额", "价税合计", "销售方银行账号", "新文件名"]
    df_display = df[cols]

    st.data_editor(
        df_display,
        column_config={
            "不含税金额": st.column_config.NumberColumn("金额(不含税)", format="¥ %.2f"),
            "税额": st.column_config.NumberColumn("税额", format="¥ %.2f"),
            "价税合计": st.column_config.NumberColumn("价税合计", format="¥ %.2f"),
            "查重": st.column_config.TextColumn("状态")
        },
        use_container_width=True
    )

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("处理张数", f"{len(df)} 张")
    with c2: st.metric("总金额 (含税)", f"¥ {df['价税合计'].sum():,.2f}")
    with c3:
        excel_name = f"发票明细汇总_{datetime.now().strftime('%Y%m%d')}.xlsx"
        df.to_excel(excel_name, index=False)
        st.download_button("📥 导出报销专用 Excel", open(excel_name, "rb"), excel_name, use_container_width=True)

    st.success(f"已完成！文件已按“购买方名称”归类至 `{SAVE_DIR}`。")

with st.sidebar:
    st.header("系统工具")
    if st.button("🗑️ 清空所有归集与记录"):
        if os.path.exists(SAVE_DIR): shutil.rmtree(SAVE_DIR)
        if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
        st.session_state.processed_nos = set()
        st.rerun()
    st.divider()
    st.caption("Accio Work 深度定制版 V5.0")
