"""
generate_report.py
==================
Builds the full report HTML from data.py, then prints it to PDF via Playwright.

Usage:
    python generate_report.py MSN YEAR MONTH
"""

import sys, json, http.server, threading, os, calendar
from pathlib import Path
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
import data
import base64

with open("logo-full.png", "rb") as f:
    logo_b64 = base64.b64encode(f.read()).decode()

with open("icon.png", "rb") as f:
    icon_b64 = base64.b64encode(f.read()).decode()

MSN   = sys.argv[1]
YEAR  = int(sys.argv[2])
MONTH = int(sys.argv[3])

month_name = calendar.month_name[MONTH]
OUTPUT_PDF = Path(rf"C:\Users\harsh\Downloads\{MSN}_{month_name}_output.pdf")

d = type('d', (), data.fetch(MSN, YEAR, MONTH))()

WAIT_MS = 1400

# ── Fixed page geometry ───────────────────────────────────────────────────────
# Every rendered page is locked to this exact size (matches the page-1 layout:
# 960px wide content area, 40/40 side padding, 40 top / 48 bottom padding).
# Pages with less content are padded up to this height via the flex footer
# (margin-top:auto); pages with naturally MORE content than this are allowed
# to grow taller rather than clip — .page uses overflow:visible, never hidden,
# so real fonts/data can never get silently cut off.
PAGE_WIDTH       = 960
PAGE_HEIGHT      = 1450     # target height every page is padded/aimed at —
                             # matches page 1's real rendered height with actual
                             # fonts/data loaded (measure and adjust if your
                             # production fonts/logo render slightly differently)
PAGE_PAD_TOP     = 40
PAGE_PAD_SIDE    = 40
PAGE_PAD_BOTTOM  = 48

# Fixed height for the DR Events chart on page 2 (kept compact — this was
# previously stretching with flex:1 to fill the whole page, which made the
# chart enormous). 460px comfortably shows ~30 days of bars without bloating.
DR_CHART_HEIGHT = 360

# Rows of the event table rendered per page chunk. Tuned higher than before
# so fewer, fuller pages are produced; remaining rows flow to subsequent
# page(s), with the stat cards + legend cards appended after the FINAL
# chunk of rows only.
ROWS_ON_PAGE2               = 12   
ROWS_PER_FIRST_TABLE_PAGE   = 24
ROWS_PER_CONTINUATION_PAGE  = 24

# ── helpers ───────────────────────────────────────────────────────────────────
def inr(n):
    return "₹" + f"{int(n):,}"

def num(n):
    return f"{int(n):,}"

# ── Computed values ───────────────────────────────────────────────────────────
total_tariff_kvah = d.offpeak_kvah + d.peak_kvah + d.normal_kvah
offpeak_width     = round(d.offpeak_kvah / total_tariff_kvah * 100, 1)
peak_width        = round(d.peak_kvah    / total_tariff_kvah * 100, 1)
normal_width      = round(100 - offpeak_width - peak_width, 1)
part_pct          = round(d.participation_days / d.total_days * 100)
part_bar_width    = min(round(d.participation_days / d.target_days * 100), 100)
total_missed      = d.missed_morning_inr + d.missed_evening_inr
morning_bar_pct   = round(d.missed_morning_inr / total_missed * 100) if total_missed > 0 else 0
line_avg          = round(sum(d.daily_consumption) / len(d.daily_consumption))
dr_max            = max(d.daily_dr_shifted) if d.daily_dr_shifted else 0
util_max_color    = "var(--alert)" if d.util_max_pct > 75 else "var(--lichen-deep)"
real_events       = [e for e in d.events if e[0] != "—"]
achieved_events   = [e for e in real_events if e[7] == "Achieved"]
missed_events     = [e for e in real_events if e[7] == "Missed"]
total_kwh_shifted = sum(e[5] for e in real_events)
success_rate      = round(len(achieved_events) / len(real_events) * 100) if real_events else 0

# ── chart_events_js — built before HTML f-string ─────────────────────────────
from collections import defaultdict
daily_shifted = defaultdict(float)
for e in d.events:
    try:
        day_num = str(int(e[0].split(",")[0].split(" ")[-1]))
        daily_shifted[day_num] += float(e[5])
    except (ValueError, IndexError):
        pass

chart_events_js = json.dumps([
    {"label": day, "shifted": round(val, 1)}
    for day, val in sorted(daily_shifted.items(), key=lambda x: int(x[0]))
])

# ── per-day booked/actual arrays for new DR chart ─────────────────────────────
daily_booked_map = defaultdict(float)
daily_actual_map = defaultdict(float)
for e in d.events:
    try:
        day_num = str(int(e[0].split(",")[0].split(" ")[-1]))
        daily_booked_map[day_num] += float(e[3])
        daily_actual_map[day_num] += float(e[5])
    except (ValueError, IndexError):
        pass

all_days_list    = [str(i) for i in range(1, d.total_days + 1)]
daily_booked_arr = [round(daily_booked_map.get(dd, 0), 1) for dd in all_days_list]
daily_actual_arr = [round(daily_actual_map.get(dd, 0), 1) for dd in all_days_list]

daily_booked_js = json.dumps(daily_booked_arr)
daily_actual_js = json.dumps(daily_actual_arr)
day_labels_js   = json.dumps([f"{d.month[:3]} {i}" for i in range(1, d.total_days + 1)])

# ── heatmap_slots_js — built before HTML f-string ────────────────────────────
heatmap_slots_js = json.dumps(d.heatmap_slots)

def event_row(e):
    date, day, window, target_kwh, actual_kwh, kwh, savings, result = e
    badge = 'badge-a">Achieved' if result == "Achieved" else 'badge-m">Missed'
    return (f'      <tr><td>{date}</td><td>{day}</td><td>{window}</td>'
            f'<td>{target_kwh} kWh</td><td>{kwh} kWh</td>'
            f'<td>{inr(savings)}</td>'
            f'<td><span class="badge {badge}</span></td></tr>')

def event_rows(events_subset):
    return "\n".join(event_row(e) for e in events_subset)

def action_items():
    items = []
    for i, text in enumerate(d.actions, 1):
        items.append(f'      <div class="action-item">'
                     f'<div class="action-num">{i}</div>'
                     f'<div class="action-text">{text}</div></div>')
    return "\n".join(items)

def why_items():
    return "\n".join(f'        <div class="why-item">{w}</div>' for w in d.missed_evening_note)

def tariff_treemap():
    segments = [
        ("Peak",   d.peak_kvah,   d.peak_inr,   "#D94F2E"),
        ("Normal", d.normal_kvah, d.normal_inr, "#E8A800"),
    ] if d.category == "COMMERCIAL-HT" else [
        ("Off-Peak", d.offpeak_kvah, d.offpeak_inr, "#6EAB85"),
        ("Peak",     d.peak_kvah,    d.peak_inr,    "#D94F2E"),
        ("Normal",   d.normal_kvah,  d.normal_inr,  "#E8A800"),
    ]
    segments.sort(key=lambda x: x[2], reverse=True)
    big   = segments[0]
    small = segments[1:]

    def block(label, kwh, inr_val, color, flex):
        return (
            f'<div style="flex:{flex};background:{color};display:flex;flex-direction:column;'
            f'justify-content:flex-start;padding:7px 8px;min-width:80px;">'
            f'<div style="font-size:7.5px;font-weight:700;color:rgba(255,255,255,.85);text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px;">{label}</div>'
            f'<div style="font-size:13px;font-weight:700;color:#fff;font-family:\'Google Sans Mono\',monospace;line-height:1;">{inr(inr_val)}</div>'
            f'<div style="font-size:9px;color:#fff;margin-top:2px;">{num(kwh)} kVAh</div>'
            f'</div>'
        )

    left  = block(big[0], big[1], big[2], big[3], big[2])
    right_inner = "".join(block(s[0], s[1], s[2], s[3], s[2]) for s in small)
    right_flex = sum(s[2] for s in small)
    right = (
        f'<div style="flex:{right_flex};display:flex;flex-direction:column;gap:3px;min-width:70px;">'
        + right_inner +
        f'</div>'
    )
    return left + right

