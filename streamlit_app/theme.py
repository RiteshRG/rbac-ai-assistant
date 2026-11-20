"""Dark Obsidian theme for the Streamlit app.

Single source of truth for the look and feel. Every page calls apply_theme()
first and builds UI only with the helpers below (or the CSS classes defined
here). Change a colour or radius here, never inside a page.

The theme uses deep charcoal surfaces, electric cyan and violet accents,
high-contrast text, and rounded cards and controls.
"""
from __future__ import annotations

from html import escape

import streamlit as st

PRODUCT_NAME = "FinSolve Assistant"  # change to the real product name

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,0,0&display=swap');

:root {
  --bg: #0a0a0a;
  --surface: #121214;
  --surface-card: #1a1a1e;
  --surface-input: #141417;
  --text: #f4f4f5;
  --text-2: #a1a1aa;
  --text-soft: #d4d4d8;
  --border: #27272a;
  --accent: #00f0ff;
  --accent-hover: #72f6ff;
  --accent-violet: #8a2be2;
  --accent-violet-light: #c4b5fd;
  --blue-600: #00f0ff;
  --blue-700: #72f6ff;
  --blue-300: #56dce8;
  --blue-100: rgba(0, 240, 255, .12);
  --fb-yellow: #00f0ff;
  --fb-amber: #8a2be2;
  --fb-orange: #00f0ff;
  --fb-flame: #8a2be2;
  --hero-bg: #0a0a0a;
  --danger: #ff3366;
  --danger-bg: #3d141d;
  --warn: #ffb800;
  --warn-bg: #3d2e00;
  --ok: #00ff66;
  --ok-bg: #0a331a;
  --radius-card: 16px;
  --radius-input: 8px;
  --radius-pill: 999px;
  --shadow-1: 0 0 12px rgba(0, 240, 255, .12);
  --shadow-2: 0 0 18px rgba(0, 240, 255, .2);
  --font: 'Google Sans','Google Sans Text',Roboto,system-ui,-apple-system,'Segoe UI',sans-serif;
}

html, body, .stApp, [class*="css"] { font-family: var(--font); color: var(--text); }
body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  background: var(--bg);
  color: var(--text);
}
h1, h2, h3, h4 { font-family: var(--font); letter-spacing: -0.01em; color: var(--text); }
p, li, label, small { color: var(--text-soft); }
.stApp a, .stApp a p {
  color:var(--accent) !important; text-decoration:none;
  transition:color .15s, text-shadow .15s;
}
.stApp a:hover, .stApp a:hover p {
  color:var(--accent-hover) !important; text-decoration:underline;
  text-shadow:0 0 10px rgba(0, 240, 255, .35);
}

/* ---- hide Streamlit chrome we do not want ---- */
#MainMenu, footer, [data-testid="stSidebarNav"], [data-testid="stDecoration"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1120px; padding-top: 0.75rem; padding-bottom: 3rem; }
.fb-narrow .block-container, .block-container.fb-narrow { max-width: 860px; }

/* ---- buttons ---- */
button[data-testid^="stBaseButton-primary"] {
  background: var(--accent); color: #0a0a0a; border: 1px solid var(--accent);
  border-radius: var(--radius-pill); padding: .55rem 1.4rem; font-weight: 700;
  box-shadow: 0 0 10px rgba(0, 240, 255, .16); transition: background .15s, box-shadow .15s, transform .15s;
}
button[data-testid^="stBaseButton-primary"]:hover {
  background: var(--accent-hover); border-color: var(--accent-hover);
  box-shadow: 0 0 15px rgba(0, 240, 255, .4); color: #0a0a0a; transform: translateY(-1px);
}
button[data-testid^="stBaseButton-secondary"] {
  background: #141417; color: var(--text); border: 1px solid var(--border);
  border-radius: var(--radius-pill); padding: .5rem 1.2rem; font-weight: 500;
}
button[data-testid^="stBaseButton-secondary"]:hover {
  background: #222228; border-color: var(--accent); color: var(--accent);
  box-shadow: 0 0 12px rgba(0, 240, 255, .16);
}
button:disabled { opacity: .48; box-shadow: none !important; transform: none !important; }
button:focus-visible, a:focus-visible, input:focus-visible, textarea:focus-visible {
  outline: 2px solid var(--accent) !important; outline-offset: 2px;
}

