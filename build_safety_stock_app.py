import csv
import json
import math
from pathlib import Path
from datetime import date, datetime, timedelta


ROOT = Path(__file__).resolve().parent
SAFETY_STOCK_FILE = ROOT / "part5_safety_stock_level" / "safety_stock_levels.csv"
INVENTORY_FILE = ROOT / "cloudpick_inventory_base.csv"
ISSUANCE_FILE = ROOT / "cloudpick_issuance.csv"
TEST_FORECAST_FILE = (
    ROOT
    / "presentation_tables"
    / "table_2_optimal_forecasted_daily_issuance_test_period.csv"
)
PART2_DAYS_TO_ZERO_FILE = (
    ROOT
    / "part2_days_to_zero"
    / "part2_days_to_zero_daily.csv"
)
OUTPUT_DIR = ROOT / "web_app"
OUTPUT_FILE = OUTPUT_DIR / "safety_stock_monitoring.html"
REAL_ISSUANCE_UPDATES_FILE = OUTPUT_DIR / "active_real_issuance_updates.csv"
LEGACY_REAL_ISSUANCE_UPDATES_FILE = OUTPUT_DIR / "real_issuance_updates.csv"
IGNORE_REAL_ISSUANCE_UPDATES_FILE = OUTPUT_DIR / "ignore_real_issuance_updates.flag"
IGNORE_REAL_ISSUANCE_VALUE = "ignore"
ACTIVE_REAL_ISSUANCE_VALUE = "active"


SITE_MAPPING = {
    "PGFS eMedcab": "Punggol",
    "Punggol Fire Station": "Punggol",
    "SengKang Fire Station": "Sengkang",
    "Sengkang Fire Station": "Sengkang",
    "SKFS eMedCab": "Sengkang",
    "pgfs emedcab": "Punggol",
    "punggol fire station": "Punggol",
    "sengkang fire station": "Sengkang",
    "skfs emedcab": "Sengkang",
}


def clean_number(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        parsed = float(value)
        if math.isnan(parsed):
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def clean_stock_threshold(value):
    return max(0.0, clean_number(value))


def canonical_site(value):
    text = (value or "").strip()
    return SITE_MAPPING.get(text, SITE_MAPPING.get(text.lower(), text.title()))


def parse_datetime(value):
    text = (value or "").strip()
    if not text:
        return None

    for date_format in (
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            pass

    return None


def load_inventory():
    inventory = {}

    with INVENTORY_FILE.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            product_id = (row.get("Material ID") or row.get("Product ID") or "").strip()
            raw_site = (
                row.get("Site Location")
                or row.get("Store Name")
                or row.get("Amended Store Name")
                or ""
            )
            site = canonical_site(raw_site)

            if site not in {"Punggol", "Sengkang"} or not product_id:
                continue

            key = (product_id, site)
            if key not in inventory:
                inventory[key] = {
                    "currentHolding": 0.0,
                    "inventoryPar": 0.0,
                    "photo": row.get("Photo", ""),
                }

            inventory[key]["currentHolding"] += clean_number(row.get("Current Holding"))
            inventory[key]["inventoryPar"] += clean_number(row.get("PAR"))
            if not inventory[key]["photo"] and row.get("Photo"):
                inventory[key]["photo"] = row["Photo"]

    return inventory


def load_monitoring_rows():
    inventory = load_inventory()
    rows = []

    with SAFETY_STOCK_FILE.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            product_id = (row.get("Product ID") or "").strip()
            site = canonical_site(row.get("Site Location"))
            key = (product_id, site)
            inv = inventory.get(key, {})

            par = clean_number(row.get("PAR"))
            safety_stock = clean_stock_threshold(row.get("Safety Stock Level"))
            current_holding = clean_number(inv.get("currentHolding"), default=None)
            threshold_gap = (
                current_holding - safety_stock
                if current_holding is not None
                else None
            )

            if current_holding is None:
                status = "No inventory"
            elif current_holding <= safety_stock:
                status = "Below threshold"
            else:
                status = "Healthy"

            rows.append({
                "productId": product_id,
                "itemDescription": row.get("Item Description", ""),
                "site": site,
                "bestMethod": row.get("Best Method", ""),
                "optimalPercentile": clean_number(row.get("Optimal Percentile")),
                "par": par,
                "currentHolding": current_holding,
                "safetyStockLevel": safety_stock,
                "lowestRemainingStock": clean_number(row.get("Lowest Remaining Stock")),
                "optimalReplenishmentDays": clean_number(row.get("Optimal Replenishment Days")),
                "optimalReplenishmentPercentage": clean_number(row.get("Optimal Replenishment Percentage")),
                "numberOfTopUps": clean_number(row.get("Number of Top Ups")),
                "thresholdGap": threshold_gap,
                "status": status,
                "photo": inv.get("photo", ""),
            })

    status_order = {
        "Below threshold": 0,
        "No inventory": 1,
        "Healthy": 2,
    }
    return sorted(
        rows,
        key=lambda item: (
            status_order.get(item["status"], 9),
            item["thresholdGap"] if item["thresholdGap"] is not None else 999999,
            item["productId"],
            item["site"],
        ),
    )


def load_daily_issuance():
    daily_issuance = {}

    with ISSUANCE_FILE.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            transaction_type = (row.get("Transaction Type") or "").strip().lower()
            if transaction_type not in {"issue", "11"}:
                continue

            product_id = (row.get("Product ID") or "").strip()
            raw_site = row.get("Site Location") or row.get("Station") or ""
            site = canonical_site(raw_site)
            issued_quantity = clean_number(row.get("Issued Quantity"))
            issued_at = parse_datetime(row.get("Date / Time"))

            if not product_id or site not in {"Punggol", "Sengkang"}:
                continue

            if issued_quantity <= 0 or issued_at is None:
                continue

            key = (product_id, site)
            date_key = issued_at.date().isoformat()
            daily_issuance.setdefault(key, {})
            daily_issuance[key][date_key] = (
                daily_issuance[key].get(date_key, 0.0)
                + issued_quantity
            )

    return daily_issuance


def load_stock_decrease_rows():
    monitoring_rows = load_monitoring_rows()
    daily_issuance = load_daily_issuance()
    rows = []

    for row in monitoring_rows:
        key = (row["productId"], row["site"])
        issue_by_date = daily_issuance.get(key, {})
        remaining = row["currentHolding"]
        timeline = []
        first_alert_date = None
        total_issued = 0.0

        if remaining is not None:
            for date_key in sorted(issue_by_date):
                issued_quantity = issue_by_date[date_key]
                remaining -= issued_quantity
                total_issued += issued_quantity

                alert = remaining <= row["safetyStockLevel"]
                if alert and first_alert_date is None:
                    first_alert_date = date_key

                timeline.append({
                    "date": date_key,
                    "issuedQuantity": round(issued_quantity, 2),
                    "remainingStock": round(remaining, 2),
                    "safetyStockLevel": row["safetyStockLevel"],
                    "alert": alert,
                })

        simulated_final_stock = (
            round(remaining, 2)
            if remaining is not None
            else None
        )

        if row["currentHolding"] is None:
            issue_status = "No inventory"
        elif row["currentHolding"] <= row["safetyStockLevel"]:
            issue_status = "Alert"
        elif first_alert_date:
            issue_status = "Projected alert"
        elif not timeline:
            issue_status = "No issuance"
        else:
            issue_status = "Clear"

        rows.append({
            **row,
            "issueStatus": issue_status,
            "timeline": timeline,
            "issuanceDays": len(timeline),
            "totalIssuedInHistory": round(total_issued, 2),
            "simulatedFinalStock": simulated_final_stock,
            "firstAlertDate": first_alert_date,
        })

    issue_status_order = {
        "Alert": 0,
        "Projected alert": 1,
        "No inventory": 2,
        "No issuance": 3,
        "Clear": 4,
    }

    return sorted(
        rows,
        key=lambda item: (
            issue_status_order.get(item["issueStatus"], 9),
            item["firstAlertDate"] or "9999-12-31",
            item["productId"],
            item["site"],
        ),
    )


def get_test_period_end_date():
    latest_date = None

    with TEST_FORECAST_FILE.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            parsed = parse_datetime(row.get("Date"))
            if parsed is None:
                continue

            row_date = parsed.date()
            if latest_date is None or row_date > latest_date:
                latest_date = row_date

    return latest_date or date.today()


def percentile(values, p):
    clean_values = sorted(float(value) for value in values)

    if not clean_values:
        return 0.0

    if len(clean_values) == 1:
        return clean_values[0]

    position = (len(clean_values) - 1) * (p / 100)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)

    if lower_index == upper_index:
        return clean_values[int(position)]

    lower_value = clean_values[lower_index]
    upper_value = clean_values[upper_index]
    weight = position - lower_index

    return lower_value + ((upper_value - lower_value) * weight)


def load_replenishment_day_values():
    values = {}

    with PART2_DAYS_TO_ZERO_FILE.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            reached_zero = (row.get("Reached Zero") or "").strip().lower() == "true"
            if not reached_zero:
                continue

            product_id = (row.get("Product ID") or "").strip()
            site = canonical_site(row.get("Site Location"))
            days_elapsed = clean_number(row.get("Days Elapsed"), default=None)

            if not product_id or site not in {"Punggol", "Sengkang"}:
                continue

            if days_elapsed is None:
                continue

            values.setdefault((product_id, site), []).append(days_elapsed - 3)

    return values


def load_test_actual_issuance():
    actual_issuance = {}

    with TEST_FORECAST_FILE.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            product_id = (row.get("Product ID") or "").strip()
            site = canonical_site(row.get("Site Location"))
            issued_at = parse_datetime(row.get("Date"))

            if not product_id or site not in {"Punggol", "Sengkang"} or issued_at is None:
                continue

            actual_quantity = clean_number(row.get("Actual Issuance"))
            key = (product_id, site)
            date_key = issued_at.date().isoformat()
            actual_issuance.setdefault(key, {})
            actual_issuance[key][date_key] = (
                actual_issuance[key].get(date_key, 0.0)
                + actual_quantity
            )

    return actual_issuance


def normalize_real_issuance_row(row):
    product_id = (row.get("Product ID") or row.get("productId") or "").strip()
    site = canonical_site(
        row.get("Site Location")
        or row.get("site")
        or row.get("Station")
        or ""
    )
    issued_quantity = clean_number(
        row.get("Issued Quantity")
        or row.get("issuedQuantity")
        or row.get("Actual Issuance")
    )
    issued_at = parse_datetime(
        row.get("Date / Time")
        or row.get("Date")
        or row.get("date")
    )

    if not product_id or site not in {"Punggol", "Sengkang"}:
        return None

    if issued_at is None or issued_quantity <= 0:
        return None

    return {
        "Product ID": product_id,
        "Site Location": site,
        "Date": issued_at.date().isoformat(),
        "Issued Quantity": round(issued_quantity, 2),
    }


def append_real_issuance_updates(rows):
    OUTPUT_DIR.mkdir(exist_ok=True)
    IGNORE_REAL_ISSUANCE_UPDATES_FILE.write_text(
        ACTIVE_REAL_ISSUANCE_VALUE,
        encoding="utf-8",
    )

    normalized_rows = []

    for row in rows:
        normalized = normalize_real_issuance_row(row)
        if normalized is not None:
            normalized_rows.append(normalized)

    file_exists = REAL_ISSUANCE_UPDATES_FILE.exists()

    with REAL_ISSUANCE_UPDATES_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Site Location",
                "Date",
                "Issued Quantity",
            ],
        )

        if not file_exists:
            writer.writeheader()

        writer.writerows(normalized_rows)

    return len(normalized_rows)