# ── Shared block builders (cards) ─────────────────────────────────────────────
def stat_cards_block():
    """The 4 summary stat cards — now placed AFTER the event table."""
    return f"""
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px;">
    <div class="card" style="padding:14px 16px;">
      <div class="card-title" style="margin-bottom:5px;">Total Events</div>
      <div style="font-size:22px;font-weight:700;font-family:'Google Sans Mono',monospace;color:var(--ink);">{len(d.events)}</div>
      <div style="font-size:10.5px;color:var(--ink-light);">This month</div>
    </div>
    <div class="card" style="padding:14px 16px;background:var(--lichen-tint);border-color:rgba(110,171,133,.3);">
      <div class="card-title" style="margin-bottom:5px;color:var(--lichen-deep);">Achieved</div>
      <div style="font-size:22px;font-weight:700;font-family:'Google Sans Mono',monospace;color:var(--lichen-deep);">{len(achieved_events)}</div>
      <div style="font-size:10.5px;color:var(--lichen-deep);opacity:.7;">{success_rate}% success rate</div>
    </div>
    <div class="card" style="padding:14px 16px;background:var(--alert-pale);border-color:rgba(217,79,46,.2);">
      <div class="card-title" style="margin-bottom:5px;color:var(--alert);">Missed</div>
      <div style="font-size:22px;font-weight:700;font-family:'Google Sans Mono',monospace;color:var(--alert);">{len(missed_events)}</div>
    </div>
    <div class="card" style="padding:14px 16px;">
      <div class="card-title" style="margin-bottom:5px;">Total kWh Shifted</div>
      <div style="font-size:22px;font-weight:700;font-family:'Google Sans Mono',monospace;color:var(--ink);">{round(total_kwh_shifted, 1)}</div>
      <div style="font-size:10.5px;color:var(--ink-light);">kWh across all events</div>
    </div>
  </div>"""

def legend_cards_block():
    """The 5 DR-chart legend explainer cards — now individual cards, AFTER the event table."""
    items = [
        ("#1B3A6B", "solid", "Actual Shifted (Blue Bar)", "Energy you actually shifted on each booked day."),
        ("#5B92AD", "dashed-line", "Booked Target (Dashed Line)", "The amount of energy you committed to shift."),
        ("rgba(173,210,230,0.55)", "solid-bordered:rgba(91,146,173,0.5)", "Under-delivery (Light Blue)", "You shifted less than committed. Aim to close this gap."),
        ("#43B97F", "solid", "Over-delivery (Green)", "Great work! You shifted more than your commitment."),
        ("rgba(91,146,173,0.3)", "striped-bordered:rgba(91,146,173,0.4)", "No Booking (Grey)", "Missed opportunity. Book more days to maximize impact."),
    ]

    def swatch(color, style):
      if style == "dashed-line":
          return (f'<span style="display:inline-block;width:18px;height:0;'
                  f'border-top:2px dashed {color};vertical-align:middle;margin-right:2px;"></span>')
      if style.startswith("solid-bordered:"):
          border = style.split(":", 1)[1]
          return (f'<span style="width:14px;height:14px;border-radius:3px;background:{color};'
                  f'border:1px solid {border};display:inline-block;"></span>')
      if style.startswith("dashed-bordered:"):
          border = style.split(":", 1)[1]
          return (f'<span style="width:14px;height:14px;border-radius:3px;background:{color};'
                  f'border:1px dashed {border};display:inline-block;"></span>')
      if style.startswith("striped-bordered:"):
          border = style.split(":", 1)[1]
          return (
              f'<span style="width:14px;height:14px;border-radius:3px;'
              f'background:repeating-linear-gradient(45deg, {color} 0, {color} 1px, transparent 1px, transparent 3px);'
              f'border:1px dashed {border};display:inline-block;"></span>'
          )

      return f'<span style="width:14px;height:14px;border-radius:3px;background:{color};display:inline-block;"></span>'

    cards = []
    for color, style, title, desc in items:
        cards.append(f"""
    <div class="card" style="padding:14px 16px;">
      <div class="legend-icon" style="margin-bottom:7px;">{swatch(color, style)}<span style="margin-left:6px;">{title}</span></div>
      <div class="legend-desc">{desc}</div>
    </div>""")

    return f"""
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:16px;">{''.join(cards)}
  </div>"""

# ── Paginate the event table across pages ────────────────────────────────────
all_events = d.events
page2_events = all_events[:ROWS_ON_PAGE2]
remaining_events = all_events[ROWS_ON_PAGE2:]

table_pages = []  # list of lists of events
if remaining_events:
    first_chunk = remaining_events[:ROWS_PER_FIRST_TABLE_PAGE]
    rest        = remaining_events[ROWS_PER_FIRST_TABLE_PAGE:]
    table_pages.append(first_chunk)
    while rest:
        chunk = rest[:ROWS_PER_CONTINUATION_PAGE]
        rest  = rest[ROWS_PER_CONTINUATION_PAGE:]
        table_pages.append(chunk)
else:
    table_pages.append([])

n_table_pages = len(table_pages)

def table_page_html(page_index_within_table, events_chunk, is_last_chunk, global_page_no, total_pages):
    """Builds one page of the event table. Stat cards + legend cards only on the LAST chunk."""
    is_first = page_index_within_table == 0
    header_block = f"""
  <div class="p2-header">
    <div class="p2-logo">
      <img src="data:image/png;base64,{icon_b64}" style="height:50px;width:auto;" />
    </div>
    <span class="p2-title">Event-Level Summary  &nbsp;·&nbsp; {d.month} &nbsp;·&nbsp; {d.site}</span>
  </div>
"""
    chapter_block = (
        '<div class="chapter" style="margin-top:0;"><div class="chapter-label"></div><div class="chapter-line"></div></div>'
        if is_first else
        '<div class="chapter" style="margin-top:0;"><div class="chapter-label"></div><div class="chapter-line"></div></div>'
    )

    table_block = f"""
  <table class="evt-tbl">
    <thead><tr><th>Date</th><th>Day</th><th>Window</th><th>Booked (kWh)</th><th>Shifted (kWh)</th><th>Savings (₹)</th><th>Result</th></tr></thead>
    <tbody>{event_rows(events_chunk)}</tbody>
  </table>"""

    trailer_block = ""
    if is_last_chunk:
        trailer_block = f"""
{stat_cards_block()}

  <div style="margin-top:16px;">

  </div>"""

    footer_block = f"""
  <div class="footer">
    <div class="footer-note">{d.footer_p1}</div>
    <div class="footer-brand"><span>elements</span> energy · {d.month}</div>
  </div>"""

    return f"""
<div class="page" id="page_table_{page_index_within_table+1}">
  <span class="page-label">Page {global_page_no} of {total_pages}</span>
  {header_block}
  {chapter_block}
  {table_block}
  {trailer_block}
  {footer_block}
</div>
"""

# Total page count: page1 (summary) + page2 (DR chart) + N table pages + final action-plan page
TOTAL_PAGES = 2 + n_table_pages

