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

# 加载历史记录用于查重
def load_processed_nos():
    if os.path.exists(LOG_FILE):
        try:
            df = pd.read_csv(LOG_FILE)
            return set(df['发票号码'].astype(str).tolist())
        except:
            return set()
    return set()

# 初始化 Session State
if 'processed_nos' not in st.session_state:
    st.session_state.processed_nos = load_processed_nos()

if 'current_results' not in st.session_state:
    st.session_state.current_results = []

if 'show_warning' not in st.session_state:
    st.session_state.show_warning = False

# --- 核心识别逻辑 ---

def extract_text(file):
    text = ""
    file_extension = os.path.splitext(file.name)[1].lower()
    if file_extension == ".pdf":
        try:
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
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
    data = {
        "查重": "OK",
        "发票号码": "未识别",
        "开票日期": "未识别",
        "购买方名称": "未识别抬头",
        "购买方税号": "未识别",
        "销售方名称": "未识别商家",
        "销售方税号": "未识别",
        "金额": 0.0,
        "税额": 0.0,
        "价税合计": 0.0,
        "发票类型": "电子发票",
        "银行账号": "未识别",
        "商品内容": "未识别"
    }
    if not text: return data

    inv_nos = re.findall(r"\b\d{20}\b", text)
    if not inv_nos: inv_nos = re.findall(r"\b\d{12}\b", text)
    if inv_nos: data["发票号码"] = inv_nos[0]
    
    tax_ids = re.findall(r"\b[A-Z0-9]{18}\b", text)
    
    companies = re.findall(r"([^\n\d\s\*：:]{4,50}(?:有限公司|股份公司|工贸有限公司|中心|经营部|店|厂))", text)
    companies = [c for c in companies if "发票" not in c and "信息" not in c]

    if len(companies) >= 2:
        data["购买方名称"] = companies[0]
        data["销售方名称"] = companies[1]
    elif len(companies) == 1:
        data["购买方名称"] = companies[0]
    
    if len(tax_ids) >= 2:
        data["购买方税号"] = tax_ids[0]
        data["销售方税号"] = tax_ids[1]
    elif len(tax_ids) == 1:
        data["购买方税号"] = tax_ids[0]

    date_match = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    if date_match:
        data["开票日期"] = re.sub(r'[\s年月日]', '-', date_match.group(1)).strip('-')

    amounts = re.findall(r"[¥￥$]\s*([\d\.,]+)", text)
    if amounts:
        parsed_amts = []
        for a in amounts:
            try: parsed_amts.append(float(a.replace(",", "")))
            except: continue
        if parsed_amts:
            data["价税合计"] = max(parsed_amts)
            data["金额"] = parsed_amts[0]

    tax_match = re.search(r"税\s*额[^0-9¥￥]*[¥￥]?\s*([\d\.]+)", text)
    if tax_match:
        data["税额"] = float(tax_match.group(1))
    elif "免税" in text or "***" in text:
        data["税额"] = 0.0

    items = re.findall(r"(\*[^*]+\*[^*]+)", text)
    if items:
        data["商品内容"] = items[0].replace("*", "")

    bank_acc = re.search(r"银行账号[:：]?\s*(\d{12,25})", text)
    if bank_acc:
        data["银行账号"] = bank_acc.group(1)

    if "专用发票" in text: data["发票类型"] = "增值税专用发票"
    elif "普通发票" in text: data["发票类型"] = "增值税普通发票"

    if str(data["发票号码"]) in st.session_state.processed_nos:
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
st.set_page_config(page_title="精准发票专家 V8.0", layout="wide", page_icon="🧾")

