"""Face specifications and prompt builders for BrowLab."""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Union

from . import presets as P


@dataclass
class FaceSpec:
    """One practice face to generate."""

    age: int
    gender: str
    face_shape: str
    brow_condition: str
    ethnicity: str
    seed: Optional[int] = None
    notes: str = ""

    # -- derived -----------------------------------------------------------
    @property
    def age_group(self) -> str:
        return P.age_group_of(self.age)

    @property
    def ipd_mm(self) -> float:
        return P.default_ipd_mm(self.gender, self.age)

    def label_ko(self) -> str:
        return " · ".join(
            [
                f"{self.age}세 {P.GENDERS[self.gender]['ko']}",
                P.ETHNICITIES[self.ethnicity].ko,
                P.FACE_SHAPES[self.face_shape].ko,
                P.BROW_CONDITIONS[self.brow_condition].ko,
            ]
        )

    def label_en(self) -> str:
        return " / ".join(
            [
                f"{self.age}y {self.gender}",
                self.ethnicity,
                f"{self.face_shape} face",
                self.brow_condition.replace("_", " "),
            ]
        )

    def slug(self) -> str:
        parts = [f"{self.gender}{self.age}", self.face_shape, self.brow_condition, self.ethnicity]
        if self.seed is not None:
            parts.append(f"s{self.seed}")
        return "_".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["age_group"] = self.age_group
        d["ipd_mm"] = self.ipd_mm
        d["label_ko"] = self.label_ko()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FaceSpec":
        return cls(
            age=int(d["age"]),
            gender=d["gender"],
            face_shape=d["face_shape"],
            brow_condition=d["brow_condition"],
            ethnicity=d["ethnicity"],
            seed=d.get("seed"),
            notes=d.get("notes", "") or "",
        )


# ---------------------------------------------------------------------------
# Random / resolved specs
# ---------------------------------------------------------------------------
def resolve_age(value: Union[str, int, None], rng: random.Random) -> int:
    if value is None or value == "random":
        group = rng.choice(list(P.AGE_GROUPS))
        lo, hi = P.AGE_GROUPS[group]
        return rng.randint(lo, hi)
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text in P.AGE_GROUPS:
        lo, hi = P.AGE_GROUPS[text]
        return rng.randint(lo, hi)
    if text.isdigit():
        return int(text)
    raise ValueError(f"unknown age value: {value!r} (use 10s..70s, a number, or random)")


def resolve_choice(value: Optional[str], table: Dict[str, Any], rng: random.Random, name: str) -> str:
    if value is None or value == "random":
        return rng.choice(list(table))
    if value in table:
        return value
    raise ValueError(f"unknown {name}: {value!r} (choose from {', '.join(table)} or random)")


def resolve_ethnicity(value: Optional[str], rng: random.Random) -> str:
    if value is None or value == "random":
        keys = list(P.ETHNICITIES)
        weights = [P.ETHNICITIES[k].weight for k in keys]
        return rng.choices(keys, weights=weights, k=1)[0]
    if value == "any":
        return rng.choice(list(P.ETHNICITIES))
    if value in P.ETHNICITIES:
        return value
    raise ValueError(f"unknown ethnicity: {value!r}")


def make_spec(
    rng: random.Random,
    *,
    age: Union[str, int, None] = None,
    gender: Optional[str] = None,
    face_shape: Optional[str] = None,
    brow_condition: Optional[str] = None,
    ethnicity: Optional[str] = None,
    notes: str = "",
    seed: Optional[int] = None,
) -> FaceSpec:
    return FaceSpec(
        age=resolve_age(age, rng),
        gender=resolve_choice(gender, P.GENDERS, rng, "gender"),
        face_shape=resolve_choice(face_shape, P.FACE_SHAPES, rng, "face shape"),
        brow_condition=resolve_choice(brow_condition, P.BROW_CONDITIONS, rng, "brow condition"),
        ethnicity=resolve_ethnicity(ethnicity, rng),
        seed=seed,
        notes=notes,
    )


def make_specs(count: int, seed: Optional[int] = None, **overrides: Any) -> List[FaceSpec]:
    """Build ``count`` specs. The same ``seed`` always yields the same list."""
    base = random.Random(seed)
    specs: List[FaceSpec] = []
    for _ in range(count):
        item_seed = base.randrange(1, 2**31 - 1)
        specs.append(make_spec(random.Random(item_seed), seed=item_seed, **overrides))
    return specs


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def _hair_description(spec: FaceSpec) -> str:
    grey = spec.age >= 55
    colour = "grey or white" if grey else "natural dark"
    if spec.gender == "female":
        return (
            f"{colour} hair pulled back tightly and tied away from the face so the whole forehead, "
            "the hairline and both eyebrows are completely uncovered"
        )
    return (
        f"short {colour} hair combed back away from the forehead so the whole forehead, "
        "the hairline and both eyebrows are completely uncovered"
    )


