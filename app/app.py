import os
import io
import re
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import boto3
from botocore.config import Config
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timezone
import time
from plotly.offline import get_plotlyjs_version
from config import *

# Hide deploy button
os.environ['STREAMLIT_SERVER_ENABLE_STATIC_SERVING'] = 'false'

# Page configuration
st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon=PAGE_ICON,
    layout=LAYOUT,
    initial_sidebar_state="collapsed"
)

# Initialize theme in session state
if 'theme' not in st.session_state:
    st.session_state.theme = 'light'

# Theme colors
theme_colors = {
    'light': {
        'bg': '#ffffff',
        'secondary_bg': '#f8f9fa',
        'text': '#1f2937',
        'border': '#e5e7eb',
        'accent': '#1f77b4'
    },
    'dark': {
        'bg': '#0e1117',
        'secondary_bg': '#1e2530',
        'text': '#fafafa',
        'border': '#374151',
        'accent': '#4dabf7'
    }
}

current_theme = theme_colors[st.session_state.theme]

# Modern UI styling with theme support
modern_style = f"""
    <style>
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    header[data-testid="stHeader"] {{display: none;}}
    [data-testid="stToolbar"] {{display: none;}}
    .stDeployButton {{display: none;}}
    [data-testid="stSidebar"] {{display: none;}}
    section[data-testid="stSidebar"] {{display: none;}}
    .stApp {{
        background-color: {current_theme['bg']};
        color: {current_theme['text']};
        font-size: 1.05rem;
    }}
    .main {{ padding: 1.5rem 2rem; }}
    .block-container {{ padding-top: 3rem; padding-bottom: 2rem; max-width: 100%; }}
    [data-testid="stMetricValue"] {{
        font-size: 2.4rem; font-weight: 700; color: {current_theme['accent']};
    }}
    [data-testid="stMetricLabel"] {{
        font-size: 1rem; font-weight: 500; color: {current_theme['text']}; opacity: 0.8;
    }}
    [data-testid="stMetricDelta"] {{
        font-size: 0.9rem;
    }}
    .stButton button {{
        border-radius: 8px; font-weight: 500; padding: 0.4rem 0.8rem;
        transition: all 0.2s ease; border: 1px solid {current_theme['border']};
        background-color: {current_theme['secondary_bg']}; color: {current_theme['text']};
    }}
    .stButton button:hover {{
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        border-color: {current_theme['accent']};
    }}
    .dashboard-title {{
        font-weight: 700; font-size: 3rem; margin: 0; padding: 0;
        color: {current_theme['text']};
    }}
    .dashboard-subtitle {{
        font-size: 1.1rem; color: {current_theme['text']}; opacity: 0.5; margin-top: 4px;
    }}
    h2 {{ font-weight: 600; font-size: 1.6rem; margin-top: 1.5rem; margin-bottom: 0.8rem; color: {current_theme['text']}; }}
    h3 {{ font-weight: 600; font-size: 1.3rem; color: {current_theme['text']}; }}
    h4 {{ font-weight: 600; font-size: 1.15rem; color: {current_theme['text']}; }}
    p, li, span, label, .stMarkdown {{ color: {current_theme['text']}; }}
    strong {{ color: {current_theme['text']}; }}
    [data-testid="stMarkdownContainer"] {{ color: {current_theme['text']}; }}
    [data-testid="stMarkdownContainer"] p {{ color: {current_theme['text']}; }}
    [data-testid="stMarkdownContainer"] h1,
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3,
    [data-testid="stMarkdownContainer"] h4 {{ color: {current_theme['text']}; }}
    hr {{ margin: 1.5rem 0; border-color: {current_theme['border']}; opacity: 0.3; }}
    .streamlit-expanderHeader {{ background-color: {current_theme['secondary_bg']}; border-radius: 8px; font-weight: 500; }}
    [data-testid="stDataFrame"] {{ border-radius: 8px; overflow: hidden; }}
    div[data-testid="stPlotlyChart"] {{
        border-radius: 12px;
        overflow: hidden;
    }}
    </style>
"""
st.markdown(modern_style, unsafe_allow_html=True)

# --- Athena helpers ---

# How many Athena queries one dashboard view runs at the same time
# (well under Athena's default quota for concurrent queries).
ATHENA_MAX_CONCURRENT = 10
# How many IAM Identity Center user lookups run at the same time.
IDENTITY_LOOKUP_CONCURRENCY = 8
# Standard retry mode backs off and retries throttled calls.
AWS_CLIENT_CONFIG = Config(retries={'mode': 'standard', 'max_attempts': 8})

@st.cache_resource
def get_athena_client():
    return boto3.client('athena', region_name=AWS_REGION, config=AWS_CLIENT_CONFIG)

@st.cache_resource
def get_identity_store_client():
    return boto3.client('identitystore', region_name=IDENTITY_STORE_REGION, config=AWS_CLIENT_CONFIG)

@st.cache_resource
def get_glue_client():
    return boto3.client('glue', region_name=AWS_REGION)

@st.cache_data(ttl=3600)
def resolve_table_name():
    """Auto-discover the table name from the Glue database.
    Falls back to GLUE_TABLE_NAME env var if set."""
    if GLUE_TABLE_NAME:
        return GLUE_TABLE_NAME
    try:
        client = get_glue_client()
        response = client.get_tables(DatabaseName=ATHENA_DATABASE, MaxResults=1)
        tables = response.get('TableList', [])
        if tables:
            return tables[0]['Name']
    except Exception:
        pass
    raise Exception(f"No tables found in Glue database '{ATHENA_DATABASE}'. "
                    "Run the Glue crawler first, or set GLUE_TABLE_NAME in .env.")

def run_athena_query(client, query):
    """Run one query and return all result rows as a DataFrame of strings.
    Takes the client as an argument so it can run in a worker thread."""
    qid = client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={'Database': ATHENA_DATABASE},
        ResultConfiguration={'OutputLocation': ATHENA_OUTPUT_BUCKET},
        WorkGroup=ATHENA_WORKGROUP
    )['QueryExecutionId']
    delay = 0.2
    while True:
        status = client.get_query_execution(QueryExecutionId=qid)['QueryExecution']['Status']
        if status['State'] == 'SUCCEEDED':
            break
        if status['State'] in ('FAILED', 'CANCELLED'):
            raise Exception(f"Query failed: {status.get('StateChangeReason', 'Unknown error')}")
        time.sleep(delay)
        delay = min(delay * 1.5, 1.0)
    # Athena returns at most 1,000 rows per page, so read every page.
    columns, rows = None, []
    for page in client.get_paginator('get_query_results').paginate(QueryExecutionId=qid):
        result_set = page['ResultSet']
        page_rows = result_set['Rows']
        if columns is None:
            columns = [col['Label'] for col in result_set['ResultSetMetadata']['ColumnInfo']]
            page_rows = page_rows[1:]  # the first row of the first page is the header
        rows.extend([field.get('VarCharValue', '') for field in row['Data']] for row in page_rows)
    return pd.DataFrame(rows, columns=columns)

@st.cache_data(ttl=300)
def fetch_data(query):
    return run_athena_query(get_athena_client(), query)

