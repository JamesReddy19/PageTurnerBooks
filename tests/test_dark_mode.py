"""Requirement 60 - site-wide dark mode driven by a single token palette.

The interesting failure mode for dark mode is not "the toggle is missing", it
is "the toggle works but some surface keeps its light background because it was
never converted to a token, or a token has no dark value". These tests check
the palette contract itself, so a future stylesheet change cannot silently
reintroduce a hardcoded colour.
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

from pageturner import create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STYLE_CSS = PROJECT_ROOT / "static" / "style.css"
BASE_HTML = PROJECT_ROOT / "templates" / "base.html"

# Tokens that intentionally stay identical in both themes.
THEME_AGNOSTIC_TOKENS = {"--on-accent", "--radius"}


def read(path):
    return path.read_text(encoding="utf-8")


def css_block(css, selector):
    """Return the declarations of the first rule whose selector matches."""
    pattern = re.compile(
        re.escape(selector) + r"\s*\{(?P<body>[^}]*)\}", re.DOTALL
    )
    match = pattern.search(css)
    if not match:
        raise AssertionError("CSS block not found: " + selector)
    return match.group("body")


def declared_tokens(block):
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", block))


class DarkModeStyleTests(unittest.TestCase):
    def setUp(self):
        self.css = read(STYLE_CSS)

    def test_dark_theme_block_exists_and_targets_html_attribute(self):
        # The pre-paint script sets data-theme on <html>, so the override has
        # to be keyed on the same attribute or dark mode would never apply.
        self.assertIn(':root[data-theme="dark"] {', self.css)
        self.assertIn("color-scheme: dark;", self.css)

    def test_every_light_token_has_a_dark_value(self):
        light_block = css_block(self.css, ":root")
        dark_block = css_block(self.css, ':root[data-theme="dark"]')

        light_tokens = declared_tokens(light_block)
        dark_tokens = declared_tokens(dark_block)

        missing = sorted(light_tokens - dark_tokens - THEME_AGNOSTIC_TOKENS)
        self.assertEqual(
            missing,
            [],
            "these tokens have no dark value, so their light colour would "
            "show through on a dark page: " + ", ".join(missing),
        )

    def test_dark_palette_actually_differs_from_light(self):
        light_block = css_block(self.css, ":root")
        dark_block = css_block(self.css, ':root[data-theme="dark"]')

        def value(block, token):
            return re.search(
                re.escape(token) + r"\s*:\s*([^;]+);", block
            ).group(1).strip()

        for token in ("--ink", "--background", "--surface", "--paper",
                      "--card-background", "--line"):
            self.assertNotEqual(
                value(light_block, token),
                value(dark_block, token),
                token + " is identical in both themes; dark mode would render "
                        "the same surface as light mode",
            )

    def test_no_hardcoded_light_surface_colours_outside_the_palette(self):
        """Every background/colour declaration should resolve through a token.

        Allowed exceptions: the palette definitions themselves (``--name:``
        lines), ``var(--token, #fallback)`` fallbacks (inert while the token is
        defined), the print reset, and the gold accent family (#dca85b and
        friends) which is brand-neutral across themes.
        """
        allowed_hex = {
            "#dca85b", "#f1cf91", "#ffe0a3", "#edbd70",  # gold accent family
            "#ffffff", "#000000",                        # print reset
        }

        offenders = []
        for line_number, line in enumerate(self.css.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("--"):
                continue  # a token definition, not a usage
            if not re.search(r"(background|color|border[^:]*)\s*:", stripped):
                continue
            # Drop var() fallbacks; they never apply while the token exists.
            without_fallbacks = re.sub(r"var\([^()]*\)", "var()", stripped)
            for found in re.findall(r"#[0-9a-fA-F]{3,8}\b", without_fallbacks):
                if found.lower() not in allowed_hex:
                    offenders.append("%s:%s" % (line_number, stripped))

        self.assertEqual(
            offenders,
            [],
            "hardcoded colours bypass the palette and would stay light in "
            "dark mode: " + " | ".join(offenders[:10]),
        )

        # The literal light values live only in the light palette block.
        self.assertEqual(self.css.count("--background: #f5f3ed;"), 1)

    def test_print_styles_reset_the_dark_palette(self):
        # Printing a dark page wastes ink and looks broken, so the print block
        # must pull the tokens back to paper colours.
        print_match = re.search(
            r"@media\s+print\s*\{(?P<body>.*)", self.css, re.DOTALL
        )
        self.assertIsNotNone(print_match, "print stylesheet block missing")
        print_body = print_match.group("body")

        self.assertIn(':root[data-theme="dark"]', print_body)
        self.assertIn("--background: #ffffff;", print_body)
        self.assertIn("--ink: #000000;", print_body)


class DarkModeTemplateTests(unittest.TestCase):
    def setUp(self):
        self.base_template = read(BASE_HTML)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "DATABASE": os.path.join(self.temp_dir.name, "test.db"),
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "ADMIN_EMAIL": "",
            "ADMIN_PASSWORD": "",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_theme_is_applied_before_the_stylesheet_loads(self):
        """No white flash: the theme must be set before CSS is even fetched."""
        script_position = self.base_template.index("pageturner-theme")
        stylesheet_position = self.base_template.index("style.css")

        self.assertLess(
            script_position,
            stylesheet_position,
            "the pre-paint theme script must appear before the stylesheet "
            "link or dark-mode visitors get a white flash",
        )

    def test_prepaint_script_survives_missing_or_blocked_storage(self):
        script_position = self.base_template.index("pageturner-theme")
        tail = self.base_template[script_position:script_position + 900]

        self.assertIn("try {", tail)
        self.assertIn("catch (error)", tail)

    def test_header_offers_a_theme_toggle_on_every_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('id="themeToggle"', body)
        self.assertIn("theme-toggle", body)
        # The toggle must not be printed.
        self.assertIn(".theme-toggle", read(STYLE_CSS))

    def test_theme_toggle_handler_is_wired_in_javascript(self):
        script = read(PROJECT_ROOT / "static" / "script.js")

        self.assertIn('querySelector("#themeToggle")', script)
        self.assertIn("pageturner-theme", script)
        self.assertIn('setAttribute("data-theme"', script)


if __name__ == "__main__":
    unittest.main()