table_pages_html = "\n".join(
    table_page_html(
        i, chunk,
        is_last_chunk=(i == n_table_pages - 1),
        global_page_no=3 + i,
        total_pages=TOTAL_PAGES
    )
    for i, chunk in enumerate(table_pages)
)



# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>elements energy — Performance Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Google+Sans+Mono:wght@400;500&display=swap');
:root {{
  --ink:#111810;--ink-mid:#3A4A3D;--ink-light:#7A8C7E;
  --lichen:#6EAB85;--lichen-deep:#2A5C3F;--lichen-tint:#EBF6F0;
  --monsoon:#5B92AD;--monsoon-deep:#1E3F5A;
  --solar:#F9C840;--solar-dark:#A87800;--solar-pale:#FFFAEA;
  --alert:#D94F2E;--alert-pale:#FEF0EC;
  --white:#ffffff;--surface:#F7F8F7;--border:#E8EAE8;--border-mid:#D4D8D4;
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Google Sans',sans-serif;background:var(--white);color:var(--ink);font-size:13px;line-height:1.5;}}
.page{{
  width:{PAGE_WIDTH}px;
  min-height:{PAGE_HEIGHT}px;
  margin:0 auto;
  background:var(--white);
  padding:{PAGE_PAD_TOP}px {PAGE_PAD_SIDE}px {PAGE_PAD_BOTTOM}px;
  position:relative;
  page-break-after:always;break-after:page;
  overflow:visible;
  display:flex;
  flex-direction:column;
}}
.page:last-child{{page-break-after:avoid;break-after:avoid;}}
.page-label{{position:absolute;top:14px;right:18px;font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--border-mid);}}
.header{{display:grid;grid-template-columns:1fr auto;align-items:start;padding-bottom:18px;margin-bottom:26px;border-bottom:2.5px solid var(--ink);}}
.logo-row{{display:flex;align-items:center;gap:9px;margin-bottom:3px;}}
.header-right{{text-align:right;}}
.report-title{{font-size:26px;font-weight:700;color:var(--ink);letter-spacing:-.02em;line-height:1.1;}}
.report-meta{{font-size:12px;color:var(--ink-light);margin-top:4px;}}
.pills{{display:flex;gap:6px;justify-content:flex-end;margin-top:9px;}}
.pill{{font-size:10.5px;font-weight:500;padding:3px 11px;border-radius:20px;border:1px solid var(--border-mid);color:var(--ink-mid);}}
.pill.solar{{background:var(--solar-pale);border-color:rgba(249,200,64,.45);color:var(--solar-dark);}}
.pill.lichen{{background:var(--lichen-tint);border-color:rgba(110,171,133,.4);color:var(--lichen-deep);}}
.chapter{{display:flex;align-items:center;gap:10px;margin-bottom:14px;margin-top:28px;}}
.chapter:first-of-type{{margin-top:0;}}
.chapter-num{{width:22px;height:22px;border-radius:50%;background:var(--ink);color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;}}
.chapter-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--ink-mid);}}
.chapter-line{{flex:1;height:1px;background:var(--border);}}
.kpi-strip{{display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr;gap:0;border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:14px;}}
.kpi{{padding:18px 20px;border-right:1px solid var(--border);background:var(--white);}}
.kpi:last-child{{border-right:none;}}
.kpi-top{{display:flex;align-items:center;gap:6px;margin-bottom:6px;}}
.kpi-dot{{width:7px;height:7px;border-radius:50%;flex-shrink:0;}}
.kpi-lbl{{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-light);}}
.kpi-val{{font-size:26px;font-weight:700;line-height:1;letter-spacing:-.02em;margin-bottom:4px;font-family:'Google Sans Mono',monospace;}}
.kpi-sub{{font-size:10.5px;color:var(--ink-light);}}
.kpi.hero{{background:var(--ink);padding:0;display:flex;flex-direction:column;}}
.kpi-half{{padding:12px 20px;flex:1;display:flex;flex-direction:column;justify-content:center;}}
.kpi-half.top{{border-bottom:1px solid rgba(255,255,255,.1);flex:1;}}
.kpi-half.bottom{{flex:0;padding-bottom:16px;}}
.kpi.hero .kpi-lbl{{color:rgba(255,255,255,.38);}}
.kpi.hero .kpi-sub{{color:rgba(255,255,255,.3);}}
.kpi-half.top .kpi-val{{color:var(--lichen);font-size:28px;}}
.kpi-half.bottom .kpi-val{{color:#F28B72;font-size:22px;}}
.kpi-half.bottom .kpi-lbl{{color:rgba(255,255,255,.3);}}
.kpi-half.bottom .kpi-sub{{color:rgba(255,255,255,.25);}}
.kpi.k-neutral .kpi-val{{color:var(--ink);font-family:'Google Sans Mono',monospace;}}
.kpi.k-yellow .kpi-val{{color:var(--solar-dark);font-family:'Google Sans Mono',monospace;}}
.card{{background:var(--white);border:1px solid var(--border);border-radius:10px;padding:16px;}}
.card-title{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-light);margin-bottom:12px;}}
.chart-card{{background:var(--white);border:1px solid var(--border);border-radius:12px;overflow:hidden;}}
.chart-head{{padding:13px 17px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;}}
.chart-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-light);}}
.legend{{display:flex;align-items:center;gap:14px;font-size:11px;color:var(--ink-light);}}
.lswatch{{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:middle;margin-right:4px;}}
.alert-tag{{background:var(--alert-pale);border:1px solid rgba(217,79,46,.25);color:var(--alert);font-size:10.5px;font-weight:600;padding:4px 11px;border-radius:20px;}}
.chart-body{{padding:14px 16px 12px 6px;position:relative;}}
.mbar{{margin-bottom:12px;}}
.mbar:last-of-type{{margin-bottom:0;}}
.mbar-header{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px;}}
.mbar-name{{font-size:12px;font-weight:500;color:var(--ink-mid);}}
.mbar-time{{font-size:10px;color:var(--ink-light);margin-left:4px;}}
.mbar-amt{{font-size:13px;font-weight:700;font-family:'Google Sans Mono',monospace;}}
.mbar-track{{height:7px;background:var(--surface);border-radius:4px;overflow:hidden;border:1px solid var(--border);}}
.mbar-fill{{height:100%;border-radius:4px;}}
.action-panel{{background:var(--lichen-deep);border-radius:12px;padding:22px 24px;}}
.action-panel-head{{display:flex;align-items:baseline;gap:10px;margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid rgba(255,255,255,.15);}}
.action-eyebrow{{font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:rgba(255,255,255,.45);}}
.action-heading{{font-size:15px;font-weight:700;color:#fff;}}
.action-items{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;}}
.action-item{{display:flex;flex-direction:column;gap:7px;}}
.action-num{{width:20px;height:20px;border-radius:50%;background:var(--solar);color:var(--ink);font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;}}
.action-text{{font-size:12px;color:rgba(255,255,255,.82);line-height:1.55;}}
.action-text strong{{color:#fff;}}
.footer{{margin-top:auto;padding-top:16px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;}}
.footer-note{{font-size:11px;color:var(--ink-light);max-width:500px;line-height:1.6;}}
.footer-brand{{font-size:11px;font-weight:700;color:var(--ink-light);}}
.footer-brand span{{color:var(--lichen-deep);}}
.p2-header{{display:flex;align-items:center;justify-content:space-between;padding-bottom:10px;margin-bottom:14px;border-bottom:1px solid var(--border);}}
.p2-logo{{display:flex;align-items:center;gap:8px;}}
.p2-title{{font-size:13px;font-weight:500;color:var(--ink-light);}}
.evt-tbl{{width:100%;border-collapse:collapse;font-size:12.5px;}}
.evt-tbl thead th{{font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-light);font-weight:700;padding:8px 12px;text-align:left;border-bottom:2px solid var(--ink);background:var(--surface);}}
.evt-tbl tbody td{{padding:10px 12px;color:var(--ink);border-bottom:1px solid var(--border);}}
.evt-tbl tbody tr:last-child td{{border-bottom:none;}}
.badge{{display:inline-block;padding:3px 10px;border-radius:20px;font-size:10.5px;font-weight:700;}}
.badge-m{{background:var(--alert-pale);color:var(--alert);border:1px solid rgba(217,79,46,.2);}}
.badge-a{{background:var(--lichen-tint);color:var(--lichen-deep);border:1px solid rgba(110,171,133,.25);}}
.evt-note{{padding:12px 16px;background:var(--lichen-tint);border-radius:8px;border:1px solid rgba(110,171,133,.3);font-size:12px;color:var(--ink-mid);line-height:1.6;}}
.legend-panel{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:14px;padding:14px 16px;background:var(--surface);border-radius:8px;border:1px solid var(--border);}}
.legend-item{{display:flex;flex-direction:column;gap:5px;}}
.legend-icon{{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:700;color:var(--ink-mid);}}
.legend-desc{{font-size:10px;color:var(--ink-light);line-height:1.4;}}
canvas{{display:block;}}
</style>
</head>
<body>

<!-- PAGE 1 -->
<div class="page" id="page1">
  <span class="page-label">Page 1 of {TOTAL_PAGES}</span>
  <div class="header">
    <div>
      <div class="logo-row">
        <img src="data:image/png;base64,{logo_b64}" style="height:70px;width:auto;" />
      </div>
    </div>
    <div class="header-right">
      <div class="report-title">Performance Report</div>
      <div class="report-meta">{d.site} &nbsp;·&nbsp; {d.month}</div>
      <div class="pills">
        <span class="pill solar">★ Streak: {d.streak}</span>
        <span class="pill lichen">Rank #{d.rank} · {d.region}</span>
      </div>
    </div>
  </div>

  <div class="chapter" style="margin-top:0;"><div class="chapter-num">1</div><div class="chapter-label">Monthly Summary</div><div class="chapter-line"></div></div>

  <div class="kpi-strip" style="margin-bottom:14px;">
    <div class="kpi hero">
      <div class="kpi-half top">
        <div class="kpi-top"><div class="kpi-dot" style="background:var(--lichen);"></div><div class="kpi-lbl">Total DR Savings</div></div>
        <div class="kpi-val">{inr(d.dr_savings)}</div>
      </div>
      <div class="kpi-half bottom">
        <div class="kpi-top"><div class="kpi-dot" style="background:#F28B72;"></div><div class="kpi-lbl">Missed Savings</div></div>
        <div class="kpi-val">{inr(d.missed_savings)}</div>
        
      </div>
    </div>
    <div class="kpi k-neutral" style="display:flex;flex-direction:column;justify-content:center;gap:0;">
      <div style="padding-bottom:10px;border-bottom:1px solid var(--border);">
        <div class="kpi-top"><div class="kpi-dot" style="background:var(--ink-light);"></div><div class="kpi-lbl">Total Energy Consumed</div></div>
        <div class="kpi-val">{num(d.total_energy_kvah)}<span style="font-size:14px;font-weight:500;color:var(--ink-light);font-family:'Google Sans',sans-serif;"> kVAh</span></div>
        <div class="kpi-sub">This month</div>
      </div>
      <div style="padding-top:10px;">
        <div class="kpi-top"><div class="kpi-dot" style="background:var(--monsoon);"></div><div class="kpi-lbl">Peak Demand</div></div>
        <div style="font-size:20px;font-weight:700;font-family:'Google Sans Mono',monospace;color:var(--monsoon-deep);letter-spacing:-.02em;line-height:1.1;">{d.peak_demand_kva}<span style="font-size:13px;font-weight:500;color:var(--ink-light);font-family:'Google Sans',sans-serif;"> kVA</span></div>
        <div class="kpi-sub">{d.peak_demand_note}</div>
      </div>
    </div>
    <div class="kpi" style="padding:14px 16px 12px;">
      <div class="kpi-top" style="margin-bottom:9px;"><div class="kpi-dot" style="background:var(--monsoon);"></div><div class="kpi-lbl">Tariff Split — kVAh</div></div>
      <div style="display:flex;height:110px;gap:3px;border-radius:6px;overflow:hidden;margin-bottom:9px;">
        {tariff_treemap()}
      </div>
    </div>
    <div class="kpi k-yellow" style="display:flex;flex-direction:column;justify-content:center;">
      <div class="kpi-top"><div class="kpi-dot" style="background:var(--solar);"></div><div class="kpi-lbl">Participation Days</div></div>
      <div style="display:flex;align-items:baseline;gap:4px;margin-bottom:10px;">
        <div class="kpi-val" style="font-size:28px;">{d.participation_days}</div>
        <div style="font-size:13px;font-weight:500;color:var(--ink-light);">/ {d.total_days} days</div>
      </div>
      <div style="height:5px;background:rgba(249,200,64,.2);border:1px solid rgba(249,200,64,.45);border-radius:3px;overflow:hidden;">
        <div style="width:{part_pct}%;height:100%;background:var(--solar);border-radius:3px;"></div>
      </div>
      <div style="display:flex;justify-content:space-between;margin-top:4px;">
        <div style="font-size:9px;color:var(--solar-dark);font-weight:700;">{part_pct}% achieved</div>
        <div style="font-size:9px;color:var(--ink-light);">100%</div>
      </div>
    </div>
  </div>

  <div class="chapter"><div class="chapter-num">2</div><div class="chapter-label">Load Analysis</div><div class="chapter-line"></div></div>

  <div class="chart-card" style="margin-bottom:12px;">
    <div class="chart-head">
      <div style="display:flex;align-items:center;gap:16px;">
        <span class="chart-title">
        Consumption — {d.month}</span>
        <div class="legend">
          <span><span style="display:inline-block;width:18px;height:2px;background:#2A5C3F;vertical-align:middle;margin-right:4px;"></span>Daily consumption</span>
          <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#D94F2E;vertical-align:middle;margin-right:4px;"></span>Highest</span>
          <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#F9C840;vertical-align:middle;margin-right:4px;"></span>Lowest</span>
          <span><span style="display:inline-block;width:18px;height:0;border-top:2px dashed #5B92AD;vertical-align:middle;margin-right:4px;"></span>Avg</span>
        </div>
      </div>
    </div>
    <div class="chart-body" style="height:215px;padding-top:8px;"><canvas id="hourlyChart"></canvas></div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 2fr;gap:12px;margin-bottom:12px;">
    <div class="card" style="display:flex;flex-direction:column;">
      <div class="card-title">Missed Savings</div>
      <div style="flex:1;display:flex;flex-direction:column;{'justify-content:center;' if d.category == 'COMMERCIAL-HT' else ''}">
      {'<div class="mbar" style="margin-bottom:24px;"><div class="mbar-header"><span class="mbar-name">☀️ Morning<span class="mbar-time"> 6–10 AM</span></span><span class="mbar-amt" style="color:var(--ink-mid);font-size:11px;font-weight:500;">' + inr(d.missed_morning_inr + d.achieved_morning_inr) + '</span></div><div class="mbar-track"><div style="width:' + str(round((d.missed_morning_inr/(d.missed_morning_inr+d.achieved_morning_inr)*100) if (d.missed_morning_inr+d.achieved_morning_inr)>0 else 0,1)) + '%;height:100%;background:var(--alert);border-radius:4px;"></div></div><div style="display:flex;justify-content:space-between;margin-top:4px;"><span style="font-size:12px;color:var(--alert);font-weight:700;">Missed ' + inr(d.missed_morning_inr) + '</span><span style="font-size:9.5px;color:var(--ink-light);">' + str(round((d.missed_morning_inr/(d.missed_morning_inr+d.achieved_morning_inr)*100) if (d.missed_morning_inr+d.achieved_morning_inr)>0 else 0)) + '% of total</span></div></div>' if d.category != 'COMMERCIAL-HT' else ''}
      <div class="mbar">
        <div class="mbar-header">
          <span class="mbar-name"><svg width="13" height="13" viewBox="0 0 24 24" style="vertical-align:-2px;margin-right:3px;"><defs><mask id="moonMask"><rect width="24" height="24" fill="white"/><circle cx="15" cy="9" r="8" fill="black"/></mask></defs><circle cx="12" cy="12" r="9" fill="var(--monsoon)" mask="url(#moonMask)"/></svg> Evening<span class="mbar-time"> 6–10 PM</span></span>
          <span class="mbar-amt" style="color:var(--ink-mid);font-size:11px;font-weight:500;">{inr(d.missed_evening_inr + d.achieved_evening_inr)}</span>
        </div>
        <div class="mbar-track">
          <div style="width:{round((d.missed_evening_inr/(d.missed_evening_inr+d.achieved_evening_inr)*100) if (d.missed_evening_inr+d.achieved_evening_inr)>0 else 0,1)}%;height:100%;background:var(--alert);border-radius:4px;"></div>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:4px;">
          <span style="font-size:12px;color:var(--alert);font-weight:700;">Missed {inr(d.missed_evening_inr)}</span>
          <span style="font-size:9.5px;color:var(--ink-light);">{round((d.missed_evening_inr/(d.missed_evening_inr+d.achieved_evening_inr)*100) if (d.missed_evening_inr+d.achieved_evening_inr)>0 else 0)}% of total</span>
        </div>
      </div>
      </div>
    </div>
    <div class="card" style="display:flex;flex-direction:column;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
        <div class="card-title" style="margin-bottom:0;">Performance: Actual vs Booked vs Max Potential</div>
        <div style="display:flex;gap:12px;">
          <span style="font-size:10px;color:var(--ink-light);display:flex;align-items:center;gap:4px;"><span style="width:10px;height:10px;border-radius:2px;background:#2A5C3F;display:inline-block;"></span>Actual</span>
          <span style="font-size:10px;color:var(--ink-light);display:flex;align-items:center;gap:4px;"><span style="width:10px;height:10px;border-radius:2px;background:#6EAB85;display:inline-block;"></span>Booked</span>
          <span style="font-size:10px;color:var(--ink-light);display:flex;align-items:center;gap:4px;"><span style="width:10px;height:10px;border-radius:2px;background:#D4D8D4;border:1.5px solid #7A8C7E;display:inline-block;"></span>Max Potential</span>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:160px;"><canvas id="perfCombinedChart"></canvas></div>
    </div>
  </div>

  <div class="chapter" style="margin-top:20px;"><div class="chapter-num">3</div><div class="chapter-label">Peak Demand Analysis</div><div class="chapter-line"></div></div>

  <div style="display:grid;grid-template-columns:1fr 1.6fr;gap:20px;margin-bottom:12px;">
    <div class="card" style="display:flex;flex-direction:column;position:relative;">
      <div class="card-title">Demand (kVA)</div>
      <div style="position:absolute;top:14px;right:16px;display:flex;gap:16px;z-index:2;">
        <div style="text-align:right;">
          <div style="font-size:9px;color:var(--ink-light);display:flex;align-items:center;gap:4px;justify-content:flex-end;">
            <span style="width:6px;height:6px;border-radius:50%;background:var(--ink);display:inline-block;"></span>Contract
          </div>
          <div style="font-size:12px;font-weight:700;font-family:'Google Sans Mono',monospace;color:var(--ink);">{num(d.contract_demand)} kVA</div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:9px;color:var(--ink-light);display:flex;align-items:center;gap:4px;justify-content:flex-end;">
            <span style="width:6px;height:6px;border-radius:50%;background:var(--alert);display:inline-block;"></span>Max
          </div>
          <div style="font-size:12px;font-weight:700;font-family:'Google Sans Mono',monospace;color:var(--alert);">{num(d.max_demand)} kVA</div>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:160px;margin-top:6px;"><canvas id="demandChart"></canvas></div>
    </div>
    <div class="card">
      <div class="card-title">Peak occurrence by hour &amp; day</div>
      <canvas id="heatmapChart" height="180"></canvas>
      <div style="display:flex;align-items:center;gap:6px;margin-top:8px;">
        <span style="font-size:9px;color:var(--ink-light);">Low</span>
        <div style="display:flex;gap:2px;flex:1;">
          <div style="flex:1;height:6px;border-radius:1px;background:#E8F3F8;"></div>
          <div style="flex:1;height:6px;border-radius:1px;background:#9DBFCE;"></div>
          <div style="flex:1;height:6px;border-radius:1px;background:var(--monsoon);"></div>
          <div style="flex:1;height:6px;border-radius:1px;background:var(--solar);"></div>
          <div style="flex:1;height:6px;border-radius:1px;background:var(--alert);"></div>
        </div>
        <span style="font-size:9px;color:var(--ink-light);">High</span>
      </div>
    </div>
  </div>
  <div class="footer">
    <div class="footer-note">{d.footer_p1}</div>
    <div class="footer-brand"><span>elements</span> energy · {d.month}</div>
  </div>
</div>


<!-- PAGE 2 — DR Events chart -->
<div class="page" id="page2">
  <span class="page-label">Page 2 of {TOTAL_PAGES}</span>
  <div class="p2-header">
    <div class="p2-logo">
      <img src="data:image/png;base64,{icon_b64}" style="height:50px;width:auto;" />
    </div>
    <span class="p2-title">DR Events &nbsp;·&nbsp; {d.month} &nbsp;·&nbsp; {d.site}</span>
  </div>

  <div class="chapter" style="margin-top:0;"><div class="chapter-num">4</div><div class="chapter-label">DR Events</div><div class="chapter-line"></div></div>
  <div class="chart-card" style="margin-bottom:0;">
    <div class="chart-head">
      <div style="display:flex;align-items:center;gap:16px;">
        <span class="chart-title">DR Shifted — {d.month}</span>
        <div class="legend">
          <span><span class="lswatch" style="background:#1B3A6B;"></span>Actual Shifted (kWh)</span>
          <span><span style="display:inline-block;width:18px;height:0;border-top:2px dashed #5B92AD;vertical-align:middle;margin-right:4px;"></span>Booked(kWh)</span>
          <span><span class="lswatch" style="background:rgba(180,210,230,0.5);border:1px solid rgba(91,146,173,0.5);"></span>Under-delivery</span>
          <span><span class="lswatch" style="background:#4CAF82;"></span>Over-delivery</span>
          <span><span class="lswatch" style="background:repeating-linear-gradient(45deg,rgba(91,146,173,0.3) 0,rgba(91,146,173,0.3) 1px,transparent 1px,transparent 3px);border:1px dashed rgba(91,146,173,0.4);"></span>No Booking</span>
        </div>
      </div>
    </div>
    <div class="chart-body" style="height:{DR_CHART_HEIGHT}px;padding-top:8px;"><canvas id="dailyChart"></canvas></div>
  </div>
{legend_cards_block()}

  <table class="evt-tbl" style="margin-top:16px;">
    <thead><tr><th>Date</th><th>Day</th><th>Window</th><th>Booked (kWh)</th><th>Shifted (kWh)</th><th>Savings (₹)</th><th>Result</th></tr></thead>
    <tbody>{event_rows(page2_events)}</tbody>
  </table>

  <div class="footer">
    <div class="footer-note">{d.footer_p1}</div>
    <div class="footer-brand"><span>elements</span> energy · {d.month}</div>
  </div>
</div>

{table_pages_html}


<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-datalabels/2.2.0/chartjs-plugin-datalabels.min.js"></script>
<script>
Chart.register(ChartDataLabels);
Chart.defaults.plugins.datalabels.display = false;

const dailyConsumption = {json.dumps(d.daily_consumption)};
const perfActual       = {json.dumps(d.perf_actual)};
const perfBooked       = {json.dumps(d.perf_booked)};
const perfMax          = {json.dumps(d.perf_max)};
const demandVals       = [{d.contract_demand},{d.max_demand}];
const lineAvg          = {line_avg};
const totalDays        = {d.total_days};
const labels           = Array.from({{length:totalDays}},(_,i)=>'{d.month[:3]} '+(i+1));

// ── Daily consumption chart ───────────────────────────────────────────────────
(function(){{
  const canvas = document.getElementById('hourlyChart');
  const dpr = window.devicePixelRatio || 2;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width  = rect.width  + 'px';
  canvas.style.height = rect.height + 'px';
  const lineMaxV = Math.max(...dailyConsumption);
  const lineMinV = Math.min(...dailyConsumption.filter(v => v > 0));
  const maxIdx   = dailyConsumption.indexOf(lineMaxV);
  const minIdx   = dailyConsumption.indexOf(lineMinV);

  const annotPlugin = {{
    id: 'annot',
    afterDatasetsDraw(chart) {{
      const c   = chart.ctx;
      const yS  = chart.scales.y;
      const meta = chart.getDatasetMeta(0);
      const avgY = yS.getPixelForValue(lineAvg);
      c.save();
      c.font = '600 9px sans-serif';
      c.fillStyle = '#5B92AD';
      c.textAlign = 'left';
      c.textBaseline = 'middle';
      c.fillText('~' + lineAvg + ' kVAh', chart.chartArea.right + 4, avgY);
      c.restore();
      const maxBar = meta.data[maxIdx];
      const minBar = meta.data[minIdx];
      if (maxBar) {{
        c.save();
        c.beginPath();
        c.arc(maxBar.x, maxBar.y - 6, 4, 0, Math.PI * 2);
        c.fillStyle = '#D94F2E';
        c.fill();
        c.font = '700 8px sans-serif';
        c.fillStyle = '#D94F2E';
        c.textAlign = 'center';
        c.textBaseline = 'bottom';
        c.fillText(lineMaxV + ' kVAh', maxBar.x, maxBar.y - 12);
        c.restore();
      }}
      if (minBar) {{
        c.save();
        c.beginPath();
        c.arc(minBar.x, minBar.y - 6, 4, 0, Math.PI * 2);
        c.fillStyle = '#F9C840';
        c.fill();
        c.font = '700 8px sans-serif';
        c.fillStyle = '#A87800';
        c.textAlign = 'center';
        c.textBaseline = 'bottom';
        c.fillText(lineMinV + ' kVAh', minBar.x, minBar.y - 12);
        c.restore();
      }}   
    }}
  }};

  new Chart(canvas, {{
    type:'bar', plugins:[annotPlugin],
    data:{{labels:labels,datasets:[
      {{label:'Daily consumption',data:dailyConsumption,backgroundColor:dailyConsumption.map((v,i)=>i===maxIdx?'rgba(217,79,46,0.8)':i===minIdx?'#F9C840':'rgba(42,92,63,0.7)'),borderColor:dailyConsumption.map((v,i)=>i===maxIdx?'#D94F2E':i===minIdx?'#A87800':'#2A5C3F'),borderWidth:1,borderRadius:3,barPercentage:0.6,datalabels:{{display:false}}}},
      {{label:'Average',data:Array(totalDays).fill(lineAvg),type:'line',borderColor:'rgba(91,146,173,0.6)',borderWidth:1.5,borderDash:[6,4],pointRadius:0,fill:false,tension:0,datalabels:{{display:false}}}}
    ]}},
    options:{{
      responsive:true, maintainAspectRatio:false, devicePixelRatio:dpr,
      layout:{{padding:{{top:36,left:4,right:55,bottom:4}}}},
      plugins:{{legend:{{display:false}},tooltip:{{mode:'index',intersect:false,callbacks:{{llabel:ctx=>ctx.datasetIndex===0?` Consumption: ${{ctx.parsed.y}} kVAh`:` Avg: ${{lineAvg}} kVAh`}}}}}},
      scales:{{
        x:{{grid:{{display:false}},ticks:{{color:'#3A4A3D',font:{{size:9,weight:'500'}},maxRotation:0,autoSkip:true,maxTicksLimit:10}},border:{{display:false}}}},
        y:{{min:0,max:Math.ceil(lineMaxV/100)*100+80,grid:{{color:'rgba(0,0,0,.06)'}},ticks:{{color:'#3A4A3D',font:{{size:9,weight:'500'}},stepSize:100,callback:v=>v+' kVAh'}},border:{{display:false}}}}
      }}
    }}
  }});
}})();

// ── Performance chart ─────────────────────────────────────────────────────────
(function(){{
  const isCommercial = {"true" if d.category == "COMMERCIAL-HT" else "false"};
  const perfLabels  = isCommercial ? ['Evening'] : ['Morning','Evening'];
  const pActual     = isCommercial ? [perfActual[1]] : perfActual;
  const pBooked     = isCommercial ? [perfBooked[1]] : perfBooked;
  const pMax        = isCommercial ? [perfMax[1]]    : perfMax;
  const barColors   = isCommercial ? ['#2A5C3F'] : ['#2A5C3F','#2A5C3F'];
  const barColorsB  = isCommercial ? ['rgba(110,171,133,.55)'] : ['rgba(110,171,133,.55)','rgba(110,171,133,.55)'];
  const barBorderB  = isCommercial ? ['#2A5C3F'] : ['#2A5C3F','#2A5C3F'];
  const barColorsM  = isCommercial ? ['rgba(110,171,133,.2)'] : ['rgba(110,171,133,.2)','rgba(110,171,133,.2)'];
  const barBorderM  = isCommercial ? ['rgba(42,92,63,.5)'] : ['rgba(42,92,63,.5)','rgba(42,92,63,.5)'];
  const labelColors = isCommercial ? ['#1a3d2b'] : ['#1a3d2b','#1a3d2b'];
  const labelColorsD= isCommercial ? ['#2A5C3F'] : ['#2A5C3F','#2A5C3F'];

  new Chart(document.getElementById('perfCombinedChart'),{{type:'bar',
    data:{{labels:perfLabels,datasets:[
      {{label:'Actual',data:pActual,backgroundColor:barColors,borderRadius:3,barPercentage:0.7,categoryPercentage:0.8,datalabels:{{display:true,color:ctx=>labelColors[ctx.dataIndex],font:{{size:9,weight:'700'}},anchor:'end',align:'end',offset:4,formatter:v=>Math.round(v)+' kWh'}}}},
      {{label:'Booked',data:pBooked,backgroundColor:barColorsB,borderColor:barBorderB,borderWidth:1.5,borderRadius:3,barPercentage:0.7,categoryPercentage:0.8,datalabels:{{display:true,color:ctx=>labelColors[ctx.dataIndex],font:{{size:9,weight:'700'}},anchor:'end',align:'end',offset:4,formatter:v=>Math.round(v)+' kWh'}}}},
      {{label:'Max Potential',data:pMax,backgroundColor:barColorsM,borderColor:barBorderM,borderWidth:1.5,borderRadius:3,barPercentage:0.7,categoryPercentage:0.8,datalabels:{{display:true,color:ctx=>labelColorsD[ctx.dataIndex],font:{{size:9,weight:'700'}},anchor:'end',align:'end',offset:4,formatter:v=>Math.round(v)+' kWh'}}}}
    ]}},
    options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,layout:{{padding:{{right:20}}}},
      plugins:{{legend:{{display:false}},tooltip:{{mode:'index',intersect:false,callbacks:{{label:ctx=>` ${{ctx.dataset.label}}: ${{Math.round(ctx.parsed.x)}} kWh`}}}}}},
      scales:{{x:{{min:0,max:Math.max(...pActual,...pBooked,...pMax)*1.15,grid:{{color:'rgba(0,0,0,.04)'}},ticks:{{color:'#7A8C7E',font:{{size:9}},callback:v=>Math.round(v)+' kWh'}},border:{{display:false}}}},y:{{grid:{{display:false}},ticks:{{color:'#3A4A3D',font:{{size:11,weight:'600'}}}},border:{{display:false}}}}}}
    }}
  }});
}})();

