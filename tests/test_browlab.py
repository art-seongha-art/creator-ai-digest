from __future__ import annotations

import base64
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - CI without Pillow
    raise unittest.SkipTest("Pillow is not installed; skipping browlab tests")

from browlab import backends as B
from browlab import landmarks as L
from browlab import masks as M
from browlab import presets as P
from browlab import prompts as PR
from browlab import sheet as S
from browlab.cli import main


def _face_image(w: int = 800, h: int = 1200) -> Image.Image:
    return Image.new("RGB", (w, h), (225, 195, 175))


def _pupil_landmarks(w: int = 800, h: int = 1200) -> L.FaceLandmarks:
    return L.from_pupils(w, h, (300.0, 480.0), (500.0, 480.0))


def _brow_center(lm: L.FaceLandmarks):
    """Centre of the subject's right eyebrow polygon (inside the mask)."""
    xs = [p[0] for p in lm.right_brow]
    ys = [p[1] for p in lm.right_brow]
    return int(sum(xs) / len(xs)), int(sum(ys) / len(ys))


class PresetTests(unittest.TestCase):
    def test_tables_have_korean_labels(self):
        for table in (P.FACE_SHAPES, P.BROW_CONDITIONS, P.BROW_STYLES, P.ETHNICITIES):
            for key, item in table.items():
                self.assertEqual(item.key, key)
                self.assertTrue(item.ko)
                self.assertTrue(item.prompt)
        for key, item in P.FACE_SHAPES.items():
            self.assertTrue(item.brow_tip_ko and item.brow_tip_en)

    def test_choice_lists(self):
        self.assertIn("random", P.AGE_CHOICES)
        self.assertIn("random", P.FACE_SHAPE_CHOICES)
        self.assertIn("any", P.ETHNICITY_CHOICES)

    def test_age_group_and_ipd(self):
        self.assertEqual(P.age_group_of(17), "10s")
        self.assertEqual(P.age_group_of(45), "40s")
        self.assertEqual(P.age_group_of(85), "70s")
        self.assertEqual(P.default_ipd_mm("female", 30), 62.0)
        self.assertEqual(P.default_ipd_mm("male", 30), 64.0)
        self.assertEqual(P.default_ipd_mm("male", 16), 60.0)
        self.assertEqual(P.default_ipd_mm(None, None), 63.0)


class PromptTests(unittest.TestCase):
    def test_specs_are_deterministic(self):
        a = PR.make_specs(4, seed=123)
        b = PR.make_specs(4, seed=123)
        c = PR.make_specs(4, seed=124)
        self.assertEqual([s.slug() for s in a], [s.slug() for s in b])
        self.assertNotEqual([s.slug() for s in a], [s.slug() for s in c])
        for s in a:
            self.assertIn(s.face_shape, P.FACE_SHAPES)
            self.assertIn(s.brow_condition, P.BROW_CONDITIONS)
            self.assertIn(s.ethnicity, P.ETHNICITIES)
            self.assertTrue(15 <= s.age <= 79)

    def test_overrides(self):
        specs = PR.make_specs(6, seed=1, age="30s", gender="female", face_shape="round", brow_condition="sparse", ethnicity="korean")
        for s in specs:
            self.assertTrue(30 <= s.age <= 39)
            self.assertEqual((s.gender, s.face_shape, s.brow_condition, s.ethnicity), ("female", "round", "sparse", "korean"))
        exact = PR.make_specs(1, seed=1, age="34")[0]
        self.assertEqual(exact.age, 34)
        with self.assertRaises(ValueError):
            PR.make_specs(1, seed=1, age="teen")
        with self.assertRaises(ValueError):
            PR.make_specs(1, seed=1, face_shape="banana")

    def test_face_prompt_content(self):
        spec = PR.FaceSpec(age=52, gender="male", face_shape="square", brow_condition="missing_tail", ethnicity="korean", seed=5)
        text = PR.build_face_prompt(spec)
        self.assertIn("52-year-old Korean man", text)
        self.assertIn(P.BROW_CONDITIONS["missing_tail"].prompt, text)
        self.assertIn(P.FACE_SHAPES["square"].prompt, text)
        self.assertIn("pure white", text)
        self.assertIn("Avoid:", text)
        self.assertIn("Do not draw full", text)
        self.assertNotIn("Additional notes", text)
        self.assertIn("Additional notes: freckles", PR.build_face_prompt(PR.FaceSpec(20, "female", "oval", "faint", "japanese", notes="freckles")))

    def test_restyle_prompt_content(self):
        text = PR.build_restyle_prompt("feathered", "ash_brown", with_guide_image=True)
        self.assertIn(P.BROW_STYLES["feathered"].prompt, text)
        self.assertIn("cool ash brown", text)
        self.assertIn("Image 2: region guide", text)
        self.assertIn("change only the eyebrows", text)
        self.assertNotIn("Image 2", PR.build_restyle_prompt("straight"))

    def test_spec_roundtrip(self):
        spec = PR.make_specs(1, seed=9)[0]
        d = spec.to_dict()
        self.assertEqual(PR.FaceSpec.from_dict(d).slug(), spec.slug())
        self.assertIn("label_ko", d)
        self.assertIn("세", spec.label_ko())


class SizeAndPrepareTests(unittest.TestCase):
    def test_validate_size(self):
        self.assertEqual(M.validate_gpt_image_size("1536x2304"), "1536x2304")
        self.assertEqual(M.validate_gpt_image_size("auto"), "auto")
        for bad in ("1000x1500", "4000x2000", "512x512", "1024x3840", "abc"):
            with self.assertRaises(ValueError, msg=bad):
                M.validate_gpt_image_size(bad)

    def test_prepare_for_edit(self):
        img, scale = M.prepare_for_edit(Image.new("RGB", (1001, 1503)))
        self.assertEqual(img.size, (992, 1488))
        self.assertAlmostEqual(scale, 1.0)
        small, scale = M.prepare_for_edit(Image.new("RGB", (640, 640)))
        self.assertGreaterEqual(small.size[0] * small.size[1], M.GPT_IMAGE_MIN_PIXELS)
        self.assertEqual(small.size[0] % 16, 0)
        big, scale = M.prepare_for_edit(Image.new("RGB", (6000, 4000)), max_edge=2048)
        self.assertLessEqual(max(big.size), 2048)
        self.assertLess(scale, 1.0)


