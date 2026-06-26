"""
demand_response_csv.py
======================
Reads data from CSV files and assembles the report_data dict expected
by demand_response_report.py / build_html().

CSV files expected (all in the same directory as this script):
  dr_events.csv       — one row per DR event
  dr_daily.csv        — one row per day of the month
  dr_config.csv       — single-row config (tariff rates, targets, etc.)

Run standalone to print a data summary:
    python demand_response_csv.py 2025-01 "Main Office Tower" "2nd" "Andhra Pradesh"
"""

import csv
import os
import sys
from datetime import datetime, date

# ── Tariff rates (₹/kWh) — override in dr_config.csv ────────────────────────
DEFAULT_PEAK_RATE    = 8.27
DEFAULT_NORMAL_RATE  = 6.75
DEFAULT_OFFPEAK_RATE = 5.55

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ═══════════════════════════════════════════════════════════════
#  CSV LOADERS
# ═══════════════════════════════════════════════════════════════

def _read_csv(filename):
    path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV not found: {path}")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_config():
    """Returns a dict of config values, with defaults for missing keys."""
    try:
        rows = _read_csv("dr_config.csv")
        cfg  = rows[0] if rows else {}
    except FileNotFoundError:
        cfg = {}
    defaults = {
        "peak_rate":    str(DEFAULT_PEAK_RATE),
        "normal_rate":  str(DEFAULT_NORMAL_RATE),
        "offpeak_rate": str(DEFAULT_OFFPEAK_RATE),
        "target_participation": "60",
        "co2_per_kwh":  "0.82",   # kg CO2 per kWh (India grid avg)
        "km_per_kg_co2": "0.21",  # km driving equivalent per kg CO2
    }
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    return cfg


def _load_events(period):
    """
    Load dr_events.csv filtered to the given period (YYYY-MM).
    Expected columns:
        date        YYYY-MM-DD
        day         e.g. Monday
        time        e.g. 6–9 PM
        target      integer %
        actual_pct  integer %
        kwh_shifted integer
        savings     integer ₹
        result      Achieved | Missed
    """
    rows = _read_csv("dr_events.csv")
    return [r for r in rows if r["date"].startswith(period)]


def _load_daily(period):
    """
    Load dr_daily.csv filtered to the given period (YYYY-MM).
    Expected columns:
        date            YYYY-MM-DD
        label           display label e.g. "Jan 1"
        base_kwh        integer — normal consumption
        dr_kwh          integer — kWh shifted during DR events (0 on non-DR days)
        offpeak_pct     float   — % of day's consumption in off-peak hours
        peak_kwh        integer — kWh consumed during peak hours
        normal_kwh      integer — kWh consumed during normal hours
        offpeak_kwh     integer — kWh consumed during off-peak hours
    """
    rows = _read_csv("dr_daily.csv")
    return [r for r in rows if r["date"].startswith(period)]


# ═══════════════════════════════════════════════════════════════
#  DERIVED CALCULATIONS
# ═══════════════════════════════════════════════════════════════

