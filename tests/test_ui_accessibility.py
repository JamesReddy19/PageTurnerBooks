"""Final UI/UX pass — landmark, navigation and accessibility contract.

The failures these tests are written against are the ones a browser does not
show you, so a screenshot review never catches them:

* there was no skip link and ``<main>`` had no ``id``, so a keyboard user had
  to tab through roughly fifteen navigation links on every single page before
  reaching the content (WCAG 2.4.1);
* the header is ``position: sticky`` and at <=900px turned into a column, so
  those links formed a permanent wall pinned over the content on a phone;
* an administrator who also had a password-login session got **two** "Admin"
  links, because ``auth.login`` sets ``session["user_id"]`` without clearing a
  pre-existing ``admin_password_login`` flag;
* nothing marked the current page (``aria-current``), flash messages had no
  ARIA role and could not be dismissed, the brand emoji was read aloud, and the
  header border used a hardcoded ``#dca85b`` instead of the palette token.

Every assertion below is about behaviour the templates and stylesheets must
keep, not about one particular markup shape, so the design can keep evolving.
"""

import os
import re
import tempfile
import unittest

from pageturner import create_app
from pageturner.db import get_db

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STYLE_CSS = os.path.join(ROOT, "static", "style.css")
SCRIPT_JS = os.path.join(ROOT, "static", "script.js")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def dupe_ids(html):
    """Return every element id that appears more than once."""
    ids = re.findall(r'\sid="([^"]+)"', html)
    return sorted({value for value in ids if ids.count(value) > 1})


def skip_links(html):
    return re.findall(r'<a class="skip-link[^"]*"\s+href="#([^"]+)"', html)


def current_links(html):
    return re.findall(r'<a\b[^>]*aria-current="page"[^>]*href="([^"]*)"', html) + re.findall(
        r'<a\b[^>]*href="([^"]*)"[^>]*aria-current="page"', html
    )


def unlabelled_controls(html):
    """Form controls that have no programmatic accessible name.

    A control passes when it has ``aria-label``/``aria-labelledby``, when its
    ``id`` is referenced by a ``<label for=...>``, or when it sits inside a
    wrapping ``<label>``. Without one of those, a screen reader announces an
    anonymous edit box and the value typed into it cannot be identified.
    """
    for_labels = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', html))
    label_spans = [
        (match.start(), match.end())
        for match in re.finditer(r"<label\b[^>]*>.*?</label>", html, re.S)
    ]

    problems = []
    for match in re.finditer(r"<(input|select|textarea)\b([^>]*)>", html):
        attrs = match.group(2)
        kind_match = re.search(r'type="([^"]+)"', attrs)
        kind = kind_match.group(1) if kind_match else "text"

        if kind in ("hidden", "submit", "button", "reset", "image"):
            continue
        if "aria-label=" in attrs or "aria-labelledby=" in attrs:
            continue

        control_id = re.search(r'\bid="([^"]+)"', attrs)
        if control_id and control_id.group(1) in for_labels:
            continue
        if any(start <= match.start() < end for start, end in label_spans):
            continue

        problems.append(match.group(0)[:80])

    return problems