@st.cache_data(ttl=300, show_spinner="Running Athena queries…")
def fetch_many(queries):
    """Run several queries in parallel threads and return their DataFrames in
    the same order. Each query needs several API round trips, so running them
    one after another is what made the page slow."""
    client = get_athena_client()
    with ThreadPoolExecutor(max_workers=ATHENA_MAX_CONCURRENT) as pool:
        return list(pool.map(lambda q: run_athena_query(client, q), queries))

# --- Users: names and IAM Identity Center status ---

STATUS_ACTIVE = 'Active'
STATUS_DISABLED = 'Disabled'
STATUS_REMOVED = 'Removed'
STATUS_UNKNOWN = 'Unknown'
# Hidden from the monthly view. The all-time view keeps their history.
INACTIVE_STATUSES = (STATUS_REMOVED, STATUS_DISABLED)

def clean_userid(raw):
    """Strip the quotes that the CSV SerDe leaves around userid values."""
    return str(raw).replace("'", "").replace('"', '').strip()

@st.cache_data(ttl=3600, show_spinner=False)
def _lookup_identity_cached(userid):
    """Look up one user in IAM Identity Center and return (name, status).
    Unexpected errors are raised, so they are not cached and get retried."""
    client = get_identity_store_client()
    try:
        response = client.describe_user(IdentityStoreId=IDENTITY_STORE_ID, UserId=userid)
    except client.exceptions.ResourceNotFoundException as e:
        # A missing USER means it was deleted from Identity Center. A missing
        # IDENTITY_STORE means IDENTITY_STORE_ID is wrong, which is not the user's state.
        if e.response.get('ResourceType') == 'USER':
            return None, STATUS_REMOVED
        raise
    name = response.get('UserName') or response.get('DisplayName') or \
           (response.get('Emails') or [{}])[0].get('Value') or userid
    status = STATUS_DISABLED if response.get('UserStatus') == 'DISABLED' else STATUS_ACTIVE
    return name, status

def _lookup_identity_safe(userid):
    try:
        return _lookup_identity_cached(userid)
    except Exception:
        return None, STATUS_UNKNOWN

def lookup_users(userids):
    """Return (labels, statuses) for cleaned userids, both dicts keyed by userid.

    A failed lookup gives status Unknown, and Unknown users are treated as
    active, so an error never hides anyone.
    """
    if IDENTITY_STORE_ID and userids:
        # Lookups are cached per user, so only the first load waits for them.
        with st.spinner(f"Looking up {len(userids)} users in IAM Identity Center…"):
            with ThreadPoolExecutor(max_workers=IDENTITY_LOOKUP_CONCURRENCY) as pool:
                results = dict(zip(userids, pool.map(_lookup_identity_safe, userids)))
    else:
        results = {uid: (uid, STATUS_UNKNOWN) for uid in userids}
    labels, statuses = {}, {}
    for uid in userids:
        name, status = results[uid]
        if status == STATUS_REMOVED:
            label = f"Removed user {uid[:8]}"
        elif status == STATUS_DISABLED:
            label = f"{name} (disabled)"
        else:
            label = name or uid
        labels[uid], statuses[uid] = label, status
    # Labels are filter options and chart categories, so they must be unique.
    used = set()
    for uid in labels:
        if labels[uid] in used:
            labels[uid] = f"{labels[uid]} [{uid}]"
        used.add(labels[uid])
    return labels, statuses

def group_raw_userids(raw_userids):
    """Map cleaned userid -> list of raw userid values as stored in the table."""
    grouped = {}
    for raw in raw_userids:
        grouped.setdefault(clean_userid(raw), []).append(raw)
    return grouped

# --- Months and plan credits ---

# Plan credits included in each Kiro tier per month (kiro.dev/docs/enterprise/billing).
# Kiro resets them at the start of every calendar month.
PLAN_CREDITS = {'pro': 1000, 'proplus': 2000, 'promax': 5000, 'power': 10000}

def tier_key(tier):
    """Normalize tier spellings (PRO_PLUS, Pro+, ProPlus, PRO_MAX ...) to one key."""
    return re.sub(r'[\s_\-]', '', str(tier).lower()).replace('+', 'plus')

def plan_for_tiers(tiers):
    """Given the comma-separated tiers a user had in one month, return
    (tier, monthly plan credits). A mid-month upgrade applies the higher tier
    to the whole month, so the tier with the most credits wins."""
    best_tier, best_credits = None, None
    for tier in str(tiers).split(','):
        tier = tier.strip()
        credits = PLAN_CREDITS.get(tier_key(tier))
        if credits is not None and (best_credits is None or credits > best_credits):
            best_tier, best_credits = tier, credits
    return (best_tier or tiers), best_credits

def current_utc_month():
    return datetime.now(timezone.utc).strftime('%Y-%m')

def month_bounds(month):
    """'YYYY-MM' -> ('YYYY-MM-01', first day of the next month) as ISO dates."""
    year, mon = int(month[:4]), int(month[5:7])
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start.isoformat(), end.isoformat()

# --- Theme helpers ---

def get_plotly_template():
    return 'plotly_dark' if st.session_state.theme == 'dark' else 'plotly_white'

def get_chart_colors():
    if st.session_state.theme == 'dark':
        return {'paper_bgcolor': '#0e1117', 'plot_bgcolor': '#0e1117',
                'font_color': '#ffffff', 'title_color': '#ffffff'}
    return {'paper_bgcolor': '#ffffff', 'plot_bgcolor': '#ffffff',
            'font_color': '#1f2937', 'title_color': '#1f2937'}

# Modern color palette
CHART_COLORS = ['#4361ee', '#3a0ca3', '#7209b7', '#f72585', '#4cc9f0',
                '#4895ef', '#560bad', '#b5179e', '#f77f00', '#06d6a0']

def apply_chart_theme(fig):
    colors = get_chart_colors()
    fc, tc = colors['font_color'], colors['title_color']
    fig.update_layout(
        template=get_plotly_template(),
        paper_bgcolor=colors['paper_bgcolor'], plot_bgcolor=colors['plot_bgcolor'],
        font=dict(color=fc, size=12, family="Inter, system-ui, sans-serif"),
        title=dict(font=dict(color=tc, size=15, family="Inter, system-ui, sans-serif"), x=0.5, xanchor='center'),
        legend=dict(font=dict(color=fc, size=11), bgcolor='rgba(0,0,0,0)', borderwidth=0),
        margin=dict(l=40, r=40, t=50, b=40),
        hoverlabel=dict(bgcolor=colors['paper_bgcolor'], font_size=12, font_color=fc),
    )
    fig.update_xaxes(
        title_font=dict(color=fc, size=11), tickfont=dict(color=fc, size=10),
        gridcolor='rgba(128,128,128,0.1)', showline=True, linewidth=1, linecolor='rgba(128,128,128,0.2)'
    )
    fig.update_yaxes(
        title_font=dict(color=fc, size=11), tickfont=dict(color=fc, size=10),
        gridcolor='rgba(128,128,128,0.1)', showline=False
    )
    fig.update_annotations(font=dict(color=tc, size=13, family="Inter, system-ui, sans-serif"))
    return fig

def safe_float(val, default=0.0):
    try:
        return float(val) if val and str(val).strip() not in ('', 'None') else default
    except (ValueError, TypeError):
        return default

def safe_int(val, default=0):
    try:
        return int(float(val)) if val and str(val).strip() not in ('', 'None') else default
    except (ValueError, TypeError):
        return default