def _period_label(period):
    """'2025-01' → 'January 2025'"""
    try:
        return datetime.strptime(period, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return period


def _output_filename(period, location):
    safe = location.replace(" ", "_").lower()
    return f"demand_response_{period}_{safe}.pdf"


def _event_kpis(events, cfg):
    peak_rate    = float(cfg["peak_rate"])
    normal_rate  = float(cfg["normal_rate"])
    offpeak_rate = float(cfg["offpeak_rate"])

    achieved = [e for e in events if e["result"] == "Achieved"]
    missed   = [e for e in events if e["result"] != "Achieved"]

    total_saved   = sum(int(e["savings"]) for e in achieved)
    total_shifted = sum(int(e["kwh_shifted"]) for e in events)

    # Missed savings: what achieved events would have earned at max target
    missed_savings = 0
    for e in missed:
        target_pct = int(e["target"]) / 100
        # Approximate: use actual kWh shifted as a proxy base
        # missed kWh ≈ (target% - actual%) / actual% * actual_kwh
        actual_pct = int(e["actual_pct"]) / 100
        actual_kwh = int(e["kwh_shifted"])
        if actual_pct > 0:
            missed_kwh = int((target_pct - actual_pct) / actual_pct * actual_kwh)
        else:
            missed_kwh = int(target_pct * 50)  # fallback estimate
        missed_savings += max(0, missed_kwh) * offpeak_rate

    participation_rate = round(len(achieved) / len(events) * 100) if events else 0

    return {
        "total_saved":        total_saved,
        "missed_savings":     int(missed_savings),
        "total_shifted_kwh":  total_shifted,
        "participation_rate": participation_rate,
        "n_achieved":         len(achieved),
        "n_missed":           len(missed),
        "n_total":            len(events),
    }


def _tariff_split(daily_rows, cfg):
    peak_rate    = float(cfg["peak_rate"])
    normal_rate  = float(cfg["normal_rate"])
    offpeak_rate = float(cfg["offpeak_rate"])

    peak_kwh    = sum(int(r["peak_kwh"])    for r in daily_rows)
    normal_kwh  = sum(int(r["normal_kwh"])  for r in daily_rows)
    offpeak_kwh = sum(int(r["offpeak_kwh"]) for r in daily_rows)

    return {
        "peak":    {"kwh": peak_kwh,    "cost": int(peak_kwh    * peak_rate)},
        "normal":  {"kwh": normal_kwh,  "cost": int(normal_kwh  * normal_rate)},
        "offpeak": {"kwh": offpeak_kwh, "cost": int(offpeak_kwh * offpeak_rate)},
    }


def _morning_evening(events):
    """
    Actual % = mean actual_pct across achieved events per window.
    Potential % = mean target % per window.
    """
    am_events = [e for e in events if "AM" in e.get("time", "")]
    pm_events = [e for e in events if "PM" in e.get("time", "")]

    def avg_actual(evs):
        achieved = [e for e in evs if e["result"] == "Achieved"]
        if not achieved:
            return int(evs[0]["actual_pct"]) if evs else 0
        return round(sum(int(e["actual_pct"]) for e in achieved) / len(achieved), 1)

    def avg_target(evs):
        if not evs:
            return 20
        return round(sum(int(e["target"]) for e in evs) / len(evs), 1)

    return {
        "morning": {
            "actual":    avg_actual(am_events) if am_events else 0,
            "potential": avg_target(am_events),
        },
        "evening": {
            "actual":    avg_actual(pm_events) if pm_events else 0,
            "potential": avg_target(pm_events),
        },
    }


def _missed_breakdown(events, cfg):
    offpeak_rate = float(cfg["offpeak_rate"])

    am_missed = [e for e in events if e["result"] != "Achieved" and "AM" in e.get("time", "")]
    pm_missed = [e for e in events if e["result"] != "Achieved" and "PM" in e.get("time", "")]

    def missed_val(evs):
        total = 0
        for e in evs:
            target_pct = int(e["target"]) / 100
            actual_pct = int(e["actual_pct"]) / 100
            actual_kwh = int(e["kwh_shifted"])
            if actual_pct > 0:
                gap_kwh = int((target_pct - actual_pct) / actual_pct * actual_kwh)
            else:
                gap_kwh = int(target_pct * 50)
            total += max(0, gap_kwh) * offpeak_rate
        return int(total)

    am_val = missed_val(am_missed)
    pm_val = missed_val(pm_missed)

    return {
        "morning_peak": {"value": am_val},
        "evening_peak": {"value": pm_val},
        "bullets": [
            f"{len(pm_missed)} evening event(s) missed — lower load response after 6 PM",
            "Evening peak demand 18–22% above morning baseline",
        ],
    }


def _annual_opportunity(monthly_missed):
    low  = int(monthly_missed * 10)
    high = int(monthly_missed * 14)
    return {"low": low, "high": high}


def _offpeak_daily(daily_rows):
    result = []
    for r in daily_rows:
        result.append((r["label"], float(r["offpeak_pct"])))
    return result


def _daily_stacked(daily_rows):
    result = []
    for r in daily_rows:
        base = int(r["base_kwh"])
        dr   = int(r["dr_kwh"])
        result.append((r["label"], base, dr, dr > 0))
    return result


# ═══════════════════════════════════════════════════════════════
#  MAIN BUILDER
# ═══════════════════════════════════════════════════════════════

def build_report_data(period, location, building_rank, building_rank_region):
    cfg        = _load_config()
    events     = _load_events(period)
    daily_rows = _load_daily(period)

    if not events:
        raise ValueError(f"No events found for period {period} in dr_events.csv")
    if not daily_rows:
        raise ValueError(f"No daily data found for period {period} in dr_daily.csv")

    per_label    = _period_label(period)
    event_kpis   = _event_kpis(events, cfg)
    tariff       = _tariff_split(daily_rows, cfg)
    total_kwh    = sum(int(r["base_kwh"]) + int(r["dr_kwh"]) for r in daily_rows)
    monthly_bill = tariff["peak"]["cost"] + tariff["normal"]["cost"] + tariff["offpeak"]["cost"]
    op_daily     = _offpeak_daily(daily_rows)
    monthly_avg  = round(sum(v for _, v in op_daily) / len(op_daily), 1)
    me           = _morning_evening(events)
    mb           = _missed_breakdown(events, cfg)
    total_missed = mb["morning_peak"]["value"] + mb["evening_peak"]["value"]

    data = {
        "meta": {
            "location":    location,
            "period":      per_label,
            "rank":        building_rank,
            "region":      building_rank_region,
            "output_file": _output_filename(period, location),
        },

        "kpis": {
            "saved":              event_kpis["total_saved"],
            "missed_savings":     event_kpis["missed_savings"],
            "total_consumed":     total_kwh,
            "monthly_bill":       monthly_bill,
            "participation_rate": event_kpis["participation_rate"],
            "streak":             3,           # update manually or add to dr_config.csv
            "peak_demand_kva":    142,          # update manually or add to dr_config.csv
            "peak_demand_ts":     f"Recorded {per_label[:3]} peak hour",
        },

        "tariff_split": tariff,

        "morning_evening": me,

        "missed_breakdown": mb,

        "recommendations": {
            "bullets": [
                "Pre-cool HVAC and shift water-pump loads before 6 PM to reduce evening peak draw.",
                "Enable automated DR alerts on the building management system for faster response.",
            ],
        },

        "annual_opportunity": _annual_opportunity(total_missed),

        "event_counts": {
            "total":           event_kpis["n_total"],
            "achieved":        event_kpis["n_achieved"],
            "missed":          event_kpis["n_missed"],
            "total_kwh_shifted": event_kpis["total_shifted_kwh"],
        },

        "event_summary": [
            {
                "date":       e["date"],
                "day":        e["day"],
                "time":       e["time"],
                "target":     int(e["target"]),
                "actual_pct": int(e["actual_pct"]),
                "kwh_shifted": int(e["kwh_shifted"]),
                "savings":    int(e["savings"]),
                "result":     e["result"],
            }
            for e in events
        ],

        "daily_stacked": _daily_stacked(daily_rows),

        "hourly_chart": {
            "offpeak_daily":   op_daily,
            "monthly_avg":     monthly_avg,
            "potential_saving": int(total_missed * 1.2),
            "offpeak_rate":    cfg["offpeak_rate"],
        },
    }

    # ── Summary printout ──────────────────────────────────────
    print("=" * 54)
    print(f"  Demand Response Report  —  {per_label}")
    print("=" * 54)
    print(f"  Location           : {location}")
    print(f"  Saved              : ₹{data['kpis']['saved']:,}")
    print(f"  Missed Savings     : ₹{data['kpis']['missed_savings']:,}")
    print(f"  Total Consumed     : {total_kwh:,} kWh")
    print(f"  Monthly Bill       : ₹{monthly_bill:,}")
    print(f"  Participation Rate : {data['kpis']['participation_rate']}%")
    print(f"  DR Events          : {event_kpis['n_total']} total, {event_kpis['n_achieved']} achieved")
    print(f"  kWh Shifted        : {event_kpis['total_shifted_kwh']}")
    print(f"  Annual Opportunity : ₹{data['annual_opportunity']['low']:,} – ₹{data['annual_opportunity']['high']:,}")
    print(f"  Output             : {data['meta']['output_file']}")
    print("=" * 54)

    return data


# ═══════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python demand_response_csv.py <period> <location> <rank> <region>")
        print('Example: python demand_response_csv.py 2025-01 "Main Office Tower" "2nd" "Andhra Pradesh"')
        print()
        print("This module is normally imported by demand_response_report.py.")
        print("Run demand_response_report.py directly to generate the PDF.")
    else:
        build_report_data(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])