// ── Demand chart ──────────────────────────────────────────────────────────────
new Chart(document.getElementById('demandChart'),{{type:'bar',
  data:{{labels:['Contract\\nDemand','Max\\nDemand'],datasets:[{{data:[{d.contract_demand},{d.max_demand}],backgroundColor:['rgba(91,146,173,.18)','rgba(217,79,46,.75)'],borderColor:['#5B92AD','#D94F2E'],borderWidth:1.5,borderRadius:4,borderSkipped:'bottom',datalabels:{{display:true,anchor:'end',align:'top',offset:2,font:{{size:9,weight:'700'}},color:['#1E3F5A','#8B2A17'],formatter:v=>v+'\\nkVA'}}}}]}},
  options:{{responsive:true,maintainAspectRatio:false,layout:{{padding:{{top:28}}}},plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>` ${{ctx.parsed.y}} kVA`}}}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:'#7A8C7E',font:{{size:9}}}},border:{{display:false}}}},y:{{min:0,max:Math.ceil(Math.max({d.contract_demand},{d.max_demand})/200)*200+200,grid:{{color:'rgba(0,0,0,.04)'}},ticks:{{color:'#7A8C7E',font:{{size:9}},callback:v=>v+' kVA'}},border:{{display:false}}}}}}}}
}});