def _sql_string_list(values):
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)

def build_user_filter_clause(selected_userids):
    """Build a SQL WHERE clause fragment for user filtering."""
    if not selected_userids:
        return ""
    return f" AND userid IN ({_sql_string_list(selected_userids)})"

def build_user_exclusion_clause(userids):
    """Build a SQL WHERE clause fragment that leaves these users out."""
    if not userids:
        return ""
    return f" AND userid NOT IN ({_sql_string_list(userids)})"

def generate_export_html(figures, title="Kiro Users Report"):
    """Generate a self-contained HTML file with all Plotly charts."""
    charts_html = ""
    for fig_name, fig in figures:
        charts_html += f'<h2>{fig_name}</h2>\n'
        charts_html += fig.to_html(full_html=False, include_plotlyjs=False)
        charts_html += '\n<hr>\n'
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"></script>
<style>
body {{ font-family: Inter, system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}
h1 {{ color: #1f2937; }} h2 {{ color: #374151; margin-top: 2rem; }}
hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 2rem 0; }}
.meta {{ color: #6b7280; font-size: 0.9rem; }}
</style></head><body>
<h1>⚡ {title}</h1>
<p class="meta">Exported on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<hr>
{charts_html}
</body></html>"""
    return html


# --- Dashboard queries ---

def build_section_queries(table_name, where_sql, monthly):
    """SQL for every dashboard section. where_sql holds ' AND ...' fragments
    (month range, hidden users, user filter)."""
    queries = {
        'overall': f"""
        SELECT
            COUNT(DISTINCT userid) as total_users,
            SUM(TRY_CAST(total_messages AS INTEGER)) as total_messages,
            SUM(TRY_CAST(chat_conversations AS INTEGER)) as total_conversations,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as total_credits,
            SUM(TRY_CAST(overage_credits_used AS DOUBLE)) as total_overage
        FROM {table_name}
        WHERE 1=1{where_sql}
        """,
        'client': f"""
        SELECT
            client_type,
            COUNT(DISTINCT userid) as unique_users,
            SUM(TRY_CAST(total_messages AS INTEGER)) as total_messages,
            SUM(TRY_CAST(chat_conversations AS INTEGER)) as total_conversations,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as total_credits
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY client_type
        ORDER BY total_messages DESC
        """,
        'top_users': f"""
        SELECT
            userid,
            SUM(TRY_CAST(total_messages AS INTEGER)) as total_messages,
            SUM(TRY_CAST(chat_conversations AS INTEGER)) as total_conversations,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as total_credits
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY userid
        ORDER BY total_messages DESC
        LIMIT 10
        """,
        'daily': f"""
        SELECT
            date,
            SUM(TRY_CAST(total_messages AS INTEGER)) as messages,
            SUM(TRY_CAST(chat_conversations AS INTEGER)) as conversations,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as credits,
            COUNT(DISTINCT userid) as active_users
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY date
        ORDER BY date
        """,
        'daily_client': f"""
        SELECT
            date,
            client_type,
            SUM(TRY_CAST(total_messages AS INTEGER)) as messages,
            SUM(TRY_CAST(chat_conversations AS INTEGER)) as conversations
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY date, client_type
        ORDER BY date
        """,
        'credits_user': f"""
        SELECT
            userid,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as total_credits,
            SUM(TRY_CAST(overage_credits_used AS DOUBLE)) as total_overage,
            MAX(TRY_CAST(overage_cap AS DOUBLE)) as overage_cap,
            MAX(overage_enabled) as overage_enabled
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY userid
        ORDER BY total_credits DESC
        """,
        'tier': f"""
        SELECT
            subscription_tier,
            COUNT(DISTINCT userid) as unique_users,
            SUM(TRY_CAST(total_messages AS INTEGER)) as total_messages,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as total_credits
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY subscription_tier
        ORDER BY total_messages DESC
        """,
        'users': f"""
        SELECT
            userid,
            SUM(TRY_CAST(total_messages AS INTEGER)) as total_messages,
            SUM(TRY_CAST(chat_conversations AS INTEGER)) as total_conversations,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as total_credits
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY userid
        ORDER BY total_messages DESC
        """,
        'activity': f"""
        SELECT
            userid,
            MAX(date) as last_active_date,
            MIN(date) as first_active_date,
            COUNT(DISTINCT date) as active_days
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY userid
        """,
    }
    if monthly:
        queries['allowance'] = f"""
        SELECT
            userid,
            array_join(array_agg(DISTINCT subscription_tier), ',') as tiers,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as credits,
            SUM(TRY_CAST(overage_credits_used AS DOUBLE)) as overage,
            SUM(TRY_CAST(total_messages AS INTEGER)) as messages,
            SUM(TRY_CAST(chat_conversations AS INTEGER)) as conversations,
            COUNT(DISTINCT date) as active_days,
            MAX(date) as last_active
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY userid
        """
    else:
        queries['credits_monthly'] = f"""
        SELECT
            userid,
            DATE_FORMAT(DATE_PARSE(date, '%Y-%m-%d'), '%Y-%m') as month,
            SUM(TRY_CAST(credits_used AS DOUBLE)) as credits_used
        FROM {table_name}
        WHERE 1=1{where_sql}
        GROUP BY userid, DATE_FORMAT(DATE_PARSE(date, '%Y-%m-%d'), '%Y-%m')
        ORDER BY month, userid
        """
    return queries


# --- Page parts ---

def render_user_filter(options, key):
    """User multiselect. options maps label -> list of raw userids.
    Returns (selected raw userids, selected labels)."""
    # Drop selections that are not options any more (e.g. after switching month).
    if key in st.session_state:
        st.session_state[key] = [label for label in st.session_state[key] if label in options]

    def clear_filter():
        st.session_state[key] = []

    st.header("🔍 User Filter")
    filter_col_a, filter_col_b = st.columns([5, 1])
    with filter_col_a:
        selected_labels = st.multiselect(
            "Select users to filter (leave empty for all users)",
            options=sorted(options),
            key=key,
            help="Filter all dashboard sections by selected users"
        )
    with filter_col_b:
        st.markdown("<div style='margin-top: 1.7rem;'></div>", unsafe_allow_html=True)
        st.button("Clear Filter", key=f"{key}_clear", on_click=clear_filter)

    selected_raw = [raw for label in selected_labels for raw in options[label]]
    if selected_labels:
        st.info(f"🔍 Filtering by {len(selected_labels)} user(s): {', '.join(selected_labels)}")
    return selected_raw, selected_labels


def render_metric_definitions():
    with st.expander("ℹ️ Metric Definitions", expanded=False):
        st.markdown("""
        **Date**: Date of the report activity (UTC).

        **UserId**: ID of the user for whom the activity is reported.

        **Client Type**: KIRO_IDE, KIRO_CLI, PLUGIN (newer reports also show KIRO_WEB).

        **Subscription Tier**: Kiro subscription plan — Pro, Pro+ (PRO_PLUS), Pro Max (PRO_MAX), Power.

        **ProfileId**: Profile associated with the user activity.

        **Total Messages**: Number of messages sent to and from Kiro.

        **Chat Conversations**: Number of conversations by the user during the day.

        **Credits Used**: Credits consumed during the day. In the report data this already includes
        overage credits, so plan (base) credits = Credits Used − Overage Credits Used.

        **Monthly Credits**: Plan credits included in the user's tier each month — Pro 1,000,
        Pro+ 2,000, Pro Max 5,000, Power 10,000. Kiro resets them at the start of every calendar
        month. A mid-month upgrade applies the higher tier to the whole month.

        **Overage Enabled**: Whether overage is enabled for this user.

        **Overage Cap**: Overage limit set by the admin when overage is enabled.

        **Overage Credits Used**: Total number of overage credits used by the user.

        **Removed / disabled users**: Users deleted from, or disabled in, IAM Identity Center.
        The Monthly view hides them. The All-time view keeps their history and labels them.

        ---

        📖 **Learn more**: [Kiro Documentation - Monitor and Track](https://kiro.dev/docs/enterprise/monitor-and-track/)
        """)


def render_monthly_allowance(df, labels, month, export_figures, export_dataframes):
    """Per-user credits for one month against the tier's monthly plan credits."""
    st.header("💳 Credits vs Monthly Allowance")
    st.caption("Plan credits reset at the start of each calendar month. "
               "Used % = Credits Used ÷ Monthly Credits, so overage pushes it past 100%.")

    df = df.copy()
    df['userid'] = df['userid'].map(clean_userid)
    for c in ('credits', 'overage'):
        df[c] = df[c].apply(safe_float)
    for c in ('messages', 'conversations', 'active_days'):
        df[c] = df[c].apply(safe_int)
    plans = df['tiers'].apply(plan_for_tiers)
    df['tier'] = [tier for tier, _ in plans]
    df['allowance'] = pd.to_numeric(pd.Series([credits for _, credits in plans], index=df.index), errors='coerce')
    df['plan_used'] = (df['credits'] - df['overage']).clip(lower=0)
    df['used_pct'] = df['credits'] / df['allowance'] * 100
    df['remaining'] = (df['allowance'] - df['plan_used']).clip(lower=0)
    df['User'] = df['userid'].map(lambda u: labels.get(u, u))
    df = df.sort_values('credits', ascending=False)

    table = pd.DataFrame({
        'User': df['User'],
        'Tier': df['tier'],
        'Monthly Credits': df['allowance'],
        'Credits Used': df['credits'].round(1),
        'Overage Credits': df['overage'].round(1),
        'Used %': df['used_pct'].round(1),
        'Remaining Plan Credits': df['remaining'].round(1),
        'Messages': df['messages'],
        'Conversations': df['conversations'],
        'Active Days': df['active_days'],
        'Last Active': df['last_active'],
    })

    col1, col2 = st.columns([3, 2])
    with col1:
        st.dataframe(
            table, width='stretch', hide_index=True,
            column_config={
                'Monthly Credits': st.column_config.NumberColumn(
                    format='%d', help="Plan credits of the tier (highest tier held this month)"),
                'Used %': st.column_config.ProgressColumn(
                    format='%.0f%%', min_value=0, max_value=100,
                    help="Credits Used ÷ Monthly Credits"),
                'Remaining Plan Credits': st.column_config.NumberColumn(
                    help="Monthly Credits − (Credits Used − Overage Credits)"),
            })
    with col2:
        chart = df.sort_values('credits')
        fig = go.Figure()
        fig.add_trace(go.Bar(y=chart['User'], x=chart['plan_used'], orientation='h',
                             name='Plan credits', marker_color='#4361ee'))
        fig.add_trace(go.Bar(y=chart['User'], x=chart['overage'], orientation='h',
                             name='Overage credits', marker_color='#f72585'))
        fig.add_trace(go.Scatter(y=chart['User'], x=chart['allowance'], mode='markers',
                                 name='Monthly allowance',
                                 marker=dict(symbol='line-ns-open', size=26,
                                             color=current_theme['text'], line=dict(width=3))))
        fig.update_layout(barmode='stack', title=f'Credits Used vs Allowance ({month})',
                          height=max(300, 70 * len(chart) + 140),
                          legend=dict(orientation='h', yanchor='bottom', y=-0.35, xanchor='center', x=0.5))
        apply_chart_theme(fig)
        st.plotly_chart(fig, width='stretch')

    export_figures.append((f'Credits Used vs Allowance ({month})', fig))
    export_dataframes[f'monthly_credits_{month}'] = table
    st.markdown("---")


def render_sections(table_name, where_sql, labels, month=None, selected_labels=()):
    """Render the dashboard sections for one scope: a month (monthly view)
    or everything (month=None, all-time view)."""
    monthly = month is not None
    filter_active = len(selected_labels) > 0
    name_of = lambda uid: labels.get(uid, uid)

    # Collection for export
    export_figures = []
    export_dataframes = {}

    queries = build_section_queries(table_name, where_sql, monthly)
    data = dict(zip(queries, fetch_many(tuple(queries.values()))))

    render_metric_definitions()

    # ── Overall Metrics ──
    st.header(f"📈 Overview — {month}" if monthly else "📈 Overall Metrics")

    df_overall = data['overall']

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        if monthly:
            st.metric("Active Users", safe_int(df_overall['total_users'].iloc[0]),
                      help="Current users with Kiro activity in this month")
        else:
            st.metric("Total Users", safe_int(df_overall['total_users'].iloc[0]),
                      help="Unique users who have used Kiro, including users removed later")
    with col2:
        st.metric("Total Messages", f"{safe_int(df_overall['total_messages'].iloc[0]):,}",
                  help="Total messages sent to Kiro")
    with col3:
        st.metric("Chat Conversations", f"{safe_int(df_overall['total_conversations'].iloc[0]):,}",
                  help="Total chat conversations initiated")
    with col4:
        st.metric("Credits Used", f"{safe_float(df_overall['total_credits'].iloc[0]):,.1f}",
                  help="Total credits consumed across all users and client types, including overage")
    with col5:
        st.metric("Overage Credits", f"{safe_float(df_overall['total_overage'].iloc[0]):,.1f}",
                  help="Credits consumed beyond the plan allowance (part of Credits Used)")

    st.markdown("---")

    if monthly:
        render_monthly_allowance(data['allowance'], labels, month, export_figures, export_dataframes)

    # ── Client Type Breakdown ──
    st.header("🖥️ Usage by Client Type")

    df_client = data['client'].copy()
    for c in ['unique_users', 'total_messages', 'total_conversations']:
        df_client[c] = df_client[c].apply(safe_int)
    df_client['total_credits'] = df_client['total_credits'].apply(safe_float)

    col1, col2 = st.columns(2)
    with col1:
        fig_client_pie = px.pie(
            df_client, values='total_messages', names='client_type',
            title='Messages by Client Type', hole=0.45,
            color_discrete_sequence=CHART_COLORS
        )
        fig_client_pie.update_traces(textinfo='label+percent', textposition='outside',
                                     pull=[0.03] * len(df_client))
        apply_chart_theme(fig_client_pie)
        st.plotly_chart(fig_client_pie, width='stretch')
        export_figures.append(('Messages by Client Type', fig_client_pie))

    with col2:
        fig_client_bar = px.bar(
            df_client, x='client_type', y='total_credits',
            title='Credits Used by Client Type',
            color='client_type', text='total_credits',
            color_discrete_sequence=CHART_COLORS,
            labels={'total_credits': 'Credits', 'client_type': 'Client Type'}
        )
        fig_client_bar.update_traces(texttemplate='%{text:.1f}', textposition='outside',
                                      marker_line_width=0)
        fig_client_bar.update_layout(showlegend=False, bargap=0.4)
        apply_chart_theme(fig_client_bar)
        st.plotly_chart(fig_client_bar, width='stretch')
        export_figures.append(('Credits Used by Client Type', fig_client_bar))

    st.markdown("---")

    # ── Top 10 Users ──
    st.header("🏆 Top 10 Users by Messages")

    df_top = data['top_users'].copy()
    df_top['userid'] = df_top['userid'].map(clean_userid)
    df_top['total_messages'] = df_top['total_messages'].apply(safe_int)
    df_top['total_conversations'] = df_top['total_conversations'].apply(safe_int)
    df_top['total_credits'] = df_top['total_credits'].apply(safe_float)
    df_top['username'] = df_top['userid'].map(name_of)

    col1, col2 = st.columns([2, 3])
    with col1:
        st.subheader("🥇 Leaderboard")
        for idx, row in df_top.iterrows():
            rank = idx + 1
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"{rank}."
            st.markdown(f"**{medal} {row['username']}** — {row['total_messages']:,} messages")

    with col2:
        fig_top = px.bar(
            df_top, x='username', y='total_messages',
            title='Messages Sent', color='total_messages',
            color_continuous_scale='Purples',
            labels={'total_messages': 'Messages', 'username': 'User'}
        )
        fig_top.update_traces(marker_line_width=0)
        fig_top.update_layout(xaxis_tickangle=-45, showlegend=False, height=400, coloraxis_showscale=False)
        apply_chart_theme(fig_top)
        st.plotly_chart(fig_top, width='stretch')
        export_figures.append(('Top Users by Messages', fig_top))

    st.markdown("---")

    # ── Daily Activity Trends ──
    st.header("📅 Daily Activity Trends")

    df_daily = data['daily'].copy()
    df_daily['date'] = pd.to_datetime(df_daily['date'])
    df_daily['messages'] = df_daily['messages'].apply(safe_int)
    df_daily['conversations'] = df_daily['conversations'].apply(safe_int)
    df_daily['credits'] = df_daily['credits'].apply(safe_float)
    df_daily['active_users'] = df_daily['active_users'].apply(safe_int)

    fig_daily = make_subplots(
        rows=2, cols=2,
        subplot_titles=('Total Messages', 'Chat Conversations', 'Credits Used', 'Active Users'),
        vertical_spacing=0.15, horizontal_spacing=0.15
    )
    fig_daily.add_trace(
        go.Scatter(x=df_daily['date'], y=df_daily['messages'],
                   mode='lines+markers', name='Messages',
                   line=dict(color='#4361ee', width=2.5), marker=dict(size=5)),
        row=1, col=1)
    fig_daily.add_trace(
        go.Scatter(x=df_daily['date'], y=df_daily['conversations'],
                   mode='lines+markers', name='Conversations',
                   line=dict(color='#f72585', width=2.5), marker=dict(size=5)),
        row=1, col=2)
    fig_daily.add_trace(
        go.Scatter(x=df_daily['date'], y=df_daily['credits'],
                   mode='lines+markers', name='Credits',
                   line=dict(color='#06d6a0', width=2.5), marker=dict(size=5)),
        row=2, col=1)
    fig_daily.add_trace(
        go.Scatter(x=df_daily['date'], y=df_daily['active_users'],
                   mode='lines+markers', name='Active Users',
                   line=dict(color='#f77f00', width=2.5), marker=dict(size=5)),
        row=2, col=2)
    fig_daily.update_layout(height=600, showlegend=False,
                            title=dict(text="Daily Activity Overview", x=0.5, xanchor='center'))
    apply_chart_theme(fig_daily)
    st.plotly_chart(fig_daily, width='stretch')
    export_figures.append(('Daily Activity Overview', fig_daily))

    st.markdown("---")

    # ── Daily Trends by Client Type ──
    st.header("📊 Daily Trends by Client Type")

    df_dc = data['daily_client'].copy()
    df_dc['date'] = pd.to_datetime(df_dc['date'])
    df_dc['messages'] = df_dc['messages'].apply(safe_int)
    df_dc['conversations'] = df_dc['conversations'].apply(safe_int)

    col1, col2 = st.columns(2)
    with col1:
        fig_msg_client = px.line(
            df_dc, x='date', y='messages', color='client_type',
            title='Daily Messages by Client Type', markers=True,
            color_discrete_sequence=CHART_COLORS,
            labels={'messages': 'Messages', 'date': 'Date', 'client_type': 'Client'}
        )
        fig_msg_client.update_traces(line=dict(width=2.5), marker=dict(size=5))
        apply_chart_theme(fig_msg_client)
        st.plotly_chart(fig_msg_client, width='stretch')
        export_figures.append(('Daily Messages by Client Type', fig_msg_client))

    with col2:
        fig_conv_client = px.line(
            df_dc, x='date', y='conversations', color='client_type',
            title='Daily Conversations by Client Type', markers=True,
            color_discrete_sequence=CHART_COLORS[2:],
            labels={'conversations': 'Conversations', 'date': 'Date', 'client_type': 'Client'}
        )
        fig_conv_client.update_traces(line=dict(width=2.5), marker=dict(size=5))
        apply_chart_theme(fig_conv_client)
        st.plotly_chart(fig_conv_client, width='stretch')
        export_figures.append(('Daily Conversations by Client Type', fig_conv_client))

    st.markdown("---")

    # ── Credits Analysis ──
    st.header("💰 Credits Analysis")

    df_credits = data['credits_user'].copy()
    df_credits['userid'] = df_credits['userid'].map(clean_userid)
    df_credits['total_credits'] = df_credits['total_credits'].apply(safe_float)
    df_credits['total_overage'] = df_credits['total_overage'].apply(safe_float)
    df_credits['overage_cap'] = df_credits['overage_cap'].apply(safe_float)
    df_credits['username'] = df_credits['userid'].map(name_of)
    # credits_used already includes overage credits; do not add them again.

    col1, col2 = st.columns(2)
    with col1:
        fig_credits = px.bar(
            df_credits.head(15), x='username', y='total_credits',
            title='Top 15 Users by Total Credits',
            color='total_credits', color_continuous_scale='Sunset',
            labels={'total_credits': 'Credits', 'username': 'User'}
        )
        fig_credits.update_traces(marker_line_width=0)
        fig_credits.update_layout(xaxis_tickangle=-45, showlegend=False, coloraxis_showscale=False)
        apply_chart_theme(fig_credits)
        st.plotly_chart(fig_credits, width='stretch')
        export_figures.append(('Top 15 Users by Total Credits', fig_credits))

    with col2:
        total_credits_sum = df_credits['total_credits'].sum()
        total_overage_sum = df_credits['total_overage'].sum()
        df_credits_summary = pd.DataFrame({
            'Category': ['Base Credits', 'Overage Credits'],
            'Amount': [max(total_credits_sum - total_overage_sum, 0), total_overage_sum]
        })
        # A zero slice still draws an outside label, which lands on the chart title.
        df_credits_summary = df_credits_summary[df_credits_summary['Amount'] > 0]
        fig_overage = px.pie(
            df_credits_summary, values='Amount', names='Category',
            title='Base vs Overage Credits', hole=0.45,
            color_discrete_sequence=['#4361ee', '#f72585']
        )
        fig_overage.update_traces(textinfo='label+percent', textposition='outside',
                                   pull=[0.03, 0.03])
        apply_chart_theme(fig_overage)
        st.plotly_chart(fig_overage, width='stretch')
        export_figures.append(('Base vs Overage Credits', fig_overage))

    if not monthly:
        # Monthly credit usage by user table (history, including removed users)
        st.subheader("📅 Credit Usage by User by Month")

        df_credits_monthly = data['credits_monthly'].copy()
        df_credits_monthly['userid'] = df_credits_monthly['userid'].map(clean_userid)
        df_credits_monthly['credits_used'] = df_credits_monthly['credits_used'].apply(safe_float)
        df_credits_monthly['User'] = df_credits_monthly['userid'].map(name_of)

        df_pivot = df_credits_monthly.pivot_table(
            index='User', columns='month', values='credits_used',
            aggfunc='sum', fill_value=0
        )
        df_pivot = df_pivot[sorted(df_pivot.columns)]
        df_pivot['Total'] = df_pivot.sum(axis=1)
        df_pivot = df_pivot.sort_values('Total', ascending=False)
        df_pivot = df_pivot.round(1)

        st.dataframe(df_pivot, width='stretch', height=400)
        export_dataframes['credit_usage_by_month'] = df_pivot.reset_index()

    st.markdown("---")

    # ── Subscription Tier Breakdown ──
    st.header("🎫 Subscription Tier Breakdown")

    df_tier = data['tier'].copy()
    df_tier['unique_users'] = df_tier['unique_users'].apply(safe_int)
    df_tier['total_messages'] = df_tier['total_messages'].apply(safe_int)
    df_tier['total_credits'] = df_tier['total_credits'].apply(safe_float)

    col1, col2 = st.columns(2)
    with col1:
        fig_tier_users = px.bar(
            df_tier, x='subscription_tier', y='unique_users',
            title='Users by Subscription Tier', color='subscription_tier',
            color_discrete_sequence=CHART_COLORS,
            labels={'unique_users': 'Users', 'subscription_tier': 'Tier'}
        )
        fig_tier_users.update_traces(marker_line_width=0)
        fig_tier_users.update_layout(showlegend=False, bargap=0.4)
        apply_chart_theme(fig_tier_users)
        st.plotly_chart(fig_tier_users, width='stretch')
        export_figures.append(('Users by Subscription Tier', fig_tier_users))

    with col2:
        fig_tier_credits = px.bar(
            df_tier, x='subscription_tier', y='total_credits',
            title='Credits by Subscription Tier', color='subscription_tier',
            color_discrete_sequence=CHART_COLORS[3:],
            labels={'total_credits': 'Credits', 'subscription_tier': 'Tier'}
        )
        fig_tier_credits.update_traces(marker_line_width=0)
        fig_tier_credits.update_layout(showlegend=False, bargap=0.4)
        apply_chart_theme(fig_tier_credits)
        st.plotly_chart(fig_tier_credits, width='stretch')
        export_figures.append(('Credits by Subscription Tier', fig_tier_credits))

    st.markdown("---")

    # ── User Engagement Analysis ──
    st.header("👥 User Engagement Analysis")

    df_users = data['users'].copy()
    df_users['userid'] = df_users['userid'].map(clean_userid)
    df_users['total_messages'] = df_users['total_messages'].apply(safe_int)
    df_users['total_conversations'] = df_users['total_conversations'].apply(safe_int)
    df_users['total_credits'] = df_users['total_credits'].apply(safe_float)
    df_users['username'] = df_users['userid'].map(name_of)

    st.subheader("📊 User Segmentation")

    def categorize_user(row):
        if row['total_messages'] >= 100 or row['total_conversations'] >= 20:
            return 'Power Users'
        elif row['total_messages'] >= 20 or row['total_conversations'] >= 5:
            return 'Active Users'
        elif row['total_messages'] > 0:
            return 'Light Users'
        else:
            return 'Idle Users'

    df_users['category'] = df_users.apply(categorize_user, axis=1)
    category_counts = df_users['category'].value_counts()
    pie_data = pd.DataFrame({'Category': category_counts.index, 'Count': category_counts.values})

    color_map = {'Power Users': '#4361ee', 'Active Users': '#06d6a0',
                 'Light Users': '#f77f00', 'Idle Users': '#e63946'}
    colors = [color_map.get(cat, '#999999') for cat in pie_data['Category']]

    col1, col2 = st.columns([2, 1])
    with col1:
        fig_seg = go.Figure(data=[go.Pie(
            labels=pie_data['Category'], values=pie_data['Count'], hole=0.45,
            marker=dict(colors=colors, line=dict(color=current_theme['bg'], width=3)),
            textinfo='label+percent+value', textposition='auto',
            hovertemplate='<b>%{label}</b><br>Users: %{value}<br>%{percent}<extra></extra>'
        )])
        fig_seg.update_layout(title='User Distribution by Engagement Level', height=450,
                              showlegend=True,
                              legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.05))
        apply_chart_theme(fig_seg)
        st.plotly_chart(fig_seg, width='stretch')
        export_figures.append(('User Engagement Segmentation', fig_seg))

    with col2:
        st.markdown("### Category Definitions")
        st.markdown("""
        **🚀 Power Users**
        100+ messages OR 20+ conversations

        **💼 Active Users**
        20+ messages OR 5+ conversations

        **🌱 Light Users**
        At least 1 message sent

        **😴 Idle Users**
        No activity recorded
        """)
        st.markdown("---")
        st.markdown("### Quick Stats")
        for _, row in pie_data.iterrows():
            pct = (row['Count'] / pie_data['Count'].sum() * 100)
            st.metric(row['Category'], f"{row['Count']} users", f"{pct:.1f}%")

    st.markdown("---")

    # ── User Activity Timeline ──
    st.subheader("📅 User Activity Timeline")

    df_activity = data['activity'].copy()
    df_activity['userid'] = df_activity['userid'].map(clean_userid)
    df_activity['last_active_date'] = pd.to_datetime(df_activity['last_active_date'])
    df_activity['first_active_date'] = pd.to_datetime(df_activity['first_active_date'])
    df_activity['active_days'] = df_activity['active_days'].apply(safe_int)
    df_activity['days_since_last_active'] = (pd.Timestamp.now() - df_activity['last_active_date']).dt.days
    df_activity['username'] = df_activity['userid'].map(name_of)

    df_act_merged = df_activity.merge(
        df_users[['userid', 'category', 'total_messages', 'total_credits']],
        on='userid', how='left'
    )

    col1, col2 = st.columns(2)
    with col1:
        df_recent = df_act_merged.nsmallest(15, 'days_since_last_active')
        fig_last = px.bar(
            df_recent, y='username', x='days_since_last_active',
            title='Days Since Last Activity (Top 15 Recent)',
            color='days_since_last_active', color_continuous_scale='Tealgrn_r',
            orientation='h', labels={'days_since_last_active': 'Days Ago', 'username': 'User'}
        )
        fig_last.update_traces(marker_line_width=0)
        fig_last.update_layout(height=500, yaxis={'categoryorder': 'total ascending'}, coloraxis_showscale=False)
        apply_chart_theme(fig_last)
        st.plotly_chart(fig_last, width='stretch')
        export_figures.append(('Days Since Last Activity', fig_last))

    with col2:
        df_most = df_act_merged.nlargest(15, 'active_days')
        fig_days = px.bar(
            df_most, y='username', x='active_days',
            title='Total Active Days (Top 15)',
            color='active_days', color_continuous_scale='Purples',
            orientation='h', labels={'active_days': 'Active Days', 'username': 'User'}
        )
        fig_days.update_traces(marker_line_width=0)
        fig_days.update_layout(height=500, yaxis={'categoryorder': 'total ascending'}, coloraxis_showscale=False)
        apply_chart_theme(fig_days)
        st.plotly_chart(fig_days, width='stretch')
        export_figures.append(('Total Active Days', fig_days))

    # Detailed table
    st.markdown("#### 📋 Detailed User Activity Table")
    df_display = df_act_merged[['username', 'category', 'last_active_date',
                                 'days_since_last_active', 'active_days',
                                 'total_messages', 'total_credits']].copy()
    df_display.columns = ['User', 'Category', 'Last Active', 'Days Ago',
                          'Active Days', 'Messages', 'Credits']
    df_display['Last Active'] = df_display['Last Active'].dt.strftime('%Y-%m-%d')
    df_display = df_display.sort_values('Days Ago')

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        cat_filter = st.multiselect('Filter by Category',
                                    options=['All'] + sorted(df_display['Category'].dropna().unique().tolist()),
                                    default=['All'])
    with filter_col2:
        rec_filter = st.selectbox('Filter by Recency',
                                  ['All Users', 'Active (Last 7 days)', 'Recent (Last 30 days)',
                                   'Inactive (30+ days)', 'Dormant (90+ days)'])
    with filter_col3:
        sort_by = st.selectbox('Sort by', ['Days Ago', 'Active Days', 'Messages', 'Credits'])

    df_f = df_display.copy()
    if 'All' not in cat_filter:
        df_f = df_f[df_f['Category'].isin(cat_filter)]
    if rec_filter == 'Active (Last 7 days)':
        df_f = df_f[df_f['Days Ago'] <= 7]
    elif rec_filter == 'Recent (Last 30 days)':
        df_f = df_f[df_f['Days Ago'] <= 30]
    elif rec_filter == 'Inactive (30+ days)':
        df_f = df_f[df_f['Days Ago'] > 30]
    elif rec_filter == 'Dormant (90+ days)':
        df_f = df_f[df_f['Days Ago'] > 90]
    df_f = df_f.sort_values(sort_by, ascending=(sort_by == 'Days Ago'))

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Filtered Users", len(df_f))
    with m2:
        st.metric("Avg Days Since Active", f"{df_f['Days Ago'].mean():.1f}" if len(df_f) else "N/A")
    with m3:
        st.metric("Avg Active Days", f"{df_f['Active Days'].mean():.1f}" if len(df_f) else "N/A")
    with m4:
        st.metric("Active Last Week", len(df_f[df_f['Days Ago'] <= 7]))

    st.dataframe(df_f, width='stretch', height=400, hide_index=True)
    export_dataframes['user_activity_detail'] = df_f

    st.markdown("---")

    # ── User Engagement Funnel ──
    st.header("🔻 User Engagement Funnel")

    total_users = len(df_users)
    users_with_messages = len(df_users[df_users['total_messages'] > 0])
    users_with_convos = len(df_users[df_users['total_conversations'] > 0])
    active_users = len(df_users[df_users['total_messages'] >= 20])
    power_users = len(df_users[df_users['total_messages'] >= 100])

    funnel_data = pd.DataFrame({
        'Stage': ['All Users', 'Sent Messages', 'Had Conversations',
                  'Active Users (20+ msgs)', 'Power Users (100+ msgs)'],
        'Count': [total_users, users_with_messages, users_with_convos, active_users, power_users]
    })
    funnel_data['Percentage'] = (funnel_data['Count'] / max(total_users, 1) * 100).round(1)

    col1, col2 = st.columns([3, 2])
    with col1:
        fig_funnel = go.Figure(go.Funnel(
            y=funnel_data['Stage'], x=funnel_data['Count'],
            textposition="inside", textinfo="value+percent initial", opacity=0.9,
            marker={"color": ['#4361ee', '#3a0ca3', '#7209b7', '#f72585', '#4cc9f0'],
                    "line": {"width": 0}},
            connector={"line": {"color": 'rgba(128,128,128,0.2)', "width": 1}}
        ))
        fig_funnel.update_layout(title='User Engagement Funnel', height=500,
                                 margin=dict(l=20, r=20, t=60, b=20))
        apply_chart_theme(fig_funnel)
        st.plotly_chart(fig_funnel, width='stretch')
        export_figures.append(('User Engagement Funnel', fig_funnel))

    with col2:
        st.subheader("📊 Funnel Metrics")
        for _, row in funnel_data.iterrows():
            st.metric(label=row['Stage'], value=f"{row['Count']} users",
                      delta=f"{row['Percentage']}% of total")
            st.markdown("")

        st.markdown("---")
        st.subheader("🔄 Conversion Rates")
        if total_users > 0:
            st.markdown(f"**Message Activation:** {users_with_messages / total_users * 100:.1f}%")
            if users_with_messages > 0:
                st.markdown(f"**Conversation Rate:** {users_with_convos / users_with_messages * 100:.1f}%")
                st.markdown(f"**Active Retention:** {active_users / users_with_messages * 100:.1f}%")
            if active_users > 0:
                st.markdown(f"**Power User Growth:** {power_users / active_users * 100:.1f}%")

    # ── Export Section ──
    st.markdown("---")
    st.header("📥 Export Report")

    scope_tag = month if monthly else 'all-time'
    scope_title = month if monthly else 'All-time'
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    export_col1, export_col2, export_col3 = st.columns(3)

    with export_col1:
        if export_figures:
            filter_label = f" ({', '.join(selected_labels)})" if filter_active else ""
            html_content = generate_export_html(
                export_figures,
                title=f"Kiro Users Report — {scope_title}{filter_label}"
            )
            st.download_button(
                label="📊 Download Charts (HTML)",
                data=html_content,
                file_name=f"kiro_report_{scope_tag}_{stamp}.html",
                mime="text/html",
                help="Download all charts as an interactive HTML file"
            )

    with export_col2:
        if export_dataframes:
            csv_buffer = io.BytesIO()
            with pd.ExcelWriter(csv_buffer, engine='openpyxl') as writer:
                for sheet_name, df_export in export_dataframes.items():
                    df_export.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            st.download_button(
                label="📋 Download Data (Excel)",
                data=csv_buffer.getvalue(),
                file_name=f"kiro_data_{scope_tag}_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.document",
                help="Download data tables as Excel file"
            )

    with export_col3:
        all_csv_parts = []
        for name, df_export in export_dataframes.items():
            all_csv_parts.append(f"# {name}")
            all_csv_parts.append(df_export.to_csv(index=False))
            all_csv_parts.append("")
        if all_csv_parts:
            csv_content = "\n".join(all_csv_parts)
            st.download_button(
                label="📄 Download Data (CSV)",
                data=csv_content,
                file_name=f"kiro_data_{scope_tag}_{stamp}.csv",
                mime="text/csv",
                help="Download data tables as CSV file"
            )


def render_monthly_view(table_name, df_months, raw_by_clean, labels, statuses):
    """One calendar month, current users only."""
    current_month = current_utc_month()
    ranges = {row.month: (row.first_day, row.last_day) for row in df_months.itertuples()
              if re.fullmatch(r'\d{4}-\d{2}', str(row.month))}
    months = sorted(set(ranges) | {current_month}, reverse=True)

    col_month, col_note = st.columns([1, 3])
    with col_month:
        month = st.selectbox(
            "Month", months, key='month',
            format_func=lambda m: f"{m} (current month)" if m == current_month else m,
            help="Kiro resets plan credits at the start of every calendar month."
        )
    with col_note:
        st.markdown("<div style='margin-top: 1.9rem;'></div>", unsafe_allow_html=True)
        if month in ranges:
            first_day, last_day = ranges[month]
            st.caption(f"📆 Usage reported for {month}: {first_day} to {last_day} (UTC report dates). "
                       "Plan credits reset at the start of each calendar month.")
        else:
            st.caption(f"📆 No usage reported for {month} yet. Kiro writes each day's report "
                       "at about 02:00 UTC the following day.")

    start, end = month_bounds(month)
    month_sql = f" AND date >= '{start}' AND date < '{end}'"

    # Users removed from or disabled in IAM Identity Center are hidden in this view.
    inactive = sorted(u for u in raw_by_clean if statuses.get(u) in INACTIVE_STATUSES)
    if inactive and len(inactive) == len(raw_by_clean):
        st.warning("Every user looks removed or disabled in IAM Identity Center, which usually means "
                   "IDENTITY_STORE_ID in app/.env points to the wrong identity store. Nobody is hidden.")
        inactive = []
    inactive_raw = [raw for u in inactive for raw in raw_by_clean[u]]
    scope_sql = month_sql + build_user_exclusion_clause(inactive_raw)

    # Who used Kiro in this month, before anyone is hidden
    df_month_users = fetch_data(f"""
    SELECT userid,
           SUM(TRY_CAST(credits_used AS DOUBLE)) AS credits,
           SUM(TRY_CAST(total_messages AS INTEGER)) AS messages
    FROM {table_name}
    WHERE 1=1{month_sql}
    GROUP BY userid
    """)
    df_month_users['uid'] = df_month_users['userid'].map(clean_userid)
    hidden = df_month_users[df_month_users['uid'].isin(inactive)]
    visible = df_month_users[~df_month_users['uid'].isin(inactive)]

    if inactive:
        note = (f"👤 Hidden in the monthly view: {len(inactive)} user(s) removed from or disabled in "
                "IAM Identity Center. ")
        if len(hidden):
            note += (f"{hidden['uid'].nunique()} of them used Kiro in {month} "
                     f"({hidden['credits'].apply(safe_float).sum():,.1f} credits, "
                     f"{hidden['messages'].apply(safe_int).sum():,} messages). ")
        note += "Their history stays in the All-time view."
        st.caption(note)
    if not IDENTITY_STORE_ID:
        st.caption("⚠️ IDENTITY_STORE_ID is not set in app/.env, so removed users cannot be detected "
                   "and nobody is hidden.")
    else:
        n_unknown = sum(1 for u in raw_by_clean if statuses.get(u) == STATUS_UNKNOWN)
        if n_unknown:
            st.caption(f"⚠️ Could not check {n_unknown} user(s) in IAM Identity Center, so they are shown.")

    if visible.empty:
        st.info(f"No Kiro usage by current users in {month}.")
        return

    visible_raw = group_raw_userids(visible['userid'])
    options = {labels.get(uid, uid): raws for uid, raws in visible_raw.items()}
    selected_raw, selected_labels = render_user_filter(options, key='user_filter_monthly')
    where_sql = scope_sql + build_user_filter_clause(selected_raw)

    st.markdown("---")
    render_sections(table_name, where_sql, labels, month=month, selected_labels=selected_labels)


def render_all_time_view(table_name, raw_by_clean, labels, statuses):
    """Everything since the reports started, including users removed later."""
    n_removed = sum(1 for u in raw_by_clean if statuses.get(u) == STATUS_REMOVED)
    n_disabled = sum(1 for u in raw_by_clean if statuses.get(u) == STATUS_DISABLED)
    note = "🗂️ All usage since the reports started, including users who were removed later."
    if n_removed:
        note += f" {n_removed} user(s) no longer in IAM Identity Center appear as \"Removed user …\"."
    if n_disabled:
        note += f" {n_disabled} user(s) disabled in IAM Identity Center are marked \"(disabled)\"."
    st.caption(note)

    options = {labels.get(uid, uid): raws for uid, raws in raw_by_clean.items()}
    selected_raw, selected_labels = render_user_filter(options, key='user_filter_all')
    where_sql = build_user_filter_clause(selected_raw)

    st.markdown("---")
    render_sections(table_name, where_sql, labels, month=None, selected_labels=selected_labels)


# --- Main app ---

VIEW_MONTHLY = "📅 Monthly"
VIEW_ALL_TIME = "🗂️ All-time (incl. history)"

def main():
    # Header
    header_col1, header_col2, header_col3 = st.columns([6, 1, 0.5])
    with header_col1:
        st.markdown('<p class="dashboard-title">⚡ Kiro Users Report</p>', unsafe_allow_html=True)
        st.markdown('<p class="dashboard-subtitle">Usage metrics across your organization</p>', unsafe_allow_html=True)
    with header_col2:
        refresh = st.button("🔄 Refresh Data")
    with header_col3:
        theme_icon = "🌙" if st.session_state.theme == 'light' else "☀️"
        if st.button(theme_icon, help="Toggle theme"):
            st.session_state.theme = 'dark' if st.session_state.theme == 'light' else 'light'
            st.rerun()

    st.markdown("")
    if refresh:
        st.cache_data.clear()

    view = st.segmented_control(
        "View", [VIEW_MONTHLY, VIEW_ALL_TIME], default=VIEW_MONTHLY,
        required=True, key='view', label_visibility='collapsed'
    )

    try:
        # Auto-discover table name from Glue database
        table_name = resolve_table_name()

        # Every user that appears in the reports, and the months that have data
        df_all_users, df_months = fetch_many((
            f"SELECT DISTINCT userid FROM {table_name} ORDER BY userid",
            f"""
            SELECT substr(date, 1, 7) AS month, MIN(date) AS first_day, MAX(date) AS last_day
            FROM {table_name}
            GROUP BY substr(date, 1, 7)
            """,
        ))
        raw_by_clean = group_raw_userids(df_all_users['userid'])
        labels, statuses = lookup_users(sorted(raw_by_clean))

        if view == VIEW_ALL_TIME:
            render_all_time_view(table_name, raw_by_clean, labels, statuses)
        else:
            render_monthly_view(table_name, df_months, raw_by_clean, labels, statuses)

    except Exception as e:
        st.error(f"Error fetching data: {str(e)}")
        st.info("Please ensure:")
        st.markdown("""
        - AWS credentials are configured
        - Glue crawler has run successfully
        - Athena database and table exist
        - S3 bucket for Athena results is accessible
        - Kiro user report data has been delivered to S3
        """)

if __name__ == "__main__":
    main()