st.markdown("""
    <style>
    div.stDownloadButton > button {
        width: 100%;
        height: 100px !important;
        font-size: 28px !important;
        font-weight: bold !important;
        background-color: #28a745 !important;
        color: white !important;
        border-radius: 15px !important;
        box-shadow: 0 8px 16px rgba(0,0,0,0.2);
        margin-top: 20px;
    }
    div.stDownloadButton > button:hover {
        background-color: #218838 !important;
        transform: scale(1.02);
    }
    .step-box {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        border-left: 5px solid #28a745;
        margin-bottom: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🧾 智能发票归集专家 V8.0")

tab_upload, tab_history = st.tabs(["📤 上传发票", "📜 历史记录管理"])

with tab_upload:
    st.markdown("""
    <div class="step-box">
    <b>使用流程：</b><br>
    1. <b>上传</b>：拖入发票文件 -> 2. <b>识别</b>：点击“开始批量识别” -> 3. <b>下载</b>：点击下方绿色大按钮下载 Excel -> 4. <b>完成</b>：点击“完成处理”
    </div>
    """, unsafe_allow_html=True)

    uploaded_files = st.file_uploader("第一步：批量上传发票 (支持多选)", accept_multiple_files=True, type=["pdf", "jpg", "jpeg", "png"])

    if uploaded_files:
        if st.button("第二步：开始批量识别内容", type="primary", use_container_width=True):
            data_list = []
            has_dup = False
            dup_details = []
            
            my_bar = st.progress(0, text="全速识别中...")
            for i, file in enumerate(uploaded_files):
                text = extract_text(file)
                info = parse_invoice_data(text)
                path, new_name = organize_file(file, info)
                
                info["上传时间"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                info["文件名"] = file.name
                info["存储路径"] = path
                data_list.append(info)
                
                if info["查重"] == "⚠️ 重复":
                    has_dup = True
                    dup_details.append(f"{file.name} (号: {info['发票号码']})")
                
                my_bar.progress((i + 1) / len(uploaded_files))
            
            st.session_state.current_results = data_list
            st.session_state.show_warning = has_dup
            if has_dup:
                st.session_state.dup_list = dup_details
            my_bar.empty()
            st.rerun()

    if st.session_state.current_results:
        if st.session_state.show_warning:
            st.error(f"🚨 注意：检测到 {len(st.session_state.dup_list)} 张重复发票！")
            with st.expander("点击查看重复清单"):
                for d in st.session_state.dup_list:
                    st.write(f"- {d}")
        
        st.write("### ✅ 识别结果汇总")
        df = pd.DataFrame(st.session_state.current_results)
        cols = ["查重", "发票号码", "开票日期", "购买方名称", "购买方税号", "销售方名称", "销售方税号", "金额", "税额", "价税合计", "商品内容"]
        st.dataframe(df[cols], use_container_width=True)
        
        # 保存到历史 (仅在识别完成后立即保存一次)
        if st.button("确认以上信息并存入数据库", use_container_width=True):
            # 更新已处理集合
            for res in st.session_state.current_results:
                if res["发票号码"] != "未识别":
                    st.session_state.processed_nos.add(str(res["发票号码"]))
            
            # 写入 CSV
            if os.path.exists(LOG_FILE):
                df.to_csv(LOG_FILE, mode='a', header=False, index=False)
            else:
                df.to_csv(LOG_FILE, index=False)
            st.success("数据已入库！现在可以下载了。")

        st.write("---")
        st.subheader("第三步：点击下方绿色大按钮导出本次结果")
        excel_name = f"发票汇总_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(excel_name, index=False)
        with open(excel_name, "rb") as f:
            st.download_button(
                label=f"📥 立即导出本次识别的 {len(df)} 张发票明细表",
                data=f,
                file_name=excel_name,
                mime="application/vnd.ms-excel"
            )
        
        st.write("---")
        if st.button("第四步：完成处理 (清空当前工作区)", use_container_width=True):
            st.session_state.current_results = []
            st.session_state.show_warning = False
            st.rerun()

with tab_history:
    st.subheader("📜 历史数据管理中心")
    if os.path.exists(LOG_FILE):
        hist_df = pd.read_csv(LOG_FILE)
        search = st.text_input("🔍 搜索历史 (公司、号码、商品)")
        if search:
            hist_df = hist_df[hist_df.apply(lambda row: row.astype(str).str.contains(search).any(), axis=1)]
        
        st.info("💡 提示：双击单元格可修改。修改后务必点击“保存修改”。")
        edited = st.data_editor(hist_df, num_rows="dynamic", use_container_width=True)
        
        c1, c2, c3 = st.columns(3)
        if c1.button("💾 保存修改", use_container_width=True):
            edited.to_csv(LOG_FILE, index=False)
            st.success("已保存！")
            st.rerun()
        if c2.button("📥 导出全量历史", use_container_width=True):
            all_name = "全量历史记录.xlsx"
            edited.to_excel(all_name, index=False)
            st.download_button("下载全量表", open(all_name, "rb"), all_name)
        if c3.button("🗑️ 彻底清空记录", use_container_width=True):
            if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
            if os.path.exists(SAVE_DIR): shutil.rmtree(SAVE_DIR)
            st.session_state.processed_nos = set()
            st.rerun()
    else:
        st.write("目前没有任何历史记录。")

with st.sidebar:
    st.header("系统概览")
    st.write(f"累计处理发票: {len(st.session_state.processed_nos)} 张")
    st.divider()
    st.caption("Accio Work 稳定版 V8.0")
