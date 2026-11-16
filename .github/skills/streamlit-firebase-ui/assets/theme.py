"""Firebase-inspired theme for the Streamlit app.

Single source of truth for the look and feel. Every page calls apply_theme()
first and builds UI only with the helpers below (or the CSS classes defined
here). Change a colour or radius here, never inside a page.

Values are an approximation of the Firebase website style (Google-style blue
actions, Firebase yellow/orange accent, dark gradient hero, pill buttons,
rounded cards, Roboto/Google Sans type). Do NOT use the Firebase logo or name.
"""
from __future__ import annotations

import streamlit as st

PRODUCT_NAME = "FinSolve Assistant"  # change to the real product name

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,0,0&display=swap');

:root {
  --fb-yellow: #ffca28;
  --fb-amber: #ffa000;
  --fb-orange: #f57c00;
  --fb-flame: #dd2c00;
  --blue-600: #1a73e8;
  --blue-700: #1967d2;
  --blue-300: #a8c7fa;
  --blue-100: #e8f0fe;
  --text: #202124;
  --text-2: #5f6368;
  --border: #dadce0;
  --surface: #ffffff;
  --surface-alt: #f8f9fa;
  --hero-bg: #131314;
  --danger: #d93025;
  --danger-bg: #fce8e6;
  --warn-bg: #fef7e0;
  --ok: #188038;
  --radius-card: 16px;
  --radius-input: 8px;
  --radius-pill: 999px;
  --shadow-1: 0 1px 2px rgba(60,64,67,.3), 0 1px 3px 1px rgba(60,64,67,.15);
  --shadow-2: 0 1px 3px rgba(60,64,67,.3), 0 4px 8px 3px rgba(60,64,67,.15);
  --font: 'Google Sans','Google Sans Text',Roboto,system-ui,-apple-system,'Segoe UI',sans-serif;
}

