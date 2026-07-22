from __future__ import annotations

import hmac
import html
from pathlib import Path

import streamlit as st

from rag_assistant.config import settings
from rag_assistant.service import AssistantService
from rag_assistant.report_extractor import (
    export_reports_xlsx,
    pdf_page_count,
    render_pdf_page,
)
from rag_assistant.report_jobs import ReportJobManager


st.set_page_config(page_title="Atlas · рабочая база знаний", page_icon="◈", layout="wide")
st.markdown(
    """
    <style>
    :root {--ink:#152033; --muted:#68758a; --line:#e4e9f0; --blue:#356df3; --blue2:#6d5dfc; --paper:#f5f7fb;}
    .stApp {background: radial-gradient(circle at 80% -10%, #e8eeff 0, transparent 28rem), var(--paper); color:var(--ink);}
    .block-container {max-width: 1380px; padding: 2rem 2.4rem 4rem;}
    header[data-testid="stHeader"], #MainMenu, footer, [data-testid="stToolbar"] {display:none !important;}
    [data-testid="stSidebar"] {background:linear-gradient(165deg,#101b31 0%,#172743 58%,#1c3155 100%); border-right:0;}
    [data-testid="stSidebar"] * {color:#eef3ff;}
    [data-testid="stSidebar"] [data-baseweb="select"] > div {background:#213757;border-color:#385174;}
    [data-testid="stSidebar"] hr {border-color:#314663;}
    [data-testid="stSidebar"] .stButton>button {background:#ffffff08;color:#eef3ff;border-color:#526783;}
    [data-testid="stSidebar"] .stButton>button:hover {background:#ffffff14;border-color:#7990af;}
    [data-testid="stSidebar"] .stButton>button[kind="primary"] {background:linear-gradient(135deg,var(--blue),var(--blue2));border:0;}
    .brand {padding:.25rem 0 1.4rem;}
    .brand-mark {display:inline-grid;place-items:center;width:2.3rem;height:2.3rem;border-radius:.75rem;background:linear-gradient(135deg,#6d8cff,#8068ff);font-weight:800;margin-right:.55rem;box-shadow:0 8px 25px #0a102088;}
    .brand-name {font-size:1.2rem;font-weight:750;letter-spacing:.01em;vertical-align:middle;}
    .brand-sub {color:#aebbd0!important;font-size:.78rem;margin:.5rem 0 0;letter-spacing:.04em;text-transform:uppercase;}
    .hero {position:relative;overflow:hidden;background:linear-gradient(120deg,#172947 0%,#244a84 65%,#5368dd 100%);border-radius:1.4rem;padding:2rem 2.2rem;margin:0 0 1.5rem;color:white;box-shadow:0 22px 50px #243f711f;}
    .hero:after {content:"";position:absolute;width:18rem;height:18rem;border-radius:50%;right:-5rem;top:-9rem;background:#ffffff12;border:1px solid #ffffff20;}
    .hero-kicker {font-size:.72rem;letter-spacing:.18em;text-transform:uppercase;color:#b8ceff;font-weight:700;}
    .hero h1 {font-size:2rem;line-height:1.12;margin:.45rem 0 .65rem;color:white;}
    .hero p {max-width:47rem;color:#dbe6ff;margin:0;font-size:.98rem;}
    .hero-stats {display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1.25rem;}
    .hero-pill {background:#ffffff13;border:1px solid #ffffff22;border-radius:999px;padding:.42rem .72rem;color:#edf3ff;font-size:.8rem;backdrop-filter:blur(6px);}
    .section-title {font-size:1.1rem;font-weight:750;margin:.35rem 0 .2rem;color:var(--ink);}
    .section-copy {color:var(--muted);font-size:.9rem;margin-bottom:1rem;}
    div[data-testid="stTabs"] button[role="tab"] {height:2.8rem;border-radius:.8rem;padding:0 1.15rem;color:#647087;font-weight:650;}
    div[data-testid="stTabs"] button[aria-selected="true"] {background:white;color:#274fbb;box-shadow:0 5px 18px #20345a12;}
    div[data-testid="stTabs"] [data-baseweb="tab-highlight"] {display:none;}
    div[data-testid="stTabs"] [data-baseweb="tab-border"] {display:none;}
    [data-testid="stVerticalBlockBorderWrapper"] {background:#ffffff;border-color:var(--line)!important;border-radius:1rem!important;box-shadow:0 8px 25px #172b4d0a;}
    [data-testid="stFileUploader"] section {background:#f8faff;border:1px dashed #aebde0;border-radius:1rem;padding:1.2rem;}
    .stButton>button {border-radius:.75rem;font-weight:650;border-color:#d8e0ed;min-height:2.55rem;}
    .stButton>button[kind="primary"] {background:linear-gradient(135deg,var(--blue),var(--blue2));border:0;box-shadow:0 8px 20px #476de638;}
    [data-testid="stChatMessage"] {background:white;border:1px solid var(--line);border-radius:1rem;margin:.65rem 0;padding:.4rem .7rem;box-shadow:0 5px 18px #182a4808;}
    [data-testid="stChatInput"] {border-radius:1rem;box-shadow:0 10px 28px #172b4d1a;}
    [data-testid="stMetric"] {background:white;border:1px solid var(--line);padding:1rem;border-radius:1rem;box-shadow:0 6px 20px #172b4d0a;}
    [data-testid="stMetricValue"] {font-size:1.65rem;color:#1b3562;}
    .source-card {background:#f6f8ff;border:1px solid #e1e7fa;border-left:3px solid #5975ee;border-radius:.65rem;padding:.65rem .8rem;margin:.6rem 0;}
    .muted {color:#77849a;font-size:.84rem;}
    .feature-note {background:linear-gradient(120deg,#edf4ff,#f2efff);border:1px solid #dce5fa;border-radius:1rem;padding:1rem 1.1rem;color:#41516c;font-size:.88rem;margin:1rem 0;}
    .login-card {max-width:28rem;margin:12vh auto 0;background:white;border:1px solid var(--line);border-radius:1.3rem;padding:2rem;box-shadow:0 25px 70px #20345624;text-align:center;}

    /* Graphite chat theme */
    :root {--ink:#ececec;--muted:#a7a7a7;--line:#3a3a3a;--paper:#212121;--panel:#2b2b2b;--panel2:#303030;--accent:#d4d4d4;}
    .stApp {background:#212121;color:#ececec;}
    .block-container {max-width:1120px;padding:1.3rem 2.2rem 5rem;}
    [data-testid="stSidebar"] {background:#171717;border-right:1px solid #2b2b2b;}
    [data-testid="stSidebar"] * {color:#dedede;}
    [data-testid="stSidebar"] [data-baseweb="select"] > div {background:#242424;border-color:#3c3c3c;}
    [data-testid="stSidebar"] .stButton>button {background:transparent;color:#d8d8d8;border-color:#3b3b3b;}
    [data-testid="stSidebar"] .stButton>button:hover {background:#262626;border-color:#505050;}
    [data-testid="stSidebar"] .stButton>button[kind="primary"] {background:#f0f0f0;color:#181818;border:0;box-shadow:none;}
    [data-testid="stSidebar"] .stButton>button[kind="primary"] * {color:#181818!important;}
    .brand {padding:.15rem 0 1rem;}
    .brand-mark {background:#ececec;color:#171717;box-shadow:none;border-radius:.65rem;}
    .brand-sub {color:#777!important;}
    .chat-head {display:flex;align-items:center;gap:.8rem;border-bottom:1px solid #343434;padding:.35rem .1rem 1rem;margin-bottom:1rem;}
    .assistant-orb {display:grid;place-items:center;width:2.35rem;height:2.35rem;border-radius:50%;background:#efefef;color:#171717;font-weight:800;}
    .chat-head h1 {font-size:1.02rem;line-height:1.2;margin:0;color:#f1f1f1;font-weight:680;}
    .chat-head p {font-size:.78rem;margin:.2rem 0 0;color:#8f8f8f;}
    .model-chip {margin-left:auto;padding:.32rem .58rem;background:#2b2b2b;border:1px solid #3d3d3d;border-radius:.55rem;color:#aaa;font-size:.72rem;}
    div[data-testid="stTabs"] button[role="tab"] {height:2.55rem;border-radius:.55rem;color:#999;font-weight:560;background:transparent;}
    div[data-testid="stTabs"] button[aria-selected="true"] {background:#2f2f2f;color:#eee;box-shadow:none;}
    [data-testid="stChatMessage"] {max-width:850px;background:transparent;border:0;border-radius:0;border-bottom:1px solid #2c2c2c;margin:0 auto;padding:1rem .15rem;box-shadow:none;}
    [data-testid="stChatMessage"] [data-testid="stAvatarIcon-assistant"] {background:#ececec;color:#181818;}
    [data-testid="stChatInput"] {max-width:850px;margin-left:auto;margin-right:auto;background:#303030;border:1px solid #444;border-radius:1.15rem;box-shadow:0 12px 35px #0005;}
    [data-testid="stChatInput"] textarea {color:#eee;}
    [data-testid="stVerticalBlockBorderWrapper"] {background:#292929;border-color:#3a3a3a!important;border-radius:.8rem!important;box-shadow:none;}
    [data-testid="stFileUploader"] section {background:#272727;border:1px dashed #555;border-radius:.8rem;}
    [data-testid="stMetric"] {background:#292929;border:1px solid #393939;box-shadow:none;}
    [data-testid="stMetricValue"] {color:#f0f0f0;}
    .stButton>button {border-radius:.65rem;background:#303030;color:#e7e7e7;border-color:#454545;box-shadow:none;}
    .stButton>button[kind="primary"] {background:#ececec;color:#171717;border:0;box-shadow:none;}
    .source-card {background:#292929;border:1px solid #3a3a3a;border-left:3px solid #929292;border-radius:.55rem;color:#ddd;}
    .muted {color:#989898;}
    .feature-note {background:#282828;border:1px solid #3b3b3b;color:#aaa;border-radius:.7rem;}
    .section-title {color:#ededed;}
    .section-copy {color:#999;}
    .empty-state {text-align:center;padding:8vh 1rem 4vh;color:#aaa;}
    .empty-logo {display:grid;place-items:center;margin:0 auto 1.2rem;width:3.2rem;height:3.2rem;border-radius:50%;background:#ececec;color:#181818;font-weight:800;font-size:1.25rem;}
    .empty-state h2 {color:#ededed;font-size:1.55rem;margin:0 0 .55rem;}
    .empty-state p {max-width:34rem;margin:auto;color:#8e8e8e;}
    .login-card {background:#242424;border-color:#3a3a3a;color:#eee;box-shadow:0 25px 70px #0008;}

    /* Professional workspace theme */
    :root {--ink:#f1f3f5;--muted:#9299a6;--line:#2b3038;--paper:#15171b;--panel:#1b1e24;--panel2:#20242b;--accent:#8fa8ff;}
    html, body, [class*="st-"] {font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
    .stApp {background:#15171b;color:var(--ink);}
    .block-container {max-width:1180px;padding:1.15rem 2.4rem 5.5rem;}
    header[data-testid="stHeader"] {display:block!important;height:3.25rem;background:transparent!important;pointer-events:none;}
    header[data-testid="stHeader"] button,
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapseButton"] {pointer-events:auto!important;}
    #MainMenu, footer, [data-testid="stToolbar"] {display:none!important;}
    [data-testid="stToolbar"] {display:flex!important;background:transparent!important;pointer-events:none!important;}
    [data-testid="stToolbar"] [data-testid="stExpandSidebarButton"] {pointer-events:auto!important;}
    [data-testid="stAppDeployButton"] {display:none!important;}
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="stExpandSidebarButton"] {
        display:flex!important;position:fixed!important;left:.8rem!important;top:.7rem!important;z-index:100000!important;
        visibility:visible!important;opacity:1!important;
    }
    [data-testid="stSidebar"][aria-expanded="true"] [data-testid="stSidebarCollapseButton"] {
        display:flex!important;position:absolute!important;right:.65rem!important;top:.65rem!important;z-index:100000!important;
        visibility:visible!important;opacity:1!important;
    }
    [data-testid="stSidebarCollapsedControl"] button,
    [data-testid="stExpandSidebarButton"] button,
    [data-testid="stSidebar"][aria-expanded="true"] [data-testid="stSidebarCollapseButton"] button {
        display:flex!important;visibility:visible!important;opacity:1!important;
        width:2.35rem!important;height:2.35rem!important;border:1px solid #343a44!important;
        border-radius:.55rem!important;background:#1b1e24!important;color:#d9dde4!important;
        box-shadow:0 8px 24px #0005!important;
    }
    [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
    [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {display:none!important;}
    [data-testid="stSidebarCollapseButton"] button::before {content:"‹";font-size:1.55rem;line-height:1;color:#d9dde4;}
    [data-testid="stExpandSidebarButton"]::before {content:"›";font-size:1.55rem;line-height:1;color:#d9dde4;}
    [data-testid="stSidebar"] {background:#0f1115;border-right:1px solid #272b32;}
    [data-testid="stSidebar"] > div:first-child {padding-top:1rem;}
    [data-testid="stSidebar"] [data-baseweb="select"] > div {background:#191c22;border-color:#30353e;}
    [data-testid="stSidebar"] .stButton>button {border-radius:.5rem;border-color:#30353e;background:#171a20;font-weight:560;}
    [data-testid="stSidebar"] .stButton>button:hover {background:#20242b;border-color:#444b57;}
    [data-testid="stSidebar"] .stButton>button[kind="primary"] {background:#e7eaf0;color:#111318;}
    .brand {padding:.1rem 0 1.15rem;border-bottom:1px solid #242830;margin-bottom:.9rem;}
    .brand-mark {width:2rem;height:2rem;border-radius:.45rem;background:#e7eaf0;color:#111318;font-size:.92rem;box-shadow:none;margin-right:.65rem;}
    .brand-name {font-size:1.05rem;font-weight:680;letter-spacing:.01em;}
    .brand-sub {font-size:.68rem;letter-spacing:.12em;color:#747c89!important;margin-top:.55rem;}
    .chat-head {padding:.35rem 0 .9rem;margin-bottom:.8rem;border-bottom:1px solid #2a2e35;}
    .assistant-orb {width:2.1rem;height:2.1rem;border-radius:.45rem;background:#e7eaf0;color:#111318;font-size:.88rem;}
    .chat-head h1 {font-size:1rem;font-weight:650;}
    .chat-head p {color:#89919e;font-size:.76rem;}
    .model-chip {background:#1b1e24;border-color:#30353e;border-radius:.4rem;color:#939ba7;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;}
    div[data-testid="stSegmentedControl"] > div {background:#1a1d22;border:1px solid #292e36;border-radius:.65rem;padding:.2rem;}
    div[data-testid="stSegmentedControl"] button {border-radius:.45rem!important;font-weight:550!important;}
    [data-testid="stChatMessage"] {max-width:880px;border-bottom:1px solid #272b32;padding:1.1rem .2rem;}
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {background:#181b20;}
    [data-testid="stChatInput"] {max-width:880px;background:#20242a;border:1px solid #3a404b;border-radius:.85rem;box-shadow:0 14px 38px #0007;}
    [data-testid="stChatInput"]:focus-within {border-color:#667aa8;box-shadow:0 14px 38px #0007,0 0 0 1px #667aa855;}
    [data-testid="stVerticalBlockBorderWrapper"], [data-testid="stMetric"] {background:#1a1d22;border-color:#2b3038!important;border-radius:.65rem!important;}
    .stButton>button {border-radius:.5rem;font-weight:560;background:#20242a;border-color:#343a44;}
    .stButton>button:hover {border-color:#505866;background:#262a31;}
    .stButton>button[kind="primary"] {background:#e7eaf0;color:#111318;}
    .source-card {background:#1a1d22;border-color:#30353e;border-left-color:#7889b5;border-radius:.45rem;}
    .feature-note {background:#1a1d22;border-color:#30353e;border-radius:.55rem;color:#aeb5c0;}
    .empty-state {padding:10vh 1rem 5vh;}
    .empty-logo {width:2.8rem;height:2.8rem;border-radius:.55rem;background:#e7eaf0;color:#111318;font-size:1rem;}
    .empty-state h2 {font-size:1.4rem;font-weight:620;letter-spacing:-.01em;}
    .empty-state p {color:#8e96a3;line-height:1.6;}
    @media (max-width: 760px) {.block-container {padding:1rem 1rem 5rem;}.model-chip {display:none;}}
    </style>
    """,
    unsafe_allow_html=True,
)


