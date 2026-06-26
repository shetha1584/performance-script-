# ══════════════════════════════════════════════════════════════════
#  data.py  —  ALL report values live here. Edit this, run generate_report.py.
# ══════════════════════════════════════════════════════════════════

import psycopg2
import requests
import calendar
from datetime import date, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── CREDENTIALS ───────────────────────────────────────────────────
PG_CONN = dict(
    host     = "65.1.248.33",
    user     = "script_client",
    password = "JlR2KPVu.s/b5fTy",
    database = "ap",
    port     = 5432
)

API_BASE_URL = "https://ap.elementsenergies.com/api"

# ── HELPERS ───────────────────────────────────────────────────────
def api_get(endpoint, params=None):
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    resp = session.get(
        f"{API_BASE_URL}/{endpoint}",
        params=params,
        timeout=120
    )
    resp.raise_for_status()
    return resp.json()

def get_pg_connection():
    return psycopg2.connect(**PG_CONN)

# ── GETTER: streak & participation (PostgreSQL) ───────────────────

def get_streak(msn, year, month):
    conn = get_pg_connection()
    cur  = conn.cursor()

    today           = date.today()
    month_start     = date(year, month, 1)
    end_of_month    = date(year, month, calendar.monthrange(year, month)[1])
    reference_date  = min(today, end_of_month)
    start_of_season = date(year, 5, 1)

    cur.execute("""
        SELECT DISTINCT date
        FROM book_savings
        WHERE msn = %s
          AND date BETWEEN %s AND %s
        ORDER BY date
    """, (msn, start_of_season, reference_date))

    rows  = cur.fetchall()
    cur.close()
    conn.close()

    print(f"  [streak] rows={len(rows)}")

    dates = {r[0] for r in rows}

    streak = 0
    check  = reference_date
    while check in dates:
        streak += 1
        check  -= timedelta(days=1)

    month_dates        = {d for d in dates if month_start <= d <= reference_date}
    days_elapsed       = (reference_date - month_start).days + 1
    participation_days = len(month_dates)
    participation_rate = round((participation_days / days_elapsed) * 100, 2)

    print(f"  [streak] streak={streak}, participation={participation_days}/{days_elapsed}")

    return {
        "streak":             streak,
        "participation_days": participation_days,
        "participation_rate": participation_rate,
    }

# ── GETTER: energy + tariff + demand (reportData API) ────────────

def get_report_data(msn, year, month):
    last_day   = calendar.monthrange(year, month)[1]
    start_date = f"{year}-{month:02d}-01"
    end_date   = f"{year}-{month:02d}-{last_day:02d}"

    print(f"  [reportData] fetching {start_date} → {end_date}...")

    data = api_get("reportData", params={
        "msn":       msn,
        "startDate": start_date,
        "endDate":   end_date,
    })

    print(f"  [reportData] total_energy_kwh={data.get('total_energy_kwh')}")
    print(f"  [reportData] max_demand={data.get('demand', {}).get('max_demand')}")
    print(f"  [reportData] days returned={len(data.get('daily_consumption', []))}")

    return data

# ── GETTER: DR data (reportDRData API) ───────────────────────────

def get_dr_data(msn, year, month):
    last_day   = calendar.monthrange(year, month)[1]
    start_date = f"{year}-{month:02d}-01"
    end_date   = f"{year}-{month:02d}-{last_day:02d}"

    print(f"  [reportDRData] fetching {start_date} → {end_date}...")

    data = api_get("reportDRData", params={
        "msn":       msn,
        "startDate": start_date,
        "endDate":   end_date,
    })

    print(f"  [reportDRData] dr_savings={data.get('dr_savings')}")
    print(f"  [reportDRData] events={len(data.get('events', []))}")

    return data

# ── GETTER: heatmap data (reportHeatmap API) ──────────────────────

def get_heatmap_data(msn, year, month):
    last_day   = calendar.monthrange(year, month)[1]
    start_date = f"{year}-{month:02d}-01"
    end_date   = f"{year}-{month:02d}-{last_day:02d}"

    print(f"  [reportHeatmap] fetching {start_date} → {end_date}...")

    data = api_get("reportHeatmap", params={
        "msn":       msn,
        "startDate": start_date,
        "endDate":   end_date,
    })

    print(f"  [reportHeatmap] slots={len(data.get('slot_labels', []))}")

    return data
def get_rank(msn):
    data = api_get("reportRank", params={"msn": msn})
    rank = data.get("rank", 0)
    print(f"  [rank] rank={rank}")
    return rank
# ── MAIN FETCH — called by generate_report.py ─────────────────────

