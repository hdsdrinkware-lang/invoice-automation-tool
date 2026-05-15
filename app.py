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
                    # 获取原始文本
                    text += page.extract_text() or ""
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
    """
    极致精准解析发票关键信息
    针对全电发票布局：抬头标签在前，数值在后的结构进行逻辑重构
    """
    data = {
        "查重": "OK",
        "发票号码": "未识别",
        "开票日期": "未识别",
        "购买方名称": "未识别",
        "购买方税号": "未识别",
        "销售方名称": "未识别",
        "销售方税号": "未识别",
        "金额": 0.0,
        "税额": 0.0,
        "价税合计": 0.0,
        "发票类型": "电子发票",
        "银行账号": "未识别",
        "商品内容": "未识别"
    }
    
    if not text: return data

    # 1. 提取所有可能的长数字 (发票号码、税号、银行账号)
    # 发票号码通常是 20 位
    inv_nos = re.findall(r"\b\d{20}\b", text)
    if inv_nos: data["发票号码"] = inv_nos[0]
    
    # 税号通常是 18 位大写字母+数字
    tax_ids = re.findall(r"\b[A-Z0-9]{18}\b", text)
    
    # 公司名称通常是 4-50 个汉字，包含“公司”或“厂”等后缀
    # 排除掉发票标题中包含的关键词
    companies = re.findall(r"([^\n\d\s\*：:]{4,50}(?:有限公司|股份公司|工贸有限公司|中心|经营部|店|厂))", text)
    # 过滤掉干扰项
    companies = [c for c in companies if "发票" not in c and "信息" not in c]

    # 2. 针对全电发票的逻辑：
    # 购买方名称、购买方税号、销售方名称、销售方税号 通常按照顺序成组出现
    # 结合你提供的样本，它们往往在发票号码和日期后面
    if len(companies) >= 2:
        data["购买方名称"] = companies[0]
        data["销售方名称"] = companies[1]
    
    if len(tax_ids) >= 2:
        # 匹配购买方税号 (通常跟在购买方名称后面或成对出现)
        data["购买方税号"] = tax_ids[0]
        data["销售方税号"] = tax_ids[1]

    # 3. 日期识别
    date_match = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    if date_match:
        data["开票日期"] = re.sub(r'[\s年月日]', '-', date_match.group(1)).strip('-')

    # 4. 金额识别 (价税合计、合计、税额)
    # 价税合计 (小写) ¥1101.00
    amounts = re.findall(r"[¥￥$]\s*([\d\.,]+)", text)
    if amounts:
        # 最大的通常是价税合计
        parsed_amts = []
        for a in amounts:
            try: parsed_amts.append(float(a.replace(",", "")))
            except: continue
        if parsed_amts:
            data["价税合计"] = max(parsed_amts)
            # 在全电发票中，合计金额(不含税)通常也是同一个值(如果免税)
            data["金额"] = parsed_amts[0] # 第一个通常是合计金额

    # 专门提取税额
    tax_match = re.search(r"税\s*额[^0-9¥￥]*[¥￥]?\s*([\d\.]+)", text)
    if tax_match:
        data["税额"] = float(tax_match.group(1))
    elif "免税" in text or "***" in text:
        data["税额"] = 0.0

    # 5. 商品内容识别
    # 寻找星号包裹的内容，如 *经纪代理服务*
    items = re.findall(r"(\*[^*]+\*[^*]+)", text)
    if items:
        data["商品内容"] = items[0].replace("*", "")

    # 6. 银行账号识别
    bank_acc = re.search(r"银行账号[:：]?\s*(\d{12,25})", text)
    if bank_acc:
        data["银行账号"] = bank_acc.group(1)

    # 7. 发票类型
    if "专用发票" in text: data["发票类型"] = "增值税专用发票"
    elif "普通发票" in text: data["发票类型"] = "增值税普通发票"

    # 查重逻辑
    if data["发票号码"] in st.session_state.processed_nos:
        data["查重"] = "⚠️ 重复"
        
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
st.set_page_config(page_title="精准发票助手 V6.0", layout="wide", page_icon="🚀")

st.title("🚀 精准发票自动识别与归集 V6.0")
st.markdown("针对全电发票深度优化。识别字段：**发票号码、日期、购买/销售方名称及税号、金额、税额、银行账号、商品明细**。")

uploaded_files = st.file_uploader("批量上传发票 (PDF/图片)", accept_multiple_files=True, type=["pdf", "jpg", "jpeg", "png"])

if uploaded_files:
    data_list = []
    my_bar = st.progress(0, text="正在进行高精度字段匹配...")
    
    for i, file in enumerate(uploaded_files):
        raw_text = extract_text(file)
        info = parse_invoice_data(raw_text)
        
        path, new_name = organize_file(file, info)
        info["文件名"] = file.name
        info["存储路径"] = path
        data_list.append(info)
        
        if info["发票号码"] != "未识别":
            st.session_state.processed_nos.add(info["发票号码"])
        my_bar.progress((i + 1) / len(uploaded_files))
    
    my_bar.empty()
    df = pd.DataFrame(data_list)
    
    # 持久化记录
    df[["发票号码", "购买方名称", "价税合计", "开票日期"]].to_csv(LOG_FILE, mode='a', header=not os.path.exists(LOG_FILE), index=False)

    st.write("### ✅ 识别明细表")
    
    # 按照用户要求的顺序展示
    cols = ["查重", "发票号码", "开票日期", "购买方名称", "购买方税号", "销售方名称", "销售方税号", "金额", "税额", "价税合计", "发票类型", "银行账号", "商品内容"]
    df_display = df[cols]

    st.data_editor(
        df_display,
        column_config={
            "金额": st.column_config.NumberColumn("金额(不含税)", format="¥ %.2f"),
            "税额": st.column_config.NumberColumn("税额", format="¥ %.2f"),
            "价税合计": st.column_config.NumberColumn("价税合计(总额)", format="¥ %.2f"),
        },
        use_container_width=True
    )

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("处理总数", f"{len(df)} 张")
    with c2: st.metric("价税合计总额", f"¥ {df['价税合计'].sum():,.2f}")
    with c3:
        excel_name = f"发票报销明细表_{datetime.now().strftime('%Y%m%d')}.xlsx"
        df.to_excel(excel_name, index=False)
        st.download_button("📥 导出高精度 Excel 报表", open(excel_name, "rb"), excel_name, use_container_width=True)

    st.success(f"处理完成！文件已按抬头归类。")

with st.sidebar:
    st.header("管理")
    if st.button("🗑️ 清空数据与记录"):
        if os.path.exists(SAVE_DIR): shutil.rmtree(SAVE_DIR)
        if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
        st.session_state.processed_nos = set()
        st.rerun()