def require_authentication() -> None:
    if not settings.app_password or st.session_state.get("authenticated"):
        return
    st.markdown(
        "<div class='login-card'><div style='font-size:2rem'>◈</div><h2>Atlas</h2>"
        "<p class='muted'>Защищённый доступ к рабочей базе знаний</p></div>",
        unsafe_allow_html=True,
    )
    with st.form("login", clear_on_submit=False):
        password = st.text_input("Пароль", type="password", placeholder="Введите пароль доступа")
        submitted = st.form_submit_button("Войти", type="primary", use_container_width=True)
    if submitted:
        if hmac.compare_digest(password, settings.app_password):
            st.session_state.authenticated = True
            st.rerun()
        st.error("Неверный пароль")
    st.stop()


require_authentication()


@st.cache_resource
def get_service() -> AssistantService:
    return AssistantService(settings)


service = get_service()
db = service.db


@st.cache_resource
def get_report_job_manager(_db) -> ReportJobManager:
    return ReportJobManager(_db, settings.data_dir)


report_jobs = get_report_job_manager(db)


@st.fragment(run_every=2.0)
def render_report_job_progress(job_id: str) -> None:
    job = db.get_report_job(job_id)
    if not job:
        st.warning("Задание OCR больше не найдено.")
        return
    done = len(db.report_job_pages(job_id))
    total = max(1, int(job["total_pages"]))
    labels = {"queued": "В очереди", "running": "Распознавание", "failed": "Ошибка"}
    st.progress(min(done / total, 1.0), text=f"{labels.get(job['status'], job['status'])}: {done} из {total} стр.")
    st.caption("Можно перейти в другой раздел Atlas — задание продолжит работу в фоне.")
    if job["status"] in {"completed", "failed"}:
        st.rerun()