class LandmarkTests(unittest.TestCase):
    def test_from_pupils_geometry(self):
        lm = _pupil_landmarks()
        self.assertAlmostEqual(lm.ipd_px, 200.0)
        self.assertEqual(lm.eye_center, (400.0, 480.0))
        self.assertLess(lm.forehead_top[1], lm.brow_top_y)
        self.assertLess(lm.brow_top_y, lm.eye_top_y)
        self.assertGreater(lm.chin[1], lm.nose_tip[1])
        self.assertEqual(len(lm.right_brow), 8)
        # subject's right eye is on the image left
        self.assertLess(lm.right_pupil[0], lm.left_pupil[0])
        with self.assertRaises(L.LandmarkError):
            L.from_pupils(10, 10, (1, 1), (1, 1))

    def test_scale_translate_roundtrip(self):
        lm = _pupil_landmarks()
        s = lm.scaled(2.0)
        self.assertAlmostEqual(s.ipd_px, 400.0)
        t = s.translated(10, -5, width=50, height=60)
        self.assertEqual(t.width, 50)
        self.assertAlmostEqual(t.right_pupil[0], 610.0)
        d = t.to_dict()
        back = L.FaceLandmarks.from_dict(json.loads(json.dumps(d)))
        self.assertAlmostEqual(back.ipd_px, 400.0)
        self.assertEqual(len(back.left_brow), 8)

    def test_codex_json_conversion_rescales(self):
        pt = lambda x, y: {"x": x, "y": y}
        data = {
            "image_width": 500, "image_height": 750,
            "right_pupil": pt(190, 300), "left_pupil": pt(310, 300),
            "right_inner_corner": pt(220, 302), "right_outer_corner": pt(160, 300),
            "left_inner_corner": pt(280, 302), "left_outer_corner": pt(340, 300),
            "right_upper_lid": pt(190, 290), "left_upper_lid": pt(310, 290),
            "right_nostril_edge": pt(225, 380), "left_nostril_edge": pt(275, 380),
            "nose_tip": pt(250, 390), "chin": pt(250, 560), "hairline_center": pt(250, 130),
            "right_face_edge": pt(120, 400), "left_face_edge": pt(380, 400),
            "right_brow": {"head": pt(225, 255), "arch": pt(180, 245), "tail": pt(150, 255), "thickness_px": 10},
            "left_brow": {"head": pt(275, 255), "arch": pt(320, 245), "tail": pt(350, 255), "thickness_px": 10},
        }
        lm = L.landmarks_from_codex_json(data, 1000, 1500)
        self.assertAlmostEqual(lm.ipd_px, 240.0)
        self.assertEqual(lm.source, "codex")
        self.assertAlmostEqual(lm.right_brow[0][1], 500.0)  # (255 - 5) * 2
        self.assertEqual(len(lm.right_brow), 6)

    def test_extract_json_with_fences(self):
        text = "```json\n{\"a\": 1}\n```"
        self.assertEqual(L._extract_json(text), {"a": 1})
        self.assertEqual(L._extract_json("blah {\"b\": 2} tail"), {"b": 2})

    def test_landmark_prompt_and_schema(self):
        self.assertIn("800 pixels wide", L.landmark_query_prompt(800, 600))
        self.assertIn("right_brow", L.LANDMARK_SCHEMA["required"])

    def test_detect_manual_requires_pupils(self):
        img = _face_image()
        self.assertIsNone(L.detect(img, None, provider="none"))
        with self.assertRaises(L.LandmarkError):
            L.detect(img, None, provider="manual")
        lm = L.detect(img, None, provider="manual", pupils=[300, 480, 500, 480])
        self.assertEqual(lm.source, "manual")


class MaskTests(unittest.TestCase):
    def test_brow_mask_bounds(self):
        lm = _pupil_landmarks()
        mask = M.brow_region_mask(lm)
        bbox = mask.getbbox()
        self.assertIsNotNone(bbox)
        self.assertLess(bbox[3], lm.eye_top_y)          # never covers the eyes
        self.assertLess(bbox[1], lm.brow_top_y)          # extends above the current brows
        self.assertEqual(mask.size, (800, 1200))

    def test_api_mask_alpha(self):
        lm = _pupil_landmarks()
        mask = M.brow_region_mask(lm)
        api = M.api_mask_image(mask)
        self.assertEqual(api.mode, "RGBA")
        self.assertEqual(api.getpixel((5, 5))[:3], (0, 0, 0))  # colour channels are irrelevant to the API: keep them black (small file)
        alpha = api.split()[-1]
        cx, cy = _brow_center(lm)
        self.assertEqual(alpha.getpixel((cx, cy)), 0)      # editable
        self.assertEqual(alpha.getpixel((5, 5)), 255)      # protected

    def test_composite_only_changes_masked_area(self):
        lm = _pupil_landmarks()
        mask = M.brow_region_mask(lm)
        original = _face_image()
        edited = Image.new("RGB", original.size, (10, 10, 10))
        out = M.composite_brows(original, edited, mask, feather_px=0)
        self.assertEqual(out.getpixel((5, 5)), (225, 195, 175))
        cx, cy = _brow_center(lm)
        self.assertEqual(out.getpixel((cx, cy)), (10, 10, 10))
        # different sizes are resized back to the original
        out2 = M.composite_brows(original, edited.resize((400, 600)), mask, feather_px=2)
        self.assertEqual(out2.size, original.size)

    def test_guide_overlay(self):
        lm = _pupil_landmarks()
        img = _face_image()
        over = M.guide_overlay(img, M.brow_region_mask(lm))
        self.assertEqual(over.size, img.size)
        self.assertEqual(over.getpixel((5, 5)), (225, 195, 175))