// ── Heatmap ───────────────────────────────────────────────────────────────────
(function(){{
  const cv=document.getElementById('heatmapChart');
  const dpr=Math.max(window.devicePixelRatio||2,5);
  const W=cv.parentElement.clientWidth-32,H=180;
  cv.width=W*dpr;cv.height=H*dpr;
  cv.style.width=W+'px';cv.style.height=H+'px';
  const ctx=cv.getContext('2d');
  ctx.scale(dpr,dpr);
  const days=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const slots={heatmap_slots_js};
  const data={json.dumps(d.heatmap)};
  const allVals=data.flat().filter(v=>v>0);
  const maxVal=Math.max(...allVals,1);
  const ramp=['#E8F3F8','#9DBFCE','#5B92AD','#3A7FA0','#F9C840','#E87820','#D94F2E'];
  const cc=v=>{{
    if(v<=0) return '#F7F8F7';
    const r=v/maxVal;
    if(r<0.1) return ramp[0];
    if(r<0.25) return ramp[1];
    if(r<0.4) return ramp[2];
    if(r<0.55) return ramp[3];
    if(r<0.7) return ramp[4];
    if(r<0.85) return ramp[5];
    return ramp[6];
  }};
  const lW=50,lH=14,cW=(W-lW)/7,cH=(H-lH)/8,gap=2;
  ctx.font='600 8.5px sans-serif';ctx.fillStyle='#3A4A3D';ctx.textAlign='center';
  days.forEach((d,j)=>ctx.fillText(d,lW+j*cW+cW/2,11));
  ctx.textAlign='right';ctx.font='500 8px sans-serif';
  slots.forEach((slot,i)=>{{
    ctx.fillStyle='#3A4A3D';ctx.fillText(slot,lW-4,lH+i*cH+cH/2+3);
    days.forEach((_,j)=>{{
      const v=data[i]?data[i][j]??0:0;
      ctx.fillStyle=cc(v);
      ctx.beginPath();ctx.roundRect(lW+j*cW+gap/2,lH+i*cH+gap/2,cW-gap,cH-gap,3);ctx.fill();
    }});
  }});
}})();