@st.cache_data(ttl=30, show_spinner=False)
def available_models(_client) -> list[str]:
    try:
        return _client.models()
    except Exception:
        return [settings.chat_model]


def ensure_conversation() -> str:
    conversations = db.list_conversations()
    known = {row["id"] for row in conversations}
    current = st.session_state.get("conversation_id")
    if current not in known:
        current = conversations[0]["id"] if conversations else db.create_conversation()
        st.session_state.conversation_id = current
    return current


def render_sources(sources: list[dict]) -> None:
    if not sources:
        return
    with st.expander(f"Источники · {len(sources)}", expanded=False):
        for number, source in enumerate(sources, start=1):
            score = source.get("score", 0)
            st.markdown(
                f"<div class='source-card'><b>[{number}] {html.escape(source['filename'])}</b><br>"
                f"<span class='muted'>{html.escape(source['location'])} · релевантность {score:.2f}</span></div>",
                unsafe_allow_html=True,
            )
            st.caption(source.get("excerpt", ""))


@st.dialog("История диалогов", width="large")
def show_conversation_history(current_id: str) -> None:
    conversations = db.list_conversations()
    current = next((row for row in conversations if row["id"] == current_id), None)
    search = st.text_input(
        "Найти диалог",
        placeholder="Введите часть названия…",
        key="conversation-history-search",
    ).strip().casefold()
    filtered = [row for row in conversations if search in row["title"].casefold()]
    st.caption(f"Найдено: {len(filtered)}")
    if not filtered:
        st.info("Диалоги с таким названием не найдены.")
    for row in filtered:
        button_col, date_col = st.columns([5, 2])
        marker = "● " if row["id"] == current_id else ""
        if button_col.button(
            f"{marker}{row['title']}",
            key=f"open-conversation-{row['id']}",
            use_container_width=True,
        ):
            st.session_state.conversation_id = row["id"]
            st.rerun()
        date_col.caption(row["updated_at"][:16].replace("T", " · "))

    if current:
        st.divider()
        st.markdown("#### Текущий диалог")
        new_title = st.text_input(
            "Название",
            value=current["title"],
            key=f"conversation-title-{current_id}",
        ).strip()
        rename_col, delete_col = st.columns([2, 1])
        if rename_col.button(
            "Сохранить название",
            key=f"rename-conversation-{current_id}",
            use_container_width=True,
            disabled=not new_title or new_title == current["title"],
        ):
            db.rename_conversation(current_id, new_title)
            st.rerun()
        confirm_delete = delete_col.checkbox(
            "Подтвердить удаление", key=f"confirm-delete-conversation-{current_id}"
        )
        if delete_col.button(
            "Удалить",
            key=f"delete-conversation-{current_id}",
            use_container_width=True,
            disabled=not confirm_delete,
        ):
            service.delete_conversation(current_id)
            st.session_state.pop("conversation_id", None)
            st.rerun()


