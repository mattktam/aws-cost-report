import boto3
import os
from datetime import datetime, timedelta, timezone

YOUR_EMAIL = os.environ["SES_SENDER_EMAIL"]
RECIPIENTS = os.environ["SES_RECIPIENTS"].split(",")
SPIKE_THRESHOLD = 20

def get_dates():
    today        = datetime.now(timezone.utc).date()
    yesterday    = today - timedelta(days=1)
    day_before   = today - timedelta(days=2)
    return str(day_before), str(yesterday)

def fetch_costs(client, start_date, end_date):
    end = str((datetime.fromisoformat(end_date) + timedelta(days=1)).date())
    response = client.get_cost_and_usage(
        TimePeriod={"Start": start_date, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    results = {}
    for result in response["ResultsByTime"]:
        for group in result["Groups"]:
            service = group["Keys"][0]
            amount  = float(group["Metrics"]["UnblendedCost"]["Amount"])
            results[service] = results.get(service, 0.0) + amount
    return results

def fetch_weekly_costs(client, compare_date):
    week_start = str((datetime.fromisoformat(compare_date) - timedelta(days=6)).date())
    week_end   = str((datetime.fromisoformat(compare_date) + timedelta(days=1)).date())
    response = client.get_cost_and_usage(
        TimePeriod={"Start": week_start, "End": week_end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    daily_totals = {}
    for result in response["ResultsByTime"]:
        date  = result["TimePeriod"]["Start"]
        total = sum(float(g["Metrics"]["UnblendedCost"]["Amount"]) for g in result["Groups"])
        daily_totals[date] = total
    return daily_totals

def fetch_monthly_costs(client, compare_date):
    month_start = datetime.fromisoformat(compare_date).replace(day=1).date()
    month_end   = str((datetime.fromisoformat(compare_date) + timedelta(days=1)).date())
    response = client.get_cost_and_usage(
        TimePeriod={"Start": str(month_start), "End": month_end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    daily_totals = {}
    for result in response["ResultsByTime"]:
        date  = result["TimePeriod"]["Start"]
        total = sum(float(g["Metrics"]["UnblendedCost"]["Amount"]) for g in result["Groups"])
        daily_totals[date] = total
    return daily_totals

def fetch_past_monthly_totals(client, compare_date, num_months=3):
    today       = datetime.now(timezone.utc).date()
    end_date    = str(today + timedelta(days=1))          # use today to get all available data
    month_start = today.replace(day=1)
    # Go back num_months so we get 3 full past months + current MTD (4 total)
    start_date  = month_start
    for _ in range(num_months):
        month      = start_date.month - 1 or 12
        year       = start_date.year - (1 if start_date.month == 1 else 0)
        start_date = start_date.replace(year=year, month=month, day=1)
    print(f"[monthly] today={today} month_start={month_start} start_date={start_date} end_date={end_date}")
    response = client.get_cost_and_usage(
        TimePeriod={"Start": str(start_date), "End": end_date},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    # Pre-initialize all expected month buckets so they always appear
    month_buckets = {}
    cur = start_date
    while cur <= today:
        month_buckets[cur] = {"total": 0.0, "avg_total": 0.0, "days": 0}
        m = cur.month + 1 if cur.month < 12 else 1
        y = cur.year + 1 if cur.month == 12 else cur.year
        cur = cur.replace(year=y, month=m, day=1)

    for result in response["ResultsByTime"]:
        day_dt    = datetime.fromisoformat(result["TimePeriod"]["Start"]).date()
        day_total = sum(float(g["Metrics"]["UnblendedCost"]["Amount"]) for g in result["Groups"])
        key = day_dt.replace(day=1)
        if key not in month_buckets:
            month_buckets[key] = {"total": 0.0, "avg_total": 0.0, "days": 0}
        month_buckets[key]["total"] += day_total
        if day_dt.day == 1:
            continue
        month_buckets[key]["avg_total"] += day_total
        month_buckets[key]["days"]      += 1

    print(f"[monthly] months found: {[k.strftime('%b %Y') for k in sorted(month_buckets.keys())]}")
    monthly_totals = {}
    for month_key, data in sorted(month_buckets.items()):
        month_label = month_key.strftime("%b %Y")
        if month_key == month_start:
            month_label += " (MTD)"
        monthly_totals[month_label] = data
    return monthly_totals


def compare_costs(base, compare):
    all_services = set(base) | set(compare)
    rows = []
    for service in all_services:
        base_cost    = base.get(service, 0.0)
        compare_cost = compare.get(service, 0.0)
        diff         = compare_cost - base_cost
        pct_change   = (diff / base_cost * 100) if base_cost else None
        rows.append({
            "service":      service,
            "base_cost":    base_cost,
            "compare_cost": compare_cost,
            "diff":         diff,
            "pct_change":   pct_change,
        })
    return sorted(rows, key=lambda r: r["compare_cost"], reverse=True)

def get_spikes(rows):
    return [
        r for r in rows
        if r["pct_change"] is not None
        and r["pct_change"] >= SPIKE_THRESHOLD
        and r["diff"] >= 1.0
    ]

def print_report(rows, base_date, compare_date):
    base_total    = sum(r["base_cost"]    for r in rows)
    compare_total = sum(r["compare_cost"] for r in rows)
    net           = compare_total - base_total
    net_pct       = (net / base_total * 100) if base_total else 0
    print(f"\nAWS Cost Comparison: {base_date} vs {compare_date}")
    print(f"  Base    ({base_date}):    ${base_total:>10.4f}")
    print(f"  Compare ({compare_date}): ${compare_total:>10.4f}")
    print(f"  Net change:               ${net:>+10.4f}  ({net_pct:+.2f}%)\n")
    header = f"{'Service':<50} {'Base':>12} {'Compare':>12} {'Diff':>12} {'Change':>10}"
    print(header)
    print("-" * len(header))
    for r in rows[:20]:
        pct = f"{r['pct_change']:+.2f}%" if r["pct_change"] is not None else "  new"
        print(
            f"{r['service'][:49]:<50}"
            f"  ${r['base_cost']:>10.4f}"
            f"  ${r['compare_cost']:>10.4f}"
            f"  ${r['diff']:>+10.4f}"
            f"  {pct:>10}"
        )

def render_html_report(rows, base_date, compare_date, weekly_totals, monthly_totals, past_monthly_totals=None):
    base_total    = sum(r["base_cost"]    for r in rows)
    compare_total = sum(r["compare_cost"] for r in rows)
    net           = compare_total - base_total
    net_pct       = (net / base_total * 100) if base_total else 0
    generated_at  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    spikes        = get_spikes(rows)

    def badge(diff, pct):
        if pct is None:
            return '<span style="display:inline-block;padding:3px 9px;border-radius:20px;font-size:12px;font-weight:600;background:rgba(249,144,0,0.15);color:rgb(255,153,0);border:1px solid rgba(249,144,0,0.4)">new</span>'
        if abs(pct) < 0.01:
            return '<span style="display:inline-block;padding:3px 9px;border-radius:20px;font-size:12px;font-weight:600;background:rgba(139,148,158,0.15);color:rgb(139,148,158);border:1px solid rgba(139,148,158,0.4)">0%</span>'
        if diff > 0:
            return f'<span style="display:inline-block;padding:3px 9px;border-radius:20px;font-size:12px;font-weight:600;background:rgba(231,76,60,0.15);color:rgb(231,76,60);border:1px solid rgba(231,76,60,0.4)">\u25b2 {pct:.2f}%</span>'
        return f'<span style="display:inline-block;padding:3px 9px;border-radius:20px;font-size:12px;font-weight:600;background:rgba(39,174,96,0.15);color:rgb(46,204,113);border:1px solid rgba(39,174,96,0.4)">\u25bc {abs(pct):.2f}%</span>'

    def diff_color(diff):
        if diff > 0:  return "#e74c3c"
        if diff < 0:  return "#27ae60"
        return "#888"

    net_sign  = "+" if net > 0 else ""
    net_color = diff_color(net)
    net_badge = badge(net, net_pct)

    # Spike alert
    spike_html = ""
    if spikes:
        spike_rows = ""
        for s in spikes:
            spike_rows += f"""
<tr>
  <td style="padding:9px 14px;font-size:13px;color:#e6edf3;border-top:1px solid rgba(231,76,60,0.2)">{s['service']}</td>
  <td style="padding:9px 14px;font-size:13px;text-align:right;color:#e6edf3;border-top:1px solid rgba(231,76,60,0.2)">${s['compare_cost']:.2f}</td>
  <td style="padding:9px 14px;font-size:13px;text-align:right;color:#e74c3c;font-weight:700;border-top:1px solid rgba(231,76,60,0.2)">+${s['diff']:.2f}</td>
  <td style="padding:9px 14px;font-size:13px;text-align:center;border-top:1px solid rgba(231,76,60,0.2)">{badge(1, s['pct_change'])}</td>
</tr>"""
        spike_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;background:rgba(231,76,60,0.07);border:1px solid rgba(231,76,60,0.35);border-radius:12px;border-collapse:separate">
  <tr><td colspan="4" style="padding:14px 14px 10px 14px;font-size:14px;font-weight:700;color:rgb(231,76,60)">\u26a0\ufe0f Cost spike alert &mdash; {len(spikes)} service{'s' if len(spikes)>1 else ''} up {SPIKE_THRESHOLD}%+</td></tr>
  <tr>
    <th style="padding:6px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:left;font-weight:600">Service</th>
    <th style="padding:6px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:right;font-weight:600">Cost</th>
    <th style="padding:6px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:right;font-weight:600">Increase</th>
    <th style="padding:6px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:center;font-weight:600">Change</th>
  </tr>
  {spike_rows}
</table>"""

    # Summary cards using table layout (Gmail safe)
    card_style      = "width:33%;padding:16px 20px;background:rgb(22,27,34);border:1px solid rgb(48,54,61);border-radius:12px"
    accent_style    = "width:33%;padding:16px 20px;background:rgba(39,174,96,0.07);border:1px solid rgba(39,174,96,0.3);border-radius:12px"
    label_style     = "font-size:11px;color:rgb(139,148,158);text-transform:uppercase;letter-spacing:0.8px;margin:0"
    value_style     = "font-size:26px;font-weight:700;margin:6px 0 4px 0;color:#e6edf3"
    accent_val      = "font-size:26px;font-weight:700;margin:6px 0 4px 0;color:rgb(39,174,96)"
    sub_style       = "font-size:12px;color:rgb(139,148,158);margin:0"

    cards_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;border-collapse:separate;border-spacing:12px 0">
  <tr>
    <td style="{card_style}">
      <p style="{label_style}">{base_date}</p>
      <p style="{value_style}">${base_total:,.2f}</p>
      <p style="{sub_style}">Base date</p>
    </td>
    <td style="{card_style}">
      <p style="{label_style}">{compare_date}</p>
      <p style="{value_style}">${compare_total:,.2f}</p>
      <p style="{sub_style}">Compare date</p>
    </td>
    <td style="{accent_style}">
      <p style="{label_style}">Net change</p>
      <p style="{accent_val}">${net:+,.2f}</p>
      <p style="{sub_style}">{net_badge} vs base date</p>
    </td>
  </tr>
</table>"""

    # Weekly trend
    weekly_html = ""
    if weekly_totals:
        max_val  = max(weekly_totals.values()) or 1
        bar_rows = ""
        for date, total in sorted(weekly_totals.items()):
            pct    = (total / max_val * 100)
            dlabel = datetime.fromisoformat(date).strftime("%a %b %d")
            bar_rows += f"""
<tr>
  <td style="padding:5px 14px;font-size:12px;color:rgb(139,148,158);white-space:nowrap;width:90px">{dlabel}</td>
  <td style="padding:5px 14px;width:100%">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="background:rgba(56,139,255,0.15);border-radius:4px;height:16px;padding:0">
        <table width="{pct:.0f}%" cellpadding="0" cellspacing="0"><tr>
          <td style="background:rgb(56,139,255);border-radius:4px;height:16px;padding:0"></td>
        </tr></table>
      </td>
    </tr></table>
  </td>
  <td style="padding:5px 14px;font-size:12px;color:#e6edf3;text-align:right;white-space:nowrap;width:80px">${total:,.2f}</td>
</tr>"""
        weekly_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;background:rgb(22,27,34);border:1px solid rgb(48,54,61);border-radius:12px;border-collapse:separate">
  <tr><td colspan="3" style="padding:14px 14px 10px 14px;font-size:14px;font-weight:600;color:rgb(201,209,217);border-bottom:1px solid rgb(48,54,61)">\U0001f4c5 7-day spend trend</td></tr>
  {bar_rows}
</table>"""

    # Monthly trend
    monthly_html = ""
    if monthly_totals:
        max_val  = max(monthly_totals.values()) or 1
        bar_rows = ""
        for date, total in sorted(monthly_totals.items()):
            pct    = (total / max_val * 100)
            dlabel = datetime.fromisoformat(date).strftime("%b %d")
            bar_rows += f"""
<tr>
  <td style="padding:4px 14px;font-size:12px;color:rgb(139,148,158);white-space:nowrap;width:70px">{dlabel}</td>
  <td style="padding:4px 14px;width:100%">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="background:rgba(56,139,255,0.15);border-radius:4px;height:12px;padding:0">
        <table width="{pct:.0f}%" cellpadding="0" cellspacing="0"><tr>
          <td style="background:rgb(56,139,255);border-radius:4px;height:12px;padding:0"></td>
        </tr></table>
      </td>
    </tr></table>
  </td>
  <td style="padding:4px 14px;font-size:12px;color:#e6edf3;text-align:right;white-space:nowrap;width:80px">${total:,.2f}</td>
</tr>"""
        monthly_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;background:rgb(22,27,34);border:1px solid rgb(48,54,61);border-radius:12px;border-collapse:separate">
  <tr><td colspan="3" style="padding:14px 14px 10px 14px;font-size:14px;font-weight:600;color:rgb(201,209,217);border-bottom:1px solid rgb(48,54,61)">\U0001f4c6 Month-to-date spend</td></tr>
  {bar_rows}
</table>"""

    # Past monthly totals
    past_monthly_html = ""
    if past_monthly_totals:
        max_val  = max(v["total"] for v in past_monthly_totals.values()) or 1
        bar_rows = ""
        for label, data in past_monthly_totals.items():
            total     = data["total"]
            days      = data["days"]
            daily_avg = data["avg_total"] / days if days else 0
            pct = (total / max_val * 100)
            bar_rows += f"""
<tr>
  <td style="padding:6px 14px;font-size:12px;color:rgb(139,148,158);white-space:nowrap;width:80px">{label}</td>
  <td style="padding:6px 14px;width:100%">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="background:rgba(188,140,255,0.15);border-radius:4px;height:14px;padding:0">
        <table width="{pct:.0f}%" cellpadding="0" cellspacing="0"><tr>
          <td style="background:rgb(188,140,255);border-radius:4px;height:14px;padding:0"></td>
        </tr></table>
      </td>
    </tr></table>
  </td>
  <td style="padding:6px 14px;font-size:13px;font-weight:600;color:#e6edf3;text-align:right;white-space:nowrap;width:90px">${total:,.2f}</td>
  <td style="padding:6px 14px;font-size:12px;color:rgb(139,148,158);text-align:right;white-space:nowrap;width:100px">${daily_avg:,.2f}/day</td>
</tr>"""
        past_monthly_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;background:rgb(22,27,34);border:1px solid rgb(48,54,61);border-radius:12px;border-collapse:separate">
  <tr><td colspan="4" style="padding:14px 14px 10px 14px;font-size:14px;font-weight:600;color:rgb(201,209,217);border-bottom:1px solid rgb(48,54,61)">&#128197; Monthly totals (last 3 months)</td></tr>
  {bar_rows}
</table>"""

    # Main service table rows
    rows_html = ""
    for i, r in enumerate(rows[:20]):
        dc       = diff_color(r["diff"])
        sign     = "+" if r["diff"] > 0 else ""
        top_border = "border-top:1px solid rgb(33,38,45);" if i > 0 else ""
        rows_html += f"""
<tr>
  <td style="padding:10px 14px;font-size:13px;color:#e6edf3;{top_border}">{r['service']}</td>
  <td style="padding:10px 14px;font-size:13px;text-align:right;color:#e6edf3;{top_border}">${r['base_cost']:.4f}</td>
  <td style="padding:10px 14px;font-size:13px;text-align:right;color:#e6edf3;{top_border}">${r['compare_cost']:.4f}</td>
  <td style="padding:10px 14px;font-size:13px;text-align:right;font-weight:600;color:{dc};{top_border}">{sign}${r['diff']:.4f}</td>
  <td style="padding:10px 14px;font-size:13px;text-align:center;{top_border}">{badge(r['diff'], r['pct_change'])}</td>
</tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AWS Cost Report</title>
</head>
<body style="margin:0;padding:0;background:rgb(13,17,23);font-family:'Segoe UI',system-ui,sans-serif;color:#e6edf3">
<table width="100%" cellpadding="0" cellspacing="0" style="background:rgb(13,17,23);padding:32px 16px">
<tr><td>
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:860px;margin:0 auto">

  <!-- Header -->
  <tr>
    <td style="padding-bottom:24px">
      <table cellpadding="0" cellspacing="0"><tr>
        <td style="width:44px;height:44px;background:linear-gradient(135deg,rgb(255,153,0) 40%,rgb(255,107,0));border-radius:10px;text-align:center;vertical-align:middle;font-size:22px">\u2601</td>
        <td style="padding-left:14px">
          <div style="font-size:22px;font-weight:700;color:#e6edf3;letter-spacing:-0.5px">AWS Cost Comparison</div>
          <div style="font-size:13px;color:rgb(139,148,158);margin-top:3px">Daily spend \u00b7 {base_date} vs {compare_date}</div>
        </td>
      </tr></table>
    </td>
  </tr>

  <!-- Summary cards -->
  <tr><td style="padding-bottom:4px">{cards_html}</td></tr>

  <!-- Spike alert -->
  <tr><td>{spike_html}</td></tr>

  <!-- Weekly trend -->
  <tr><td>{weekly_html}</td></tr>

  <!-- Monthly trend -->
  <tr><td>{monthly_html}</td></tr>

  <!-- Past monthly totals -->
  <tr><td>{past_monthly_html}</td></tr>

  <!-- Service table -->
  <tr><td>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:rgb(22,27,34);border:1px solid rgb(48,54,61);border-radius:12px;border-collapse:separate">
      <tr><td colspan="5" style="padding:14px 14px 12px 14px;font-size:14px;font-weight:600;color:rgb(201,209,217);border-bottom:1px solid rgb(48,54,61)">\U0001f4ca Top 20 Services by Cost ({compare_date})</td></tr>
      <tr style="background:rgb(13,17,23)">
        <th style="padding:9px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:left;font-weight:600;border-bottom:1px solid rgb(48,54,61)">Service</th>
        <th style="padding:9px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:right;font-weight:600;border-bottom:1px solid rgb(48,54,61)">{base_date}</th>
        <th style="padding:9px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:right;font-weight:600;border-bottom:1px solid rgb(48,54,61)">{compare_date}</th>
        <th style="padding:9px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:right;font-weight:600;border-bottom:1px solid rgb(48,54,61)">Difference</th>
        <th style="padding:9px 14px;font-size:11px;text-transform:uppercase;letter-spacing:0.7px;color:rgb(139,148,158);text-align:center;font-weight:600;border-bottom:1px solid rgb(48,54,61)">Change %</th>
      </tr>
      {rows_html}
      <!-- Total row -->
      <tr style="background:rgb(13,17,23)">
        <td style="padding:11px 14px;font-size:13px;font-weight:700;color:#e6edf3;border-top:2px solid rgb(48,54,61)">Total</td>
        <td style="padding:11px 14px;font-size:13px;font-weight:700;text-align:right;color:#e6edf3;border-top:2px solid rgb(48,54,61)">${base_total:.4f}</td>
        <td style="padding:11px 14px;font-size:13px;font-weight:700;text-align:right;color:#e6edf3;border-top:2px solid rgb(48,54,61)">${compare_total:.4f}</td>
        <td style="padding:11px 14px;font-size:13px;font-weight:700;text-align:right;color:{net_color};border-top:2px solid rgb(48,54,61)">{net_sign}${abs(net):.4f}</td>
        <td style="padding:11px 14px;font-size:13px;text-align:center;border-top:2px solid rgb(48,54,61)">{net_badge}</td>
      </tr>
    </table>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding-top:24px;text-align:center;font-size:12px;color:rgb(72,79,88)">Generated {generated_at} \u00b7 AWS Cost Explorer (UnblendedCost)</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""
    return html

def send_email(html, base_date, compare_date, spikes):
    client  = boto3.client("ses", region_name="us-east-1")
    subject = f"AWS Cost Report: {base_date} vs {compare_date}"
    if spikes:
        subject += f" \u26a0\ufe0f {len(spikes)} spike{'s' if len(spikes)>1 else ''} detected"
    client.send_email(
        Source=YOUR_EMAIL,
        Destination={"ToAddresses": RECIPIENTS},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body":    {"Html": {"Data": html, "Charset": "UTF-8"}},
        },
    )
    print(f"Report emailed to {RECIPIENTS}")

def main(event=None, context=None):
    base_date, compare_date = get_dates()
    client = boto3.client("ce", region_name="us-east-1")

    print(f"Fetching costs for {base_date} and {compare_date}...")
    base_data      = fetch_costs(client, base_date,    base_date)
    compare_data   = fetch_costs(client, compare_date, compare_date)
    rows           = compare_costs(base_data, compare_data)
    spikes         = get_spikes(rows)
    weekly_totals        = fetch_weekly_costs(client, compare_date)
    monthly_totals       = fetch_monthly_costs(client, compare_date)
    past_monthly_totals  = fetch_past_monthly_totals(client, compare_date, num_months=3)

    # Use the working MTD data for the March row
    today       = datetime.now(timezone.utc).date()
    march_label = today.replace(day=1).strftime("%b %Y") + " (MTD)"
    march_total = sum(monthly_totals.values())
    march_avg   = sum(v for d, v in monthly_totals.items() if not d.endswith("-01"))
    march_days  = sum(1 for d in monthly_totals if not d.endswith("-01"))
    past_monthly_totals[march_label] = {
        "total":     march_total,
        "avg_total": march_avg,
        "days":      march_days,
    }

    print_report(rows, base_date, compare_date)
    html = render_html_report(rows, base_date, compare_date, weekly_totals, monthly_totals, past_monthly_totals)
    send_email(html, base_date, compare_date, spikes)

def lambda_handler(event, context):
    main(event, context)
    return {
        'statusCode': 200,
        'body': 'AWS cost report sent successfully'
    }