def load_real_issuance_updates():
    daily_updates = {}

    if (
        IGNORE_REAL_ISSUANCE_UPDATES_FILE.exists()
        and IGNORE_REAL_ISSUANCE_UPDATES_FILE.read_text(encoding="utf-8").strip()
        == IGNORE_REAL_ISSUANCE_VALUE
    ):
        return daily_updates

    if not REAL_ISSUANCE_UPDATES_FILE.exists():
        return daily_updates

    with REAL_ISSUANCE_UPDATES_FILE.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            normalized = normalize_real_issuance_row(row)
            if normalized is None:
                continue

            key = (
                normalized["Product ID"],
                normalized["Site Location"],
            )
            date_key = normalized["Date"]
            daily_updates.setdefault(key, {})
            daily_updates[key][date_key] = (
                daily_updates[key].get(date_key, 0.0)
                + normalized["Issued Quantity"]
            )

    return daily_updates


def clear_real_issuance_updates():
    OUTPUT_DIR.mkdir(exist_ok=True)
    removed = False

    if REAL_ISSUANCE_UPDATES_FILE.exists():
        try:
            REAL_ISSUANCE_UPDATES_FILE.unlink()
            removed = True
        except PermissionError:
            IGNORE_REAL_ISSUANCE_UPDATES_FILE.write_text(
                IGNORE_REAL_ISSUANCE_VALUE,
                encoding="utf-8",
            )

    if LEGACY_REAL_ISSUANCE_UPDATES_FILE.exists():
        try:
            LEGACY_REAL_ISSUANCE_UPDATES_FILE.unlink()
            removed = True
        except PermissionError:
            removed = True

    IGNORE_REAL_ISSUANCE_UPDATES_FILE.write_text(
        IGNORE_REAL_ISSUANCE_VALUE,
        encoding="utf-8",
    )
    return removed


def merge_issue_series(*series_list):
    merged = {}

    for issue_series in series_list:
        for date_key, quantity in issue_series.items():
            merged[date_key] = merged.get(date_key, 0.0) + quantity

    return merged


def simulate_part3_replenishment(row, issue_by_date, replenishment_day_values):
    baseline_result = {
        "optimalPercentile": row["optimalPercentile"],
        "optimalReplenishmentDays": row["optimalReplenishmentDays"],
        "lowestRemainingStock": clean_stock_threshold(row["safetyStockLevel"]),
        "numberOfTopUps": row["numberOfTopUps"],
    }

    if row["currentHolding"] is None or not issue_by_date:
        return baseline_result

    if not replenishment_day_values:
        replenishment_day_values = [row["optimalReplenishmentDays"]]

    previous_valid_result = None

    for p in range(1, 101):
        candidate_replenishment_days = percentile(replenishment_day_values, p)
        stock = row["currentHolding"]
        days_since_top_up = 0
        lowest_remaining_stock = stock
        number_of_top_ups = 0
        negative_stock = False

        for date_key in sorted(issue_by_date):
            stock -= issue_by_date[date_key]
            days_since_top_up += 1
            lowest_remaining_stock = min(lowest_remaining_stock, stock)

            if stock < 0:
                negative_stock = True
                break

            if days_since_top_up >= candidate_replenishment_days:
                stock = row["par"]
                days_since_top_up = 0
                number_of_top_ups += 1

        result = {
            "optimalPercentile": p,
            "optimalReplenishmentDays": round(candidate_replenishment_days, 2),
            "lowestRemainingStock": round(max(0.0, lowest_remaining_stock), 2),
            "numberOfTopUps": number_of_top_ups,
        }

        if negative_stock:
            break

        previous_valid_result = result

    return previous_valid_result or baseline_result


def build_threshold_series(
    row,
    test_issue_by_date,
    uploaded_issue_by_date,
    replenishment_day_values,
    future_dates,
):
    baseline_threshold = clean_stock_threshold(row["safetyStockLevel"])
    series = []

    for future_date in future_dates:
        uploaded_issues_to_date = {
            date_key: quantity
            for date_key, quantity in uploaded_issue_by_date.items()
            if date_key <= future_date
        }
        extended_issue_by_date = merge_issue_series(
            test_issue_by_date,
            uploaded_issues_to_date,
        )
        simulation_result = simulate_part3_replenishment(
            row,
            extended_issue_by_date,
            replenishment_day_values,
        )

        series.append({
            "date": future_date,
            "stockThreshold": simulation_result["lowestRemainingStock"],
            "optimalReplenishmentDays": simulation_result["optimalReplenishmentDays"],
            "optimalPercentile": simulation_result["optimalPercentile"],
            "isUpdated": bool(uploaded_issues_to_date),
        })

    if series:
        series[0]["stockThreshold"] = baseline_threshold
        series[0]["isUpdated"] = False

    return series


def load_future_threshold_rows():
    monitoring_rows = load_monitoring_rows()
    real_updates = load_real_issuance_updates()
    test_actual_issuance = load_test_actual_issuance()
    replenishment_day_values_by_key = load_replenishment_day_values()
    test_end_date = get_test_period_end_date()
    future_dates = [
        (test_end_date + timedelta(days=offset)).isoformat()
        for offset in range(31)
    ]

    rows = []

    for row in monitoring_rows:
        key = (row["productId"], row["site"])
        uploaded_issue_by_date = real_updates.get(key, {})
        test_issue_by_date = test_actual_issuance.get(key, {})
        replenishment_day_values = replenishment_day_values_by_key.get(key, [])
        extended_issue_by_date = merge_issue_series(
            test_issue_by_date,
            uploaded_issue_by_date,
        )
        real_data_total_issued = round(sum(uploaded_issue_by_date.values()), 2)
        simulation_result = simulate_part3_replenishment(
            row,
            extended_issue_by_date,
            replenishment_day_values,
        )
        threshold_series = build_threshold_series(
            row,
            test_issue_by_date,
            uploaded_issue_by_date,
            replenishment_day_values,
            future_dates,
        )
        baseline_current_holding = row["currentHolding"]
        remaining_stock = baseline_current_holding
        stock_decrease_series = []

        if remaining_stock is not None:
            stock_decrease_series.append({
                "date": test_end_date.isoformat(),
                "remainingStock": round(remaining_stock, 2),
                "issuedQuantity": 0.0,
            })

            for date_key in sorted(uploaded_issue_by_date):
                remaining_stock -= uploaded_issue_by_date[date_key]
                stock_decrease_series.append({
                    "date": date_key,
                    "remainingStock": round(remaining_stock, 2),
                    "issuedQuantity": round(uploaded_issue_by_date[date_key], 2),
                })

        current_holding = (
            round(baseline_current_holding - real_data_total_issued, 2)
            if baseline_current_holding is not None
            else None
        )
        threshold_gap = (
            current_holding - simulation_result["lowestRemainingStock"]
            if current_holding is not None
            else None
        )

        if current_holding is None:
            alert_status = "No inventory"
        elif current_holding <= simulation_result["lowestRemainingStock"]:
            alert_status = "Alert"
        elif uploaded_issue_by_date:
            alert_status = "Updated with real data"
        else:
            alert_status = "Safe"

        rows.append({
            **row,
            "baselineCurrentHolding": baseline_current_holding,
            "currentHolding": current_holding,
            "safetyStockLevel": simulation_result["lowestRemainingStock"],
            "baselineSafetyStockLevel": clean_stock_threshold(row["safetyStockLevel"]),
            "optimalPercentile": simulation_result["optimalPercentile"],
            "optimalReplenishmentDays": simulation_result["optimalReplenishmentDays"],
            "numberOfTopUps": simulation_result["numberOfTopUps"],
            "thresholdGap": threshold_gap,
            "alertStatus": alert_status,
            "realDataRows": len(uploaded_issue_by_date),
            "realDataTotalIssued": real_data_total_issued,
            "stockDecreaseSeries": stock_decrease_series,
            "futureDates": future_dates,
            "thresholdSeries": threshold_series,
            "testEndDate": test_end_date.isoformat(),
            "futureEndDate": future_dates[-1],
        })

    status_order = {
        "Alert": 0,
        "Updated with real data": 1,
        "No inventory": 2,
        "Safe": 3,
    }

    return sorted(
        rows,
        key=lambda item: (
            status_order.get(item["alertStatus"], 9),
            item["productId"],
            item["site"],
        ),
    )