conversation_id = ensure_conversation()

with st.sidebar:
    st.markdown(
        "<div class='brand'><span class='brand-mark'>A</span><span class='brand-name'>Atlas</span>"
        "<div class='brand-sub'>Рабочая база знаний</div></div>",
        unsafe_allow_html=True,
    )
    stats = db.stats()
    st.caption(f"{stats['documents']} документов · {stats['chunks']} фрагментов")
    new_col, history_col = st.columns(2)
    if new_col.button("＋ Новый", use_container_width=True, type="primary"):
        st.session_state.conversation_id = db.create_conversation()
        st.rerun()
    if history_col.button("История", use_container_width=True):
        show_conversation_history(conversation_id)
    current_conversation = db.get_conversation(conversation_id)
    st.caption("ТЕКУЩИЙ ДИАЛОГ")
    st.write(current_conversation["title"] if current_conversation else "Новый диалог")
    attachment_count = len(db.list_chat_attachments(conversation_id))
    if attachment_count:
        st.caption(f"Вложений без индексации: {attachment_count}")
    st.divider()
    st.caption("РЕЖИМ ОТВЕТА")
    knowledge_mode = st.radio(
        "Источник ответа",
        ["Только документы", "Документы + знания модели"],
        index=0,
        help="В первом режиме ответ строится только по загруженной базе; во втором модель может дополнять его общими знаниями.",
    )
    strict_mode = knowledge_mode == "Только документы"
    quality_profile = st.selectbox(
        "Профиль качества",
        ["Быстро", "Баланс", "Глубокий анализ", "Вручную"],
        index=1,
    )
    answer_mode = st.selectbox(
        "Формат ответа",
        ["Краткий ответ", "Подробный ответ", "Извлечь все данные", "Аналитический разбор"],
        index=1,
    )
    with st.popover("⚙ Расширенные настройки", use_container_width=True):
        model_options = available_models(service.ollama)
        if settings.chat_model not in model_options:
            model_options.insert(0, settings.chat_model)
        selected_model = st.selectbox(
            "Модель ответа",
            options=model_options,
            index=model_options.index(settings.chat_model),
            help="Список моделей, установленных в Ollama.",
        )
        supports_thinking = "thinking" in service.ollama.capabilities(selected_model)
        if supports_thinking:
            reasoning_mode = st.selectbox(
                "Рассуждение модели",
                ["Выключено — быстрее", "Включено — сложный анализ"],
                index=0,
                help="Рассуждение повышает качество сложного анализа, но расходует часть лимита токенов до формирования ответа.",
            )
            think = reasoning_mode.startswith("Включено")
        else:
            think = False
        if selected_model.startswith("qwen2.5"):
            st.caption("Для контекста 64К и ответов до 32К выберите `qwen3.5:9b`.")
        scope_documents = [row for row in db.list_documents() if row["status"] == "ready"]
        scope_options = [None] + [row["id"] for row in scope_documents]
        scope_labels = {None: "Все документы", **{row["id"]: row["filename"] for row in scope_documents}}
        selected_document_id = st.selectbox(
            "Область поиска",
            scope_options,
            format_func=lambda value: scope_labels[value],
            help="Выберите конкретный файл, если нужно извлечь из него все данные.",
        )
        profile_values = {
            "Быстро": {"temperature": 0.1, "tokens": 1024, "chunks": 5, "ctx": 8192, "top_p": 0.85},
            "Баланс": {"temperature": 0.2, "tokens": 4096, "chunks": 9, "ctx": 16384, "top_p": 0.9},
            "Глубокий анализ": {"temperature": 0.15, "tokens": 8192, "chunks": 15, "ctx": 32768, "top_p": 0.9},
            "Вручную": {"temperature": 0.25, "tokens": 4096, "chunks": 10, "ctx": 32768, "top_p": 0.9},
        }
        defaults = profile_values[quality_profile]
        model_context_limit = service.ollama.context_length(selected_model)
        context_options = [value for value in [8192, 16384, 32768, 65536] if value <= model_context_limit]
        if not context_options:
            context_options = [min(8192, model_context_limit)]
        temperature = st.slider(
            "Температура", 0.0, 1.0, defaults["temperature"], 0.05,
            key=f"temperature-{quality_profile}",
            help="Ниже — точнее и стабильнее; выше — разнообразнее.",
        )
        default_ctx = max(value for value in context_options if value <= min(defaults["ctx"], max(context_options)))
        num_ctx = st.select_slider(
            "Контекст модели",
            context_options,
            value=default_ctx,
            key=f"ctx-{quality_profile}-{selected_model}",
            help="Общий бюджет: системная инструкция, история, найденные фрагменты и ответ.",
        )
        answer_ceiling = max(512, min(32768, num_ctx - 4096))
        answer_options = [
            value
            for value in [512, 1024, 2048, 4096, 8192, 12288, 16384, 24576, 28672, 32768]
            if value <= answer_ceiling
        ]
        default_tokens = min(defaults["tokens"], max(answer_options))
        default_tokens = max(value for value in answer_options if value <= default_tokens)
        num_predict = st.select_slider(
            "Максимум токенов ответа",
            answer_options,
            value=default_tokens,
            key=f"tokens-{quality_profile}-{selected_model}-{num_ctx}",
            help="Это верхняя граница ответа, а не обязательная длина. Большой ответ оставляет меньше контекста для документов.",
        )
        final_k = st.slider(
            "Фрагментов в контексте", 3, 20, defaults["chunks"], 1,
            key=f"chunks-{quality_profile}",
        )
        top_p = st.slider(
            "Top P", 0.5, 1.0, defaults["top_p"], 0.05,
            key=f"top-p-{quality_profile}",
        )
        custom_instruction = st.text_area(
            "Дополнительная инструкция",
            placeholder="Например: сначала покажи итог, затем таблицу и проверь арифметику.",
            height=90,
        )
        st.caption(
            f"Модель поддерживает до {model_context_limit // 1024}К токенов контекста; "
            f"в Atlas доступно до {max(context_options) // 1024}К."
        )
        if num_predict >= num_ctx // 2:
            st.warning("Ответу отдана половина контекста или больше — для поиска по документам останется меньше места.")
        st.divider()
        healthy, status = service.ollama.health(selected_model)
        (st.success if healthy else st.error)(status)
        st.caption(f"LLM: {selected_model}")
        st.caption(f"Embeddings: {settings.embedding_model}")
        st.caption(f"Reranker: {settings.reranker_model if settings.enable_reranker else 'выключен'}")
    if answer_mode == "Извлечь все данные":
        final_k = max(final_k, 16)
        num_predict = max(num_predict, 4096)