/* ---- inputs ---- */
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"],
[data-baseweb="select"] {
  background: var(--surface-input) !important; border: 1px solid var(--border) !important;
  border-radius: var(--radius-input) !important;
}
input, textarea, select {
  background-color: var(--surface-input) !important; color: var(--text) !important;
  border-color: var(--border) !important; border-radius: var(--radius-input) !important;
}
input::placeholder, textarea::placeholder { color: var(--text-2) !important; opacity: 1; }
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within,
[data-baseweb="select"]:focus-within {
  border-color: var(--accent) !important;
  box-shadow: 0 0 10px rgba(0, 240, 255, .3) !important;
}
[role="listbox"], [role="option"] { background: var(--surface-card); color: var(--text); }
[data-testid="stChatInput"] {
  background: var(--surface-input); border-radius: 28px;
  border: 1px solid var(--border); box-shadow: var(--shadow-1);
}
[data-testid="stChatInput"]:focus-within {
  border-color: var(--accent); box-shadow: 0 0 10px rgba(0, 240, 255, .3);
}
[data-testid="stChatInput"] textarea { background: transparent !important; }

/* ---- sidebar ---- */
[data-testid="stSidebar"] {
  background: var(--surface); border-right: 1px solid var(--border);
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] { color: var(--text); }
[data-testid="stSidebar"] hr, hr { border-color: var(--border); }

/* ---- chat messages ---- */
[data-testid="stChatMessage"] {
  background: transparent; color: var(--text); padding: .75rem 0;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  background: #141417; border: 1px solid var(--border); border-radius: 12px;
  padding: .75rem 1rem; margin: .35rem 0;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
  background: var(--surface-card); border: 1px solid var(--border);
  border-left: 2px solid rgba(0, 240, 255, .7);
  border-radius: var(--radius-card); padding: .9rem 1rem; margin: .45rem 0;
  box-shadow: inset 0 0 24px rgba(138, 43, 226, .035);
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
  color: var(--text-soft); line-height: 1.6;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
  color: var(--text-soft); line-height: 1.6; margin: .35rem 0 .85rem;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] strong {
  color: var(--text); font-weight: 700;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] ul,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] ol {
  margin: .4rem 0 .9rem; padding-left: 1.5rem;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] li {
  line-height: 1.55; margin: .3rem 0; padding-left: .2rem;
}

/* ---- top navigation ---- */
.fb-brand {
  display:flex; align-items:center; gap:.6rem; font-weight:600; font-size:1.15rem;
  color:var(--text); white-space:nowrap;
}
.fb-nav-link { font-size:.92rem; white-space:nowrap; }
.fb-brand-mark {
  width:28px; height:28px; border-radius:8px;
  background: linear-gradient(135deg, var(--accent), var(--accent-violet));
  box-shadow: 0 0 14px rgba(0, 240, 255, .25); display:inline-block;
}
.fb-nav-sep { border-bottom: 1px solid var(--border); margin: .25rem 0 0; }