// ── DR Events chart ───────────────────────────────────────────────────────────
(function(){{
  const canvas  = document.getElementById('dailyChart');
  const dpr     = window.devicePixelRatio || 2;
  const booked  = {daily_booked_js};
  const actual  = {daily_actual_js};
  const lbls    = {day_labels_js};

  const over    = actual.map((v,i) => Math.max(0, v - booked[i]));
  const base    = actual.map((v,i) => booked[i] > 0 ? Math.min(v, booked[i]) : 0);
  const under   = booked.map((bk,i) => bk > 0 ? Math.max(0, bk - actual[i]) : 0);
  const yMax    = Math.ceil(Math.max(...booked, ...actual, 1) / 10) * 10 + 10;
  const noBookH = yMax * 0.75;

  const drPlugin = {{
    id: 'drPlugin',
    beforeDatasetsDraw(chart) {{
      const c    = chart.ctx;
      const yS   = chart.scales.y;
      const meta = chart.getDatasetMeta(0);
      c.save();
      booked.forEach((bk, i) => {{
        const bar = meta.data[i]; if (!bar) return;
        const x = bar.x - bar.width / 2;
        const w = bar.width;
        if (bk === 0) {{
          const y = yS.getPixelForValue(noBookH);
          const h = yS.getPixelForValue(0) - y;
          c.save();
          c.beginPath(); c.rect(x, y, w, h); c.clip();
          c.strokeStyle = 'rgba(91,146,173,0.3)'; c.lineWidth = 1.5;
          const step = 7;
          for (let dd = -(h+w); dd < (h+w); dd += step) {{
            c.beginPath(); c.moveTo(x+dd, y); c.lineTo(x+dd+h, y+h); c.stroke();
          }}
          c.restore();
        }}
      }});
      c.restore();
    }},
    afterDatasetsDraw(chart) {{
      const c        = chart.ctx;
      const yS       = chart.scales.y;
      const metaBase = chart.getDatasetMeta(0);
      const metaUnder= chart.getDatasetMeta(1);
      const metaOver = chart.getDatasetMeta(2);
      const MIN_GAP  = 11;
      c.save();

      const placedY = {{}};
      const claimY = (dayIdx, desiredY) => {{
        const existing = placedY[dayIdx];
        const y = (existing !== undefined && desiredY > existing - MIN_GAP)
          ? existing - MIN_GAP
          : desiredY;
        placedY[dayIdx] = y;
        return y;
      }};

      booked.forEach((bk, i) => {{
        if (bk <= 0) return;
        const barB   = metaBase.data[i];
        const barU   = metaUnder.data[i];
        const barRef = (barU && barU.width > 0) ? barU : barB;
        if (!barRef) return;
        const x  = barRef.x - barRef.width / 2;
        const w  = barRef.width;
        const cx = barRef.x;
        const bookedY = yS.getPixelForValue(bk);
        c.save();
        c.setLineDash([3, 3]);
        c.strokeStyle = '#5B92AD'; c.lineWidth = 1.2;
        c.beginPath(); c.moveTo(x - 4, bookedY); c.lineTo(x + w + 4, bookedY); c.stroke();
        c.setLineDash([]);
        const actualTop = yS.getPixelForValue(actual[i] || 0);
        if (bookedY < actualTop - 12 || under[i] > 2) {{
          const labelY = claimY(i, bookedY - 3);
          c.font = '600 8px sans-serif'; c.fillStyle = '#5B92AD';
          c.textAlign = 'center'; c.textBaseline = 'bottom';
          c.fillText(String(Math.round(bk)), cx, labelY);
        }}
        c.restore();
      }});

      base.forEach((v, i) => {{
        if (v <= 0 || booked[i] <= 0) return;
        const bar = metaBase.data[i]; if (!bar) return;
        const barH = yS.getPixelForValue(0) - yS.getPixelForValue(v);
        if (barH < 6) return;
        const labelY = claimY(i, yS.getPixelForValue(v) - 3);
        c.save();
        c.font = '700 8px sans-serif'; c.fillStyle = '#1B3A6B';
        c.textAlign = 'center'; c.textBaseline = 'bottom';
        c.fillText(String(Math.round(v)), bar.x, labelY);
        c.restore();
      }});

      over.forEach((v, i) => {{
        if (v <= 0) return;
        const bar = metaOver.data[i]; if (!bar) return;
        const labelY = claimY(i, bar.y - 3);
        c.save();
        c.font = '700 8px sans-serif'; c.fillStyle = '#1a6b40';
        c.textAlign = 'center'; c.textBaseline = 'bottom';
        c.fillText(String(Math.round(v)), bar.x, labelY);
        c.restore();
      }});

      c.restore();
    }}
  }};

  new Chart(canvas, {{
    type: 'bar', plugins: [drPlugin],
    data: {{
      labels: lbls,
      datasets: [
        {{ label:'Actual Shifted', data:base, backgroundColor:'#1B3A6B', borderWidth:0, borderRadius:2, barPercentage:0.8, categoryPercentage:1, stack:'s' }},
        {{ label:'Under-delivery', data:under, backgroundColor:'rgba(173,210,230,0.55)', borderColor:'rgba(91,146,173,0.5)', borderWidth:1, borderRadius:2, barPercentage:0.8, categoryPercentage:1, stack:'s' }},
        {{ label:'Over-delivery', data:over, backgroundColor:'#43B97F', borderWidth:0, borderRadius:2, barPercentage:0.8, categoryPercentage:1, stack:'s' }},
        {{ label:'No Booking', data:booked.map(v => v === 0 ? noBookH : null), backgroundColor:'transparent', borderWidth:0, barPercentage:0.8, categoryPercentage:1, stack:'nb' }}
      ]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false, devicePixelRatio:dpr,
      layout:{{ padding:{{ top:20, left:4, right:30, bottom:4 }} }},
      plugins:{{ legend:{{display:false}}, datalabels:{{display:false}},
        tooltip:{{ mode:'index', intersect:false, callbacks:{{ label:ctx => {{
          if(ctx.datasetIndex===0&&ctx.parsed.y>0) return ` Actual: ${{Math.round(actual[ctx.dataIndex])}} kWh`;
          if(ctx.datasetIndex===1&&ctx.parsed.y>0) return ` Under-delivery: ${{Math.round(ctx.parsed.y)}} kWh`;
          if(ctx.datasetIndex===2&&ctx.parsed.y>0) return ` Over-delivery: ${{Math.round(ctx.parsed.y)}} kWh`;
          return null;
        }}}}}}
      }},
      scales:{{
        x:{{ stacked:true, grid:{{display:false}}, ticks:{{color:'#3A4A3D',font:{{size:9,weight:'500'}},maxRotation:0,autoSkip:true,maxTicksLimit:16}}, border:{{display:false}} }},
        y:{{ stacked:true, min:0, max:yMax, grid:{{color:'rgba(0,0,0,.06)'}}, ticks:{{color:'#3A4A3D',font:{{size:9,weight:'500'}},callback:v=>Math.round(v)}}, border:{{display:false}},
             title:{{display:true,text:'kWh',color:'#7A8C7E',font:{{size:9}}}} }}
      }}
    }}
  }});
}})();
</script>
</body>
</html>"""

# ── Write HTML ────────────────────────────────────────────────────────────────
html_path = OUTPUT_PDF.with_suffix(".html")
html_path.write_text(HTML, encoding="utf-8")
print(f"  HTML  : {html_path}")

# ── Serve locally ─────────────────────────────────────────────────────────────
os.chdir(html_path.parent)
handler = http.server.SimpleHTTPRequestHandler
handler.log_message = lambda *a: None
httpd = http.server.HTTPServer(("127.0.0.1", 8765), handler)
thread = threading.Thread(target=httpd.serve_forever)
thread.daemon = True
thread.start()

# ── Print each page at a uniform height, using PAGE 1's actual rendered
#    height (with real fonts/logo/data loaded) as the source of truth for
#    "the standard page size" — this is far more reliable than a hardcoded
#    guess, since fonts and logo dimensions can shift that number slightly.
#    Pages with MORE content than that are still allowed to print taller
#    rather than clip anything (see measured_h below). ────────────────────
with sync_playwright() as p:
    browser = p.chromium.launch()
    pg = browser.new_page(device_scale_factor=3)
    pg.goto(f"http://127.0.0.1:8765/{html_path.name}", wait_until="networkidle")
    pg.wait_for_timeout(WAIT_MS)

    n_pages = pg.evaluate("() => document.querySelectorAll('.page').length")

    page1_height = pg.evaluate("""
        () => Math.ceil(document.querySelector('.page').getBoundingClientRect().height)
    """)
    target_height = max(page1_height, PAGE_HEIGHT)
    print(f"  Pages : {n_pages} — page 1's real height is {page1_height}px, using {target_height}px as the standard")

    tmp_files = []
    for i in range(n_pages):
        pg.evaluate(f"""
            () => {{
                document.querySelectorAll('.page').forEach((el, idx) => {{
                    el.style.display = idx === {i} ? 'flex' : 'none';
                }});
            }}
        """)
        pg.wait_for_timeout(100)
        measured_h = pg.evaluate(f"""
            () => Math.ceil(document.querySelectorAll('.page')[{i}].getBoundingClientRect().height)
        """)
        print_h = max(measured_h, target_height)
        if measured_h > target_height:
            print(f"  ⚠ Page {i+1} content ({measured_h}px) exceeds standard {target_height}px — printing taller instead of clipping")
        tmp = OUTPUT_PDF.with_stem(OUTPUT_PDF.stem + f"_p{i+1}")
        pg.pdf(path=str(tmp), print_background=True,
               margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
               width=f"{PAGE_WIDTH}px", height=f"{print_h}px")
        tmp_files.append(tmp)
        print(f"  Page {i+1}: {print_h}px → {tmp.name}")

    browser.close()

httpd.shutdown()

writer = PdfWriter()
for tmp in tmp_files:
    writer.append(str(tmp))
with open(str(OUTPUT_PDF), "wb") as f:
    writer.write(f)
for tmp in tmp_files:
    tmp.unlink()

size_kb = OUTPUT_PDF.stat().st_size // 1024
print(f"  PDF   : {OUTPUT_PDF}  ({size_kb} KB)")
print("  Done ✓")