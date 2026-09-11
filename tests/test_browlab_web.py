"""Tests for the BrowLab web UI server (browlab/web.py).

Runs a real server on a random port and drives it with urllib. Jobs are executed as
``python -m browlab ...`` subprocesses with the manual backend, so no network is used.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.cookiejar import CookieJar
from pathlib import Path
from urllib import error, request

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

if Image is not None:
    from browlab import web as W


def _png_b64(size=(600, 800)) -> str:
    im = Image.new("RGB", size, (230, 200, 180))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@unittest.skipIf(Image is None, "Pillow is required")
class WebServerTest(unittest.TestCase):
    PASSWORD = "pw-1234"

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = W.WebConfig(
            data_dir=Path(cls.tmp.name) / "data", repo_root=REPO, password=cls.PASSWORD, base_path="/browlab",
            codex_bin="definitely-not-a-codex-binary", default_backend="manual", timeout=120,
        )
        cls.server = W.BrowLabServer(("127.0.0.1", 0), cfg)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.jar = CookieJar()
        cls.opener = request.build_opener(request.HTTPCookieProcessor(cls.jar))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    # -- helpers --------------------------------------------------------------
    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}/browlab{path}"

    def call(self, path: str, body=None, method=None, raw=False):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = request.Request(self.url(path), data=data, method=method or ("POST" if data is not None else "GET"), headers=headers)
        try:
            with self.opener.open(req, timeout=60) as r:
                payload = r.read()
                return r.status, (payload if raw else json.loads(payload or b"{}")), r.headers
        except error.HTTPError as exc:
            payload = exc.read()
            try:
                parsed = json.loads(payload or b"{}")
            except ValueError:
                parsed = payload
            return exc.code, parsed, exc.headers

    def login(self) -> None:
        status, body, _ = self.call("/api/login", {"password": self.PASSWORD})
        self.assertEqual(status, 200, body)

    def wait(self, job_id: str, timeout: float = 120.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, body, _ = self.call(f"/api/jobs/{job_id}")
            self.assertEqual(status, 200, body)
            if body["status"] in ("done", "failed", "cancelled"):
                return body
            time.sleep(0.3)
        self.fail(f"job {job_id} did not finish")

    # -- tests ------------------------------------------------------------------
    def test_01_login_required_and_page(self):
        self.jar.clear()
        status, body, _ = self.call("/api/jobs")
        self.assertEqual(status, 401)
        status, html, headers = self.call("/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"BrowLab", html)
        self.assertIn("text/html", headers["Content-Type"])
        # /browlab (no slash) redirects to /browlab/
        req = request.Request(f"http://127.0.0.1:{self.port}/browlab")
        opener = request.build_opener(_NoRedirect())
        try:
            opener.open(req, timeout=10)
            self.fail("expected redirect")
        except error.HTTPError as exc:
            self.assertEqual(exc.code, 302)
            self.assertEqual(exc.headers["Location"], "/browlab/")
        status, me, _ = self.call("/api/me")
        self.assertFalse(me["authed"])
        self.assertTrue(me["auth_required"])
        self.assertFalse(me["codex"])  # fake binary
        self.assertIsInstance(me["api_key"], bool)

    def test_02_login_wrong_then_right(self):
        self.jar.clear()
        status, body, _ = self.call("/api/login", {"password": "nope"})
        self.assertEqual(status, 401)
        self.login()
        status, me, _ = self.call("/api/me")
        self.assertTrue(me["authed"])
        status, presets, _ = self.call("/api/presets")
        self.assertEqual(status, 200)
        self.assertEqual(len(presets["brow_styles"]), 12)
        self.assertEqual([b["key"] for b in presets["backends"]], ["auto", "codex-only", "api", "manual"])
        self.assertFalse(next(b for b in presets["backends"] if b["key"] == "codex-only")["available"])  # fake codex binary
        self.assertEqual(presets["server"]["version"], W.__version__)
        self.assertIsInstance(presets["server"]["commit"], str)
        self.assertEqual(presets["default_backend"], "manual")

    def test_02b_update_endpoint(self):
        self.login()
        canned = {"ok": True, "before": "aaa1111", "after": "bbb2222", "changed": True, "output": "Updating aaa1111..bbb2222"}
        with mock.patch.object(W, "git_update", lambda repo_root: canned), mock.patch.object(W, "schedule_restart") as restart:
            status, body, _ = self.call("/api/update", {"restart": False})
            self.assertEqual(status, 200, body)
            self.assertEqual((body["before"], body["after"], body["changed"], body["restarting"]), ("aaa1111", "bbb2222", True, False))
            self.assertIn("commit", body["server"])
            restart.assert_not_called()
            status, body, _ = self.call("/api/update", {"restart": True})
            self.assertEqual(status, 200, body)
            self.assertTrue(body["restarting"])
            restart.assert_called_once()

    def test_03_calibrate_job_files_and_traversal(self):
        self.login()
        status, job, _ = self.call("/api/jobs", {"kind": "calibrate", "ipd_mm": "63"})
        self.assertEqual(status, 201, job)
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "done", done["log"])
        names = [f["name"] for f in done["files"]]
        self.assertIn("calibration_A4.pdf", names)
        self.assertIn("calibration_A4.png", names)
        status, pdf, headers = self.call(f"/files/{job['id']}/calibration_A4.pdf", raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertTrue(pdf.startswith(b"%PDF"))
        status, thumb, headers = self.call(f"/files/{job['id']}/calibration_A4.png?w=120", raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        with Image.open(io.BytesIO(thumb)) as im:
            self.assertEqual(im.size[0], 120)  # ?w=120 -> 120 px wide
        status, body, _ = self.call(f"/files/{job['id']}/../../data/jobs/{job['id']}/job.json")
        self.assertEqual(status, 404)
        status, body, _ = self.call(f"/files/{job['id']}/job.json")
        self.assertEqual(status, 404)  # job.json is not exposed

    def test_04_generate_manual_backend(self):
        self.login()
        status, job, _ = self.call("/api/jobs", {
            "kind": "generate", "count": "2", "age": "30s", "gender": "female", "face_shape": "oval",
            "brow_condition": "sparse", "notes": "-starts with a dash", "seed": 5, "backend": "manual",
            "size": "1024x1536", "quality": "medium", "layout": "face", "guides": True,
        })
        self.assertEqual(status, 201, job)
        self.assertEqual(job["params"]["count"], 2)
        self.assertEqual(job["params"]["backend"], "manual")
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "done", done["log"])
        names = [f["name"] for f in done["files"]]
        self.assertEqual(len([n for n in names if n.endswith(".prompt.txt")]), 2)
        self.assertIn("manifest.json", names)
        self.assertEqual(len(done["manifest"]["faces"]), 2)
        self.assertIn("-starts with a dash", done["manifest"]["faces"][0]["prompt"])
        status, listing, _ = self.call("/api/jobs")
        self.assertIn(job["id"], [j["id"] for j in listing["jobs"]])

    def test_05_sheet_upload_with_manual_pupils(self):
        self.login()
        status, job, _ = self.call("/api/jobs", {
            "kind": "sheet", "photo": _png_b64(), "photo_name": "face.png", "layout": "both",
            "pupils": "220,300,380,300", "gender": "female", "guides": True,
        })
        self.assertEqual(status, 201, job)
        self.assertEqual(job["params"]["landmarks"], "manual")
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "done", done["log"])
        names = [f["name"] for f in done["files"]]
        self.assertIn("input.png", names)
        self.assertTrue(any(n.endswith("_A4.pdf") for n in names), names)
        self.assertTrue(any("browzone" in n and n.endswith(".pdf") for n in names), names)

    def test_06_restyle_manual_without_landmarks(self):
        self.login()
        status, job, _ = self.call("/api/jobs", {
            "kind": "restyle", "photo": "data:image/png;base64," + _png_b64((640, 640)), "photo_name": "me.png",
            "styles": ["straight", "feathered", "bogus"], "color": "dark_brown", "backend": "manual",
            "landmarks": "none", "sheet": "none",
        })
        self.assertEqual(status, 201, job)
        self.assertEqual(job["params"]["styles"], ["straight", "feathered"])
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "done", done["log"])
        names = [f["name"] for f in done["files"]]
        self.assertEqual(len([n for n in names if n.endswith(".prompt.txt")]), 2)

    def test_07_bad_requests(self):
        self.login()
        status, body, _ = self.call("/api/jobs", {"kind": "restyle"})
        self.assertEqual(status, 400)
        status, body, _ = self.call("/api/jobs", {"kind": "generate", "size": "123x45", "backend": "manual"})
        self.assertEqual(status, 400)
        status, body, _ = self.call("/api/jobs", {"kind": "nope"})
        self.assertEqual(status, 400)
        status, body, _ = self.call("/api/jobs", {"kind": "sheet", "photo": base64.b64encode(b"not an image").decode()})
        self.assertEqual(status, 400)
        status, body, _ = self.call("/api/jobs/does_not_exist")
        self.assertEqual(status, 404)
        # a failed job leaves no folder behind
        self.assertFalse(any(p.name.endswith("_restyle") for p in (Path(self.tmp.name) / "data" / "jobs").iterdir() if not any(p.iterdir())))

    def test_08_usage_and_settings(self):
        self.login()
        status, usage, _ = self.call("/api/usage")
        self.assertEqual(status, 200)
        self.assertEqual(usage["total_usd"], 0)  # manual jobs cost nothing
        self.assertIsNone(usage["budget_usd"])
        status, body, _ = self.call("/api/settings", {"budget_usd": "10", "budget_note": "첫 충전"})
        self.assertEqual(status, 200)
        self.assertEqual(body["budget_usd"], 10.0)
        self.assertEqual(body["remaining_usd"], 10.0)
        self.assertEqual(body["budget_note"], "첫 충전")
        status, body, _ = self.call("/api/settings", {"budget_usd": "-3"})
        self.assertEqual(status, 400)
        status, body, _ = self.call("/api/settings", {"budget_usd": ""})
        self.assertIsNone(body["budget_usd"])

    def test_09_gallery_photo_from_and_delete(self):
        self.login()
        status, gal, _ = self.call("/api/gallery")
        self.assertEqual(status, 200)
        cal = [i for i in gal["items"] if i["kind"] == "calibrate"]
        self.assertTrue(cal and cal[0]["image"] and cal[0]["pdfs"][0]["label"] == "보정 시트 PDF")
        sheet = [i for i in gal["items"] if i["kind"] == "sheet"]
        self.assertTrue(sheet and sheet[0]["image_name"] == "input.png" and any(p["label"] == "얼굴 1:1 PDF" for p in sheet[0]["pdfs"]))
        # reuse the sheet job's input image for a new sheet job
        status, job, _ = self.call("/api/jobs", {"kind": "sheet", "photo_from": {"job": sheet[0]["id"], "file": "input.png"},
                                                 "pupils": "220,300,380,300", "layout": "face"})
        self.assertEqual(status, 201, job)
        self.assertEqual(job["params"]["photo_from"]["job"], sheet[0]["id"])
        done = self.wait(job["id"])
        self.assertEqual(done["status"], "done", done["log"])
        status, body, _ = self.call("/api/jobs", {"kind": "sheet", "photo_from": {"job": sheet[0]["id"], "file": "../job.json"}})
        self.assertEqual(status, 400)
        # delete -> gone from the list, folder moved to trash
        status, body, _ = self.call(f"/api/jobs/{job['id']}/delete", {})
        self.assertEqual(status, 200)
        status, body, _ = self.call(f"/api/jobs/{job['id']}")
        self.assertEqual(status, 404)
        self.assertTrue((Path(self.tmp.name) / "data" / "trash" / job["id"] / "job.json").exists())
        status, body, _ = self.call(f"/api/jobs/{job['id']}/delete", {})
        self.assertEqual(status, 404)

    def test_10_keepalive_post_without_reading_body(self):
        # cancel/delete/logout used to leave the JSON body unread; on a keep-alive connection the
        # next request then started with "{}" and failed with 501.
        import http.client
        self.login()
        cookie = "; ".join(f"{c.name}={c.value}" for c in self.jar)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request("POST", "/browlab/api/jobs/does_not_exist/cancel", body=b"{}",
                         headers={"Content-Type": "application/json", "Cookie": cookie})
            self.assertEqual(conn.getresponse().read() and 404, 404)
            conn.request("GET", "/browlab/api/gallery", headers={"Cookie": cookie})
            r = conn.getresponse()
            self.assertEqual(r.status, 200, r.read())
            r.read()
            conn.request("POST", "/browlab/api/logout", body=b"{}", headers={"Content-Type": "application/json", "Cookie": cookie})
            r = conn.getresponse(); r.read()
            self.assertEqual(r.status, 200)
            conn.request("GET", "/browlab/api/me", headers={"Cookie": cookie})
            r = conn.getresponse()
            self.assertEqual(r.status, 200)
            self.assertFalse(json.loads(r.read())["authed"])
        finally:
            conn.close()

    def test_11_logout(self):
        self.login()
        status, body, _ = self.call("/api/logout", {})
        self.assertEqual(status, 200)
        status, body, _ = self.call("/api/jobs")
        self.assertEqual(status, 401)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


@unittest.skipIf(Image is None, "Pillow is required")
class GitUpdateTest(unittest.TestCase):
    def test_git_update_fast_forwards_from_upstream(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            upstream, clone = tmp / "up", tmp / "clone"
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

            def git(cwd, *args):
                return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()

            upstream.mkdir()
            git(upstream, "init", "-q", "-b", "main")
            (upstream / "f.txt").write_text("1")
            git(upstream, "add", "f.txt")
            git(upstream, "commit", "-q", "-m", "one")
            subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True, env=env)
            first = W.git_update(clone)
            self.assertTrue(first["ok"], first)
            self.assertFalse(first["changed"])
            (upstream / "f.txt").write_text("2")
            git(upstream, "commit", "-q", "-am", "two")
            second = W.git_update(clone)
            self.assertTrue(second["ok"], second)
            self.assertTrue(second["changed"])
            self.assertNotEqual(second["before"], second["after"])
            self.assertEqual((clone / "f.txt").read_text(), "2")
            info = W.server_info(clone)
            self.assertEqual(info["commit"], second["after"])
            self.assertEqual(info["branch"], "main")
            broken = W.git_update(tmp / "missing")
            self.assertFalse(broken["ok"])


class ArgvBuilderTest(unittest.TestCase):
    def cfg(self, tmp: str) -> "W.WebConfig":
        return W.WebConfig(data_dir=Path(tmp), repo_root=REPO, password=None, codex_bin="/opt/codex", default_backend="codex", timeout=42, font="/f.ttf")

    def test_generate_whitelist_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(W.BadRequest):  # garbage age is rejected, not silently randomised
                W.build_argv("generate", {"age": "; rm -rf /"}, Path(tmp), self.cfg(tmp))
            argv, shown = W.build_argv("generate", {
                "count": 99, "age": "", "gender": "alien", "backend": "evil", "quality": "ultra",
                "model": "gpt-image-2", "layout": "weird", "copies": "0", "ipd_mm": "",
            }, Path(tmp), self.cfg(tmp))
            self.assertEqual(argv[:4], [sys.executable, "-m", "browlab", "generate"])
            self.assertEqual(argv[argv.index("-n") + 1], "8")
            self.assertEqual(argv[argv.index("--age") + 1], "random")
            self.assertEqual(argv[argv.index("--gender") + 1], "random")
            self.assertEqual(argv[argv.index("--ethnicity") + 1], "korean")  # default is Korean, not random
            self.assertEqual(shown["brow_condition"], "random")
            self.assertEqual(shown["ethnicity"], "korean")
            self.assertEqual(argv[argv.index("--backend") + 1], "codex")
            self.assertEqual(argv[argv.index("--quality") + 1], "high")
            self.assertEqual(argv[argv.index("--layout") + 1], "both")
            self.assertEqual(argv[argv.index("--copies") + 1], "1")
            self.assertEqual(argv[argv.index("--codex-bin") + 1], "/opt/codex")
            self.assertEqual(argv[argv.index("--timeout") + 1], "42")
            self.assertEqual(argv[argv.index("--font") + 1], "/f.ttf")
            self.assertIn("--guides", argv)
            self.assertEqual(shown["model"], "gpt-image-2")
            self.assertNotIn("--ipd-mm", argv)
            with self.assertRaises(W.BadRequest):
                W.build_argv("generate", {"model": "bad model!"}, Path(tmp), self.cfg(tmp))
            with self.assertRaises(W.BadRequest):
                W.build_argv("generate", {"age": "12"}, Path(tmp), self.cfg(tmp))
            argv, shown = W.build_argv("generate", {"age": "44", "guides": False}, Path(tmp), self.cfg(tmp))
            self.assertEqual(argv[argv.index("--age") + 1], "44")
            self.assertNotIn("--guides", argv)

    def test_sessions_lockout(self):
        s = W.Sessions("secret")
        for _ in range(W.LOGIN_FAILS):
            self.assertIsNone(s.login("wrong", "1.2.3.4"))
        self.assertTrue(s.locked("1.2.3.4"))
        self.assertIsNone(s.login("secret", "1.2.3.4"))  # locked even with the right password
        token = s.login("secret", "5.6.7.8")
        self.assertTrue(token and s.check(token))
        s.logout(token)
        self.assertFalse(s.check(token))
        self.assertTrue(W.Sessions(None).check(None))  # --no-auth


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