def fetch(msn, year, month):
    month_label = f"{calendar.month_name[month]} {year}"
    last_day    = calendar.monthrange(year, month)[1]

    print(f"\n  Fetching data for MSN={msn} {month_label}...")
    streak_data  = get_streak(msn, year, month)
    report       = get_report_data(msn, year, month)
    dr           = get_dr_data(msn, year, month)
    heatmap_data = get_heatmap_data(msn, year, month)

    # ── unpack reportData ─────────────────────────────────────────
    tariff     = report.get("tariff",  {})
    demand     = report.get("demand",  {})
    daily_list = report.get("daily_consumption", [])
    scno       = report.get("scno",    msn)
    category   = report.get("category", "")

    daily_kwh = [d["kwh"] for d in daily_list]
    while len(daily_kwh) < last_day:
        daily_kwh.append(0)

    contract = demand.get("contract_demand") or 0
    max_dem  = demand.get("max_demand")      or 0
    avg_dem  = demand.get("avg_demand")      or 0
    util_max = round((max_dem / contract) * 100) if contract else 0
    util_avg = round((avg_dem / contract) * 100) if contract else 0

    offpeak_rate_label = "₹5.55" if "INDUSTRY" in category else "₹7.65"
    site_name = f"SCNO {scno}" if scno else msn

    # ── unpack reportDRData ───────────────────────────────────────
    
    dr_events_raw = dr.get("events", [])
    daily_dr      = dr.get("daily_dr_shifted", [0] * last_day)
    while len(daily_dr) < last_day:
        daily_dr.append(0)

    events = [
        (
            e["date"],
            e["day"],
            e["window"],
            round(e["target_kwh"], 1),
            round(e["actual_kwh"], 1),
            round(e["kwh_shifted"], 1),
            int(e["savings_inr"]),
            e["result"],
        )
        for e in dr_events_raw
    ] if dr_events_raw else [("—", "—", "—", 0, 0, 0, 0, "Achieved")]

    missed_note = dr.get("missed_evening_note", ["DR data coming soon"])

    # ── unpack heatmap ────────────────────────────────────────────
    heatmap       = heatmap_data.get("heatmap", [[0]*7]*8)
    heatmap_slots = heatmap_data.get("slot_labels", [
        "12–3 AM","3–6 AM","6–9 AM","9–12 PM",
        "12–3 PM","3–6 PM","6–9 PM","9–12 AM"
    ])

    return dict(
        # META
        site         = site_name,
        month        = month_label,
        region       = "Andhra Pradesh",
        offpeak_rate = offpeak_rate_label,
        rank         = get_rank(msn),

        # STREAK — live
        streak             = streak_data["streak"],
        participation_days = streak_data["participation_days"],
        total_days         = last_day,
        target_days        = 19,
        target_pct         = 60,

        # KPIs — live
        total_energy_kwh  = report.get("total_energy_kwh", 0),
        peak_demand_kva   = demand.get("peak_demand_kva",  0),
        peak_demand_note  = demand.get("peak_demand_note", ""),

        # Tariff — live
        offpeak_kwh  = tariff.get("offpeak_kwh",  0),
        offpeak_inr  = tariff.get("offpeak_cost", 0),
        peak_kwh     = tariff.get("peak_kwh",     0),
        peak_inr     = tariff.get("peak_cost",    0),
        normal_kwh   = tariff.get("normal_kwh",   0),
        normal_inr   = tariff.get("normal_cost",  0),
        total_bill   = tariff.get("total_bill",   0),

        # Peak demand — live
        contract_demand = contract,
        max_demand      = max_dem,
        avg_demand      = avg_dem,
        util_max_pct    = util_max,
        util_avg_pct    = util_avg,

        # Daily chart — live
        daily_consumption = daily_kwh,

        # DR savings — live
        dr_savings          = dr.get("dr_savings",          0),
        dr_savings_delta    = dr.get("dr_savings_delta",    "N/A"),
        missed_savings      = dr.get("missed_savings",      0),
        missed_savings_note = dr.get("missed_savings_note", "No data"),
        missed_morning_inr  = dr.get("missed_morning_inr",  0),
        missed_evening_inr   = dr.get("missed_evening_inr",   0),
        achieved_morning_inr = dr.get("achieved_morning_inr", 0),
        achieved_evening_inr = dr.get("achieved_evening_inr", 0),
        missed_evening_note  = missed_note,

        # Performance chart — live
        perf_actual = dr.get("perf_actual", [0, 0]),
        perf_booked = dr.get("perf_booked", [0, 0]),
        perf_max    = dr.get("perf_max",    [0, 0]),

        # DR shifted chart — live
        daily_dr_shifted = daily_dr,

        # Heatmap — live
        heatmap       = heatmap,
        heatmap_slots = heatmap_slots,

        # Events — live
        events = events,

        # Actions
        actions = [
            "Most missed savings occurred during evening peak (6–9 PM) due to reduced response to DR alerts. "
            "Improving evening response alone could unlock <strong>₹1.4–1.6 lakh annually</strong> for this site.",
            "If you were powered by solar, your monthly bill would be <strong>₹12,400</strong> — "
            "a saving of over ₹33,000/month compared to your current grid bill.",
            "Target a minimum <strong>15% reduction</strong> during all 6–9 PM DR events. "
            "Shift non-critical loads (HVAC pre-cooling, water pumps) before 6 PM to make this achievable.",
        ],

        # Footers
        footer_p1 = f"This report covers DR performance for {site_name}, Andhra Pradesh region, {month_label}.",
        footer_p2 = "Event data represents verified DR responses. kWh shifted calculated against baseline day consumption profiles.",
    )