class FaceTileTests(unittest.TestCase):
    def test_tile_box_geometry(self):
        lm = _pupil_landmarks()  # 800x1200 image, pupils at (300,480) / (500,480)
        box = M.face_tile_box(lm, (800, 1200))
        x0, y0, x1, y1 = box
        self.assertAlmostEqual((x1 - x0) / (y1 - y0), 2 / 3, places=2)
        self.assertTrue(0 <= x0 < x1 <= 800 and 0 <= y0 < y1 <= 1200)
        self.assertLess(y0, lm.brow_top_y - lm.ipd_px)      # room above the brows
        self.assertGreater(y1, lm.chin[1])                   # chin inside
        self.assertLess(x0, lm.right_cheek[0])
        self.assertGreater(x1, lm.left_cheek[0])
        # a face that fills the photo: the box shrinks to the photo instead of padding
        big = L.from_pupils(800, 900, (200.0, 400.0), (600.0, 400.0))
        bx = M.face_tile_box(big, (800, 900))
        self.assertTrue(0 <= bx[0] < bx[2] <= 800 and 0 <= bx[1] < bx[3] <= 900)
        self.assertAlmostEqual((bx[2] - bx[0]) / (bx[3] - bx[1]), 2 / 3, places=2)

    def test_crop_and_landmark_transfer(self):
        lm = _pupil_landmarks()
        box = M.face_tile_box(lm, (800, 1200))
        tile = M.crop_tile(_face_image(), box, (1024, 1536))
        self.assertEqual(tile.size, (1024, 1536))
        lt = M.landmarks_to_tile(lm, box, (1024, 1536))
        sx = 1024 / (box[2] - box[0])
        self.assertAlmostEqual(lt.ipd_px, lm.ipd_px * sx, places=3)
        self.assertAlmostEqual(lt.right_pupil[0], (300 - box[0]) * sx, places=3)
        self.assertTrue(0 < lt.right_pupil[0] < lt.left_pupil[0] < 1024)
        self.assertEqual(M.parse_size("1024x1536"), (1024, 1536))
        small, sc = M.downscale_to(Image.new("RGB", (4000, 3000)), max_edge=2000)
        self.assertEqual(small.size, (2000, 1500))
        self.assertAlmostEqual(sc, 0.5)
        same, sc = M.downscale_to(Image.new("RGB", (640, 480)), max_edge=2000)
        self.assertEqual((same.size, sc), ((640, 480), 1.0))

    def test_similarity_warp_moves_pupils_back(self):
        ref = _pupil_landmarks()
        # the "edited" picture has the face shifted, slightly enlarged and rotated
        moved = L.from_pupils(800, 1200, (330.0, 470.0), (545.0, 486.0))
        sim = M.similarity_from_pupils(moved, ref)
        self.assertGreater(sim.shift_frac, 0.1)
        self.assertGreater(sim.scale, 0.85)
        edited = Image.new("RGB", (800, 1200), "white")
        d = ImageDraw.Draw(edited)
        for (x, y) in (moved.right_pupil, moved.left_pupil):
            d.ellipse((x - 7, y - 7, x + 7, y + 7), fill="black")
        warped = M.warp_similarity(edited, sim, (800, 1200))
        for (x, y) in (ref.right_pupil, ref.left_pupil):
            window = [warped.getpixel((int(x) + dx, int(y) + dy))[0] for dx in (-2, 0, 2) for dy in (-2, 0, 2)]
            self.assertLess(min(window), 80, (x, y, window))
        self.assertGreater(warped.getpixel((400, 300))[0], 200)  # background stays white where the source exists

    def test_match_tone_removes_colour_cast(self):
        original = Image.new("RGB", (400, 400), (150, 150, 150))
        edited = Image.new("RGB", (400, 400), (190, 150, 130))
        mask = Image.new("L", (400, 400), 0)
        ImageDraw.Draw(mask).rectangle((100, 100, 300, 180), fill=255)
        fixed = M.match_tone(edited, original, mask)
        self.assertEqual(fixed.getpixel((200, 140)), (150, 150, 150))
        # nothing to do when the ring already matches
        same = M.match_tone(original, original, mask)
        self.assertEqual(same.getpixel((200, 140)), (150, 150, 150))

    def test_paste_back_only_changes_masked_area(self):
        full = Image.new("RGB", (800, 1200), (10, 20, 30))
        box = (100, 150, 500, 750)  # 400x600 -> tile 1024x1536
        tile = Image.new("RGB", (1024, 1536), (200, 100, 50))
        mask = Image.new("L", (1024, 1536), 0)
        ImageDraw.Draw(mask).rectangle((200, 300, 800, 500), fill=255)
        out = M.paste_back(full, tile, box, mask, feather_px=0)
        self.assertEqual(out.size, full.size)
        self.assertEqual(out.getpixel((5, 5)), (10, 20, 30))
        self.assertEqual(out.getpixel((150, 200)), (10, 20, 30))      # inside box, outside mask
        self.assertEqual(out.getpixel((100 + 195, 150 + 156)), (200, 100, 50))  # inside mask (tile 500,400 -> 195,156)


class SheetTests(unittest.TestCase):
    def test_face_sheet_is_a4_and_life_size(self):
        opts = S.SheetOptions(ipd_mm=63.0, guides=True, caption="cap", note="note")
        canvas, page_lm = S.compose_face_sheet(_face_image(), _pupil_landmarks(), opts)
        self.assertEqual(canvas.size, (2480, 3508))
        self.assertAlmostEqual(page_lm.ipd_px, 63.0 * 300 / 25.4, places=0)
        self.assertAlmostEqual(page_lm.eye_center[0], 2480 / 2, delta=2)

    def test_face_sheet_without_landmarks(self):
        opts = S.SheetOptions(fallback_image_height_mm=320.0)
        canvas, page_lm = S.compose_face_sheet(_face_image(), None, opts)
        self.assertEqual(canvas.size, (2480, 3508))
        self.assertIsNone(page_lm)
        self.assertAlmostEqual(S.life_size_scale(None, 1200, opts), 320 * 300 / 25.4 / 1200)

    def test_browzone_and_grid_and_calibration(self):
        opts = S.SheetOptions(ipd_mm=63.0)
        img, lm = _face_image(), _pupil_landmarks()
        pages = S.compose_browzone_sheet([("a", img, lm)], opts, copies=3)
        self.assertGreaterEqual(len(pages), 1)
        self.assertEqual(pages[0].size, (2480, 3508))
        grid = S.compose_grid_sheet([(f"v{i}", img) for i in range(7)], opts)
        self.assertEqual(len(grid), 2)  # 6 per page when more than 4 items
        self.assertEqual(S.calibration_sheet(opts).size, (2480, 3508))

    def test_save_pages_png_dpi_and_pdf(self):
        opts = S.SheetOptions(dpi=150)
        canvas = S.calibration_sheet(opts)
        self.assertEqual(canvas.size, (1240, 1754))
        with tempfile.TemporaryDirectory() as tmp:
            written = S.save_pages([canvas, canvas], Path(tmp) / "x.png", 150, pdf=True)
            names = sorted(p.name for p in written)
            self.assertEqual(names, ["x.pdf", "x_p1.png", "x_p2.png"])
            with Image.open(Path(tmp) / "x_p1.png") as im:
                self.assertAlmostEqual(im.info["dpi"][0], 150, delta=0.5)
            self.assertGreater((Path(tmp) / "x.pdf").stat().st_size, 1000)


