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

# 确保保存目录存在
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

# 加载历史记录
def load_history():
    if os.path.exists(LOG_FILE):
        try:
            return pd.read_csv(LOG_FILE)
        except:
            return pd.DataFrame()
    return pd.DataFrame()

# 初始化 Session State
if 'processed_nos' not in st.session_state:
    df_hist = load_history()
    if not df_hist.empty and '发票号码' in df_hist.columns:
        st.session_state.processed_nos = set(df_hist['发票号码'].astype(str).tolist())
    else:
        st.session_state.processed_nos = set()

if 'confirm_upload' not in st.session_state:
    st.session_state.confirm_upload = False

if 'pre_results' not in st.session_state:
    st.session_state.pre_results = []

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

    # 识别发票号码 (全电发票号码通常较长)
    inv_nos = re.findall(r"\b\d{20}\b", text)
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
st.set_page_config(page_title="智能发票助手 V7.5", layout="wide", page_icon="🧾")

# 自定义 CSS 放大下载按钮
st.markdown("""
    <style>
    div.stDownloadButton > button {
        width: 100%;
        height: 80px !important;
        font-size: 24px !important;
        background-color: #007bff !important;
        color: white !important;
        border-radius: 10px !important;
        border: none !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    div.stDownloadButton > button:hover {
        background-color: #0056b3 !important;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🧾 智能发票归集专家 V7.5")

tab_upload, tab_history = st.tabs(["📤 上传发票", "📜 历史记录"])

with tab_upload:
    uploaded_files = st.file_uploader("批量上传发票 (PDF/图片)", accept_multiple_files=True, type=["pdf", "jpg", "jpeg", "png"])

    if uploaded_files:
        if not st.session_state.confirm_upload:
            duplicates = []
            st.session_state.pre_results = []
            
            with st.status("正在进行查重预检...", expanded=True) as status:
                for file in uploaded_files:
                    text = extract_text(file)
                    inv_no_match = re.search(r"\b\d{20}\b", text)
                    if inv_no_match:
                        inv_no = inv_no_match.group(0)
                        if str(inv_no) in st.session_state.processed_nos:
                            duplicates.append(f"{file.name} (发票号: {inv_no})")
                    # 保存预处理结果到 Session State
                    st.session_state.pre_results.append({"name": file.name, "text": text, "buffer": file.getbuffer()})
                status.update(label="预检完成", state="complete")

            if duplicates:
                st.error(f"🚨 检测到 {len(duplicates)} 张发票已在记录中！")
                for dup in duplicates:
                    st.write(f"- {dup}")
                
                c1, c2 = st.columns(2)
                if c1.button("✅ 确定：包含重复项继续上传", use_container_width=True, type="primary"):
                    st.session_state.confirm_upload = True
                    st.rerun()
                if c2.button("❌ 取消：不上传重复项", use_container_width=True):
                    st.session_state.confirm_upload = False
                    st.session_state.pre_results = []
                    st.rerun()
            else:
                st.session_state.confirm_upload = True
                st.rerun()

        # 处理并展示结果
        if st.session_state.confirm_upload and st.session_state.pre_results:
            data_list = []
            my_bar = st.progress(0, text="正在解析数据...")
            
            for i, res in enumerate(st.session_state.pre_results):
                raw_text = res["text"]
                info = parse_invoice_data(raw_text)
                
                # 模拟 uploaded_file 对象用于保存
                class MockFile:
                    def __init__(self, name, buffer):
                        self.name = name
                        self.buffer = buffer
                    def getbuffer(self):
                        return self.buffer
                
                mock_file = MockFile(res["name"], res["buffer"])
                path, new_name = organize_file(mock_file, info)
                
                info["上传时间"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                info["文件名"] = res["name"]
                info["存储路径"] = path
                data_list.append(info)
                
                if info["发票号码"] != "未识别":
                    st.session_state.processed_nos.add(str(info["发票号码"]))
                my_bar.progress((i + 1) / len(st.session_state.pre_results))
            
            my_bar.empty()
            df = pd.DataFrame(data_list)
            
            # 保存到历史记录
            if os.path.exists(LOG_FILE):
                df.to_csv(LOG_FILE, mode='a', header=False, index=False)
            else:
                df.to_csv(LOG_FILE, index=False)

            st.write("### ✅ 处理结果汇总")
            cols = ["查重", "发票号码", "开票日期", "购买方名称", "购买方税号", "销售方名称", "销售方税号", "金额", "税额", "价税合计", "商品内容", "银行账号"]
            st.dataframe(df[cols], use_container_width=True)
            
            # 放大导出按钮
            st.write("---")
            st.subheader("🔥 汇总完成！请点击下方蓝色大按钮导出 Excel")
            excel_name = f"本次处理汇总_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            df.to_excel(excel_name, index=False)
            with open(excel_name, "rb") as f:
                st.download_button(
                    label=f"📥 立即导出本次处理的 {len(df)} 张发票汇总表",
                    data=f,
                    file_name=excel_name,
                    mime="application/vnd.ms-excel",
                    use_container_width=True
                )
            
            if st.button("完成处理 (清空当前列表)", use_container_width=True):
                st.session_state.confirm_upload = False
                st.session_state.pre_results = []
                st.rerun()

with tab_history:
    st.subheader("📜 历史上传记录管理")
    hist_df = load_history()
    if not hist_df.empty:
        search_query = st.text_input("🔍 快速搜索 (支持公司名、税号、发票号等)")
        if search_query:
            hist_df = hist_df[hist_df.apply(lambda row: row.astype(str).str.contains(search_query).any(), axis=1)]
        
        st.info("💡 提示：您可以直接在下表中双击单元格进行修改。修改后请点击下方的“保存修改”按钮。")
        edited_hist = st.data_editor(hist_df, num_rows="dynamic", use_container_width=True, key="history_editor")
        
        col_save, col_dl, col_clear = st.columns(3)
        if col_save.button("💾 保存所有修改", use_container_width=True):
            edited_hist.to_csv(LOG_FILE, index=False)
            st.success("历史记录已更新！")
            st.rerun()
            
        if col_dl.button("📥 导出历史全量 Excel", use_container_width=True):
            excel_name = f"全量发票历史_{datetime.now().strftime('%Y%m%d')}.xlsx"
            edited_hist.to_excel(excel_name, index=False)
            st.download_button("点击下载全量表", open(excel_name, "rb"), excel_name)
            
        if col_clear.button("🗑️ 清空所有记录", use_container_width=True):
            if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
            if os.path.exists(SAVE_DIR): shutil.rmtree(SAVE_DIR)
            st.session_state.processed_nos = set()
            st.rerun()
    else:
        st.write("暂无历史记录。")

with st.sidebar:
    st.header("系统状态")
    st.write(f"数据库记录: {len(st.session_state.processed_nos)} 条")
    st.divider()
    st.caption("Accio Work V7.5 | 修正增强版")