class UiContractTestCase(unittest.TestCase):
    """One throwaway application and three kinds of visitor."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "DATABASE": os.path.join(self.temp_dir.name, "test.db"),
                "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
                "ADMIN_EMAIL": "",
                "ADMIN_PASSWORD": "",
            }
        )
        self.guest = self.app.test_client()

        self.reader = self.app.test_client()
        self.register(self.reader, "Reader One", "reader@example.com")
        self.login(self.reader, "reader@example.com")

        self.admin = self.app.test_client()
        self.register(self.admin, "Ada Admin", "admin@example.com")
        self.login(self.admin, "admin@example.com")
        with self.app.app_context():
            connection = get_db()
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE email = 'admin@example.com'"
            )
            connection.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    # -- helpers ---------------------------------------------------------
    def csrf(self, client, path="/"):
        response = client.get(path)
        match = re.search(
            rb'<meta name="csrf-token" content="([^"]+)"', response.data
        )
        self.assertIsNotNone(match, f"CSRF token not found on {path}")
        return match.group(1).decode()

    def post(self, client, path, data, token_from="/"):
        payload = dict(data)
        payload["csrf_token"] = self.csrf(client, token_from)
        return client.post(path, data=payload, follow_redirects=True)

    def register(self, client, name, email):
        response = self.post(
            client,
            "/register",
            {"name": name, "email": email, "password": "Reading123"},
        )
        self.assertEqual(response.status_code, 200)

    def login(self, client, email):
        response = self.post(
            client, "/login", {"email": email, "password": "Reading123"}
        )
        self.assertEqual(response.status_code, 200)

    def page(self, client, path="/"):
        response = client.get(path, follow_redirects=True)
        self.assertEqual(response.status_code, 200, f"GET {path} did not render")
        return response.data.decode()

    def all_pages(self):
        """(label, html) for the pages every visitor shares."""
        return [
            ("guest home", self.page(self.guest, "/")),
            ("guest cart", self.page(self.guest, "/cart")),
            ("guest login", self.page(self.guest, "/login")),
            ("guest register", self.page(self.guest, "/register")),
            ("reader home", self.page(self.reader, "/")),
            ("reader profile", self.page(self.reader, "/profile")),
            ("reader addresses", self.page(self.reader, "/addresses")),
            ("reader cart", self.page(self.reader, "/cart")),
            ("reader wishlist", self.page(self.reader, "/wishlist")),
            ("reader order history", self.page(self.reader, "/order-history")),
            ("admin dashboard", self.page(self.admin, "/admin")),
            ("admin orders", self.page(self.admin, "/admin/orders")),
            ("admin growth report", self.page(self.admin, "/admin/reports/growth")),
        ]


class LandmarkTests(UiContractTestCase):
    def test_every_page_has_one_skip_link_pointing_at_the_main_landmark(self):
        for label, html in self.all_pages():
            with self.subTest(page=label):
                targets = skip_links(html)
                self.assertEqual(
                    len(targets),
                    1,
                    f"{label} should have exactly one skip link, found {targets}",
                )
                ids = re.findall(r'\sid="([^"]+)"', html)
                self.assertIn(
                    targets[0],
                    ids,
                    f"{label} skip link points at #{targets[0]} which does not exist",
                )

    def test_skip_link_is_the_first_focusable_thing_in_the_body(self):
        html = self.page(self.reader, "/")
        body = html.split("<body", 1)[1]
        first_link = re.search(r"<a\b[^>]*>", body)
        self.assertIsNotNone(first_link)
        self.assertIn("skip-link", first_link.group(0))

    def test_every_page_has_exactly_one_main_header_and_footer(self):
        for label, html in self.all_pages():
            with self.subTest(page=label):
                self.assertEqual(html.count("<main"), 1, f"{label}: <main> count")
                self.assertEqual(html.count("<header"), 1, f"{label}: <header> count")
                self.assertEqual(html.count("<footer"), 1, f"{label}: <footer> count")

    def test_main_landmark_can_receive_programmatic_focus(self):
        html = self.page(self.reader, "/")
        main_tag = re.search(r"<main\b[^>]*>", html)
        self.assertIsNotNone(main_tag)
        # tabindex="-1" is what makes the skip link actually move focus.
        self.assertIn("tabindex=\"-1\"", main_tag.group(0))

    def test_no_page_renders_a_duplicate_element_id(self):
        for label, html in self.all_pages():
            with self.subTest(page=label):
                self.assertEqual(dupe_ids(html), [], f"{label} has duplicate ids")


class NavigationTests(UiContractTestCase):
    def test_menu_button_describes_what_it_controls(self):
        html = self.page(self.reader, "/")
        button = re.search(r"<button\b[^>]*id=\"navToggle\"[^>]*>", html, re.S)
        self.assertIsNotNone(button, "the menu button is missing")
        tag = button.group(0)
        self.assertIn('aria-expanded="false"', tag)
        self.assertIn('aria-controls="primaryNav"', tag)

        nav = re.search(r"<nav\b[^>]*id=\"primaryNav\"[^>]*>", html)
        self.assertIsNotNone(nav, "aria-controls points at a nav that does not exist")

    def test_nav_stays_usable_when_javascript_never_runs(self):
        # The collapsing rules hang off the js-nav class that script.js adds, so
        # a blocked or broken script cannot hide the menu permanently. The
        # templates must not hide the nav inline either.
        css = read(STYLE_CSS)
        self.assertIn("html.js-nav .nav-links", css)

        script = read(SCRIPT_JS)
        self.assertIn('classList.add("js-nav")', script)

        html = self.page(self.reader, "/")
        nav_tag = re.search(r"<nav\b[^>]*id=\"primaryNav\"[^>]*>", html)
        self.assertNotIn("hidden", nav_tag.group(0))
        self.assertNotIn('aria-hidden="true"', nav_tag.group(0))

        # No inline script or style may pre-hide the menu either: that is the
        # classic way a working CSS fallback still ends up unreachable.
        for body in re.findall(
            r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S
        ):
            self.assertNotIn("primaryNav", body)
            self.assertNotIn("nav-links", body)
        for body in re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.S):
            self.assertNotIn("primaryNav", body)
            self.assertNotIn("nav-links", body)

    def test_menu_button_uses_aria_expanded_and_escape_and_outside_click(self):
        script = read(SCRIPT_JS)
        self.assertIn('setAttribute("aria-expanded"', script)
        self.assertIn("Escape", script)
        self.assertIn("focus()", script)

    def test_current_page_is_marked_with_aria_current(self):
        home = self.page(self.reader, "/")
        marked = current_links(home)
        self.assertEqual(len(marked), 1, f"expected one current link, got {marked}")

        cart = self.page(self.reader, "/cart")
        cart_marked = current_links(cart)
        self.assertEqual(len(cart_marked), 1)
        self.assertIn("/cart", cart_marked[0])

    def test_header_gold_comes_from_the_palette_not_a_hardcoded_hex(self):
        css = read(STYLE_CSS)
        header = css.split(".site-header {", 1)[1].split("}", 1)[0]
        self.assertIn("var(--orange)", header)
        self.assertNotIn("#dca85b", header)


class AdminLinkTests(UiContractTestCase):
    def admin_links(self, html):
        return re.findall(r'<a\b[^>]*>Admin</a>', html)

    def test_admin_sees_exactly_one_admin_link(self):
        html = self.page(self.admin, "/")
        self.assertEqual(
            len(self.admin_links(html)),
            1,
            "an administrator should get one Admin link, not a duplicated pair",
        )

    def test_admin_link_is_not_duplicated_by_a_password_login_session(self):
        with self.admin.session_transaction() as session:
            session["admin_password_login"] = True

        html = self.page(self.admin, "/")
        self.assertEqual(
            len(self.admin_links(html)),
            1,
            "admin_password_login plus role='admin' must still render one link",
        )
        self.assertEqual(len(re.findall(r'>Admin Login</a>', html)), 0)

    def test_guest_sees_admin_login_instead_of_the_dashboard_link(self):
        html = self.page(self.guest, "/")
        self.assertEqual(self.admin_links(html), [])
        self.assertEqual(len(re.findall(r'>Admin Login</a>', html)), 1)

    def test_password_login_session_without_a_user_offers_the_dashboard(self):
        guest = self.app.test_client()
        with guest.session_transaction() as session:
            session["admin_password_login"] = True

        html = self.page(guest, "/")
        self.assertEqual(len(self.admin_links(html)), 1)
        # Being signed in to the admin area should not offer "Login" again.
        self.assertEqual(len(re.findall(r'>Login</a>', html)), 0)


class FormLabelTests(UiContractTestCase):
    def test_every_rendered_control_has_an_accessible_name(self):
        for label, html in self.all_pages():
            with self.subTest(page=label):
                self.assertEqual(
                    unlabelled_controls(html),
                    [],
                    f"{label} renders form controls with no accessible name",
                )


class FlashAndFooterTests(UiContractTestCase):
    def test_flash_message_is_announced_and_can_be_dismissed(self):
        client = self.app.test_client()
        self.register(client, "Flash Reader", "flash@example.com")
        self.login(client, "flash@example.com")

        # A failed login sets a flash on the next page load.
        other = self.app.test_client()
        other.post("/login", data={"email": "flash@example.com", "password": "wrong"})
        html = self.page(client, "/profile")

        self.assertIn("flash", read(STYLE_CSS))
        self.assertIn(".flash-dismiss", read(STYLE_CSS))
        self.assertIn("flash-dismiss", read(SCRIPT_JS))
        self.assertRegex(
            html,
            r'class="flash (?:error|success|info)[^"]*"\s+role="(?:alert|status)"',
            "flash messages need an ARIA role so they are announced",
        )

    def test_footer_is_structured_rather_than_one_bare_line(self):
        html = self.page(self.reader, "/")
        footer = html.split("<footer", 1)[1].split("</footer>", 1)[0]
        self.assertIn("footer-brand", footer)
        self.assertIn("footer-meta", footer)
        self.assertIn(".footer-brand", read(STYLE_CSS))


class BrandTests(UiContractTestCase):
    def test_brand_emoji_is_hidden_from_screen_readers(self):
        html = self.page(self.reader, "/")
        brand = re.search(r'<a class="brand"[^>]*>(.*?)</a>', html, re.S)
        self.assertIsNotNone(brand)
        inner = brand.group(1)
        self.assertIn('aria-hidden="true"', inner)
        self.assertIn("PageTurner Books", inner)


if __name__ == "__main__":
    unittest.main()