st.markdown(
    f"""<section class="chat-head">
    <div class="assistant-orb">A</div>
    <div><h1>Atlas</h1><p>{html.escape(current_conversation['title'] if current_conversation else 'Новый диалог')}</p></div>
    <span class="model-chip">{html.escape(selected_model)}</span>
    </section>""",
    unsafe_allow_html=True,
)
active_section = st.segmented_control(
    "Раздел",
    ["Чат", "Файлы", "Отчеты в Excel", "Диагностика"],
    default="Чат",
    key="main-section",
    label_visibility="collapsed",
) or "Чат"

if active_section == "Чат":
    notice = st.session_state.pop("chat-attachment-notice", None)
    if notice:
        st.success(notice)
    conversation_attachments = db.list_chat_attachments(conversation_id)
    use_rag_for_chat = True
    if conversation_attachments:
        with st.expander(
            f"Вложения диалога · {len(conversation_attachments)} · без индексации в RAG",
            expanded=False,
        ):
            for attachment in conversation_attachments:
                name_col, size_col, remove_col = st.columns([5, 2, 1])
                name_col.markdown(f"**{attachment['filename']}**")
                size_col.caption(f"{attachment['size_bytes'] / 1024 / 1024:.1f} МБ")
                if remove_col.button(
                    "Убрать",
                    key=f"remove-chat-attachment-{attachment['id']}",
                    use_container_width=True,
                ):
                    service.delete_chat_attachment(conversation_id, attachment["id"])
                    st.session_state["chat-attachment-notice"] = "Вложение удалено из диалога."
                    st.rerun()
            use_rag_for_chat = st.checkbox(
                "Также искать в постоянной базе знаний",
                value=False,
                key=f"use-rag-with-attachments-{conversation_id}",
                help="Выключено по умолчанию: ответ строится только по вложениям этого диалога.",
            )
    messages = db.messages(conversation_id)
    if not messages:
        st.markdown(
            "<div class='empty-state'><div class='empty-logo'>A</div><h2>Чем помочь?</h2>"
            "<p>Спросите о документации, попросите собрать таблицу параметров, сравнить требования или извлечь все данные со скана.</p></div>",
            unsafe_allow_html=True,
        )
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                render_sources(service.decode_sources(message))
    st.caption("Можно писать обычными словами — Atlas уточнит задачу, если данных недостаточно.")
    submission = st.chat_input(
        "Напишите вопрос или прикрепите файл без добавления в RAG…",
        accept_file="multiple",
        file_type=["pdf", "doc", "docx", "xlsx", "txt", "md", "csv", "jpg", "jpeg", "png", "mp3", "wav", "m4a", "ogg", "flac"],
    )
    if submission:
        question = str(getattr(submission, "text", submission) or "").strip()
        direct_files = list(getattr(submission, "files", []) or [])
        attached_names: list[str] = []
        attachment_errors: list[str] = []
        if direct_files:
            with st.spinner("Читаю вложения без добавления в RAG…"):
                for uploaded in direct_files:
                    try:
                        row, created = service.attach_to_conversation(
                            conversation_id, uploaded.name, uploaded.getvalue()
                        )
                        attached_names.append(row["filename"])
                        if not created:
                            st.info(f"{uploaded.name}: уже прикреплён к этому диалогу")
                    except Exception as exc:
                        attachment_errors.append(f"{uploaded.name}: {exc}")
            if attached_names:
                st.session_state["chat-attachment-notice"] = (
                    f"Вложения готовы: {', '.join(attached_names)}. Они не добавлены в RAG."
                )
            for error in attachment_errors:
                st.error(error)
        if not question:
            if attached_names:
                st.rerun()
            st.stop()
        with st.chat_message("user"):
            st.markdown(question)
            if attached_names:
                st.caption(f"Вложения: {', '.join(attached_names)}")
        with st.chat_message("assistant"):
            try:
                with st.spinner("Понимаю задачу и ищу подтверждения в документах…"):
                    answer, sources, standalone = service.answer(
                        conversation_id,
                        question,
                        strict=strict_mode,
                        model=selected_model,
                        temperature=temperature,
                        num_predict=num_predict,
                        top_p=top_p,
                        num_ctx=num_ctx,
                        final_k=final_k,
                        answer_mode=answer_mode,
                        custom_instruction=custom_instruction,
                        document_id=selected_document_id,
                        think=think,
                        use_rag=use_rag_for_chat if conversation_attachments else not bool(attached_names),
                    )
                st.markdown(answer)
                render_sources(sources)
                if standalone.casefold().strip() != question.casefold().strip():
                    st.caption(f"Поисковый запрос с учётом контекста: {standalone}")
            except Exception as exc:
                st.error(f"Не удалось сформировать ответ: {exc}")
        st.rerun()