def build_face_prompt(spec: FaceSpec) -> str:
    shape = P.FACE_SHAPES[spec.face_shape]
    cond = P.BROW_CONDITIONS[spec.brow_condition]
    eth = P.ETHNICITIES[spec.ethnicity]
    noun = P.gender_noun(spec.gender, spec.age)
    lines = [
        "Use case: photorealistic-natural",
        "Asset type: life-size printed reference portrait for eyebrow tattoo (semi-permanent makeup) design practice",
        (
            f"Primary request: photorealistic straight-on studio portrait of a {spec.age}-year-old {eth.prompt} {noun} "
            f"with {shape.prompt}. The person has {cond.prompt}."
        ),
        "Scene/backdrop: seamless pure white studio background, nothing else in the frame",
        (
            f"Subject: {P.skin_description(spec.age)}; {_hair_description(spec)}; no glasses, no hat, no earrings, "
            "no visible makeup, no jewellery; both ears visible"
        ),
        (
            f"Eyebrows (most important): {cond.prompt}. The brow bone, the skin above and around both eyebrows and "
            "the whole forehead must be fully visible and unobstructed, because new eyebrows will be drawn by hand "
            "on top of the printed photo. "
            + (
                "Draw the eyebrows exactly in this natural, untouched shape: real hair only, no makeup, "
                "no pencil, no tattooed or drawn-on brows, no grooming or trimming."
                if cond.shape
                else "Do not draw full, groomed or well-defined eyebrows."
            )
        ),
        (
            "Style/medium: photorealistic studio photograph with a clinical beauty-reference look, 85mm lens, "
            "true-to-life skin texture with pores and fine lines, no retouching, no beauty filter, no stylisation"
        ),
        (
            "Composition/framing: frontal view exactly at eye level, head perfectly level and centred, not tilted or "
            "rotated, eyes open and looking straight into the camera, neutral relaxed expression, mouth closed; "
            "head-and-shoulders framing in vertical 2:3 portrait orientation; the face from hairline to chin fills "
            "about 55-60% of the image height and the whole head including the top of the hair stays inside the frame"
        ),
        (
            "Lighting/mood: soft, even, shadowless frontal beauty lighting from a large softbox, neutral white "
            "balance, no shadows under the brow bone"
        ),
        "Color palette: natural skin tones on pure white",
        (
            f"Constraints: exactly one person; symmetrical frontal pose; both eyebrows fully visible; keep the eyebrows "
            f"{cond.prompt}; no text, no watermark, no logo, no border, no vignette"
        ),
        (
            "Avoid: glasses, sunglasses, bangs or any hair covering the forehead or eyebrows, hats, headbands over the "
            "hairline, heavy makeup, drawn or tattooed eyebrows, side profile, three-quarter view, tilted head, "
            "smiling with teeth, dramatic lighting, black-and-white, painting or illustration look"
        ),
    ]
    if spec.notes:
        lines.append(f"Additional notes: {spec.notes.strip()}")
    return "\n".join(lines)


def build_restyle_prompt(
    style_key: str,
    color_key: str = "match_hair",
    *,
    height_key: str = "keep",
    with_guide_image: bool = False,
    notes: str = "",
) -> str:
    style = P.BROW_STYLES[style_key]
    colour = P.BROW_COLORS[color_key]["prompt"]
    height = P.BROW_HEIGHTS[height_key]["prompt"]
    inputs = "Input images: Image 1: edit target (the client's photo)"
    if with_guide_image:
        inputs += "; Image 2: region guide - the translucent red area marks the only region that may change"
    lines = [
        "Use case: identity-preserve",
        "Asset type: eyebrow design preview for a brow tattoo / semi-permanent makeup consultation",
        inputs,
        (
            f"Primary request: redraw ONLY the two eyebrows of the person in Image 1 in this style: {style.prompt}. "
            f"Eyebrow colour: {colour}."
        ),
        (
            f"Eyebrow height (most important): {height}. The person's own eyebrows are visible in Image 1 - redraw them "
            "in place. The lower edge of each new brow must follow the lower edge of the existing brow, and the gap "
            "between the upper eyelid and the brow must stay exactly as it is in the photo. Never move the brows up "
            "onto the forehead and never widen the eye-to-brow distance; the face must not look surprised or lifted."
        ),
        (
            "Eyebrow placement (horizontal only): the brow head starts on the vertical line rising from the outer edge "
            "of the nostril; the arch peak sits on the line from the nostril through the outer edge of the iris; the "
            "tail ends on the line from the nostril through the outer corner of the eye, level with or slightly above "
            "the head; both brows symmetrical; realistic individual hairs with natural growth direction, matching the "
            "lighting of the photo"
        ),
        (
            "Constraints: change only the eyebrows; keep the face identity, skin texture, eyes, eyelids, nose, mouth, "
            "hair, pose, expression, lighting, background, framing and image size exactly unchanged; no other "
            "retouching or smoothing; the result must look like the same photograph with new eyebrows"
        ),
        (
            "Avoid: raising the brows higher on the forehead, widening the gap between the eye and the brow, a lifted "
            "or surprised expression, changing eye shape or eyelids, adding makeup, smoothing or brightening skin, "
            "cropping or re-framing, adding text or watermark"
        ),
    ]
    if notes:
        lines.append(f"Additional notes: {notes.strip()}")
    return "\n".join(lines)
