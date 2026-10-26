"""Requirement 74: growth report built on SQL window functions.

These tests are written against the *behaviour* the window functions are there
to produce, not against their syntax:

``LAG`` over a zero-filled month series
    a quiet month must still appear, and the next month's "previous" must be
    that zero - not the last month that happened to contain an order.
``SUM() OVER (... ROWS UNBOUNDED PRECEDING)``
    the running total has to equal the hand-added cumulative sum.
``AVG() OVER (... ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)``
    the rolling average has to equal the hand-computed three-month mean.

On the pre-batch code every test here fails for the same reason: there is no
growth report and no ``pageturner.growth`` module.
"""

import csv
import io
import os
import unittest
from datetime import date


def month_key(offset=0, today=None):
    """Return ``YYYY-MM`` ``offset`` months from now (negative = in the past)."""
    today = today or date.today()
    absolute = (today.year * 12) + (today.month - 1) + offset
    return f"{absolute // 12:04d}-{absolute % 12 + 1:02d}"


def day_in_month(month, day=15):
    """A date inside ``YYYY-MM``; day 15 avoids every month-length edge."""
    return f"{month}-{day:02d}"


class GrowthTestBase(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile

        self.temp_dir = tempfile.TemporaryDirectory()

        from pageturner import create_app as factory

        self.app = factory(
            {
                "TESTING": True,
                "SECRET_KEY": "growth-report-secret",
                "DATABASE": os.path.join(self.temp_dir.name, "growth.db"),
                "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
                "ADMIN_EMAIL": "",
                "ADMIN_PASSWORD": "",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    # --- fixtures -------------------------------------------------------

    def add_order(self, when, total_paise, status="Placed"):
        """Insert a bare order.  ``when`` is an ISO date or datetime string."""
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                """
                INSERT INTO orders(
                    user_id, name, phone, address, subtotal_paise, total_paise,
                    date, status
                )
                VALUES (NULL, 'Test Buyer', '9000000000', '1 Test Road',
                        ?, ?, ?, ?)
                """,
                (total_paise, total_paise, when, status),
            )
            connection.commit()

    def register_and_login(self, admin=True):
        response = self.client.post(
            "/register",
            data={
                "name": "Ada Reader",
                "email": "ada@example.com",
                "password": "Reading123",
                "confirm_password": "Reading123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        if admin:
            with self.app.app_context():
                connection = get_db()
                connection.execute(
                    "UPDATE users SET role='admin' WHERE email='ada@example.com'"
                )
                connection.commit()

    def rows(self, months=4):
        with self.app.app_context():
            from pageturner.growth import monthly_growth

            return monthly_growth(get_db(), months=months)

    def by_month(self, months=4):
        return {row["month"]: row for row in self.rows(months=months)}


class MonthSeriesTests(GrowthTestBase):
    """A gap month must appear, and LAG must not skip over it."""

    def setUp(self):
        super().setUp()
        # Three months ago, then a completely quiet month, then two months of
        # sales.  A plain GROUP BY would compare "two months ago" with
        # "four months ago" and report growth that never happened.
        self.m3, self.m2, self.m1, self.m0 = (
            month_key(-3), month_key(-2), month_key(-1), month_key(0)
        )
        self.add_order(day_in_month(self.m3), 10_000)
        self.add_order(day_in_month(self.m1), 30_000)
        self.add_order(day_in_month(self.m0), 60_000)

    def test_the_quiet_month_is_present_with_zero_revenue(self):
        rows = self.by_month()

        self.assertIn(self.m2, rows)
        self.assertEqual(rows[self.m2]["revenue"], 0)
        self.assertEqual(rows[self.m2]["orders"], 0)

    def test_previous_month_is_the_previous_calendar_month_not_the_last_sale(self):
        """The heart of the requirement.

        After the gap, ``previous_revenue`` must be the gap month's 0.  A
        ``GROUP BY`` + Python loop would give 10,000 here (the last month with
        an order) and invent +200% growth out of nothing.
        """
        rows = self.by_month()

        self.assertEqual(rows[self.m1]["previous_revenue"], 0)
        self.assertEqual(rows[self.m1]["change_paise"], 30_000)

        # And the gap month really does see the drop.
        self.assertEqual(rows[self.m2]["previous_revenue"], 10_000)
        self.assertEqual(rows[self.m2]["change_paise"], -10_000)

    def test_every_month_in_the_window_is_returned_in_order(self):
        rows = self.rows(months=4)

        self.assertEqual([row["month"] for row in rows], [self.m3, self.m2, self.m1, self.m0])
        self.assertEqual([row["month"] for row in rows], sorted(row["month"] for row in rows))

    def test_orders_outside_the_window_are_excluded(self):
        self.add_order(day_in_month(month_key(-6)), 999_999)

        rows = self.by_month()

        self.assertNotIn(month_key(-6), rows)
        self.assertEqual(rows[self.m3]["revenue"], 10_000)


class RunningTotalTests(GrowthTestBase):
    """SUM() OVER must equal the hand-added cumulative sum."""

    def setUp(self):
        super().setUp()
        self.amounts = [10_000, 0, 30_000, 60_000]
        for offset, amount in zip((-3, -2, -1, 0), self.amounts):
            if amount:
                self.add_order(day_in_month(month_key(offset)), amount)

    def test_running_total_is_the_cumulative_sum(self):
        rows = self.rows(months=4)
        expected = 0

        for row in rows:
            expected += row["revenue"]
            self.assertEqual(row["running_revenue"], expected, row["month"])

        self.assertEqual(rows[-1]["running_revenue"], 100_000)

    def test_rolling_average_is_the_mean_of_the_last_three_months(self):
        rows = self.rows(months=4)

        for index, row in enumerate(rows):
            window = [item["revenue"] for item in rows[max(0, index - 2): index + 1]]
            expected = round(sum(window) / len(window))
            self.assertEqual(row["rolling_three_revenue"], expected, row["month"])

        # Hand-computed: (10,000 + 0 + 30,000) / 3 = 13,333.33 -> 13,333
        self.assertEqual(rows[2]["rolling_three_revenue"], 13_333)


class GrowthPercentTests(GrowthTestBase):
    def setUp(self):
        super().setUp()
        self.m1, self.m0 = month_key(-1), month_key(0)
        self.add_order(day_in_month(self.m1), 30_000)
        self.add_order(day_in_month(self.m0), 60_000)

    def test_growth_is_computed_from_the_previous_month(self):
        rows = self.by_month(months=2)

        self.assertEqual(rows[self.m0]["growth_percent"], 100.0)
        self.assertEqual(rows[self.m0]["change_paise"], 30_000)

    def test_the_first_month_has_no_growth_because_it_has_no_predecessor(self):
        rows = self.rows(months=2)

        self.assertIsNone(rows[0]["previous_revenue"])
        self.assertIsNone(rows[0]["change_paise"])
        self.assertIsNone(rows[0]["growth_percent"])

    def test_growth_from_zero_is_undefined_rather_than_enormous(self):
        """0 -> 30,000 paise is not "infinity percent" and not 100%."""
        rows = self.by_month()
        m2 = month_key(-2)

        self.assertIn(m2, rows)
        self.assertEqual(rows[m2]["previous_revenue"], 0)
        self.assertIsNone(rows[m2]["growth_percent"])
        # The absolute change is still meaningful and must be reported.
        self.assertEqual(rows[m2]["change_paise"], 30_000)


class ExclusionTests(GrowthTestBase):
    def test_cancelled_orders_do_not_count_towards_revenue(self):
        month = month_key(0)
        self.add_order(day_in_month(month), 20_000)
        self.add_order(day_in_month(month), 50_000, status="Cancelled")

        self.assertEqual(self.by_month()[month]["revenue"], 20_000)

    def test_a_sale_late_on_the_last_day_of_the_month_stays_in_that_month(self):
        month = month_key(-1)
        self.add_order(f"{month}-28 23:59:59", 12_345)

        self.assertEqual(self.by_month()[month]["revenue"], 12_345)


class WindowFunctionUseTests(GrowthTestBase):
    """The aggregation must happen in one windowed statement, not a Python loop."""

    def test_the_report_is_one_statement_that_uses_window_functions(self):
        self.add_order(day_in_month(month_key(0)), 5_000)

        with self.app.app_context():
            from pageturner.growth import monthly_growth

            connection = get_db()
            statements = []
            connection.set_trace_callback(statements.append)
            try:
                monthly_growth(connection, months=6)
            finally:
                connection.set_trace_callback(None)

        selects = [text for text in statements if text.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 1, statements)

        sql = selects[0].upper()
        self.assertIn("LAG(", sql)
        self.assertIn("OVER (", sql)
        self.assertIn("SUM(", sql)
        self.assertIn("AVG(", sql)


class RouteTests(GrowthTestBase):
    def test_the_report_requires_the_reports_permission(self):
        self.assertEqual(self.client.get("/admin/reports/growth").status_code, 403)

        # Staff may fulfil orders but reports stay admin-only (existing rule).
        self.register_and_login(admin=False)
        with self.app.app_context():
            connection = get_db()
            connection.execute("UPDATE users SET role='staff' WHERE email='ada@example.com'")
            connection.commit()

        self.assertEqual(self.client.get("/admin/reports/growth").status_code, 403)

    def test_an_admin_can_open_the_report_and_sees_the_numbers(self):
        month = month_key(-1)
        self.add_order(day_in_month(month), 42_000)
        self.register_and_login()

        response = self.client.get("/admin/reports/growth?months=4")
        self.assertEqual(response.status_code, 200)

        page = response.get_data(as_text=True)
        self.assertIn("Growth report", page)
        self.assertIn(money(42_000), page)
        # The page must not leak an unformatted paise integer.
        self.assertNotIn("42000", page)

    def test_the_month_count_is_clamped_and_bad_input_falls_back_to_the_default(self):
        self.register_and_login()

        with self.app.app_context():
            from pageturner.growth import DEFAULT_MONTHS, MAX_MONTHS, monthly_growth

            self.assertEqual(len(monthly_growth(get_db(), months=1)), 1)
            self.assertEqual(len(monthly_growth(get_db(), months=0)), 1)
            self.assertEqual(len(monthly_growth(get_db(), months=MAX_MONTHS + 50)), MAX_MONTHS)
            self.assertEqual(len(monthly_growth(get_db(), months=DEFAULT_MONTHS)), DEFAULT_MONTHS)

        self.assertEqual(self.client.get("/admin/reports/growth?months=0").status_code, 200)

        response = self.client.get("/admin/reports/growth?months=not-a-number")
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            from pageturner.growth import DEFAULT_MONTHS

            self.assertEqual(response.get_data(as_text=True).count("<tr>"), DEFAULT_MONTHS + 1)

    def test_the_csv_matches_the_page(self):
        """The CSV export is the hand-check the requirement asks for."""
        self.add_order(day_in_month(month_key(-1)), 25_000)
        self.add_order(day_in_month(month_key(0)), 50_000)
        self.register_and_login()

        response = self.client.get("/admin/reports/growth.csv?months=3")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["Content-Type"])

        lines = response.get_data(as_text=True).strip().splitlines()
        self.assertEqual(
            lines[0],
            "month,orders,revenue_paise,previous_month_revenue_paise,change_paise,"
            "growth_percent,running_revenue_paise,rolling_three_month_revenue_paise",
        )
        self.assertEqual(len(lines), 4)  # header + 3 months

        rows = self.by_month(months=3)
        header = lines[0].split(",")

        for line in lines[1:]:
            fields = dict(zip(header, line.split(",")))
            expected = rows[fields["month"]]
            self.assertEqual(int(fields["revenue_paise"]), expected["revenue"])
            self.assertEqual(int(fields["running_revenue_paise"]), expected["running_revenue"])

            if expected["growth_percent"] is None:
                self.assertEqual(fields["growth_percent"], "")
            else:
                self.assertEqual(fields["growth_percent"], str(expected["growth_percent"]))

    def test_the_growth_csv_agrees_with_the_order_level_csv(self):
        """Hand-check: sum the order-level export per month, same answer.

        ``/admin/reports.csv`` is the pre-existing order-level export.  Adding
        its ``total_paise`` column up per month, skipping cancelled orders, must
        reproduce the growth report's revenue figures.
        """
        self.add_order(f"{month_key(-1)}-03", 11_111)
        self.add_order(f"{month_key(-1)}-20", 22_222)
        self.add_order(f"{month_key(0)}-05", 33_333)
        self.add_order(f"{month_key(0)}-06", 44_444, status="Cancelled")
        self.register_and_login()

        start = f"{month_key(-1)}-01"
        end = date.today().isoformat()
        export = self.client.get(f"/admin/reports.csv?start={start}&end={end}")
        self.assertEqual(export.status_code, 200)

        lines = export.get_data(as_text=True).strip().splitlines()
        header = lines[0].split(",")
        totals = {}

        for line in lines[1:]:
            fields = dict(zip(header, line.split(",")))
            if fields["status"] == "Cancelled":
                continue
            key = fields["date"][:7]
            totals[key] = totals.get(key, 0) + int(fields["total_paise"])

        report = self.client.get("/admin/reports/growth.csv?months=2")
        report_rows = report.get_data(as_text=True).strip().splitlines()[1:]

        for line in report_rows:
            fields = dict(zip(report.headers and [
                "month", "orders", "revenue_paise", "previous_month_revenue_paise",
                "change_paise", "growth_percent", "running_revenue_paise",
                "rolling_three_month_revenue_paise",
            ], line.split(",")))
            self.assertEqual(
                int(fields["revenue_paise"]), totals.get(fields["month"], 0), fields["month"]
            )

        self.assertEqual(totals.get(month_key(0)), 33_333)
        self.assertEqual(totals.get(month_key(-1)), 33_333)

    def test_the_report_is_linked_from_the_reports_page_and_the_dashboard(self):
        self.register_and_login()

        self.assertIn("/admin/reports/growth", self.client.get("/admin/reports").get_data(as_text=True))
        self.assertIn("/admin/reports/growth", self.client.get("/admin/").get_data(as_text=True))

    def test_the_theme_classes_used_by_the_page_exist_in_the_stylesheet(self):
        """Reversed red/green is a silent money bug, so the classes must exist."""
        import os

        from pageturner import create_app as factory

        css_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static",
            "style.css",
        )
        with open(css_path, encoding="utf-8") as handle:
            css = handle.read()

        self.assertIn(".positive", css)
        self.assertIn(".negative", css)
        # Both must resolve through theme tokens, or dark mode loses them.
        self.assertIn("var(--success)", css)
        self.assertIn("var(--danger)", css)


if __name__ == "__main__":
    unittest.main()