/* ---- landing ---- */
.fb-hero {
  background:
    radial-gradient(circle at 75% 20%, rgba(0, 240, 255, .14) 0%, transparent 36%),
    radial-gradient(circle at 20% 85%, rgba(138, 43, 226, .15) 0%, transparent 42%),
    var(--hero-bg);
  color:var(--text); border:1px solid var(--border); border-radius:28px;
  padding:4.5rem 3rem; margin:1rem 0 2.5rem;
}
.fb-hero h1 { color:var(--text); font-size:clamp(2rem, 4.5vw, 3.4rem); line-height:1.15; margin:.25rem 0 1rem; font-weight:600; }
.fb-hero h1 .accent {
  background:linear-gradient(90deg, var(--accent), var(--accent-violet-light));
  -webkit-background-clip:text; background-clip:text; color:transparent;
}
.fb-hero p { color:var(--text-soft); font-size:1.15rem; max-width:640px; }
.fb-eyebrow { text-transform:uppercase; letter-spacing:.12em; font-size:.78rem; color:var(--accent); font-weight:600; }
.fb-section-title { font-size:1.8rem; font-weight:600; margin:2.5rem 0 .4rem; }
.fb-section-sub { color:var(--text-2); font-size:1.05rem; max-width:680px; margin-bottom:1.2rem; }
.fb-card {
  border:1px solid var(--border); border-radius:var(--radius-card); padding:1.4rem;
  background:rgba(26, 26, 30, .82); backdrop-filter:blur(12px); height:100%;
  transition:border-color .15s, box-shadow .15s;
}
.fb-card:hover { border-color:rgba(0, 240, 255, .55); box-shadow:var(--shadow-1); }
.fb-card h4 { margin:.6rem 0 .3rem; font-size:1.1rem; font-weight:600; }
.fb-card p { color:var(--text-2); margin:0; font-size:.98rem; }
.fb-icon {
  width:44px; height:44px; border-radius:12px;
  background:rgba(0, 240, 255, .12); color:var(--accent);
  border:1px solid rgba(0, 240, 255, .2);
  display:flex; align-items:center; justify-content:center;
}
.fb-icon .material-symbols-rounded { font-size: 26px; }
.material-symbols-rounded { font-family:'Material Symbols Rounded'; font-weight:normal; font-style:normal; line-height:1; display:inline-block; }
.fb-step-num {
  width:32px; height:32px; border-radius:50%; background:var(--accent); color:#0a0a0a;
  font-weight:700; display:flex; align-items:center; justify-content:center;
  box-shadow:0 0 12px rgba(0, 240, 255, .2);
}
.fb-band {
  background:linear-gradient(120deg, rgba(0, 240, 255, .08), rgba(138, 43, 226, .1)), var(--surface);
  border:1px solid var(--border); border-radius:24px; padding:2rem; margin:2rem 0;
}

/* ---- auth card ---- */
.fb-auth-title { font-size:1.6rem; font-weight:600; margin:.25rem 0; }
.fb-auth-sub { color:var(--text-2); margin-bottom:1rem; }
[data-testid="stForm"] {
  background:var(--surface-card); border:1px solid var(--border);
  border-radius:var(--radius-card); padding:1.6rem; box-shadow:var(--shadow-1);
}
[data-testid="stForm"] button[data-testid^="stBaseButton-primary"] { width:100%; }

/* ---- status banners and citations ---- */
.fb-banner, [data-testid="stAlert"] {
  border-radius:12px; padding:.8rem 1rem; margin:.5rem 0; font-size:.95rem;
}
.fb-banner.error, [data-testid="stAlert"][kind="error"] { background:var(--danger-bg); color:var(--danger); }
.fb-banner.warn, [data-testid="stAlert"][kind="warning"] { background:var(--warn-bg); color:var(--warn); }
.fb-banner.info, [data-testid="stAlert"][kind="info"] { background:rgba(0, 240, 255, .1); color:var(--accent); }
[data-testid="stAlert"] p { color:inherit; }
[data-testid="stAlertContainer"] { border:1px solid var(--border); border-radius:12px; overflow:hidden; }
[data-testid="stAlertContentInfo"] { background:rgba(0, 240, 255, .1); color:var(--accent); }
[data-testid="stAlertContentWarning"] { background:var(--warn-bg); color:var(--warn); }
[data-testid="stAlertContentError"] { background:var(--danger-bg); color:var(--danger); }
[data-testid="stAlertContentSuccess"] { background:var(--ok-bg); color:var(--ok); }
[data-testid^="stAlertContent"] p { color:inherit !important; }
.fb-chip {
  display:inline-block; border:1px solid var(--border); border-radius:var(--radius-pill);
  padding:.15rem .7rem; font-size:.82rem; color:var(--accent-violet-light);
  background:var(--surface-input); margin:0 .35rem .35rem 0;
}

/* ---- legal pages ---- */
.st-key-fb-legal { max-width:760px; line-height:1.65; color:var(--text-soft); }
.st-key-fb-legal h2 { font-size:1.3rem; margin-top:2rem; font-weight:600; color:var(--text); }
.st-key-fb-legal a { color:var(--accent); }
.fb-footer { border-top:1px solid var(--border); margin-top:3rem; padding-top:1.2rem; color:var(--text-2); font-size:.9rem; }