class BackendTests(unittest.TestCase):
    def test_manual_backend_writes_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "face.png"
            r = B.ManualBackend().generate("HELLO", out)
            self.assertTrue(r.pending)
            self.assertIn("HELLO", out.with_suffix(".prompt.txt").read_text(encoding="utf-8"))
            r2 = B.ManualBackend().edit("EDIT", [Path(tmp) / "in.png"], Path(tmp) / "e.png", mask=Path(tmp) / "m.png")
            self.assertIn("Mask", (Path(tmp) / "e.prompt.txt").read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "fake executable needs a POSIX shell")
    def test_codex_backend_with_fake_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            home = tmp / "codex_home"
            (home / "generated_images").mkdir(parents=True)
            fake = tmp / "codex"
            fake.write_text(textwrap.dedent(f"""\
                #!/usr/bin/env python3
                import sys, time, pathlib
                from PIL import Image
                args = sys.argv[1:]
                prompt = sys.stdin.read()
                home = pathlib.Path({str(home)!r})
                (home / "args.txt").write_text(" ".join(args))
                (home / "prompt.txt").write_text(prompt)
                d = home / "generated_images" / "today"
                d.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (32, 48), (1, 2, 3)).save(d / "img.png")
                if "-o" in args:
                    pathlib.Path(args[args.index("-o") + 1]).write_text("SAVED: nope")
                print("ok")
                """), encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            backend = B.CodexBackend(codex_bin=str(fake), codex_home=home, model="m1")
            out = tmp / "out" / "face_01.png"
            r = backend.generate("MY PROMPT", out)
            self.assertTrue(r.path.exists())
            with Image.open(r.path) as im:
                self.assertEqual(im.size, (32, 48))
            args = (home / "args.txt").read_text()
            self.assertIn("exec --skip-git-repo-check -s workspace-write -C", args)
            self.assertIn("-m m1", args)
            self.assertTrue(args.endswith(" -"))
            prompt = (home / "prompt.txt").read_text()
            self.assertTrue(prompt.startswith("$imagegen"))
            self.assertIn("face_01.png", prompt)
            self.assertIn("MY PROMPT", prompt)
            # edit attaches the images in order
            src = tmp / "src.png"
            Image.new("RGB", (16, 16)).save(src)
            r2 = backend.edit("EDIT", [src, src], tmp / "out" / "edit.png")
            self.assertTrue(r2.path.exists())
            self.assertEqual((home / "args.txt").read_text().count("-i "), 2)
            self.assertIn("EDIT", (home / "prompt.txt").read_text())

    @unittest.skipIf(os.name == "nt", "fake executable needs a POSIX shell")
    def test_codex_backend_passes_absolute_workdir(self):
        # Regression: a relative out path used to be passed to `-C` while cwd was
        # already that folder -> codex exec failed with "No such file or directory".
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            home = tmp / "codex_home"
            (home / "generated_images").mkdir(parents=True)
            fake = tmp / "codex"
            fake.write_text(textwrap.dedent(f"""\
                #!/usr/bin/env python3
                import sys, pathlib
                from PIL import Image
                args = sys.argv[1:]
                sys.stdin.read()
                c = pathlib.Path(args[args.index("-C") + 1])
                o = pathlib.Path(args[args.index("-o") + 1])
                assert c.is_absolute() and c.is_dir(), f"-C must be an absolute existing dir: {{c}}"
                assert o.is_absolute(), f"-o must be absolute: {{o}}"
                Image.new("RGB", (8, 8)).save(c / "face_01.png")
                """), encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                backend = B.CodexBackend(codex_bin=str(fake), codex_home=home)
                r = backend.generate("P", Path("out") / "face_01.png")
                self.assertTrue(r.path.exists())
            finally:
                os.chdir(cwd)

    def test_codex_backend_missing_binary(self):
        backend = B.CodexBackend(codex_bin="definitely-not-a-real-codex-binary")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(B.GenerationError):
                backend.generate("x", Path(tmp) / "a.png")

    def test_openai_backend_with_fake_client(self):
        calls = []
        png = io.BytesIO()
        Image.new("RGB", (16, 24), (9, 9, 9)).save(png, format="PNG")
        b64 = base64.b64encode(png.getvalue()).decode()

        class Item:
            b64_json = b64
            url = None

        class Resp:
            data = [Item()]

        class Images:
            def generate(self, **kw):
                calls.append(("generate", kw))
                return Resp()

            def edit(self, **kw):
                calls.append(("edit", kw))
                return Resp()

        class Client:
            images = Images()

        backend = B.OpenAIBackend(client=Client())
        self.assertEqual(backend.model, "gpt-image-2.5-flare")
        self.assertEqual(backend.edit_model, "gpt-image-2.5-sunburst")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r = backend.generate("P", tmp / "g.png", size="1536x2304", quality="high")
            with Image.open(r.path) as im:
                self.assertEqual(im.size, (16, 24))
            self.assertEqual(calls[-1][1]["size"], "1536x2304")
            src = tmp / "s.png"
            Image.new("RGB", (16, 16)).save(src)
            mask = tmp / "m.png"
            Image.new("RGBA", (16, 16)).save(mask)
            backend.edit("E", [src], tmp / "e.png", mask=mask, size="16x16")
            kw = calls[-1][1]
            self.assertEqual(kw["model"], "gpt-image-2.5-sunburst")
            self.assertIn("mask", kw)
            self.assertNotIn("input_fidelity", kw)
            legacy = B.OpenAIBackend(client=Client(), model="gpt-image-1")
            legacy.edit("E", [src], tmp / "e2.png", size="16x16")
            self.assertEqual(calls[-1][1]["input_fidelity"], "high")
            self.assertEqual(calls[-1][1]["size"], "auto")  # gpt-image-1 family: no custom sizes
            mini = B.OpenAIBackend(client=Client(), model="gpt-image-1-mini")
            mini.generate("P", tmp / "g2.png", size="1536x2304", quality="low")
            self.assertEqual(calls[-1][1]["size"], "1024x1536")  # snapped to the nearest fixed portrait size
            self.assertNotIn("input_fidelity", calls[-1][1])
            mini.edit("E", [src], tmp / "e3.png", size="1360x2048")
            self.assertEqual(calls[-1][1]["size"], "auto")
            self.assertNotIn("input_fidelity", calls[-1][1])

    def test_usage_dict_and_cost_estimate(self):
        class Det:
            text_tokens = 120
            image_tokens = 0

        class Usage:
            input_tokens = 120
            output_tokens = 2127
            total_tokens = 2247
            input_tokens_details = Det()

        class Resp:
            usage = Usage()

        u = B.usage_dict(Resp())
        self.assertEqual(u["output_tokens"], 2127)
        self.assertEqual(u["text_tokens"], 120)
        # 120 text x $5/M + 2127 image-out x $30/M
        self.assertAlmostEqual(B.estimate_cost_usd(u, "gpt-image-2.5-flare"), 0.06441, places=5)
        self.assertAlmostEqual(B.estimate_cost_usd(u, "gpt-image-1"), 0.0857, places=4)
        self.assertAlmostEqual(B.estimate_cost_usd(u, "gpt-image-1-mini"), 0.017256, places=6)  # (120x2 + 2127x8) / 1M
        self.assertIsNone(B.usage_dict(object()))
        self.assertIsNone(B.estimate_cost_usd(None, "gpt-image-2"))

        class Item:
            b64_json = base64.b64encode(b"x").decode()
            url = None

        class RespWithUsage:
            data = [Item()]
            usage = Usage()

        class Images:
            def generate(self, **kw):
                return RespWithUsage()

        class Client:
            images = Images()

        with tempfile.TemporaryDirectory() as tmp:
            r = B.OpenAIBackend(client=Client()).generate("P", Path(tmp) / "g.png")
            self.assertEqual(r.usage["output_tokens"], 2127)
            self.assertAlmostEqual(r.cost_usd, 0.06441, places=5)

    def test_openai_backend_needs_key(self):
        old = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(B.GenerationError):
                B.OpenAIBackend().client()
        finally:
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old

    def test_make_backend(self):
        self.assertIsInstance(B.make_backend("codex"), B.CodexBackend)
        self.assertIsInstance(B.make_backend("api"), B.OpenAIBackend)
        self.assertIsInstance(B.make_backend("manual"), B.ManualBackend)
        with self.assertRaises(ValueError):
            B.make_backend("nope")


class _StubBackend(B.BaseBackend):
    """Scripted backend: each call pops the next outcome (an Exception to raise, or "ok")."""

    def __init__(self, name: str, model, outcomes):
        self.name = name
        self.model = model
        self.outcomes = list(outcomes)
        self.calls = 0
        self.last_edit = None

    def _next(self, out_path):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x")
        return B.GenResult(path=Path(out_path), backend=self.name, prompt="p", model=self.model)

    def generate(self, prompt, out_path, *, size="1536x2304", quality="high"):
        return self._next(out_path)

    def edit(self, prompt, images, out_path, *, mask=None, size="auto", quality="high"):
        self.last_edit = {"prompt": prompt, "images": list(images), "mask": mask, "size": size}
        return self._next(out_path)


LIMIT0 = RuntimeError("Error code: 429 - {'error': {'code': 'rate_limit_exceeded', 'message': 'Rate limit reached for gpt-image-2 "
                      "(for limit gpt-image) in organization org-x: Limit 0, Used 0, Requested 1.'}}")


class FallbackBackendTests(unittest.TestCase):
    def test_unavailable_backends_are_skipped_for_the_rest_of_the_run(self):
        codex = _StubBackend("codex", None, [B.GenerationError("codex exec finished but no image was produced.\nstderr: ERROR: You've hit your usage limit")])
        flare = _StubBackend("api", "gpt-image-2.5-flare", [LIMIT0])
        mini = _StubBackend("api", "gpt-image-1-mini", [])
        fb = B.FallbackBackend([("codex", codex), ("api:gpt-image-2.5-flare", flare), ("api:gpt-image-1-mini", mini)])
        fb.log = lambda m: None
        with tempfile.TemporaryDirectory() as tmp:
            r1 = fb.generate("p", Path(tmp) / "a.png")
            self.assertEqual((r1.backend, r1.model), ("api", "gpt-image-1-mini"))
            self.assertEqual(r1.fallback, ["codex: Codex 사용 한도 소진", "api:gpt-image-2.5-flare: 모델 미개방(Limit 0)"])
            r2 = fb.generate("p", Path(tmp) / "b.png")
            self.assertEqual(r2.fallback, [])
        self.assertEqual((codex.calls, flare.calls, mini.calls), (1, 1, 2))
        self.assertEqual(set(fb.skipped), {"codex", "api:gpt-image-2.5-flare"})
        self.assertEqual(fb.model, "gpt-image-1-mini")

    def test_transient_failure_is_retried_on_the_next_image(self):
        codex = _StubBackend("codex", None, [B.GenerationError("codex exec timed out after 900s"), "ok"])
        api = _StubBackend("api", "gpt-image-2", [])
        fb = B.FallbackBackend([("codex", codex), ("api:gpt-image-2", api)])
        fb.log = lambda m: None
        with tempfile.TemporaryDirectory() as tmp:
            r1 = fb.generate("p", Path(tmp) / "a.png")
            r2 = fb.generate("p", Path(tmp) / "b.png")
        self.assertEqual((r1.backend, r2.backend), ("api", "codex"))
        self.assertEqual(fb.skipped, {})

    def test_all_failed_raises_combined_error(self):
        codex = _StubBackend("codex", None, [B.GenerationError("Codex CLI not found (codex)")])
        api = _StubBackend("api", "gpt-image-2", [RuntimeError("boom 500"), LIMIT0])
        fb = B.FallbackBackend([("codex", codex), ("api:gpt-image-2", api)])
        fb.log = lambda m: None
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(B.GenerationError) as ctx:
                fb.generate("p", Path(tmp) / "a.png")
            self.assertIn("codex: Codex 없음", str(ctx.exception))
            self.assertIn("boom 500", str(ctx.exception))
            with self.assertRaises(B.GenerationError) as ctx2:  # codex now sticky-skipped, api hits Limit 0
                fb.generate("p", Path(tmp) / "b.png")
            self.assertIn("앞서 사용 불가 판정", str(ctx2.exception))
            self.assertIn("Limit 0", str(ctx2.exception))
        with self.assertRaises(B.GenerationError):
            B.FallbackBackend([]).generate("p", Path("x.png"))

    def test_edit_uses_per_backend_variants(self):
        codex = _StubBackend("codex", None, [B.GenerationError("stderr: You've hit your usage limit")])
        api = _StubBackend("api", "gpt-image-2.5-sunburst", [])
        fb = B.FallbackBackend([("codex", codex), ("api:gpt-image-2.5-sunburst", api)])
        fb.log = lambda m: None
        with tempfile.TemporaryDirectory() as tmp:
            img, guide, mask = Path(tmp) / "i.png", Path(tmp) / "g.png", Path(tmp) / "m.png"
            r = fb.edit("api prompt", [img], Path(tmp) / "o.png", mask=mask, size="512x512",
                        variants={"codex": {"prompt": "codex prompt", "images": [img, guide], "mask": None, "size": "auto"}})
        self.assertEqual(r.backend, "api")
        self.assertEqual(codex.last_edit, {"prompt": "codex prompt", "images": [img, guide], "mask": None, "size": "auto"})
        self.assertEqual(api.last_edit, {"prompt": "api prompt", "images": [img], "mask": mask, "size": "512x512"})

    def test_coerce_quality_and_reasons(self):
        self.assertEqual(B.coerce_quality("gpt-image-2", "xhigh"), "high")
        self.assertEqual(B.coerce_quality("gpt-image-1-mini", "max"), "high")
        self.assertEqual(B.coerce_quality("gpt-image-2.5-flare", "max"), "max")
        self.assertEqual(B.coerce_quality("gpt-image-2", "medium"), "medium")
        self.assertEqual(B.unavailable_reason(LIMIT0), "모델 미개방(Limit 0)")
        self.assertEqual(B.unavailable_reason(RuntimeError("Your organization must be verified to use the model")), "조직 인증 필요")
        self.assertIsNone(B.unavailable_reason(RuntimeError("Error code: 429 - too many requests, retry after 3s")))
        self.assertIsNone(B.unavailable_reason(B.GenerationError("codex exec timed out after 900s")))

    def test_make_auto_backend_composition(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            fb = B.make_backend("auto", codex_bin="definitely-not-a-codex-binary")
            self.assertEqual([l for l, _ in fb.attempts], [
                "api:gpt-image-2.5-flare|gpt-image-2.5-sunburst", "api:gpt-image-2", "api:gpt-image-1.5", "api:gpt-image-1", "api:gpt-image-1-mini",
            ])
            self.assertTrue(any("codex" in n for n in fb.notes))
            pinned = B.make_backend("auto", codex_bin="definitely-not-a-codex-binary", model="gpt-image-2")
            self.assertEqual([l for l, _ in pinned.attempts], ["api:gpt-image-2"])
            custom = B.make_backend("auto", codex_bin="definitely-not-a-codex-binary", model_chain=["gpt-image-2", "gpt-image-1-mini"])
            self.assertEqual([l for l, _ in custom.attempts], ["api:gpt-image-2", "api:gpt-image-1-mini"])
            both = B.make_backend("auto", codex_bin="definitely-not-a-codex-binary", model_chain=["gpt-image-2"], edit_model_chain=["gpt-image-2.5-sunburst"])
            self.assertEqual([l for l, _ in both.attempts], ["api:gpt-image-2|gpt-image-2.5-sunburst"])
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            fb = B.make_backend("auto", codex_bin="definitely-not-a-codex-binary")
            self.assertEqual(fb.attempts, [])
            self.assertEqual(len(fb.notes), 2)
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "codex"
            fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            with mock.patch.dict(os.environ, env, clear=True):
                fb = B.make_backend("auto", codex_bin=str(fake))
                self.assertEqual([l for l, _ in fb.attempts], ["codex"])


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = main(argv)
        finally:
            sys.stdout = old
        return rc, buf.getvalue()

    def test_generate_dry_run(self):
        rc, out = self._run(["generate", "--dry-run", "-n", "2", "--seed", "5", "--age", "20s", "--gender", "male"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("Use case: photorealistic-natural"), 2)
        self.assertIn("남성", out)

    def test_presets(self):
        rc, out = self._run(["presets"])
        self.assertEqual(rc, 0)
        self.assertIn("korean_natural", out)

    def test_sheet_with_manual_pupils(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "face.png"
            _face_image().save(src)
            rc, out = self._run([
                "sheet", str(src), "--out-dir", str(tmp / "sheets"), "--landmarks", "manual", "--pupils", "300,480,500,480",
                "--layout", "both", "--guides", "--face-shape", "round", "--no-pdf", "--save-landmarks", "--dpi", "150",
            ])
            self.assertEqual(rc, 0, out)
            self.assertTrue((tmp / "sheets" / "face_A4.png").exists())
            self.assertTrue(list((tmp / "sheets").glob("face_browzone*.png")))
            lm = json.loads((tmp / "sheets" / "face_landmarks.json").read_text(encoding="utf-8"))
            self.assertEqual(lm["source"], "manual")

    def test_generate_manual_backend_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._run(["generate", "-n", "2", "--seed", "1", "--backend", "manual", "--out-dir", tmp, "--landmarks", "none"])
            self.assertEqual(rc, 0, out)
            manifest = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["faces"]), 2)
            self.assertTrue(all(f["pending"] for f in manifest["faces"]))
            self.assertEqual(len(list(Path(tmp).glob("*.prompt.txt"))), 2)

    def test_restyle_manual_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "photo.jpg"
            _face_image(1000, 1000).save(src, quality=90)
            rc, out = self._run([
                "restyle", str(src), "--backend", "manual", "--styles", "straight,feathered", "--out-dir", str(tmp / "r"),
                "--landmarks", "manual", "--pupils", "400,450,600,450",
            ])
            self.assertEqual(rc, 0, out)
            self.assertTrue((tmp / "r" / "00_original.png").exists())
            self.assertTrue((tmp / "r" / "mask.png").exists())
            self.assertTrue((tmp / "r" / "mask_api.png").exists())
            self.assertTrue((tmp / "r" / "01_straight_match_hair.prompt.txt").exists())
            manifest = json.loads((tmp / "r" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["styles"], ["straight", "feathered"])
            self.assertIsNotNone(manifest["landmarks"])
            with Image.open(tmp / "r" / "00_original.png") as im:
                self.assertEqual(im.size, (1000, 1000))  # the photo itself is kept as is
            with Image.open(tmp / "r" / "00_face_tile.png") as im:
                self.assertEqual(im.size, (1024, 1536))  # the model edits the face tile

    @unittest.skipIf(os.name == "nt", "fake executable needs a POSIX shell")
    def test_generate_auto_backend_falls_back_to_api(self):
        png = io.BytesIO()
        Image.new("RGB", (16, 24), (9, 9, 9)).save(png, format="PNG")
        b64 = base64.b64encode(png.getvalue()).decode()

        class Item:
            b64_json = b64
            url = None

        class Resp:
            data = [Item()]
            usage = None

        class Images:
            def generate(self, **kw):
                if kw["model"] != "gpt-image-1-mini":
                    raise LIMIT0
                return Resp()

        class Client:
            images = Images()

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fake = tmp / "codex"
            fake.write_text("#!/bin/sh\ncat >/dev/null\necho \"ERROR: You've hit your usage limit. Try again at Sep 15th\" >&2\nexit 1\n", encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}), \
                 mock.patch.object(B.OpenAIBackend, "client", lambda self: Client()):
                rc, out = self._run(["generate", "-n", "2", "--seed", "1", "--backend", "auto", "--codex-bin", str(fake),
                                     "--out-dir", str(tmp / "o"), "--landmarks", "none", "--no-sheet"])
            self.assertEqual(rc, 0, out)
            manifest = json.loads((tmp / "o" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["backend"], "auto")
            faces = manifest["faces"]
            self.assertEqual([(f["backend"], f["model"]) for f in faces], [("api", "gpt-image-1-mini")] * 2)
            self.assertEqual(len(faces[0]["fallback"]), 5)  # codex + 2.5 + 2 + 1.5 + 1
            self.assertTrue(faces[0]["fallback"][0].startswith("codex: Codex 사용 한도 소진"))
            self.assertNotIn("fallback", faces[1])  # unavailable attempts are not retried
            self.assertIn("사용 불가", out)
            self.assertTrue((tmp / "o" / faces[1]["image"].split("/")[-1]).exists())

    def test_generate_auto_backend_without_anything_fails_cleanly(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            rc, out = self._run(["generate", "-n", "1", "--seed", "1", "--backend", "auto", "--codex-bin", "definitely-not-a-codex-binary",
                                 "--out-dir", tmp, "--landmarks", "none", "--no-sheet"])
            self.assertEqual(rc, 1)
            manifest = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("사용할 수 있는 백엔드가 없습니다", manifest["faces"][0]["error"])

    def test_restyle_auto_backend_uses_codex_variant(self):
        # codex succeeds -> it must receive the guide image and the guide prompt, no mask.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            home = tmp / "home"
            (home / "generated_images").mkdir(parents=True)
            fake = tmp / "codex"
            fake.write_text(textwrap.dedent(f"""\
                #!/usr/bin/env python3
                import sys, pathlib
                from PIL import Image
                args = sys.argv[1:]
                prompt = sys.stdin.read()
                home = pathlib.Path({str(home)!r})
                with open(home / "args.txt", "a") as fh:
                    fh.write(" ".join(args) + "\\n")
                with open(home / "prompt.txt", "a") as fh:
                    fh.write(prompt + "\\n=====\\n")
                src = pathlib.Path(args[args.index("-i") + 1])
                with Image.open(src) as im:
                    im.convert("RGB").save(home / "generated_images" / "edit.png")
                """), encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            src = tmp / "photo.png"
            _face_image(1000, 1000).save(src)
            env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
            env["CODEX_HOME"] = str(home)
            with mock.patch.dict(os.environ, env, clear=True):
                rc, out = self._run(["restyle", str(src), "--backend", "auto", "--codex-bin", str(fake), "--styles", "straight",
                                     "--out-dir", str(tmp / "r"), "--landmarks", "manual", "--pupils", "400,450,600,450", "--sheet", "none",
                                     "--no-composite"])
            self.assertEqual(rc, 0, out)
            first_call = (home / "args.txt").read_text().splitlines()[0]
            self.assertEqual(first_call.count("-i "), 2)  # prepared image + red region guide
            self.assertIn("Image 2: region guide", (home / "prompt.txt").read_text())
            manifest = json.loads((tmp / "r" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["variants"][0]["backend"], "codex")


    @unittest.skipIf(os.name == "nt", "fake executable needs a POSIX shell")
    def test_restyle_tile_flow_aligns_and_pastes_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            home = tmp / "home"
            (home / "generated_images").mkdir(parents=True)
            fake = tmp / "codex"
            fake.write_text(textwrap.dedent(f"""\
                #!/usr/bin/env python3
                import sys, pathlib
                from PIL import Image, ImageChops
                args = sys.argv[1:]
                sys.stdin.read()
                home = pathlib.Path({str(home)!r})
                with open(home / "args.txt", "a") as fh:
                    fh.write(" ".join(args) + "\\n")
                src = pathlib.Path(args[args.index("-i") + 1])
                with Image.open(src) as im:
                    ImageChops.offset(im.convert("RGB"), 30, 20).save(home / "generated_images" / "edit.png")
                """), encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            src = tmp / "photo.jpg"
            photo = Image.new("RGB", (1000, 1000), (225, 195, 175))
            ImageDraw.Draw(photo).rectangle((0, 900, 1000, 1000), fill=(20, 40, 60))  # a stripe far from the face
            photo.save(src, quality=95)
            lm_full = L.from_pupils(1000, 1000, (400.0, 450.0), (600.0, 450.0))
            lm_tile = M.landmarks_to_tile(lm_full, M.face_tile_box(lm_full, (1000, 1000)), (1024, 1536))

            def fake_detect(args_, image, path=None):
                # the fake codex shifts the tile by (30, 20): report the tile landmarks moved by the same amount
                return lm_tile.translated(30, 20)

            env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
            env["CODEX_HOME"] = str(home)
            from browlab import cli as C
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(C, "_detect_plain", fake_detect):
                rc, out = self._run(["restyle", str(src), "--backend", "auto", "--codex-bin", str(fake), "--styles", "straight",
                                     "--out-dir", str(tmp / "r"), "--landmarks", "manual", "--pupils", "400,450,600,450", "--sheet", "none"])
            self.assertEqual(rc, 0, out)
            with Image.open(tmp / "r" / "00_face_tile.png") as tile:
                self.assertEqual(tile.size, (1024, 1536))
            first_call = (home / "args.txt").read_text().splitlines()[0]
            self.assertIn("00_face_tile.png", first_call)
            self.assertIn("mask_guide.png", first_call)
            manifest = json.loads((tmp / "r" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["tile"]["size"], [1024, 1536])
            v = manifest["variants"][0]
            self.assertEqual(v["align"]["status"], "warped", v["align"])
            self.assertTrue(v["aligned"])
            self.assertIn("정렬 보정", out)
            with Image.open(v["composited"]) as comp, Image.open(tmp / "r" / "00_original.png") as base:
                self.assertEqual(comp.size, (1000, 1000))
                for xy in ((500, 950), (5, 5), (990, 500)):  # untouched outside the brow mask (JPEG-decoded values)
                    self.assertEqual(comp.getpixel(xy), base.getpixel(xy), xy)

    def test_restyle_dry_run_and_bad_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "photo.png"
            _face_image(1000, 1000).save(src)
            rc, out = self._run(["restyle", str(src), "--dry-run", "--styles", "random:2", "--seed", "3", "--out-dir", str(tmp / "d"), "--landmarks", "none"])
            self.assertEqual(rc, 0, out)
            self.assertEqual(out.count("Use case: identity-preserve"), 2)
            with self.assertRaises(SystemExit):
                self._run(["restyle", str(src), "--dry-run", "--styles", "nope", "--out-dir", str(tmp / "d2"), "--landmarks", "none"])

    def test_edit_alignment_check(self):
        import argparse
        from browlab import cli as C

        args = argparse.Namespace(landmarks="auto", codex_bin="codex", landmark_model=None, no_download=True, align_max=0.45)
        orig = L.from_pupils(1000, 1500, (400.0, 600.0), (600.0, 600.0))
        img = Image.new("RGB", (1000, 1500))
        saved = C.L.detect
        try:
            C.L.detect = lambda *a, **k: L.from_pupils(1000, 1500, (402.0, 603.0), (602.0, 603.0))
            out, status, info, lm = C._align_edit(args, img, orig, (1000, 1500))
            self.assertEqual(status, "aligned", info)
            self.assertIs(out, img)
            C.L.detect = lambda *a, **k: L.from_pupils(1000, 1500, (400.0, 520.0), (600.0, 520.0))  # eyes moved up 40% IPD
            out, status, info, lm = C._align_edit(args, img, orig, (1000, 1500))
            self.assertEqual(status, "warped", info)  # within --align-max: corrected instead of skipped
            self.assertIsNotNone(out)
            C.L.detect = lambda *a, **k: L.from_pupils(1000, 1500, (400.0, 480.0), (600.0, 480.0))  # moved up 60% IPD
            out, status, info, lm = C._align_edit(args, img, orig, (1000, 1500))
            self.assertEqual(status, "misaligned", info)
            self.assertIsNone(out)
            C.L.detect = lambda *a, **k: L.from_pupils(1000, 1500, (380.0, 600.0), (620.0, 600.0))  # zoomed 20%
            out, status, info, lm = C._align_edit(args, img, orig, (1000, 1500))
            self.assertEqual(status, "warped", info)
            C.L.detect = lambda *a, **k: L.from_pupils(1000, 1500, (350.0, 600.0), (650.0, 600.0))  # zoomed 50%
            out, status, info, lm = C._align_edit(args, img, orig, (1000, 1500))
            self.assertEqual(status, "misaligned", info)
            C.L.detect = lambda *a, **k: None  # no face found -> do not block compositing
            out, status, info, lm = C._align_edit(args, img, orig, (1000, 1500))
            self.assertEqual(status, "unchecked")
            self.assertIsNotNone(out)
        finally:
            C.L.detect = saved

    def test_explain_errors(self):
        from browlab import cli as C
        codex = B.GenerationError("codex exec finished but no image was produced.\nrc=1\nstderr: ERROR: You've hit your usage limit. try again at Sep 15th")
        self.assertIn("자동", C._explain_api_error(codex))
        self.assertIn("조직", C._explain_api_error(LIMIT0))
        self.assertEqual(C._explain_api_error(RuntimeError("boom")), "")

    def test_calibrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._run(["calibrate", "--out-dir", tmp, "--no-pdf"])
            self.assertEqual(rc, 0, out)
            self.assertTrue((Path(tmp) / "calibration_A4.png").exists())


class MediapipeTests(unittest.TestCase):
    """Runs only when a real face photo is supplied via BROWLAB_TEST_FACE."""

    def test_detect_real_face(self):
        photo = os.environ.get("BROWLAB_TEST_FACE")
        if not photo or not Path(photo).is_file():
            self.skipTest("set BROWLAB_TEST_FACE=/path/to/face.jpg to run")
        try:
            import mediapipe  # noqa: F401
        except ImportError:
            self.skipTest("mediapipe not installed")
        with Image.open(photo) as im:
            lm = L.detect_mediapipe(im, download=False)
        self.assertIsNotNone(lm)
        self.assertGreater(lm.ipd_px, 5)
        self.assertLess(lm.brow_top_y, lm.eye_top_y)
        mask = M.brow_region_mask(lm)
        self.assertIsNotNone(mask.getbbox())


if __name__ == "__main__":
    unittest.main()
