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

    def test_shape_conditions_ask_for_the_shape_not_for_faint_brows(self):
        shapes = [k for k, v in P.BROW_CONDITIONS.items() if v.shape]
        self.assertEqual(shapes, ["arch", "high_arch", "straight_flat", "half", "thin", "thick", "spread"])
        for key in shapes:
            text = PR.build_face_prompt(PR.FaceSpec(30, "female", "oval", key, "korean"))
            self.assertIn(P.BROW_CONDITIONS[key].prompt, text)
            self.assertIn("natural, untouched shape", text)
            self.assertNotIn("Do not draw full", text)          # a thick or arched brow must not be told to stay faint
            self.assertIn("no tattooed or drawn-on brows", text)
        # the sparse conditions keep the original instruction
        faint = PR.build_face_prompt(PR.FaceSpec(30, "female", "oval", "sparse", "korean"))
        self.assertIn("Do not draw full", faint)
        self.assertNotIn("natural, untouched shape", faint)

    def test_restyle_prompt_content(self):
        text = PR.build_restyle_prompt("feathered", "ash_brown", with_guide_image=True)
        self.assertIn(P.BROW_STYLES["feathered"].prompt, text)
        self.assertIn("cool ash brown", text)
        self.assertIn("Image 2: region guide", text)
        self.assertIn("change only the eyebrows", text)
        self.assertNotIn("Image 2", PR.build_restyle_prompt("straight"))

    def test_restyle_prompt_pins_the_brow_height(self):
        """Models lift the brows onto the forehead unless the height is stated."""
        text = PR.build_restyle_prompt("soft_arch", "dark_brown")
        self.assertIn("Eyebrow height (most important)", text)
        self.assertIn("keep the new brows at exactly the height of the existing ones", text)
        self.assertIn("lower edge of each brow must stay on the lower edge of the existing brow", text)
        self.assertIn("gap between the upper eyelid and the brow must stay exactly as it is", text)
        self.assertIn("Never move the brows up", text)
        self.assertIn("raising the brows higher on the forehead", text)      # in Avoid
        self.assertIn("horizontal only", text)                                # the nostril lines are horizontal guides
        up = PR.build_restyle_prompt("soft_arch", "dark_brown", height_key="slight_up")
        self.assertIn("raise the brows very slightly, by at most 2-3 mm", up)
        self.assertNotIn("keep the new brows at exactly the height", up)
        self.assertIn("Never move the brows up", up)                          # the hard limits still apply
        with self.assertRaises(KeyError):
            PR.build_restyle_prompt("soft_arch", "dark_brown", height_key="sky")

    def test_restyle_prompt_keeps_the_brows_from_going_opaque(self):
        """Shape and hue alone leave the model free to paint a solid dark block."""
        text = PR.build_restyle_prompt("korean_natural", "match_hair")
        self.assertIn("Density and opacity", text)
        self.assertIn("no darker than the person's own brow hair", text)     # the "natural" default
        self.assertIn("leave bare skin visible between them", text)
        self.assertIn("No outline, no stencil edge, no uniform block of colour", text)
        for phrase in ("a solid opaque block of colour", "a hard painted or stencilled outline",
                       "brows darker than the person's own hair", "a glossy freshly-tattooed look"):
            self.assertIn(phrase, text)                                       # all in Avoid
        self.assertNotIn("semi-permanent makeup consultation", text)          # invited the salon look
        # the brow is corrected, not replaced: "redraw" told the model to start over
        self.assertNotIn("redraw ONLY the two eyebrows", text)
        self.assertIn("groom the eyebrows the person in Image 1 already has", text)
        self.assertIn("keep the hairs that form the brow exactly where they are", text)
        self.assertNotIn("redraw them", text)                      # nothing tells it to start over any more
        self.assertIn("shaving off or covering the body of the brow", text)
        # grooming, not pure addition: the strays get tidied away too
        self.assertIn("stray hairs sitting above, below and beyond the brow line are plucked away", text)
        self.assertIn("fill the thin and bare patches", text)
        self.assertIn("extend the brow only where it is genuinely missing", text)
        self.assertIn("thicker or heavier than the one in the photo", text)     # the "heavy makeup" failure
        self.assertIn("fully healed and settled", text)
        soft = PR.build_restyle_prompt("korean_natural", "match_hair", intensity_key="soft")
        bold = PR.build_restyle_prompt("korean_natural", "match_hair", intensity_key="bold")
        self.assertIn("barely noticeable", soft)
        self.assertIn("one shade deeper", bold)
        self.assertIn("never a solid shape", bold)                            # even "bold" stays hair, not fill
        self.assertNotIn("barely noticeable", bold)
        # the intensity block never displaces the height block
        for t in (text, soft, bold):
            self.assertIn("Eyebrow height (most important)", t)
        with self.assertRaises(KeyError):
            PR.build_restyle_prompt("korean_natural", intensity_key="very_dark")

    def test_dense_style_prompts_do_not_ask_for_a_filled_shape(self):
        self.assertIn("rather than a filled block", P.BROW_STYLES["bold_thick"].prompt)
        self.assertIn("bare skin left between the strokes", P.BROW_STYLES["feathered"].prompt)
        self.assertNotIn("dense", P.BROW_STYLES["bold_thick"].prompt)

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
        self.assertGreater(bbox[1], lm.brow_top_y - 0.12 * lm.ipd_px)  # ...but hugs them, no forehead band
        self.assertLess(bbox[1], lm.brow_top_y)
        self.assertEqual(mask.size, (800, 1200))
        # the brow-shaped mask is much smaller than the old bounding box
        box = M.brow_region_mask(lm, shape="box", pad_side=0.16, pad_up=0.40, pad_down=0.12)
        area = lambda im: sum(1 for v in im.getdata() if v)
        self.assertLess(area(mask), 0.55 * area(box))  # hugging the brow is far smaller than the old forehead box
        self.assertLess(box.getbbox()[3], lm.eye_top_y)
        # the middle of the forehead between the brows stays untouched
        cx, top_y = int(lm.eye_center[0]), int(lm.brow_top_y - 0.05 * lm.ipd_px)
        self.assertEqual(mask.getpixel((cx, top_y)), 0)
        # every brow polygon point is inside the mask
        for x, y in lm.right_brow + lm.left_brow:
            self.assertEqual(mask.getpixel((int(x), int(y))), 255)

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
        for ch in fixed.getpixel((200, 140)):
            self.assertAlmostEqual(ch, 150, delta=2)
        # nothing to do when the ring already matches
        same = M.match_tone(original, original, mask)
        for ch in same.getpixel((200, 140)):
            self.assertAlmostEqual(ch, 150, delta=1)

    def test_match_tone_follows_uneven_lighting(self):
        """A cast that varies across the face: one global offset cannot fix it, a local field can."""
        import numpy as np

        w = h = 400
        ramp = np.linspace(120, 190, w, dtype=np.float32)[None, :, None].repeat(h, 0).repeat(3, 2)
        original = Image.fromarray(ramp.astype(np.uint8))
        edited = Image.fromarray(np.clip(ramp + np.linspace(-30, 30, w, dtype=np.float32)[None, :, None], 0, 255).astype(np.uint8))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rectangle((60, 150, 340, 230), fill=255)
        fixed = np.asarray(M.match_tone(edited, original, mask), np.float32)
        inside = np.asarray(mask) > 128
        before = float(np.abs(np.asarray(edited, np.float32)[inside] - ramp[inside]).mean())
        after = float(np.abs(fixed[inside] - ramp[inside]).mean())
        self.assertGreater(before, 10.0)
        self.assertLess(after, before / 4.0, f"before {before:.2f} after {after:.2f}")
        self.assertLess(after, 3.0, f"before {before:.2f} after {after:.2f}")

    def test_feather_scales_with_the_mask(self):
        thin = Image.new("L", (400, 400), 0)
        ImageDraw.Draw(thin).rectangle((100, 190, 300, 210), fill=255)
        thick = Image.new("L", (400, 400), 0)
        ImageDraw.Draw(thick).rectangle((100, 100, 300, 300), fill=255)
        self.assertEqual(M._mask_band_px(thin), 21)
        self.assertLess(M.feather_for(thin), M.feather_for(thick))
        self.assertGreaterEqual(M.feather_for(thin), 3)          # never a hard edge
        self.assertEqual(M.feather_for(Image.new("L", (10, 10), 0)), 3)  # empty mask

    def test_moves_never_leave_a_black_strip_behind(self):
        """PIL fills what a transform vacates with black; inside the mask that is a smear."""
        img = Image.new("RGB", (80, 80), (60, 50, 45))
        back = Image.new("RGB", (80, 80), (205, 175, 155))          # the original tile

        bare = M.shift_image(img, 0, 20)
        self.assertLess(bare.getpixel((40, 5))[0], 20)              # vacated strip: black

        filled = M.shift_image(img, 0, 20, background=back)
        self.assertGreater(filled.getpixel((40, 5))[0], 180)        # ...now the original photo
        self.assertEqual(filled.getpixel((40, 60)), (60, 50, 45))   # the moved content is untouched

        sim = M.Similarity(scale=1.0, angle_deg=12.0, tx=0.0, ty=0.0, shift_frac=0.0, scale_ratio=1.0)
        dark = lambda im: sum(1 for p in im.convert("L").getdata() if p < 20)
        self.assertGreater(dark(M.warp_similarity(img, sim, img.size)), 100)          # a black wedge
        self.assertEqual(dark(M.warp_similarity(img, sim, img.size, background=back)), 0)

    def test_brow_baseline_and_shift(self):
        lm = _pupil_landmarks()
        base = M.brow_baseline(lm)
        lower = [p[1] for p in lm.right_brow[len(lm.right_brow) // 2:]]
        self.assertIsNotNone(base)
        self.assertGreater(base, lm.brow_top_y)            # the baseline is the lower edge, not the top
        self.assertLess(base, lm.eye_top_y)
        self.assertAlmostEqual(base, (sum(lower) * 2) / (len(lower) * 2), delta=1)
        blank = L.from_pupils(100, 100, (30.0, 50.0), (70.0, 50.0))
        blank.right_brow = []
        blank.left_brow = []
        self.assertIsNone(M.brow_baseline(blank))
        img = Image.new("RGB", (60, 60), "black")
        img.putpixel((30, 10), (255, 0, 0))
        moved = M.shift_image(img, 0, 12)
        self.assertGreater(moved.getpixel((30, 22))[0], 200)
        self.assertLess(moved.getpixel((30, 10))[0], 60)
        self.assertEqual(M.shift_image(img, 0, 0).mode, img.mode)

    def test_by_default_the_model_brow_is_used_as_drawn(self):
        """Overlaying the original brow stacks two brows wherever the new one is thinner."""
        base = Image.new("RGB", (120, 120), (200, 170, 150))
        ImageDraw.Draw(base).rectangle((20, 60, 100, 90), fill=(40, 30, 25))     # a tall original brow
        edited = Image.new("RGB", (120, 120), (200, 170, 150))
        ImageDraw.Draw(edited).rectangle((20, 60, 100, 74), fill=(40, 30, 25))   # the model drew it thinner
        mask = Image.new("L", (120, 120), 0)
        ImageDraw.Draw(mask).rectangle((10, 50, 110, 100), fill=255)

        plain = M.composite_brows(base, edited, mask, feather_px=0)
        self.assertGreater(plain.getpixel((60, 85))[0], 170)      # below the new brow: skin, as the model drew it
        self.assertLess(plain.getpixel((60, 66))[0], 100)         # the new brow itself is there

        overlaid = M.composite_brows(base, edited, mask, feather_px=0, keep_hair=True)
        self.assertLess(overlaid.getpixel((60, 85))[0], 100)      # the old brow hangs on below: two brows
        self.assertEqual(plain.getpixel((60, 20)), base.getpixel((60, 20)))   # outside the mask: untouched either way

    def test_keep_hair_never_loses_an_existing_brow_hair(self):
        """The real procedure adds pigment into the gaps; it cannot remove a hair."""
        base = Image.new("RGB", (80, 80), (200, 170, 150))       # skin
        ImageDraw.Draw(base).line([(10, 40), (70, 40)], fill=(40, 30, 25), width=5)   # an existing brow hair
        edited = Image.new("RGB", (80, 80), (200, 170, 150))     # the model erased it...
        ImageDraw.Draw(edited).line([(10, 55), (70, 55)], fill=(40, 30, 25), width=5)  # ...and drew its own lower down
        mask = Image.new("L", (80, 80), 0)
        ImageDraw.Draw(mask).rectangle((5, 25, 75, 70), fill=255)

        replaced = M.composite_brows(base, edited, mask, feather_px=0, keep_hair=False)
        self.assertGreater(replaced.getpixel((40, 40))[0], 150)   # the original hair is gone
        self.assertLess(replaced.getpixel((40, 55))[0], 90)       # the new one is there

        kept = M.composite_brows(base, edited, mask, feather_px=0, keep_hair=True)
        self.assertLess(kept.getpixel((40, 40))[0], 90)           # the original hair survived
        self.assertLess(kept.getpixel((40, 55))[0], 90)           # ...and the new one still came through
        self.assertEqual(kept.getpixel((40, 10)), base.getpixel((40, 10)))  # outside the mask: untouched

        # strength dials the addition down without ever touching the original hair
        half = M.composite_brows(base, edited, mask, feather_px=0, keep_hair=True, strength=0.5)
        self.assertLess(half.getpixel((40, 40))[0], 90)           # existing hair stays fully dark
        self.assertGreater(half.getpixel((40, 55))[0], kept.getpixel((40, 55))[0])   # the addition is lighter
        self.assertLess(half.getpixel((40, 55))[0], base.getpixel((40, 55))[0])      # ...but still visible
        # strength is clamped, not trusted
        self.assertEqual(M.composite_brows(base, edited, mask, feather_px=0, strength=9).tobytes(),
                         M.composite_brows(base, edited, mask, feather_px=0, strength=1.0).tobytes())

    def test_near_px_blocks_a_second_eyebrow_floating_above_the_real_one(self):
        """Keeping the original hair only works if the addition lands on it, not above it."""
        base = Image.new("RGB", (120, 120), (200, 170, 150))
        ImageDraw.Draw(base).rectangle((20, 70, 100, 82), fill=(40, 30, 25))     # the real brow
        edited = base.copy()
        ImageDraw.Draw(edited).rectangle((20, 30, 100, 42), fill=(40, 30, 25))   # a second brow, 28px higher
        ImageDraw.Draw(edited).rectangle((20, 64, 100, 70), fill=(40, 30, 25))   # ...and a little fill on the real one
        mask = Image.new("L", (120, 120), 0)
        ImageDraw.Draw(mask).rectangle((10, 20, 110, 95), fill=255)

        loose = M.composite_brows(base, edited, mask, feather_px=0, keep_hair=True)
        self.assertLess(loose.getpixel((60, 36))[0], 100)        # the floating brow comes through

        tight = M.composite_brows(base, edited, mask, feather_px=0, keep_hair=True, near_px=6)
        self.assertGreater(tight.getpixel((60, 36))[0], 170)     # ...and is rejected: too far from any real hair
        self.assertLess(tight.getpixel((60, 66))[0], 110)        # the fill touching the real brow is kept
        self.assertLess(tight.getpixel((60, 76))[0], 100)        # the real brow itself is untouched

    def test_tidy_removes_the_strays_but_keeps_the_brow_body(self):
        """Grooming is not only additive: the scattered hairs get plucked away too."""
        base = Image.new("RGB", (140, 140), (200, 170, 150))
        d = ImageDraw.Draw(base)
        d.rectangle((20, 70, 120, 86), fill=(40, 30, 25))          # the brow body
        d.line((30, 40, 38, 46), fill=(40, 30, 25), width=2)       # a stray hair, 24px above it
        d.line((95, 104, 103, 110), fill=(40, 30, 25), width=2)    # another below it
        edited = Image.new("RGB", (140, 140), (200, 170, 150))     # the model: clean skin...
        ImageDraw.Draw(edited).rectangle((20, 68, 125, 86), fill=(40, 30, 25))   # ...and a tidy brow
        mask = Image.new("L", (140, 140), 0)
        ImageDraw.Draw(mask).rectangle((10, 30, 130, 120), fill=255)

        keep_all = M.composite_brows(base, edited, mask, feather_px=0, keep_hair=True)
        self.assertLess(keep_all.getpixel((34, 43))[0], 110)        # every stray survives
        self.assertLess(keep_all.getpixel((99, 107))[0], 110)

        tidied = M.composite_brows(base, edited, mask, feather_px=0, keep_hair=True, tidy_px=4)
        self.assertGreater(tidied.getpixel((34, 43))[0], 165)       # the strays are plucked
        self.assertGreater(tidied.getpixel((99, 107))[0], 165)
        self.assertLess(tidied.getpixel((70, 78))[0], 100)          # the brow body is still there
        self.assertLess(tidied.getpixel((123, 78))[0], 100)         # ...and the extended tail came through
        self.assertEqual(tidied.getpixel((70, 10)), base.getpixel((70, 10)))    # outside the mask: untouched

    def test_mask_opens_outward_for_the_tail_not_upward(self):
        """MediaPipe traces ~46mm where a brow is 50-55mm, and designs lengthen the tail."""
        lm = _pupil_landmarks()
        tight = M.brow_region_mask(lm, pad_tail=0.0, pad_head=0.0)
        wide = M.brow_region_mask(lm)
        t, w = tight.getbbox(), wide.getbbox()
        self.assertLess(w[0], t[0])                      # opened to the left...
        self.assertGreater(w[2], t[2])                   # ...and to the right
        self.assertEqual((w[1], w[3]), (t[1], t[3]))     # but not one pixel up or down
        reach = min(t[0] - w[0], w[2] - t[2])
        self.assertGreater(reach, 0.10 * lm.ipd_px)      # roughly 8mm on a 63mm face
        self.assertLess(w[3], lm.eye_top_y)              # still never over the eyes

    def test_core_mask_judges_each_brow_against_itself(self):
        """A sparse brow measured against a dense one is erased wholesale."""
        base = Image.new("RGB", (300, 140), (200, 170, 150))
        d = ImageDraw.Draw(base)
        d.rectangle((20, 60, 120, 84), fill=(30, 22, 18))            # a dense brow
        d.rectangle((180, 66, 280, 74), fill=(120, 100, 90))         # a much fainter, thinner one
        mask = Image.new("L", (300, 140), 0)
        ImageDraw.Draw(mask).rectangle((10, 40, 130, 110), fill=255)   # two separate blobs,
        ImageDraw.Draw(mask).rectangle((170, 40, 290, 110), fill=255)  # one per brow
        core = M.brow_core_mask(base, mask, 4)
        self.assertGreater(core.getpixel((70, 72)), 200)             # the dense brow is body
        self.assertGreater(core.getpixel((230, 70)), 200)            # ...and so is the faint one
        self.assertEqual(core.getpixel((150, 70)), 0)                # the gap between them is not

    def test_hair_proximity_reaches_further_along_the_brow_than_across_it(self):
        """Floating a second brow above is the worry; lengthening the tail is the goal."""
        base = Image.new("RGB", (260, 160), (200, 170, 150))
        ImageDraw.Draw(base).rectangle((80, 70, 180, 90), fill=(40, 30, 25))     # the brow
        mask = Image.new("L", (260, 160), 0)
        ImageDraw.Draw(mask).rectangle((20, 50, 240, 110), fill=255)             # the brow band
        near = M.hair_proximity(base, mask, 6)
        along = next(x - 180 for x in range(181, 240) if near.getpixel((x, 80)) < 128)
        across = next(70 - y for y in range(69, 50, -1) if near.getpixel((130, y)) < 128)
        self.assertGreater(along, 3 * across)                        # far further along the brow
        self.assertGreater(along, 15)                                # enough to lengthen a tail
        self.assertLess(across, 12)                                  # not enough to float a second brow

    def test_brow_core_mask_separates_the_body_from_the_strays(self):
        base = Image.new("RGB", (140, 140), (200, 170, 150))
        d = ImageDraw.Draw(base)
        d.rectangle((20, 70, 120, 86), fill=(40, 30, 25))
        d.line((30, 40, 38, 46), fill=(40, 30, 25), width=2)
        mask = Image.new("L", (140, 140), 0)
        ImageDraw.Draw(mask).rectangle((10, 30, 130, 120), fill=255)
        core = M.brow_core_mask(base, mask, 4)
        self.assertGreater(core.getpixel((70, 78)), 200)            # the body is core
        self.assertLess(core.getpixel((34, 43)), 60)                # an isolated stray is not
        self.assertEqual(core.getpixel((70, 10)), 0)                # never outside the mask
        bare = Image.new("RGB", (140, 140), (200, 170, 150))
        self.assertIs(M.brow_core_mask(bare, mask, 4), mask)        # no hair at all: nothing to separate

    def test_hair_proximity_gives_up_when_there_is_no_hair_to_sit_beside(self):
        """Sparse brows have nothing to anchor to, so the limit must not block everything."""
        mask = Image.new("L", (120, 120), 0)
        ImageDraw.Draw(mask).rectangle((10, 20, 110, 95), fill=255)
        bare = Image.new("RGB", (120, 120), (200, 170, 150))
        self.assertIs(M.hair_proximity(bare, mask, 6), mask)                     # nothing there: mask unchanged
        full = bare.copy()
        ImageDraw.Draw(full).rectangle((20, 60, 100, 85), fill=(40, 30, 25))
        limited = M.hair_proximity(full, mask, 6)
        self.assertIsNot(limited, mask)
        import numpy as np
        self.assertLess(float(np.asarray(limited).sum()), float(np.asarray(mask).sum()))  # a real brow narrows it
        self.assertEqual(limited.size, mask.size)

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

    def test_grow_mm_adds_exactly_that_much_face_on_every_side(self):
        """The practitioner asks for "a centimetre more each way"; that is what prints."""
        lm, img = _pupil_landmarks(), _face_image()
        plain = S.SheetOptions(ipd_mm=63.0)
        base_w = S.face_width_mm(lm, plain.ipd_mm)
        base_h = S.face_height_mm(lm, plain.ipd_mm)
        for grow in (5.0, 10.0):
            opts = S.SheetOptions(ipd_mm=63.0, grow_mm=grow)
            canvas, page = S.compose_face_sheet(img, lm, opts)
            ppm = S.px_per_mm(opts.dpi)
            width = abs(page.left_cheek[0] - page.right_cheek[0]) / ppm
            height = abs(page.chin[1] - page.forehead_top[1]) / ppm
            self.assertAlmostEqual(width, base_w + 2 * grow, delta=0.6)   # grow mm on the left AND the right
            self.assertAlmostEqual(height / base_h, width / base_w, places=2)  # proportions untouched
            self.assertEqual(canvas.size, (2480, 3508))
            self.assertLess(page.chin[1], canvas.size[1])                 # the chin is still on the paper
            self.assertGreater(page.brow_top_y, 0)                        # ...and so is the working area
            self.assertIn("배율", S.size_note(lm, opts))
        # life size stays the default, and says nothing about a scale
        self.assertAlmostEqual(S.enlargement(lm, plain), 1.0)
        self.assertNotIn("배율", S.size_note(lm, plain))
        self.assertIn(f"{base_w:.0f}", S.size_note(lm, plain))   # the header states the size a ruler will find
        # print_scale is the raw multiplier and multiplies with grow_mm
        both = S.SheetOptions(ipd_mm=63.0, grow_mm=10.0, print_scale=1.1)
        self.assertAlmostEqual(S.enlargement(lm, both), 1.1 * (base_w + 20) / base_w, places=4)
        self.assertAlmostEqual(S.enlargement(None, S.SheetOptions(print_scale=1.2, grow_mm=10)), 1.2)

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
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsInstance(B.make_backend("codex"), B.CodexBackend)      # no key: Codex only
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            self.assertIsInstance(B.make_backend("codex"), B.FallbackBackend)   # key present: Codex first, then the API
            self.assertIsInstance(B.make_backend("codex-only"), B.CodexBackend)
        api = B.make_backend("api")
        self.assertIsInstance(api, B.FallbackBackend)  # API-only ladder, no Codex step
        self.assertEqual(api.name, "api")
        self.assertEqual([l for l, _ in api.attempts], [
            "api:gpt-image-2.5-flare|gpt-image-2.5-sunburst", "api:gpt-image-2", "api:gpt-image-1.5", "api:gpt-image-1", "api:gpt-image-1-mini",
        ])
        self.assertEqual([l for l, _ in B.make_backend("api", model="gpt-image-1.5").attempts], ["api:gpt-image-1.5", "api:gpt-image-1", "api:gpt-image-1-mini"])
        self.assertEqual([l for l, _ in B.make_backend("api", model="gpt-image-2.5-sunburst").attempts][0], "api:gpt-image-2.5-sunburst")
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


class ModelCheckTests(unittest.TestCase):
    """check_models answers "which image models can this key use?" without guessing."""

    class _Api:
        def __init__(self, visible, generate_error=None):
            outer = self
            class Models:
                def retrieve(self, m):
                    if m not in visible:
                        raise RuntimeError("Error code: 404 - {'error': {'code': 'model_not_found'}}")
                    return {"id": m}
            class Usage:
                input_tokens = 20; output_tokens = 300; total_tokens = 320
                input_tokens_details = None
            class Images:
                def generate(self, **kw):
                    outer.calls.append(kw)
                    if generate_error:
                        raise generate_error
                    return type("R", (), {"data": [type("I", (), {"b64_json": None, "url": None})()], "usage": Usage()})()
            self.models = Models(); self.images = Images(); self.calls = []

    def test_free_check_reports_visibility_only(self):
        api = self._Api({"gpt-image-1", "gpt-image-1-mini"})
        rows = B.check_models(client=api)
        self.assertEqual([r["model"] for r in rows], B.GENERATE_MODEL_CHAIN)
        by = {r["model"]: r for r in rows}
        self.assertEqual(by["gpt-image-2.5-flare"]["status"], "not_found")
        self.assertFalse(by["gpt-image-2.5-flare"]["usable"])
        self.assertEqual(by["gpt-image-1"]["status"], "visible")
        self.assertTrue(by["gpt-image-1"]["usable"])
        self.assertEqual(api.calls, [])                      # free: nothing was generated

    def test_probe_confirms_the_first_visible_model(self):
        api = self._Api(set(B.GENERATE_MODEL_CHAIN))
        rows = B.check_models(client=api, probe=True)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(len(api.calls), 1)                  # only the best model costs money
        self.assertEqual(api.calls[0]["size"], "1024x1024")
        self.assertEqual(api.calls[0]["quality"], "low")
        self.assertGreater(rows[0]["cost_usd"], 0)
        self.assertEqual(rows[1]["status"], "visible")       # the rest stay at the free answer

    def test_probe_walks_down_when_the_limit_is_zero(self):
        err = RuntimeError("Error code: 429 - rate_limit_exceeded (for limit gpt-image) ... Limit 0, Requested 1.")
        api = self._Api(set(B.GENERATE_MODEL_CHAIN), generate_error=err)
        rows = B.check_models(client=api, probe=True)
        self.assertTrue(all(r["status"] == "limit0" for r in rows), [r["status"] for r in rows])
        self.assertFalse(any(r["usable"] for r in rows))
        self.assertEqual(len(api.calls), len(B.GENERATE_MODEL_CHAIN))
        self.assertIn("조직 인증", rows[0]["label"])

    def test_missing_key_is_reported_per_model(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            rows = B.check_models()
        self.assertTrue(all(r["status"] == "auth" and not r["usable"] for r in rows))


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
            self.assertEqual([l for l, _ in pinned.attempts], ["api:gpt-image-2", "api:gpt-image-1.5", "api:gpt-image-1", "api:gpt-image-1-mini"])
            last = B.make_backend("auto", codex_bin="definitely-not-a-codex-binary", model="gpt-image-1-mini")
            self.assertEqual([l for l, _ in last.attempts], ["api:gpt-image-1-mini"])
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
                                     "--out-dir", str(tmp / "r"), "--landmarks", "manual", "--pupils", "400,450,600,450", "--sheet", "none"])
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
                                     "--out-dir", str(tmp / "r"), "--landmarks", "manual", "--pupils", "400,450,600,450", "--sheet", "none",
                                     "--composite"])
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

    def test_brow_height_fix_pulls_a_lifted_brow_back_down(self):
        """The model lifts the brow onto the forehead; the fix slides the edit back."""
        import argparse
        from browlab import cli as C

        lm = _pupil_landmarks()                                    # IPD 200; brow mask spans y 363..438
        args = argparse.Namespace(brow_align_max=0.35)             # limit 70px

        def face(brow_bottom):
            """A pale face with a dark brow bar whose LOWER edge sits at ``brow_bottom``."""
            im = Image.new("RGB", (800, 1200), (210, 180, 160))
            ImageDraw.Draw(im).rectangle((250, brow_bottom - 25, 550, brow_bottom), fill=(40, 30, 25))
            return im

        ref_img = face(430)
        out, info = C._brow_height_fix(args, face(390), lm, ref_img)   # drawn 40px too high
        self.assertAlmostEqual(info["brow_shift_px"], 40, delta=3)     # down, by the amount it was lifted
        self.assertGreater(info["brow_shift_ipd"], 0)
        self.assertGreater(out.getpixel((400, 375))[0], 150)           # the bar left its lifted place
        self.assertLess(out.getpixel((400, 420))[0], 120)              # ...and landed on the original line

        # the shift is capped, so a bad measurement cannot drag the face around
        tight = argparse.Namespace(brow_align_max=0.1)                 # limit 20px
        _, info = C._brow_height_fix(tight, face(390), lm, ref_img)
        self.assertAlmostEqual(info["brow_shift_px"], 20, delta=0.5)

        # already in place: no shift at all
        out, info = C._brow_height_fix(args, face(430), lm, ref_img)
        self.assertEqual(info["brow_shift_px"], 0.0)

        # nothing drawn in the brow band: say so instead of guessing
        blank = Image.new("RGB", (800, 1200), (210, 180, 160))
        out, info = C._brow_height_fix(args, blank, lm, blank)
        self.assertIn("찾지 못해", info["brow_align"])
        self.assertIs(out, blank)

    def test_height_fix_follows_the_pigment_not_the_landmarks(self):
        """MediaPipe's brow points fit a face model, so they hardly move when a brow is redrawn."""
        lm = _pupil_landmarks()
        mask = M.brow_region_mask(lm)
        def bar(bottom):
            im = Image.new("RGB", (800, 1200), (210, 180, 160))
            ImageDraw.Draw(im).rectangle((250, bottom - 20, 550, bottom), fill=(40, 30, 25))
            return im
        top_h, bot_h, mid_h = M.hair_span(bar(390), mask)
        top_l, bot_l, mid_l = M.hair_span(bar(430), mask)
        self.assertAlmostEqual(bot_l - bot_h, 40, delta=3)          # the measurement follows the drawn bar
        self.assertAlmostEqual(top_l - top_h, 40, delta=3)
        # the weighted centre drifts (the brow-shaped mask is narrower at the bottom),
        # which is why the height fix lines up lower edges rather than centres
        self.assertAlmostEqual(mid_l - mid_h, 40, delta=6)
        self.assertIsNone(M.hair_span(Image.new("RGB", (800, 1200), (210, 180, 160)), mask))

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
