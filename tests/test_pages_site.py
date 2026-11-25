import json
import posixpath
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
REPO_PATH = "/aneesh2411/whoanswered"


class SitePage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.result_rows = []
        self.in_results = False
        self.cell = None

    def handle_starttag(self, tag, attrs):
        self.urls.extend((tag, name, value) for name, value in attrs if name in {"src", "href"})
        if tag == "table" and ("id", "eval-results") in attrs:
            self.in_results = True
        if self.in_results:
            if tag == "tr":
                self.result_rows.append([])
            if tag in {"th", "td"}:
                self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if self.in_results and tag in {"th", "td"}:
            self.result_rows[-1].append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "table":
            self.in_results = False


class PagesSiteTest(unittest.TestCase):
    def test_site_matches_published_results_and_uses_only_allowed_links(self):
        self.assertEqual({path.name for path in SITE.iterdir()}, {"index.html", "style.css"})
        self.assertTrue((SITE / "style.css").is_file())

        page = SitePage()
        page.feed((SITE / "index.html").read_text(encoding="utf-8"))
        page.close()
        self.assertIn(("link", "href", "style.css"), page.urls)

        comparison = json.loads((ROOT / "docs/v2-eval-comparison.json").read_bytes())
        expected = [["Detector", "Committed", "Correct", "Wrong", "Median time"]]
        for key, name in (("v1", "v1"), ("veto", "Veto"), ("score", "Evidence score")):
            metrics = comparison["acoustic"][key]
            coverage = metrics["committed_coverage"]
            median_ms = f"{metrics['median_commitment_ms']:,}".removesuffix(".0")
            expected.append([
                name,
                f"{coverage['numerator']}/{coverage['denominator']}",
                str(metrics["correct_over_all_eligible"]["numerator"]),
                str(metrics["committed_error"]["numerator"]),
                f"{median_ms} ms",
            ])
        self.assertEqual(page.result_rows, expected)

        for tag, name, value in page.urls:
            with self.subTest(tag=tag, attribute=name, value=value):
                location = (value or "").strip().replace("\\", "/")
                if location.startswith("//"):
                    location = "//" + location.lstrip("/")
                url = urlsplit(location)
                if url.scheme or url.netloc:
                    path = posixpath.normpath(unquote(url.path))
                    self.assertEqual((tag, name), ("a", "href"))
                    self.assertIn(url.scheme, {"", "http", "https"})
                    self.assertEqual(url.netloc.lower(), "github.com")
                    self.assertTrue(path == REPO_PATH or path.startswith(REPO_PATH + "/"))
