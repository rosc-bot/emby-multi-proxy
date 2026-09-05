import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("panel_test_module", ROOT / "panel.py")
panel = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = panel
spec.loader.exec_module(panel)


class SiteValidationTests(unittest.TestCase):
    def test_valid_site(self):
        site = panel.Site.from_dict({
            "id": "sg",
            "name": "Singapore",
            "upstream": "https://sg.example.com:443",
            "path": "sg",
        })
        self.assertEqual(site.path, "sg")

    def test_legacy_aliases(self):
        site = panel.Site.from_dict({
            "name": "Legacy SG",
            "url": "https://sg.example.com",
            "prefix": "sg",
        })
        self.assertEqual(site.id, "sg")
        self.assertEqual(site.upstream, "https://sg.example.com")

    def test_reject_injection(self):
        with self.assertRaises(panel.ConfigError):
            panel.Site.from_dict({
                "id": "x",
                "name": "bad",
                "upstream": "https://ok.example.com; return 200",
                "path": "x",
            })

    def test_duplicate_prefix(self):
        a = panel.Site.from_dict({"id": "a", "name": "A", "upstream": "https://a.example", "path": "same"})
        b = panel.Site.from_dict({"id": "b", "name": "B", "upstream": "https://b.example", "path": "same"})
        with self.assertRaises(panel.ConfigError):
            panel.render_nginx([a, b])

    def test_store_reads_legacy_wrapper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "sites.json").write_text(
                '{"sites":[{"name":"A","url":"http://127.0.0.1:8096","prefix":"a"}]}',
                encoding="utf-8",
            )
            store = panel.Store(root, root / "sites.conf")
            sites = store.load()
            self.assertEqual(sites[0].id, "a")


if __name__ == "__main__":
    unittest.main()