if active_section == "Файлы":
    st.markdown("<div class='section-title'>Добавить материалы</div><div class='section-copy'>Документы сохраняются локально и сразу становятся доступны в поиске.</div>", unsafe_allow_html=True)
    uploaded_files = st.file_uploader(
        "PDF, DOC, DOCX, XLSX, изображения или аудио",
        type=["pdf", "doc", "docx", "xlsx", "txt", "md", "csv", "jpg", "jpeg", "png", "mp3", "wav", "m4a", "ogg", "flac"],
        accept_multiple_files=True,
        help="Оригиналы сохраняются локально. Повторная загрузка того же файла определяется по SHA-256.",
    )
    st.markdown(
        "<div class='feature-note'><b>OCR для производственных сканов.</b> "
        "Полностраничные изображения в PDF распознаются автоматически; таблицы восстанавливаются по ячейкам и сохраняют разделители столбцов.</div>",
        unsafe_allow_html=True,
    )
    if uploaded_files and st.button("Добавить в библиотеку", type="primary"):
        progress = st.progress(0, text="Подготовка")
        for number, uploaded in enumerate(uploaded_files, start=1):
            progress.progress((number - 1) / len(uploaded_files), text=f"Индексирую {uploaded.name}")
            try:
                result = service.ingest(uploaded.name, uploaded.getvalue())
                if result["status"] == "duplicate":
                    st.warning(f"{uploaded.name}: этот файл уже есть в библиотеке")
                else:
                    st.success(f"{uploaded.name}: добавлено {result['chunks']} фрагментов")
            except Exception as exc:
                st.error(f"{uploaded.name}: {exc}")
        progress.progress(1.0, text="Готово")
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("<div class='section-title'>Библиотека</div><div class='section-copy'>Оригиналы, статус обработки и управление индексом.</div>", unsafe_allow_html=True)
    documents = db.list_documents()
    if not documents:
        st.caption("Библиотека пока пуста.")
    for document in documents:
        status_icon = {"ready": "✅", "processing": "⏳", "error": "⚠️"}.get(document["status"], "•")
        with st.container(border=True):
            title_col, meta_col, action_col = st.columns([4, 2, 1])
            title_col.markdown(f"**{status_icon} {document['filename']}**")
            title_col.caption(f"{document['chunk_count']} фрагментов · {document['extension'].upper().lstrip('.')}")
            meta_col.caption(f"{document['size_bytes'] / 1024 / 1024:.2f} МБ")
            meta_col.caption(document["created_at"][:16].replace("T", " · "))
            if document["error"]:
                st.error(document["error"])
            if action_col.button("Удалить", key=f"delete-doc-{document['id']}", use_container_width=True):
                service.delete_document(document["id"])
                st.rerun()