def build_html(rows):
    data_json = json.dumps(rows, ensure_ascii=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Safety Stock Monitoring</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d7dde5;
      --green: #16794c;
      --green-bg: #e7f6ee;
      --amber: #a05a00;
      --amber-bg: #fff3d6;
      --red: #b42318;
      --red-bg: #ffe8e5;
      --blue: #2357c5;
      --blue-bg: #eaf0ff;
      --shadow: 0 14px 35px rgba(15, 23, 42, 0.08);
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 15px;
    }}

    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}

    .wrap {{
      width: min(1420px, calc(100% - 32px));
      margin: 0 auto;
    }}

    .topbar {{
      min-height: 96px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
    }}

    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      line-height: 1.1;
      letter-spacing: 0;
    }}

    .subtitle {{
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }}

    main {{
      padding: 24px 0 40px;
    }}

    .toolbar {{
      display: grid;
      grid-template-columns: minmax(240px, 1.3fr) repeat(3, minmax(150px, 0.55fr));
      gap: 12px;
      margin-bottom: 16px;
    }}

    input, select {{
      width: 100%;
      height: 42px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
    }}

    button, .button-link {{
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 0 13px;
      font: inherit;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
    }}

    .nav-actions {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}

    .metrics {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}

    .metric, .detail, .table-panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}

    .metric {{
      padding: 16px;
      min-height: 94px;
    }}

    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-bottom: 9px;
    }}

    .metric-value {{
      font-size: 28px;
      font-weight: 700;
      line-height: 1;
    }}

    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.55fr) minmax(320px, 0.75fr);
      gap: 16px;
      align-items: start;
    }}

    .table-panel {{
      overflow: hidden;
    }}

    .table-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}

    .table-head strong {{
      font-size: 16px;
    }}

    .table-wrap {{
      overflow: auto;
      max-height: 68vh;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
    }}

    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid #ebeff4;
      text-align: left;
      vertical-align: middle;
      white-space: nowrap;
    }}

    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f9fafb;
      color: #344054;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}

    tbody tr {{
      cursor: pointer;
    }}

    tbody tr:hover, tbody tr.active {{
      background: #f2f6ff;
    }}

    .item-cell {{
      white-space: normal;
      min-width: 260px;
      max-width: 380px;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 26px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }}

    .status-healthy {{ color: var(--green); background: var(--green-bg); }}
    .status-below-threshold {{ color: var(--red); background: var(--red-bg); }}
    .status-no-inventory {{ color: var(--blue); background: var(--blue-bg); }}

    .detail {{
      padding: 18px;
      position: sticky;
      top: 16px;
    }}

    .detail-photo {{
      width: 100%;
      aspect-ratio: 16 / 9;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      margin-bottom: 14px;
    }}

    .detail h2 {{
      margin: 0 0 8px;
      font-size: 21px;
      line-height: 1.25;
      letter-spacing: 0;
    }}

    .detail .muted {{
      color: var(--muted);
      margin-bottom: 14px;
      line-height: 1.45;
    }}

    .stock-bars {{
      display: grid;
      gap: 12px;
      margin: 18px 0;
    }}

    .bar-row {{
      display: grid;
      grid-template-columns: 118px minmax(0, 1fr) 70px;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
    }}

    .bar-track {{
      height: 13px;
      background: #edf1f5;
      border-radius: 999px;
      overflow: hidden;
    }}

    .bar-fill {{
      height: 100%;
      width: 0;
      background: var(--blue);
      border-radius: 999px;
    }}

    .bar-fill.threshold {{ background: var(--amber); }}
    .bar-fill.current.bad {{ background: var(--red); }}
    .bar-fill.current.good {{ background: var(--green); }}

    .facts {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}

    .fact {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfe;
      min-height: 72px;
    }}

    .fact span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 7px;
    }}

    .fact strong {{
      display: block;
      font-size: 18px;
      line-height: 1.2;
    }}

    .empty {{
      padding: 28px 16px;
      color: var(--muted);
      text-align: center;
    }}

    @media (max-width: 980px) {{
      .topbar {{
        align-items: flex-start;
        flex-direction: column;
        padding: 18px 0;
      }}

      .toolbar, .metrics, .grid {{
        grid-template-columns: 1fr;
      }}

      .detail {{
        position: static;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap topbar">
      <div>
        <h1>Safety Stock Monitoring</h1>
        <p class="subtitle">Product-site thresholds use the previously identified Lowest Remaining Stock level as the safety stock trigger.</p>
      </div>
      <div class="nav-actions">
        <a class="button-link" href="/future-thresholds">Future threshold view</a>
        <button id="downloadBtn" type="button">Download filtered CSV</button>
      </div>
    </div>
  </header>

  <main class="wrap">
    <section class="toolbar" aria-label="Filters">
      <input id="searchInput" type="search" placeholder="Search product ID or item description">
      <select id="siteFilter">
        <option value="All">All sites</option>
      </select>
      <select id="statusFilter">
        <option value="All">All statuses</option>
      </select>
      <select id="sortSelect">
        <option value="risk">Sort by risk</option>
        <option value="gap">Sort by threshold gap</option>
        <option value="product">Sort by product</option>
        <option value="site">Sort by site</option>
      </select>
    </section>

    <section class="metrics" aria-label="Summary">
      <div class="metric"><div class="metric-label">Monitored SKUs</div><div id="totalMetric" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">Below Threshold</div><div id="belowMetric" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">Healthy</div><div id="healthyMetric" class="metric-value">0</div></div>
    </section>

    <section class="grid">
      <div class="table-panel">
        <div class="table-head">
          <strong>Product-site threshold register</strong>
          <span id="resultCount" class="subtitle">0 rows</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Product</th>
                <th>Item</th>
                <th>Site</th>
                <th>Current</th>
                <th>Threshold</th>
                <th>Gap</th>
                <th>PAR</th>
                <th>Replenish Days</th>
                <th>Top Ups</th>
              </tr>
            </thead>
            <tbody id="tableBody"></tbody>
          </table>
          <div id="emptyState" class="empty" hidden>No matching product-site thresholds.</div>
        </div>
      </div>

      <aside id="detailPanel" class="detail" aria-live="polite"></aside>
    </section>
  </main>

  <script>
    const DATA = {data_json};
    const riskOrder = {{
      "Below threshold": 0,
      "No inventory": 1,
      "Healthy": 2
    }};

    const state = {{
      rows: DATA,
      filtered: DATA,
      selectedKey: "",
    }};

    const els = {{
      search: document.getElementById("searchInput"),
      site: document.getElementById("siteFilter"),
      status: document.getElementById("statusFilter"),
      sort: document.getElementById("sortSelect"),
      body: document.getElementById("tableBody"),
      empty: document.getElementById("emptyState"),
      count: document.getElementById("resultCount"),
      detail: document.getElementById("detailPanel"),
      total: document.getElementById("totalMetric"),
      below: document.getElementById("belowMetric"),
      healthy: document.getElementById("healthyMetric"),
      download: document.getElementById("downloadBtn"),
    }};

    function rowKey(row) {{
      return `${{row.productId}}|${{row.site}}`;
    }}

    function fmt(value, digits = 0) {{
      if (value === null || value === undefined || Number.isNaN(value)) return "-";
      return Number(value).toLocaleString(undefined, {{
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }});
    }}

    function cssStatus(status) {{
      return "status-" + status.toLowerCase().replaceAll(" ", "-");
    }}

    function fillSelect(select, values) {{
      values.forEach((value) => {{
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }});
    }}

    function setupFilters() {{
      fillSelect(els.site, [...new Set(DATA.map((row) => row.site))].sort());
      fillSelect(els.status, [...new Set(DATA.map((row) => row.status))].sort((a, b) => riskOrder[a] - riskOrder[b]));

      [els.search, els.site, els.status, els.sort].forEach((el) => {{
        el.addEventListener("input", applyFilters);
        el.addEventListener("change", applyFilters);
      }});
      els.download.addEventListener("click", downloadCsv);
    }}

    function applyFilters() {{
      const term = els.search.value.trim().toLowerCase();
      const site = els.site.value;
      const status = els.status.value;

      let rows = DATA.filter((row) => {{
        const searchable = `${{row.productId}} ${{row.itemDescription}}`.toLowerCase();
        return (!term || searchable.includes(term))
          && (site === "All" || row.site === site)
          && (status === "All" || row.status === status);
      }});

      rows = sortRows(rows, els.sort.value);
      state.filtered = rows;

      if (!rows.some((row) => rowKey(row) === state.selectedKey)) {{
        state.selectedKey = rows[0] ? rowKey(rows[0]) : "";
      }}

      render();
    }}

    function sortRows(rows, mode) {{
      const sorted = [...rows];
      sorted.sort((a, b) => {{
        if (mode === "gap") {{
          return nullableGap(a) - nullableGap(b);
        }}
        if (mode === "product") {{
          return a.productId.localeCompare(b.productId) || a.site.localeCompare(b.site);
        }}
        if (mode === "site") {{
          return a.site.localeCompare(b.site) || a.productId.localeCompare(b.productId);
        }}
        return (riskOrder[a.status] - riskOrder[b.status])
          || (nullableGap(a) - nullableGap(b))
          || a.productId.localeCompare(b.productId);
      }});
      return sorted;
    }}

    function nullableGap(row) {{
      return row.thresholdGap === null || row.thresholdGap === undefined ? 999999 : row.thresholdGap;
    }}

    function render() {{
      renderMetrics();
      renderTable();
      renderDetail();
    }}

    function renderMetrics() {{
      const rows = state.filtered;
      els.total.textContent = fmt(rows.length);
      els.below.textContent = fmt(rows.filter((row) => row.status === "Below threshold").length);
      els.healthy.textContent = fmt(rows.filter((row) => row.status === "Healthy").length);
      els.count.textContent = `${{fmt(rows.length)}} rows`;
    }}

    function renderTable() {{
      els.body.innerHTML = "";
      els.empty.hidden = state.filtered.length > 0;

      const fragment = document.createDocumentFragment();
      state.filtered.forEach((row) => {{
        const tr = document.createElement("tr");
        const key = rowKey(row);
        if (key === state.selectedKey) tr.classList.add("active");
        tr.innerHTML = `
          <td><span class="pill ${{cssStatus(row.status)}}">${{row.status}}</span></td>
          <td><strong>${{escapeHtml(row.productId)}}</strong></td>
          <td class="item-cell">${{escapeHtml(row.itemDescription)}}</td>
          <td>${{escapeHtml(row.site)}}</td>
          <td>${{fmt(row.currentHolding)}}</td>
          <td>${{fmt(row.safetyStockLevel)}}</td>
          <td>${{fmt(row.thresholdGap)}}</td>
          <td>${{fmt(row.par)}}</td>
          <td>${{fmt(row.optimalReplenishmentDays, 1)}}</td>
          <td>${{fmt(row.numberOfTopUps)}}</td>
        `;
        tr.addEventListener("click", () => {{
          state.selectedKey = key;
          render();
        }});
        fragment.appendChild(tr);
      }});
      els.body.appendChild(fragment);
    }}

    function renderDetail() {{
      const row = state.filtered.find((item) => rowKey(item) === state.selectedKey) || state.filtered[0];
      if (!row) {{
        els.detail.innerHTML = "<h2>No threshold selected</h2><p class='muted'>Adjust filters to show product-site records.</p>";
        return;
      }}

      const maxValue = Math.max(row.par || 0, row.currentHolding || 0, row.safetyStockLevel || 0, 1);
      const currentClass = row.status === "Below threshold" ? "bad" : "good";
      const image = row.photo
        ? `<img class="detail-photo" src="${{escapeAttribute(row.photo)}}" alt="">`
        : "";

      els.detail.innerHTML = `
        ${{image}}
        <span class="pill ${{cssStatus(row.status)}}">${{row.status}}</span>
        <h2>${{escapeHtml(row.productId)}} - ${{escapeHtml(row.site)}}</h2>
        <div class="muted">${{escapeHtml(row.itemDescription || "No item description available.")}}</div>
        <div class="stock-bars">
          ${{bar("Current Holding", row.currentHolding, maxValue, `current ${{currentClass}}`)}}
          ${{bar("Safety Threshold", row.safetyStockLevel, maxValue, "threshold")}}
          ${{bar("PAR", row.par, maxValue, "")}}
        </div>
        <div class="facts">
          <div class="fact"><span>Threshold Gap</span><strong>${{fmt(row.thresholdGap)}}</strong></div>
          <div class="fact"><span>Safety Stock % of PAR</span><strong>${{fmt(row.optimalReplenishmentPercentage, 1)}}%</strong></div>
          <div class="fact"><span>Optimal Replenishment Days</span><strong>${{fmt(row.optimalReplenishmentDays, 1)}}</strong></div>
          <div class="fact"><span>Best Forecast Method</span><strong>${{escapeHtml(row.bestMethod || "-")}}</strong></div>
          <div class="fact"><span>Lowest Remaining Stock</span><strong>${{fmt(row.lowestRemainingStock)}}</strong></div>
          <div class="fact"><span>Top Ups in Simulation</span><strong>${{fmt(row.numberOfTopUps)}}</strong></div>
        </div>
      `;
    }}

    function bar(label, value, maxValue, className) {{
      const pct = Math.max(0, Math.min(100, ((value || 0) / maxValue) * 100));
      return `
        <div class="bar-row">
          <span>${{label}}</span>
          <div class="bar-track"><div class="bar-fill ${{className}}" style="width: ${{pct}}%"></div></div>
          <strong>${{fmt(value)}}</strong>
        </div>
      `;
    }}

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      }}[char]));
    }}

    function escapeAttribute(value) {{
      return escapeHtml(value).replace(/`/g, "&#096;");
    }}

    function downloadCsv() {{
      const headers = [
        "Product ID", "Item Description", "Site Location", "Status",
        "Current Holding", "Safety Stock Level", "Threshold Gap", "PAR",
        "Optimal Replenishment Days", "Number of Top Ups"
      ];
      const lines = [
        headers.join(","),
        ...state.filtered.map((row) => [
          row.productId,
          row.itemDescription,
          row.site,
          row.status,
          row.currentHolding,
          row.safetyStockLevel,
          row.thresholdGap,
          row.par,
          row.optimalReplenishmentDays,
          row.numberOfTopUps,
        ].map(csvCell).join(","))
      ];
      const blob = new Blob([lines.join("\\n")], {{ type: "text/csv;charset=utf-8" }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "filtered_safety_stock_monitoring.csv";
      link.click();
      URL.revokeObjectURL(url);
    }}

    function csvCell(value) {{
      const text = String(value ?? "");
      return /[",\\n]/.test(text) ? `"${{text.replaceAll('"', '""')}}"` : text;
    }}

    setupFilters();
    applyFilters();
  </script>
</body>
</html>
"""


def build_stock_decrease_html(rows):
    data_json = json.dumps(rows, ensure_ascii=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Actual Issuance Stock Decrease</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d7dde5;
      --green: #16794c;
      --green-bg: #e7f6ee;
      --amber: #a05a00;
      --amber-bg: #fff3d6;
      --red: #b42318;
      --red-bg: #ffe8e5;
      --blue: #2357c5;
      --blue-bg: #eaf0ff;
      --shadow: 0 14px 35px rgba(15, 23, 42, 0.08);
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 15px;
    }}

    header {{
      background: #fff;
      border-bottom: 1px solid var(--line);
    }}

    .wrap {{
      width: min(1480px, calc(100% - 32px));
      margin: 0 auto;
    }}

    .topbar {{
      min-height: 96px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 22px;
    }}

    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      line-height: 1.1;
      letter-spacing: 0;
    }}

    .subtitle {{
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }}

    .button-link, button, input, select {{
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
    }}

    .button-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
    }}

    main {{ padding: 24px 0 40px; }}

    .toolbar {{
      display: grid;
      grid-template-columns: minmax(260px, 1.2fr) repeat(4, minmax(150px, .55fr));
      gap: 12px;
      margin-bottom: 16px;
    }}

    .toolbar input, .toolbar select {{ width: 100%; }}

    .metrics {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}

    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}

    .metric {{
      padding: 16px;
      min-height: 92px;
    }}

    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-bottom: 9px;
    }}

    .metric-value {{
      font-size: 28px;
      font-weight: 700;
      line-height: 1;
    }}

    .grid {{
      display: grid;
      grid-template-columns: minmax(0, .9fr) minmax(460px, 1.35fr);
      gap: 16px;
      align-items: start;
    }}

    .panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}

    .list {{
      max-height: 72vh;
      overflow: auto;
    }}

    .product-row {{
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      padding: 12px 16px;
      border: 0;
      border-bottom: 1px solid #ebeff4;
      border-radius: 0;
      text-align: left;
      cursor: pointer;
    }}

    .product-row:hover, .product-row.active {{
      background: #f2f6ff;
    }}

    .row-title {{
      font-weight: 700;
      margin-bottom: 5px;
    }}

    .row-subtitle {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 26px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }}

    .status-clear {{ color: var(--green); background: var(--green-bg); }}
    .status-no-issuance {{ color: var(--blue); background: var(--blue-bg); }}
    .status-no-inventory {{ color: var(--blue); background: var(--blue-bg); }}
    .status-projected-alert {{ color: var(--amber); background: var(--amber-bg); }}
    .status-alert {{ color: var(--red); background: var(--red-bg); }}

    .detail {{
      padding: 18px;
    }}

    .detail-top {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 180px;
      gap: 16px;
      align-items: start;
      margin-bottom: 14px;
    }}

    .detail h2 {{
      margin: 8px 0;
      font-size: 22px;
      line-height: 1.25;
      letter-spacing: 0;
    }}

    .detail-photo {{
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
    }}

    .alert-box {{
      display: none;
      border: 1px solid #ffc9c2;
      background: var(--red-bg);
      color: var(--red);
      border-radius: 8px;
      padding: 12px;
      margin: 12px 0;
      font-weight: 700;
    }}

    .alert-box.show {{ display: block; }}

    .facts {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin: 14px 0;
    }}

    .fact {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfe;
      min-height: 72px;
    }}

    .fact span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 7px;
    }}

    .fact strong {{
      display: block;
      font-size: 18px;
      line-height: 1.2;
    }}

    .chart-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
    }}

    canvas {{
      width: 100%;
      height: 380px;
      display: block;
    }}

    .legend {{
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
    }}

    .legend span::before {{
      content: "";
      display: inline-block;
      width: 18px;
      height: 3px;
      margin-right: 7px;
      vertical-align: middle;
      background: var(--blue);
    }}

    .legend .threshold::before {{ background: var(--red); }}

    .events {{
      margin-top: 14px;
      overflow: auto;
      max-height: 260px;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 620px;
    }}

    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #ebeff4;
      text-align: left;
      white-space: nowrap;
    }}

    th {{
      position: sticky;
      top: 0;
      background: #f9fafb;
      color: #344054;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}

    .empty {{
      padding: 24px 16px;
      color: var(--muted);
      text-align: center;
    }}

    @media (max-width: 1060px) {{
      .topbar, .detail-top {{
        align-items: flex-start;
        flex-direction: column;
        display: flex;
      }}

      .toolbar, .metrics, .grid, .facts {{
        grid-template-columns: 1fr;
      }}

      canvas {{ height: 300px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap topbar">
      <div>
        <h1>Actual Issuance Stock Decrease</h1>
        <p class="subtitle">Current holding is reduced by real daily issue quantities and compared with the safety stock threshold from Lowest Remaining Stock.</p>
      </div>
      <a class="button-link" href="/">Safety threshold register</a>
    </div>
  </header>

  <main class="wrap">
    <section class="toolbar" aria-label="Filters">
      <input id="searchInput" type="search" placeholder="Search product ID or item description">
      <select id="siteFilter"><option value="All">All sites</option></select>
      <select id="statusFilter"><option value="All">All issue statuses</option></select>
      <select id="issuanceFilter">
        <option value="All">All records</option>
        <option value="With issuance">With issuance</option>
        <option value="No issuance">No issuance</option>
      </select>
      <select id="sortSelect">
        <option value="risk">Sort by alert risk</option>
        <option value="date">Sort by alert date</option>
        <option value="product">Sort by product</option>
        <option value="site">Sort by site</option>
      </select>
    </section>

    <section class="metrics" aria-label="Summary">
      <div class="metric"><div class="metric-label">Product-Sites</div><div id="totalMetric" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">Alerts</div><div id="currentMetric" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">Projected Alerts</div><div id="projectedMetric" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">With Issuance</div><div id="issuedMetric" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">Total Issued</div><div id="totalIssuedMetric" class="metric-value">0</div></div>
    </section>

    <section class="grid">
      <div class="panel">
        <div class="panel-head">
          <strong>Product-site issue feed</strong>
          <span id="resultCount" class="subtitle">0 rows</span>
        </div>
        <div id="productList" class="list"></div>
      </div>

      <div id="detailPanel" class="panel detail" aria-live="polite"></div>
    </section>
  </main>

  <script>
    const DATA = {data_json};
    const statusOrder = {{
      "Alert": 0,
      "Projected alert": 1,
      "No inventory": 2,
      "No issuance": 3,
      "Clear": 4
    }};

    const state = {{
      filtered: DATA,
      selectedKey: "",
    }};

    const els = {{
      search: document.getElementById("searchInput"),
      site: document.getElementById("siteFilter"),
      status: document.getElementById("statusFilter"),
      issuance: document.getElementById("issuanceFilter"),
      sort: document.getElementById("sortSelect"),
      list: document.getElementById("productList"),
      detail: document.getElementById("detailPanel"),
      count: document.getElementById("resultCount"),
      total: document.getElementById("totalMetric"),
      current: document.getElementById("currentMetric"),
      projected: document.getElementById("projectedMetric"),
      issued: document.getElementById("issuedMetric"),
      totalIssued: document.getElementById("totalIssuedMetric"),
    }};

    function key(row) {{
      return `${{row.productId}}|${{row.site}}`;
    }}

    function fmt(value, digits = 0) {{
      if (value === null || value === undefined || Number.isNaN(value)) return "-";
      return Number(value).toLocaleString(undefined, {{
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }});
    }}

    function cssStatus(status) {{
      return "status-" + status.toLowerCase().replaceAll(" ", "-");
    }}

    function setup() {{
      fillSelect(els.site, [...new Set(DATA.map((row) => row.site))].sort());
      fillSelect(els.status, [...new Set(DATA.map((row) => row.issueStatus))].sort((a, b) => statusOrder[a] - statusOrder[b]));

      [els.search, els.site, els.status, els.issuance, els.sort].forEach((el) => {{
        el.addEventListener("input", applyFilters);
        el.addEventListener("change", applyFilters);
      }});

      applyFilters();
    }}

    function fillSelect(select, values) {{
      values.forEach((value) => {{
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }});
    }}

    function applyFilters() {{
      const term = els.search.value.trim().toLowerCase();
      const site = els.site.value;
      const status = els.status.value;
      const issuance = els.issuance.value;

      let rows = DATA.filter((row) => {{
        const searchable = `${{row.productId}} ${{row.itemDescription}}`.toLowerCase();
        const issuanceMatch = issuance === "All"
          || (issuance === "With issuance" && row.issuanceDays > 0)
          || (issuance === "No issuance" && row.issuanceDays === 0);

        return (!term || searchable.includes(term))
          && (site === "All" || row.site === site)
          && (status === "All" || row.issueStatus === status)
          && issuanceMatch;
      }});

      rows = sortRows(rows, els.sort.value);
      state.filtered = rows;

      if (!rows.some((row) => key(row) === state.selectedKey)) {{
        state.selectedKey = rows[0] ? key(rows[0]) : "";
      }}

      render();
    }}

    function sortRows(rows, mode) {{
      const sorted = [...rows];
      sorted.sort((a, b) => {{
        if (mode === "date") {{
          return (a.firstAlertDate || "9999-12-31").localeCompare(b.firstAlertDate || "9999-12-31")
            || a.productId.localeCompare(b.productId);
        }}
        if (mode === "product") {{
          return a.productId.localeCompare(b.productId) || a.site.localeCompare(b.site);
        }}
        if (mode === "site") {{
          return a.site.localeCompare(b.site) || a.productId.localeCompare(b.productId);
        }}
        return (statusOrder[a.issueStatus] - statusOrder[b.issueStatus])
          || (a.firstAlertDate || "9999-12-31").localeCompare(b.firstAlertDate || "9999-12-31")
          || a.productId.localeCompare(b.productId);
      }});
      return sorted;
    }}

    function render() {{
      renderMetrics();
      renderList();
      renderDetail();
    }}

    function renderMetrics() {{
      const rows = state.filtered;
      els.total.textContent = fmt(rows.length);
      els.current.textContent = fmt(rows.filter((row) => row.issueStatus === "Alert").length);
      els.projected.textContent = fmt(rows.filter((row) => row.issueStatus === "Projected alert").length);
      els.issued.textContent = fmt(rows.filter((row) => row.issuanceDays > 0).length);
      els.totalIssued.textContent = fmt(rows.reduce((sum, row) => sum + (row.totalIssuedInHistory || 0), 0));
      els.count.textContent = `${{fmt(rows.length)}} rows`;
    }}

    function renderList() {{
      els.list.innerHTML = "";

      if (!state.filtered.length) {{
        els.list.innerHTML = `<div class="empty">No matching product-site issuance records.</div>`;
        return;
      }}

      const fragment = document.createDocumentFragment();
      state.filtered.forEach((row) => {{
        const button = document.createElement("button");
        button.type = "button";
        button.className = "product-row" + (key(row) === state.selectedKey ? " active" : "");
        button.innerHTML = `
          <div>
            <div class="row-title">${{escapeHtml(row.productId)}} - ${{escapeHtml(row.site)}}</div>
            <div class="row-subtitle">${{escapeHtml(row.itemDescription)}}<br>Current ${{fmt(row.currentHolding)}} | Threshold ${{fmt(row.safetyStockLevel)}} | Issue days ${{fmt(row.issuanceDays)}}</div>
          </div>
          <span class="pill ${{cssStatus(row.issueStatus)}}">${{row.issueStatus}}</span>
        `;
        button.addEventListener("click", () => {{
          state.selectedKey = key(row);
          render();
        }});
        fragment.appendChild(button);
      }});
      els.list.appendChild(fragment);
    }}

    function renderDetail() {{
      const row = state.filtered.find((item) => key(item) === state.selectedKey) || state.filtered[0];
      if (!row) {{
        els.detail.innerHTML = `<h2>No product-site selected</h2><p class="subtitle">Adjust filters to show issue records.</p>`;
        return;
      }}

      const alertText = row.issueStatus === "Alert"
        ? `Alert: current holding is at or below the safety stock level.`
        : row.firstAlertDate
          ? `Alert: stock reaches the safety threshold on ${{row.firstAlertDate}} when actual issue quantities are applied.`
          : "";

      const image = row.photo ? `<img class="detail-photo" src="${{escapeAttribute(row.photo)}}" alt="">` : "";

      els.detail.innerHTML = `
        <div class="detail-top">
          <div>
            <span class="pill ${{cssStatus(row.issueStatus)}}">${{row.issueStatus}}</span>
            <h2>${{escapeHtml(row.productId)}} - ${{escapeHtml(row.site)}}</h2>
            <p class="subtitle">${{escapeHtml(row.itemDescription || "No item description available.")}}</p>
          </div>
          ${{image}}
        </div>
        <div class="alert-box ${{alertText ? "show" : ""}}">${{escapeHtml(alertText)}}</div>
        <div class="facts">
          <div class="fact"><span>Current Holding</span><strong>${{fmt(row.currentHolding)}}</strong></div>
          <div class="fact"><span>Safety Stock Level</span><strong>${{fmt(row.safetyStockLevel)}}</strong></div>
          <div class="fact"><span>Simulated Final Stock</span><strong>${{fmt(row.simulatedFinalStock)}}</strong></div>
          <div class="fact"><span>First Alert Date</span><strong>${{row.firstAlertDate || "-"}}</strong></div>
          <div class="fact"><span>Total Issued</span><strong>${{fmt(row.totalIssuedInHistory)}}</strong></div>
          <div class="fact"><span>Issuance Days</span><strong>${{fmt(row.issuanceDays)}}</strong></div>
          <div class="fact"><span>PAR</span><strong>${{fmt(row.par)}}</strong></div>
          <div class="fact"><span>Best Forecast Method</span><strong>${{escapeHtml(row.bestMethod || "-")}}</strong></div>
        </div>
        <div class="chart-card">
          <canvas id="stockChart" width="1100" height="430"></canvas>
          <div class="legend">
            <span>Remaining stock after actual issuance</span>
            <span class="threshold">Safety stock level</span>
          </div>
        </div>
        <div class="events">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Issued Qty</th>
                <th>Remaining Stock</th>
                <th>Safety Stock</th>
                <th>Alert</th>
              </tr>
            </thead>
            <tbody>
              ${{renderTimelineRows(row)}}
            </tbody>
          </table>
        </div>
      `;

      drawChart(row);
    }}

    function renderTimelineRows(row) {{
      if (!row.timeline.length) {{
        return `<tr><td colspan="5">No actual issuance records found for this product-site.</td></tr>`;
      }}

      return row.timeline.map((point) => `
        <tr>
          <td>${{point.date}}</td>
          <td>${{fmt(point.issuedQuantity)}}</td>
          <td>${{fmt(point.remainingStock)}}</td>
          <td>${{fmt(point.safetyStockLevel)}}</td>
          <td>${{point.alert ? "Alert" : ""}}</td>
        </tr>
      `).join("");
    }}

    function drawChart(row) {{
      const canvas = document.getElementById("stockChart");
      if (!canvas) return;

      const ctx = canvas.getContext("2d");
      const width = canvas.width;
      const height = canvas.height;
      const pad = {{ left: 64, right: 24, top: 24, bottom: 54 }};
      ctx.clearRect(0, 0, width, height);

      const points = [
        {{ label: "Start", remainingStock: row.currentHolding, safetyStockLevel: row.safetyStockLevel }},
        ...row.timeline
      ];

      if (!points.length || row.currentHolding === null || row.currentHolding === undefined) {{
        drawEmptyChart(ctx, width, height, "No inventory data available.");
        return;
      }}

      const values = points.flatMap((point) => [point.remainingStock, point.safetyStockLevel]).filter((value) => value !== null && value !== undefined);
      const minValue = Math.min(0, ...values);
      const maxValue = Math.max(1, ...values);
      const span = maxValue - minValue || 1;
      const xStep = points.length > 1 ? (width - pad.left - pad.right) / (points.length - 1) : 0;

      const x = (index) => pad.left + (xStep * index);
      const y = (value) => pad.top + ((maxValue - value) / span) * (height - pad.top - pad.bottom);

      ctx.strokeStyle = "#d7dde5";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, height - pad.bottom);
      ctx.lineTo(width - pad.right, height - pad.bottom);
      ctx.stroke();

      ctx.fillStyle = "#667085";
      ctx.font = "14px Arial";
      ctx.fillText(fmt(maxValue), 10, y(maxValue) + 4);
      ctx.fillText(fmt(minValue), 10, y(minValue));

      drawLine(ctx, points.map((point, index) => [x(index), y(point.safetyStockLevel)]), "#b42318", true);
      drawLine(ctx, points.map((point, index) => [x(index), y(point.remainingStock)]), "#2357c5", false);

      points.forEach((point, index) => {{
        ctx.beginPath();
        ctx.fillStyle = point.remainingStock <= row.safetyStockLevel ? "#b42318" : "#2357c5";
        ctx.arc(x(index), y(point.remainingStock), 4, 0, Math.PI * 2);
        ctx.fill();
      }});

      const firstLabel = points[0].date || points[0].label;
      const lastLabel = points[points.length - 1].date || points[points.length - 1].label;
      ctx.fillStyle = "#667085";
      ctx.fillText(firstLabel, pad.left, height - 18);
      ctx.textAlign = "right";
      ctx.fillText(lastLabel, width - pad.right, height - 18);
      ctx.textAlign = "left";
    }}

    function drawLine(ctx, coordinates, color, dashed) {{
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = dashed ? 2 : 3;
      ctx.setLineDash(dashed ? [8, 7] : []);
      ctx.beginPath();
      coordinates.forEach(([x, y], index) => {{
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }});
      ctx.stroke();
      ctx.restore();
    }}

    function drawEmptyChart(ctx, width, height, message) {{
      ctx.fillStyle = "#667085";
      ctx.font = "16px Arial";
      ctx.fillText(message, 24, 42);
    }}

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      }}[char]));
    }}

    function escapeAttribute(value) {{
      return escapeHtml(value).replace(/`/g, "&#096;");
    }}

    setup();
  </script>
</body>
</html>
"""


def build_future_threshold_html(rows, upload_message=""):
    data_json = json.dumps(rows, ensure_ascii=True)
    upload_message_json = json.dumps(upload_message, ensure_ascii=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Future Safety Stock Thresholds</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d7dde5;
      --green: #16794c;
      --green-bg: #e7f6ee;
      --amber: #a05a00;
      --amber-bg: #fff3d6;
      --red: #b42318;
      --red-bg: #ffe8e5;
      --blue: #2357c5;
      --blue-bg: #eaf0ff;
      --shadow: 0 14px 35px rgba(15, 23, 42, 0.08);
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 15px;
    }}

    header {{
      background: #fff;
      border-bottom: 1px solid var(--line);
    }}

    .wrap {{
      width: min(1480px, calc(100% - 32px));
      margin: 0 auto;
    }}

    .topbar {{
      min-height: 96px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 22px;
    }}

    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      line-height: 1.1;
      letter-spacing: 0;
    }}

    .subtitle {{
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }}

    .button-link, button, input, select {{
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
    }}

    button {{ cursor: pointer; }}

    .button-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
    }}

    main {{ padding: 24px 0 40px; }}

    .upload-panel, .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}

    .upload-panel {{
      padding: 14px;
      margin-bottom: 16px;
    }}

    .upload-form {{
      display: grid;
      grid-template-columns: minmax(260px, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      margin-top: 10px;
    }}

    .toolbar {{
      display: grid;
      grid-template-columns: minmax(260px, 1.2fr) repeat(3, minmax(150px, .55fr));
      gap: 12px;
      margin-bottom: 16px;
    }}

    .toolbar input, .toolbar select {{ width: 100%; }}

    .metrics {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}

    .metric {{
      padding: 16px;
      min-height: 92px;
    }}

    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-bottom: 9px;
    }}

    .metric-value {{
      font-size: 28px;
      font-weight: 700;
      line-height: 1;
    }}

    .grid {{
      display: grid;
      grid-template-columns: minmax(0, .86fr) minmax(480px, 1.35fr);
      gap: 16px;
      align-items: start;
    }}

    .panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}

    .list {{
      max-height: 72vh;
      overflow: auto;
    }}

    .product-row {{
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      padding: 12px 16px;
      border: 0;
      border-bottom: 1px solid #ebeff4;
      border-radius: 0;
      text-align: left;
      cursor: pointer;
    }}

    .product-row:hover, .product-row.active {{
      background: #f2f6ff;
    }}

    .row-title {{
      font-weight: 700;
      margin-bottom: 5px;
    }}

    .row-subtitle {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 26px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }}

    .status-safe {{ color: var(--green); background: var(--green-bg); }}
    .status-updated-with-real-data {{ color: var(--blue); background: var(--blue-bg); }}
    .status-no-inventory {{ color: var(--amber); background: var(--amber-bg); }}
    .status-alert {{ color: var(--red); background: var(--red-bg); }}

    .message {{
      display: none;
      border-radius: 8px;
      padding: 10px 12px;
      margin-bottom: 12px;
      background: var(--blue-bg);
      color: var(--blue);
      font-weight: 700;
    }}

    .message.show {{ display: block; }}

    .detail {{
      padding: 18px;
    }}

    .detail-top {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 180px;
      gap: 16px;
      align-items: start;
      margin-bottom: 14px;
    }}

    .detail h2 {{
      margin: 8px 0;
      font-size: 22px;
      line-height: 1.25;
      letter-spacing: 0;
    }}

    .detail-photo {{
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
    }}

    .alert-box {{
      display: none;
      border: 1px solid #ffc9c2;
      background: var(--red-bg);
      color: var(--red);
      border-radius: 8px;
      padding: 12px;
      margin: 12px 0;
      font-weight: 700;
    }}

    .alert-box.show {{ display: block; }}

    .facts {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin: 14px 0;
    }}

    .fact {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfe;
      min-height: 72px;
    }}

    .fact span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 7px;
    }}

    .fact strong {{
      display: block;
      font-size: 18px;
      line-height: 1.2;
    }}

    .chart-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
    }}

    canvas {{
      width: 100%;
      height: 380px;
      display: block;
    }}

    .legend {{
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
    }}

    .legend span::before {{
      content: "";
      display: inline-block;
      width: 18px;
      height: 3px;
      margin-right: 7px;
      vertical-align: middle;
      background: var(--red);
    }}

    .legend .stock-line::before {{
      background: var(--blue);
    }}

    .legend .baseline-line::before {{
      background: #8a94a6;
    }}

    .empty {{
      padding: 24px 16px;
      color: var(--muted);
      text-align: center;
    }}

    @media (max-width: 1060px) {{
      .topbar, .detail-top {{
        align-items: flex-start;
        flex-direction: column;
        display: flex;
      }}

      .toolbar, .metrics, .grid, .facts, .upload-form {{
        grid-template-columns: 1fr;
      }}

      canvas {{ height: 300px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap topbar">
      <div>
        <h1>Future Safety Stock Thresholds</h1>
        <p class="subtitle">Each product-site graph shows a constant stock threshold from the test-period end date through the next month.</p>
      </div>
      <a class="button-link" href="/">Safety threshold register</a>
    </div>
  </header>

  <main class="wrap">
    <section class="upload-panel">
      <div id="uploadMessage" class="message"></div>
      <strong>Real issuance data input</strong>
      <p class="subtitle">Upload CSV rows with Product ID, Site Location, Date or Date / Time, and Issued Quantity. Affected thresholds are recalculated, current holding is reduced by the uploaded issue quantity, and no replenishment is applied.</p>
      <form class="upload-form" method="post" action="/future-thresholds/upload" enctype="multipart/form-data">
        <input type="file" name="issuance_file" accept=".csv" required>
        <button type="submit">Upload and refresh thresholds</button>
        <button type="submit" form="clearUploadForm">Remove uploaded data</button>
      </form>
      <form id="clearUploadForm" method="post" action="/future-thresholds/clear"></form>
    </section>

    <section class="toolbar" aria-label="Filters">
      <input id="searchInput" type="search" placeholder="Search product ID or item description">
      <select id="siteFilter"><option value="All">All sites</option></select>
      <select id="statusFilter"><option value="All">All threshold statuses</option></select>
      <select id="sortSelect">
        <option value="risk">Sort by threshold status</option>
        <option value="product">Sort by product</option>
        <option value="site">Sort by site</option>
        <option value="threshold">Sort by threshold</option>
        <option value="replenishment_days">Sort by optimal replenishment days</option>
      </select>
    </section>

    <section class="metrics" aria-label="Summary">
      <div class="metric"><div class="metric-label">Product-Sites</div><div id="totalMetric" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">Alerts</div><div id="alertMetric" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">Test End Date</div><div id="testEndMetric" class="metric-value">-</div></div>
      <div class="metric"><div class="metric-label">Future End Date</div><div id="futureEndMetric" class="metric-value">-</div></div>
    </section>

    <section class="grid">
      <div class="panel">
        <div class="panel-head">
          <strong>Product-site thresholds</strong>
          <span id="resultCount" class="subtitle">0 rows</span>
        </div>
        <div id="productList" class="list"></div>
      </div>

      <div id="detailPanel" class="panel detail" aria-live="polite"></div>
    </section>
  </main>

  <script>
    const DATA = {data_json};
    const UPLOAD_MESSAGE = {upload_message_json};
    const statusOrder = {{
      "Alert": 0,
      "Updated with real data": 1,
      "No inventory": 2,
      "Safe": 3
    }};

    const state = {{
      filtered: DATA,
      selectedKey: "",
    }};

    const els = {{
      search: document.getElementById("searchInput"),
      site: document.getElementById("siteFilter"),
      status: document.getElementById("statusFilter"),
      sort: document.getElementById("sortSelect"),
      list: document.getElementById("productList"),
      detail: document.getElementById("detailPanel"),
      count: document.getElementById("resultCount"),
      total: document.getElementById("totalMetric"),
      alert: document.getElementById("alertMetric"),
      testEnd: document.getElementById("testEndMetric"),
      futureEnd: document.getElementById("futureEndMetric"),
      uploadMessage: document.getElementById("uploadMessage"),
    }};

    function key(row) {{
      return `${{row.productId}}|${{row.site}}`;
    }}

    function fmt(value, digits = 0) {{
      if (value === null || value === undefined || Number.isNaN(value)) return "-";
      return Number(value).toLocaleString(undefined, {{
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }});
    }}

    function cssStatus(status) {{
      return "status-" + status.toLowerCase().replaceAll(" ", "-");
    }}

    function setup() {{
      if (UPLOAD_MESSAGE) {{
        els.uploadMessage.textContent = UPLOAD_MESSAGE;
        els.uploadMessage.classList.add("show");
      }}

      fillSelect(els.site, [...new Set(DATA.map((row) => row.site))].sort());
      fillSelect(els.status, [...new Set(DATA.map((row) => row.alertStatus))].sort((a, b) => statusOrder[a] - statusOrder[b]));

      [els.search, els.site, els.status, els.sort].forEach((el) => {{
        el.addEventListener("input", applyFilters);
        el.addEventListener("change", applyFilters);
      }});

      applyFilters();
    }}

    function fillSelect(select, values) {{
      values.forEach((value) => {{
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }});
    }}

    function applyFilters() {{
      const term = els.search.value.trim().toLowerCase();
      const site = els.site.value;
      const status = els.status.value;

      let rows = DATA.filter((row) => {{
        const searchable = `${{row.productId}} ${{row.itemDescription}}`.toLowerCase();
        return (!term || searchable.includes(term))
          && (site === "All" || row.site === site)
          && (status === "All" || row.alertStatus === status);
      }});

      rows = sortRows(rows, els.sort.value);
      state.filtered = rows;

      if (!rows.some((row) => key(row) === state.selectedKey)) {{
        state.selectedKey = rows[0] ? key(rows[0]) : "";
      }}

      render();
    }}

    function sortRows(rows, mode) {{
      const sorted = [...rows];
      sorted.sort((a, b) => {{
        if (mode === "product") {{
          return a.productId.localeCompare(b.productId) || a.site.localeCompare(b.site);
        }}
        if (mode === "site") {{
          return a.site.localeCompare(b.site) || a.productId.localeCompare(b.productId);
        }}
        if (mode === "threshold") {{
          return a.safetyStockLevel - b.safetyStockLevel || a.productId.localeCompare(b.productId);
        }}
        if (mode === "replenishment_days") {{
          return a.optimalReplenishmentDays - b.optimalReplenishmentDays
            || (statusOrder[a.alertStatus] - statusOrder[b.alertStatus])
            || a.productId.localeCompare(b.productId)
            || a.site.localeCompare(b.site);
        }}
        return (statusOrder[a.alertStatus] - statusOrder[b.alertStatus])
          || a.productId.localeCompare(b.productId)
          || a.site.localeCompare(b.site);
      }});
      return sorted;
    }}

    function render() {{
      renderMetrics();
      renderList();
      renderDetail();
    }}

    function renderMetrics() {{
      const rows = state.filtered;
      const first = rows[0] || DATA[0] || {{}};
      els.total.textContent = fmt(rows.length);
      els.alert.textContent = fmt(rows.filter((row) => row.alertStatus === "Alert").length);
      els.testEnd.textContent = first.testEndDate || "-";
      els.futureEnd.textContent = first.futureEndDate || "-";
      els.count.textContent = `${{fmt(rows.length)}} rows`;
    }}

    function renderList() {{
      els.list.innerHTML = "";

      if (!state.filtered.length) {{
        els.list.innerHTML = `<div class="empty">No matching product-site thresholds.</div>`;
        return;
      }}

      const fragment = document.createDocumentFragment();
      state.filtered.forEach((row) => {{
        const button = document.createElement("button");
        button.type = "button";
        button.className = "product-row" + (key(row) === state.selectedKey ? " active" : "");
        button.innerHTML = `
          <div>
            <div class="row-title">${{escapeHtml(row.productId)}} - ${{escapeHtml(row.site)}}</div>
            <div class="row-subtitle">${{escapeHtml(row.itemDescription)}}<br>Best method ${{escapeHtml(row.bestMethod || "-")}} | Replenishment days ${{fmt(row.optimalReplenishmentDays, 1)}}<br>Current threshold ${{fmt(row.safetyStockLevel)}} | Safe threshold ${{fmt(row.baselineSafetyStockLevel)}} | Current after real data ${{fmt(row.currentHolding)}} | Real rows ${{fmt(row.realDataRows)}}</div>
          </div>
          <span class="pill ${{cssStatus(row.alertStatus)}}">${{row.alertStatus}}</span>
        `;
        button.addEventListener("click", () => {{
          state.selectedKey = key(row);
          render();
        }});
        fragment.appendChild(button);
      }});
      els.list.appendChild(fragment);
    }}

    function renderDetail() {{
      const row = state.filtered.find((item) => key(item) === state.selectedKey) || state.filtered[0];
      if (!row) {{
        els.detail.innerHTML = `<h2>No product-site selected</h2><p class="subtitle">Adjust filters to show threshold records.</p>`;
        return;
      }}

      const alertText = row.alertStatus === "Alert"
        ? `Replenishment alert: current holding is at or below the safety stock threshold.`
        : "";
      const image = row.photo ? `<img class="detail-photo" src="${{escapeAttribute(row.photo)}}" alt="">` : "";

      els.detail.innerHTML = `
        <div class="detail-top">
          <div>
            <span class="pill ${{cssStatus(row.alertStatus)}}">${{row.alertStatus}}</span>
            <h2>${{escapeHtml(row.productId)}} - ${{escapeHtml(row.site)}}</h2>
            <p class="subtitle">${{escapeHtml(row.itemDescription || "No item description available.")}}</p>
          </div>
          ${{image}}
        </div>
        <div class="alert-box ${{alertText ? "show" : ""}}">${{escapeHtml(alertText)}}</div>
        <div class="facts">
          <div class="fact"><span>Current / Latest Threshold</span><strong>${{fmt(row.safetyStockLevel)}}</strong></div>
          <div class="fact"><span>Safe Threshold</span><strong>${{fmt(row.baselineSafetyStockLevel)}}</strong></div>
          <div class="fact"><span>Current Holding After Real Data</span><strong>${{fmt(row.currentHolding)}}</strong></div>
          <div class="fact"><span>Threshold Gap</span><strong>${{fmt(row.thresholdGap)}}</strong></div>
          <div class="fact"><span>PAR</span><strong>${{fmt(row.par)}}</strong></div>
          <div class="fact"><span>Optimal Replenishment Days</span><strong>${{fmt(row.optimalReplenishmentDays, 1)}}</strong></div>
          <div class="fact"><span>Best Forecast Method</span><strong>${{escapeHtml(row.bestMethod || "-")}}</strong></div>
          <div class="fact"><span>Starting Current Holding</span><strong>${{fmt(row.baselineCurrentHolding)}}</strong></div>
          <div class="fact"><span>Future Window</span><strong>${{row.testEndDate}} to ${{row.futureEndDate}}</strong></div>
        </div>
        <div class="chart-card">
          <canvas id="thresholdChart" width="1100" height="430"></canvas>
          <div class="legend">
            <span>Current / latest safety stock threshold</span>
            <span class="stock-line">Stock after uploaded issuance</span>
            <span class="baseline-line">Safe threshold</span>
          </div>
        </div>
      `;

      drawChart(row);
    }}

    function drawChart(row) {{
      const canvas = document.getElementById("thresholdChart");
      if (!canvas) return;

      const ctx = canvas.getContext("2d");
      const width = canvas.width;
      const height = canvas.height;
      const pad = {{ left: 64, right: 24, top: 24, bottom: 54 }};
      ctx.clearRect(0, 0, width, height);

      const points = row.thresholdSeries;
      const stockPoints = row.stockDecreaseSeries || [];
      const threshold = row.safetyStockLevel || 0;
      const baselineThreshold = row.baselineSafetyStockLevel || 0;
      const minValue = 0;
      const stockValues = stockPoints.map((point) => point.remainingStock || 0);
      const thresholdValues = points.map((point) => point.stockThreshold || 0);
      const rawMaxValue = Math.max(1, threshold * 1.35, baselineThreshold * 1.35, row.par || 0, row.currentHolding || 0, row.baselineCurrentHolding || 0, ...stockValues, ...thresholdValues);
      const tickStep = niceTickStep(rawMaxValue);
      const maxValue = Math.ceil(rawMaxValue / tickStep) * tickStep;
      const span = maxValue - minValue || 1;
      const startTime = new Date(points[0].date).getTime();
      const endTime = new Date(points[points.length - 1].date).getTime();
      const timeSpan = endTime - startTime || 1;
      const x = (dateText) => {{
        const timeValue = new Date(dateText).getTime();
        const ratio = Math.max(0, Math.min(1, (timeValue - startTime) / timeSpan));
        return pad.left + ratio * (width - pad.left - pad.right);
      }};
      const y = (value) => pad.top + ((maxValue - value) / span) * (height - pad.top - pad.bottom);

      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);

      ctx.font = "13px Arial";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      for (let value = minValue; value <= maxValue + tickStep * 0.001; value += tickStep) {{
        const py = y(value);

        ctx.strokeStyle = value === minValue ? "#d7dde5" : "#ebeff4";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(pad.left, py);
        ctx.lineTo(width - pad.right, py);
        ctx.stroke();

        ctx.fillStyle = "#667085";
        ctx.fillText(formatAxisTick(value, tickStep), pad.left - 10, py);
      }}
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";

      ctx.save();
      ctx.translate(18, pad.top + (height - pad.top - pad.bottom) / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.fillStyle = "#667085";
      ctx.font = "13px Arial";
      ctx.textAlign = "center";
      ctx.fillText("Stock level", 0, 0);
      ctx.restore();

      ctx.strokeStyle = "#d7dde5";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, height - pad.bottom);
      ctx.lineTo(width - pad.right, height - pad.bottom);
      ctx.stroke();

      drawStepSeries(ctx, points, x, y, "#b42318", 3, [8, 7]);

      if (baselineThreshold !== threshold) {{
        ctx.strokeStyle = "#8a94a6";
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 6]);
        ctx.beginPath();
        points.forEach((point, index) => {{
          const px = x(point.date);
          const py = y(baselineThreshold);
          if (index === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        }});
        ctx.stroke();
        ctx.setLineDash([]);
      }}

      ctx.fillStyle = "#b42318";
      points.forEach((point, index) => {{
        ctx.beginPath();
        ctx.arc(x(point.date), y(point.stockThreshold), 3.5, 0, Math.PI * 2);
        ctx.fill();
      }});

      if (stockPoints.length > 1) {{
        ctx.strokeStyle = "#2357c5";
        ctx.lineWidth = 3;
        ctx.beginPath();
        stockPoints.forEach((point, index) => {{
          const px = x(point.date);
          const py = y(point.remainingStock);
          if (index === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        }});
        ctx.stroke();

        ctx.fillStyle = "#2357c5";
        stockPoints.forEach((point) => {{
          ctx.beginPath();
          ctx.arc(x(point.date), y(point.remainingStock), 4, 0, Math.PI * 2);
          ctx.fill();
        }});
      }}

      ctx.font = "13px Arial";
      ctx.fillStyle = "#b42318";
      ctx.fillText(`Current threshold: ${{fmt(threshold)}}`, pad.left + 8, y(threshold) - 8);

      if (baselineThreshold !== threshold) {{
        ctx.fillStyle = "#667085";
        ctx.fillText(`Baseline: ${{fmt(baselineThreshold)}}`, pad.left + 8, y(baselineThreshold) + 16);
      }}

      ctx.fillStyle = "#667085";
      ctx.fillText(points[0].date, pad.left, height - 18);
      ctx.textAlign = "right";
      ctx.fillText(points[points.length - 1].date, width - pad.right, height - 18);
      ctx.textAlign = "left";
    }}

    function drawStepSeries(ctx, points, x, y, color, lineWidth, dash) {{
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.setLineDash(dash || []);
      ctx.beginPath();

      points.forEach((point, index) => {{
        const px = x(point.date);
        const py = y(point.stockThreshold);

        if (index === 0) {{
          ctx.moveTo(px, py);
          return;
        }}

        const previous = points[index - 1];
        const previousY = y(previous.stockThreshold);
        ctx.lineTo(px, previousY);
        ctx.lineTo(px, py);
      }});

      ctx.stroke();
      ctx.restore();
    }}

    function niceTickStep(maxValue) {{
      if (maxValue <= 5) {{
        return 1;
      }}

      const targetTickCount = 5;
      const roughStep = maxValue / targetTickCount;
      const magnitude = Math.pow(10, Math.floor(Math.log10(roughStep)));
      const residual = roughStep / magnitude;

      if (residual <= 1) {{
        return magnitude;
      }}
      if (residual <= 2) {{
        return 2 * magnitude;
      }}
      if (residual <= 5) {{
        return 5 * magnitude;
      }}
      return 10 * magnitude;
    }}

    function formatAxisTick(value, tickStep) {{
      if (tickStep >= 1) {{
        return fmt(Math.round(value));
      }}

      return fmt(value, 1);
    }}

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      }}[char]));
    }}

    function escapeAttribute(value) {{
      return escapeHtml(value).replace(/`/g, "&#096;");
    }}

    setup();
  </script>
</body>
</html>
"""


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    rows = load_monitoring_rows()
    OUTPUT_FILE.write_text(build_html(rows), encoding="utf-8")
    print(f"Built {OUTPUT_FILE.relative_to(ROOT)} with {len(rows)} product-site thresholds.")


if __name__ == "__main__":
    main()