@media (max-width:640px) {
  .fb-hero { padding:2.5rem 1.4rem; border-radius:20px; }
  [data-testid="stForm"] { padding:1.2rem; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
"""


def apply_theme() -> None:
    """Inject shared page CSS; no explicit parameters; return None; each Streamlit page script calls this at render start."""
    st.markdown(_CSS, unsafe_allow_html=True)


def brand(name: str = PRODUCT_NAME) -> None:
    """Render the product mark and name; name is the displayed product label; return None; public_nav and the chat page call this helper."""
    st.markdown(
        f'<div class="fb-brand"><span class="fb-brand-mark"></span>{escape(name)}</div>',
        unsafe_allow_html=True,
    )


def hero(eyebrow: str, title_html: str, subtitle: str) -> None:
    """Render the landing-page hero; eyebrow, title_html, and subtitle provide its text, with optional accent markup in the title; return None; the landing page calls this helper."""
    st.markdown(
        f'<div class="fb-hero"><div class="fb-eyebrow">{eyebrow}</div>'
        f"<h1>{title_html}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


def section_title(title: str, subtitle: str = "") -> None:
    """Render a section heading and optional subtitle; title and subtitle provide display text; return None; the landing page calls this helper."""
    st.markdown(
        f'<div class="fb-section-title">{escape(title)}</div>',
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(
            f'<div class="fb-section-sub">{escape(subtitle)}</div>',
            unsafe_allow_html=True,
        )


def feature_card(icon: str, title: str, body: str) -> None:
    """Render a feature card; icon is a Material Symbols name and title/body provide its text; return None; the landing page calls this helper."""
    st.markdown(
        f'<div class="fb-card"><div class="fb-icon"><span class="material-symbols-rounded">{escape(icon)}</span></div>'
        f"<h4>{escape(title)}</h4><p>{escape(body)}</p></div>",
        unsafe_allow_html=True,
    )


def step(number: int, title: str, body: str) -> None:
    """Render one numbered process card; number, title, and body define its display; return None; the landing page calls this helper."""
    st.markdown(
        f'<div class="fb-card"><div class="fb-step-num">{number}</div><h4>{escape(title)}</h4><p>{escape(body)}</p></div>',
        unsafe_allow_html=True,
    )


def banner(kind: str, text: str) -> None:
    """Render a safely escaped status banner; kind selects error, warn, or info and text is plain display text; return None; page scripts may call this public theme helper."""
    safe_kind = kind if kind in {"error", "warn", "info"} else "error"
    st.markdown(
        f'<div class="fb-banner {safe_kind}">{escape(text)}</div>',
        unsafe_allow_html=True,
    )


def chips(labels: list[str]) -> None:
    """Render escaped pill labels; labels contains the display strings; return None; the chat page calls this helper for citations."""
    rendered = "".join(
        f'<span class="fb-chip">{escape(label)}</span>' for label in labels
    )
    st.markdown(rendered, unsafe_allow_html=True)


def footer(product: str = PRODUCT_NAME) -> None:
    """Render footer text only; product is the displayed product name; return None; public_footer calls this helper and adds navigation links."""
    st.markdown(
        f'<div class="fb-footer">&copy; {escape(product)}. Demonstration project.</div>',
        unsafe_allow_html=True,
    )


def public_nav() -> None:
    """Render the shared public-page navigation; no explicit parameters; return None; the landing, login, privacy, and terms page scripts call this helper."""
    columns = st.columns([2.7, .8, 1.35, .95, .7, .7, .8])
    with columns[0]:
        brand()
    for column, label, href in (
        (columns[1], "Features", "#features"),
        (columns[2], "How it works", "#how-it-works"),
        (columns[3], "Security", "#security"),
    ):
        column.markdown(
            f'<a class="fb-nav-link" href="{href}">{label}</a>',
            unsafe_allow_html=True,
        )
    columns[4].page_link("pages/privacy.py", label="Privacy")
    columns[5].page_link("pages/terms.py", label="Terms")
    columns[6].page_link("pages/login.py", label="Sign in")
    st.markdown('<div class="fb-nav-sep"></div>', unsafe_allow_html=True)


def public_footer() -> None:
    """Render the shared public footer and legal links; no explicit parameters; return None; the landing, login, privacy, and terms page scripts call this helper."""
    columns = st.columns([3, 1, 1])
    with columns[0]:
        footer()
    columns[1].page_link("pages/privacy.py", label="Privacy")
    columns[2].page_link("pages/terms.py", label="Terms")