if active_section == "Отчеты в Excel":
    st.markdown(
        "<div class='section-title'>Пакетное извлечение отчетов</div>"
        "<div class='section-copy'>Специализированный модуль для формы предполимеризации BCNX-A10. "
        "Каждая страница PDF обрабатывается как отдельный отчет, а результат собирается в Excel.</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='feature-note'><b>Этот модуль работает отдельно от чата.</b> "
        "Он сохраняет исходный порядок полей, извлекает все пять колонок журнала и отмечает страницы, которые требуют проверки.</div>",
        unsafe_allow_html=True,
    )
    batch_pdf = st.file_uploader(
        "Многостраничный PDF с отчетами",
        type=["pdf"],
        accept_multiple_files=False,
        key="batch-report-pdf",
        help="Ожидается один отчет на каждой странице. Для 40–60 страниц обработка на CPU может занять несколько минут.",
    )
    if batch_pdf is not None:
        pdf_bytes = batch_pdf.getvalue()
        try:
            pages_total = pdf_page_count(pdf_bytes)
            st.caption(f"{batch_pdf.name} · {pages_total} стр. · {len(pdf_bytes) / 1024 / 1024:.1f} МБ")
            left, right = st.columns([2, 1])
            with left:
                if pages_total > 1:
                    page_range = st.slider("Страницы для обработки", 1, pages_total, (1, pages_total))
                else:
                    page_range = (1, 1)
                    st.caption("Будет обработана страница 1")
            with right:
                quality_label = st.selectbox(
                    "Качество OCR",
                    ["Точно · 260 DPI", "Баланс · 220 DPI", "Быстро · 180 DPI"],
                    help="Точный режим рекомендован для финального Excel; 220/180 DPI ускоряют черновую проверку.",
                )
                dpi = {"Быстро · 180 DPI": 180, "Баланс · 220 DPI": 220, "Точно · 260 DPI": 260}[quality_label]
            if st.button("Распознать отчеты", type="primary", use_container_width=True):
                job, created = report_jobs.submit(
                    batch_pdf.name,
                    pdf_bytes,
                    page_start=page_range[0],
                    page_end=page_range[1],
                    dpi=dpi,
                )
                st.session_state.report_job_id = job["id"]
                if created:
                    st.success("Задание добавлено в очередь. Можно продолжать работу в других разделах Atlas.")
                else:
                    st.info("Для этого PDF, диапазона страниц и DPI уже существует задание — открываю его.")
                st.rerun()
        except Exception as exc:
            st.error(f"Не удалось подготовить пакет отчетов: {exc}")

    current_job_id = st.session_state.get("report_job_id")
    current_job = db.get_report_job(current_job_id) if current_job_id else None
    if current_job is None:
        current_job = db.latest_report_job()
        if current_job:
            current_job_id = current_job["id"]
            st.session_state.report_job_id = current_job_id

    if current_job:
        st.markdown(f"#### Задание · {current_job['filename']}")
        st.caption(
            f"Страницы {current_job['page_start']}–{current_job['page_end']} · "
            f"{current_job['dpi']} DPI · ID {str(current_job_id)[:8]}"
        )
        if current_job["status"] in {"queued", "running"}:
            render_report_job_progress(current_job_id)
        elif current_job["status"] == "failed":
            st.error(f"Задание завершилось с ошибкой: {current_job['error']}")
        else:
            reports_df, journal_df, quality_df = report_jobs.result_frames(current_job_id)
            ready_count = int((reports_df["Статус"] == "Готово").sum()) if not reports_df.empty else 0
            check_count = len(reports_df) - ready_count
            metric_a, metric_b, metric_c = st.columns(3)
            metric_a.metric("Отчетов", len(reports_df))
            metric_b.metric("Готово", ready_count)
            metric_c.metric("Нужна проверка", check_count)
            st.markdown("#### Сводные параметры")
            edited_reports = st.data_editor(
                reports_df,
                use_container_width=True,
                hide_index=True,
                key=f"reports-editor-{current_job_id}",
                disabled=["Файл", "Страница"],
            )
            st.markdown("#### Журнал процесса")
            edited_journal = st.data_editor(
                journal_df,
                use_container_width=True,
                hide_index=True,
                key=f"journal-editor-{current_job_id}",
                disabled=["Файл", "Страница"],
            )
            if not quality_df.empty:
                with st.expander(f"Контроль распознавания · {len(quality_df)} предупреждений"):
                    st.dataframe(quality_df, use_container_width=True, hide_index=True)
            preview_pages = edited_reports["Страница"].dropna().astype(int).tolist()
            if preview_pages:
                preview_page = st.selectbox(
                    "Сверить с оригиналом — страница",
                    preview_pages,
                    key=f"report-preview-page-{current_job_id}",
                )
                stored_pdf = Path(current_job["source_path"]).read_bytes()
                st.image(
                    render_pdf_page(stored_pdf, preview_page),
                    caption=f"Оригинал · страница {preview_page}",
                    use_container_width=True,
                )
            excel_bytes = export_reports_xlsx(edited_reports, edited_journal, quality_df)
            output_name = f"{Path(current_job['filename']).stem}_данные.xlsx"
            st.download_button(
                "Скачать проверенный Excel",
                data=excel_bytes,
                file_name=output_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )

if active_section == "Диагностика":
    st.subheader("Проверка retrieval без генерации ответа")
    diagnostic_query = st.text_input("Поисковый запрос", placeholder="Например: периодичность технического обслуживания")
    diagnostic_k = st.slider("Показать фрагментов", 3, 15, settings.final_chunks)
    if diagnostic_query and st.button("Проверить поиск"):
        try:
            with st.spinner("Dense + BM25 + reranker…"):
                results = service.retriever.search(diagnostic_query, final_k=diagnostic_k)
            if not results:
                st.warning("Релевантные фрагменты не прошли порог.")
            for number, result in enumerate(results, start=1):
                with st.expander(
                    f"[{number}] {result.chunk.filename} · {result.chunk.location} · {result.score:.3f}",
                    expanded=number <= 3,
                ):
                    st.write(result.chunk.content)
                    st.caption(
                        f"dense={result.dense_score:.3f} · BM25={result.lexical_score:.3f} · "
                        f"reranker={result.reranker_score if result.reranker_score is not None else 'off'}"
                    )
        except Exception as exc:
            st.error(f"Ошибка поиска: {exc}")
    st.divider()
    current_stats = db.stats()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Документов", current_stats["documents"])
    col2.metric("Фрагментов", current_stats["chunks"])
    col3.metric("С embeddings", current_stats["embedded"])
    col4.metric("Диалогов", current_stats["conversations"])
    retrieval_profile = service.retriever.profile()
    st.caption(
        f"Лексический индекс: {retrieval_profile['lexical_backend']} · "
        f"кандидаты dense/lexical/reranker: "
        f"{retrieval_profile['dense_candidates']}/"
        f"{retrieval_profile['lexical_candidates']}/"
        f"{retrieval_profile['rerank_candidates']}"
    )
    if Path("faiss_index").exists():
        st.info("Старый индекс сохранён в `faiss_index` как резервная копия и новой версией не изменяется.")
