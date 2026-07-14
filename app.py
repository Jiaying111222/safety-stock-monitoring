import csv
from io import StringIO, TextIOWrapper

from flask import Flask, Response, jsonify, redirect, request, url_for

from build_safety_stock_app import (
    append_real_issuance_updates,
    build_future_threshold_html,
    build_html,
    clear_real_issuance_updates,
    load_future_threshold_rows,
    load_monitoring_rows,
)


app = Flask(__name__)


@app.route("/")
def index():
    rows = load_monitoring_rows()
    return build_html(rows)


@app.route("/api/safety-stock")
def safety_stock_api():
    return jsonify(load_monitoring_rows())


@app.route("/future-thresholds")
def future_thresholds():
    rows = load_future_threshold_rows()
    upload_message = request.args.get("message", "")
    return build_future_threshold_html(rows, upload_message)


@app.route("/api/future-thresholds")
def future_thresholds_api():
    return jsonify(load_future_threshold_rows())


@app.route("/future-thresholds/upload", methods=["POST"])
def upload_future_threshold_issuance():
    uploaded_file = request.files.get("issuance_file")

    if uploaded_file is None or uploaded_file.filename == "":
        return redirect(
            url_for(
                "future_thresholds",
                message="No CSV file was selected.",
            )
        )

    text_stream = TextIOWrapper(
        uploaded_file.stream,
        encoding="utf-8-sig",
        newline="",
    )
    reader = csv.DictReader(text_stream)
    rows_added = append_real_issuance_updates(reader)

    return redirect(
        url_for(
            "future_thresholds",
            message=f"Uploaded {rows_added} valid real issuance rows and refreshed thresholds.",
        )
    )


@app.route("/future-thresholds/clear", methods=["POST"])
def clear_future_threshold_issuance():
    removed = clear_real_issuance_updates()
    message = (
        "Removed uploaded real issuance data. Showing baseline Part 3 thresholds."
        if removed
        else "No uploaded real issuance data was found."
    )

    return redirect(
        url_for(
            "future_thresholds",
            message=message,
        )
    )


@app.route("/stock-decrease")
def stock_decrease():
    return redirect(url_for("future_thresholds"))


@app.route("/api/stock-decrease")
def stock_decrease_api():
    return jsonify(load_future_threshold_rows())


@app.route("/download/safety-stock.csv")
def download_safety_stock_csv():
    rows = load_monitoring_rows()
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "productId",
            "itemDescription",
            "site",
            "status",
            "currentHolding",
            "safetyStockLevel",
            "thresholdGap",
            "par",
            "optimalReplenishmentDays",
            "optimalReplenishmentPercentage",
            "lowestRemainingStock",
            "numberOfTopUps",
            "bestMethod",
        ],
    )
    writer.writeheader()

    for row in rows:
        writer.writerow({
            "productId": row["productId"],
            "itemDescription": row["itemDescription"],
            "site": row["site"],
            "status": row["status"],
            "currentHolding": row["currentHolding"],
            "safetyStockLevel": row["safetyStockLevel"],
            "thresholdGap": row["thresholdGap"],
            "par": row["par"],
            "optimalReplenishmentDays": row["optimalReplenishmentDays"],
            "optimalReplenishmentPercentage": row["optimalReplenishmentPercentage"],
            "lowestRemainingStock": row["lowestRemainingStock"],
            "numberOfTopUps": row["numberOfTopUps"],
            "bestMethod": row["bestMethod"],
        })

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=safety_stock_monitoring.csv"
        },
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