html, body, .stApp, [class*="css"] { font-family: var(--font); color: var(--text); }
.stApp { background: var(--surface); }
h1, h2, h3, h4 { font-family: var(--font); letter-spacing: -0.01em; color: var(--text); }
a { color: var(--blue-600); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ---- hide Streamlit chrome we do not want ---- */
#MainMenu, footer, [data-testid="stSidebarNav"], [data-testid="stDecoration"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1120px; padding-top: 0.75rem; padding-bottom: 3rem; }
.fb-narrow .block-container, .block-container.fb-narrow { max-width: 860px; }

/* ---- buttons: pill shaped, Google style ---- */
button[data-testid^="stBaseButton-primary"] {
  background: var(--blue-600); color: #fff; border: 0; border-radius: var(--radius-pill);
  padding: .55rem 1.4rem; font-weight: 500; box-shadow: none; transition: background .15s, box-shadow .15s;
}
button[data-testid^="stBaseButton-primary"]:hover { background: var(--blue-700); box-shadow: var(--shadow-1); color: #fff; }
button[data-testid^="stBaseButton-secondary"] {
  background: transparent; color: var(--blue-600); border: 1px solid var(--border);
  border-radius: var(--radius-pill); padding: .5rem 1.2rem; font-weight: 500;
}
button[data-testid^="stBaseButton-secondary"]:hover { background: var(--blue-100); border-color: var(--blue-300); color: var(--blue-700); }
button:focus-visible, a:focus-visible, input:focus-visible { outline: 2px solid var(--blue-600) !important; outline-offset: 2px; }

/* ---- inputs ---- */
[data-baseweb="input"], [data-baseweb="base-input"] { border-radius: var(--radius-input) !important; }
[data-baseweb="input"]:focus-within { border-color: var(--blue-600) !important; box-shadow: 0 0 0 1px var(--blue-600); }
[data-testid="stChatInput"] { border-radius: 28px; border: 1px solid var(--border); box-shadow: var(--shadow-1); }
[data-testid="stChatInput"]:focus-within { border-color: var(--blue-600); }

/* ---- sidebar ---- */
[data-testid="stSidebar"] { background: var(--surface-alt); border-right: 1px solid var(--border); }

/* ---- chat messages ---- */
[data-testid="stChatMessage"] { background: transparent; padding: .75rem 0; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) { background: var(--surface-alt); border-radius: var(--radius-card); padding: .9rem 1rem; }

/* ---- top navigation ---- */
.fb-brand { display:flex; align-items:center; gap:.6rem; font-weight:500; font-size:1.15rem; color: var(--text); }
.fb-brand-mark { width:28px; height:28px; border-radius:8px; background: linear-gradient(135deg, var(--fb-yellow), var(--fb-orange) 60%, var(--fb-flame)); display:inline-block; }
.fb-nav-sep { border-bottom: 1px solid var(--border); margin: .25rem 0 0; }

/* ---- landing ---- */
.fb-hero { background: radial-gradient(1200px 400px at 80% -10%, rgba(245,124,0,.35), transparent 60%), var(--hero-bg);
  color:#fff; border-radius: 28px; padding: 4.5rem 3rem; margin: 1rem 0 2.5rem; }
.fb-hero h1 { color:#fff; font-size: clamp(2rem, 4.5vw, 3.4rem); line-height:1.15; margin:.25rem 0 1rem; font-weight:500; }
.fb-hero h1 .accent { background: linear-gradient(90deg, var(--fb-yellow), var(--fb-orange)); -webkit-background-clip:text; background-clip:text; color:transparent; }
.fb-hero p { color:#c4c7c5; font-size:1.15rem; max-width: 640px; }
.fb-eyebrow { text-transform: uppercase; letter-spacing:.12em; font-size:.78rem; color: var(--fb-amber); font-weight:500; }
.fb-section-title { font-size: 1.8rem; font-weight:500; margin: 2.5rem 0 .4rem; }
.fb-section-sub { color: var(--text-2); font-size:1.05rem; max-width: 680px; margin-bottom: 1.2rem; }
.fb-card { border:1px solid var(--border); border-radius: var(--radius-card); padding: 1.4rem; background: var(--surface); height:100%; transition: box-shadow .15s; }
.fb-card:hover { box-shadow: var(--shadow-1); }
.fb-card h4 { margin: .6rem 0 .3rem; font-size:1.1rem; font-weight:500; }
.fb-card p { color: var(--text-2); margin:0; font-size:.98rem; }
.fb-icon { width:44px; height:44px; border-radius:12px; background: var(--blue-100); color: var(--blue-600); display:flex; align-items:center; justify-content:center; }
.fb-icon .material-symbols-rounded { font-size: 26px; }
.material-symbols-rounded { font-family:'Material Symbols Rounded'; font-weight:normal; font-style:normal; line-height:1; display:inline-block; }
.fb-step-num { width:32px; height:32px; border-radius:50%; background: var(--fb-yellow); color:#202124; font-weight:700; display:flex; align-items:center; justify-content:center; }
.fb-band { background: var(--surface-alt); border-radius: 24px; padding: 2rem; margin: 2rem 0; }

/* ---- auth card ---- */
.fb-auth-title { font-size:1.6rem; font-weight:500; margin:.25rem 0; }
.fb-auth-sub { color: var(--text-2); margin-bottom: 1rem; }
[data-testid="stForm"] { border:1px solid var(--border); border-radius: var(--radius-card); padding: 1.6rem; box-shadow: var(--shadow-1); }

/* ---- status banners and citations ---- */
.fb-banner { border-radius: 12px; padding: .8rem 1rem; margin: .5rem 0; font-size:.95rem; }
.fb-banner.error { background: var(--danger-bg); color: var(--danger); }
.fb-banner.warn  { background: var(--warn-bg); color: #7a5b00; }
.fb-banner.info  { background: var(--blue-100); color: var(--blue-700); }
.fb-chip { display:inline-block; border:1px solid var(--border); border-radius: var(--radius-pill); padding:.15rem .7rem; font-size:.82rem; color: var(--text-2); background: var(--surface); margin: 0 .35rem .35rem 0; }

/* ---- legal pages ---- */
.fb-legal { max-width: 760px; line-height: 1.65; }
.fb-legal h2 { font-size:1.3rem; margin-top:2rem; font-weight:500; }
.fb-footer { border-top:1px solid var(--border); margin-top:3rem; padding-top:1.2rem; color: var(--text-2); font-size:.9rem; }

@media (max-width: 640px) { .fb-hero { padding: 2.5rem 1.4rem; border-radius: 20px; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
"""


def apply_theme() -> None:
    """Inject the shared CSS. Call at the top of every page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def brand(name: str = PRODUCT_NAME) -> None:
    st.markdown(
        f'<div class="fb-brand"><span class="fb-brand-mark"></span>{name}</div>',
        unsafe_allow_html=True,
    )


def hero(eyebrow: str, title_html: str, subtitle: str) -> None:
    """title_html may wrap one phrase in <span class="accent">...</span>."""
    st.markdown(
        f'<div class="fb-hero"><div class="fb-eyebrow">{eyebrow}</div>'
        f"<h1>{title_html}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


def section_title(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="fb-section-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="fb-section-sub">{subtitle}</div>', unsafe_allow_html=True)


def feature_card(icon: str, title: str, body: str) -> None:
    """icon is a Material Symbols name, for example 'description' or 'lock'."""
    st.markdown(
        f'<div class="fb-card"><div class="fb-icon"><span class="material-symbols-rounded">{icon}</span></div>'
        f"<h4>{title}</h4><p>{body}</p></div>",
        unsafe_allow_html=True,
    )


def step(number: int, title: str, body: str) -> None:
    st.markdown(
        f'<div class="fb-card"><div class="fb-step-num">{number}</div><h4>{title}</h4><p>{body}</p></div>',
        unsafe_allow_html=True,
    )


def banner(kind: str, text: str) -> None:
    """kind: 'error' | 'warn' | 'info'. Text must be plain, safe text."""
    st.markdown(f'<div class="fb-banner {kind}">{text}</div>', unsafe_allow_html=True)


def chips(labels: list[str]) -> None:
    html = "".join(f'<span class="fb-chip">{l}</span>' for l in labels)
    st.markdown(html, unsafe_allow_html=True)


def footer(product: str = PRODUCT_NAME) -> None:
    """Footer text only. Privacy/Terms links are added with st.page_link by the caller."""
    st.markdown(
        f'<div class="fb-footer">&copy; {product}. Demonstration project.</div>',
        unsafe_allow_html=True,
    )