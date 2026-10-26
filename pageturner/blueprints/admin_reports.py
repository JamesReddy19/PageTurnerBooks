"""Date-range reports and CSV export."""
import csv
import io
from datetime import datetime, timedelta
from flask import abort, flash, make_response, redirect, request, render_template, url_for
from ..db import get_db
from ..helpers import log_admin_action
from .admin import admin_bp

@admin_bp.get("/reports")
@permission_required("reports.view")
def reports():
    start = request.args.get("start", (datetime.now().date()-timedelta(days=30)).isoformat())
    end = request.args.get("end", datetime.now().date().isoformat())
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
        if end_date < start_date or (end_date-start_date).days > 366:
            raise ValueError
    except ValueError:
        flash("Choose a valid date range of at most 367 days.", "error")
        return redirect(url_for("admin.reports"))
    connection = get_db()
    summary = connection.execute("SELECT COUNT(*) AS order_count,COALESCE(SUM(total_paise),0) AS revenue,COALESCE(SUM(total_paise)/NULLIF(COUNT(*),0),0) AS average FROM orders WHERE date(date)>=? AND date(date)<=? AND status!='Cancelled'", (start,end)).fetchone()
    best_category = connection.execute("SELECT c.name,COALESCE(SUM(oi.quantity),0) AS sold FROM categories c JOIN book_categories bc ON bc.category_id=c.id JOIN order_items oi ON oi.book_id=bc.book_id JOIN orders o ON o.id=oi.order_id WHERE date(o.date)>=? AND date(o.date)<=? AND o.status!='Cancelled' GROUP BY c.id ORDER BY sold DESC LIMIT 1", (start,end)).fetchone()
    returns_count = connection.execute("SELECT COUNT(*) AS count FROM returns r JOIN order_items oi ON oi.id=r.order_item_id JOIN orders o ON o.id=oi.order_id WHERE date(r.created_at)>=? AND date(r.created_at)<=? AND r.status='Approved'", (start,end)).fetchone()["count"]
    daily = connection.execute("SELECT date(date) AS day,COUNT(*) AS orders,COALESCE(SUM(total_paise),0) AS revenue FROM orders WHERE date(date)>=? AND date(date)<=? AND status!='Cancelled' GROUP BY date(date) ORDER BY day", (start,end)).fetchall()
    return render_template("admin_report.html", start=start, end=end, summary=summary, best_category=best_category, returns_count=returns_count, daily=daily)


@admin_bp.get("/reports.csv")
@permission_required("reports.view")
def reports_csv():
    start = request.args.get("start", (datetime.now().date()-timedelta(days=30)).isoformat())
    end = request.args.get("end", datetime.now().date().isoformat())
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
        if end_date < start_date or (end_date - start_date).days > 366:
            abort(400)
    except ValueError:
        abort(400)
    rows = get_db().execute("SELECT id,date,name,status,subtotal_paise,discount_paise,shipping_paise,tax_paise,total_paise FROM orders WHERE date(date)>=? AND date(date)<=? ORDER BY id", (start,end)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["order_id","date","customer","status","subtotal_paise","discount_paise","shipping_paise","tax_paise","total_paise"])
    writer.writerows([tuple(row) for row in rows])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=pageturner-report.csv"
    return response


@admin_bp.get("/export/orders.csv")
@permission_required("reports.view")
def export_orders():
    rows = get_db().execute("SELECT id,date,name,phone,address,status,subtotal_paise,discount_paise,shipping_paise,tax_paise,total_paise FROM orders ORDER BY id").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(rows[0].keys() if rows else ["id","date","name","phone","address","status","subtotal_paise","discount_paise","shipping_paise","tax_paise","total_paise"])
    writer.writerows([tuple(row) for row in rows])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=pageturner-orders.csv"
    log_admin_action("orders_exported", len(rows))
    get_db().commit()
    return response




@admin_bp.get("/export")
@permission_required("reports.view")
def old_export_url():
    return redirect(url_for("admin.export_orders"))


@admin_bp.get("/report")
@permission_required("reports.view")
def old_report_url():
    return redirect(url_for("admin.reports"))
