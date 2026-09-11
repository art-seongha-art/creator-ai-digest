"""Preset vocabularies for BrowLab.

Everything the CLI lets the user choose (age group, gender, face shape,
eyebrow condition, ethnicity, brow style, brow colour) lives here together
with the Korean labels used on the printed sheet and the English phrases
used inside image-generation prompts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Age
# ---------------------------------------------------------------------------
# Age groups are inclusive integer ranges. Teens start at 15 so that the
# generated practice faces are always late-teen or older.
AGE_GROUPS: Dict[str, Tuple[int, int]] = {
    "10s": (15, 19),
    "20s": (20, 29),
    "30s": (30, 39),
    "40s": (40, 49),
    "50s": (50, 59),
    "60s": (60, 69),
    "70s": (70, 79),
}

AGE_GROUP_KO: Dict[str, str] = {
    "10s": "10대",
    "20s": "20대",
    "30s": "30대",
    "40s": "40대",
    "50s": "50대",
    "60s": "60대",
    "70s": "70대",
}


def age_group_of(age: int) -> str:
    for key, (lo, hi) in AGE_GROUPS.items():
        if lo <= age <= hi:
            return key
    return "70s" if age > 79 else "10s"


def skin_description(age: int) -> str:
    """Age-appropriate skin/texture wording so the model does not idealise."""
    if age < 20:
        return "youthful skin with natural texture, slight unevenness and a few small blemishes"
    if age < 30:
        return "smooth but real skin with visible pores and natural texture"
    if age < 40:
        return "healthy adult skin with visible pores, faint expression lines and natural texture"
    if age < 50:
        return "mature skin with fine lines around the eyes, visible pores and slight loss of firmness"
    if age < 60:
        return "middle-aged skin with clear wrinkles around the eyes and mouth, mild sagging and uneven tone"
    if age < 70:
        return "aged skin with deep wrinkles, sun spots, sagging eyelids and thinning, greying hair"
    return "elderly skin with deep wrinkles, age spots, hooded eyelids, thin sparse grey or white hair"


# ---------------------------------------------------------------------------
# Gender
# ---------------------------------------------------------------------------
GENDERS: Dict[str, Dict[str, str]] = {
    "female": {"ko": "여성", "noun": "woman", "young_noun": "girl"},
    "male": {"ko": "남성", "noun": "man", "young_noun": "boy"},
}


def gender_noun(gender: str, age: int) -> str:
    g = GENDERS[gender]
    if age < 20:
        return "teenage " + g["young_noun"]
    return g["noun"]


# Average inter-pupillary distance (mm) used to print faces at life size.
# Sources: anthropometric averages (adult women ~62 mm, adult men ~64 mm,
# late teens ~60 mm). These are only used to fix the print scale.
DEFAULT_IPD_MM: Dict[str, float] = {
    "female": 62.0,
    "male": 64.0,
    "teen": 60.0,
    "unknown": 63.0,
}


def default_ipd_mm(gender: str | None, age: int | None) -> float:
    if age is not None and age < 20:
        return DEFAULT_IPD_MM["teen"]
    if gender in DEFAULT_IPD_MM:
        return DEFAULT_IPD_MM[gender]
    return DEFAULT_IPD_MM["unknown"]


# ---------------------------------------------------------------------------
# Face shapes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FaceShape:
    key: str
    ko: str
    prompt: str          # description used in the generation prompt
    brow_tip_ko: str     # design guidance printed on the sheet
    brow_tip_en: str


FACE_SHAPES: Dict[str, FaceShape] = {
    "oval": FaceShape(
        "oval", "계란형",
        "an oval face with balanced proportions, a gently rounded chin and a forehead slightly wider than the jaw",
        "대부분의 눈썹 형태가 어울림. 부드러운 앵글 아치가 기본. 아치를 지나치게 높이지 않기.",
        "Most brow shapes suit an oval face; a soft angled arch is the classic choice.",
    ),
    "round": FaceShape(
        "round", "둥근형",
        "a round face with full cheeks, a soft rounded chin and roughly equal face width and length",
        "각이 있는 아치로 얼굴을 길어 보이게. 둥근 눈썹은 얼굴을 더 둥글게 보이므로 피하기.",
        "Use a defined angled arch to add length; avoid rounded brows.",
    ),
    "square": FaceShape(
        "square", "각진형",
        "a square face with a strong angular jawline, broad forehead and straight sides",
        "부드러운 곡선 아치로 턱선의 각을 완화. 너무 얇거나 각진 눈썹은 피하기.",
        "Soften the jaw with a curved, softly arched brow; avoid sharp angles and very thin brows.",
    ),
    "long": FaceShape(
        "long", "긴형",
        "a long oblong face, noticeably longer than it is wide, with a high forehead and a long chin",
        "일자형(플랫) 또는 낮은 아치로 얼굴 길이를 짧아 보이게. 높은 아치는 피하기.",
        "A straight, flat brow with a low arch visually shortens the face; avoid high arches.",
    ),
    "heart": FaceShape(
        "heart", "하트형",
        "a heart-shaped face with a wide forehead, high cheekbones and a narrow pointed chin",
        "부드럽게 둥근 아치, 중간 두께. 너무 두껍거나 진한 눈썹은 이마를 더 넓어 보이게 함.",
        "A soft rounded arch of medium thickness balances the wide forehead; avoid very heavy brows.",
    ),
    "diamond": FaceShape(
        "diamond", "다이아몬드형",
        "a diamond-shaped face with a narrow forehead, wide prominent cheekbones and a narrow chin",
        "곡선형(라운드) 아치로 광대를 부드럽게. 눈썹 앞머리를 너무 좁게 잡지 않기.",
        "A curved, rounded arch softens prominent cheekbones; keep the brow head reasonably full.",
    ),
    "triangle": FaceShape(
        "triangle", "삼각형(역하트형)",
        "a pear-shaped face with a narrow forehead and a wide, strong jaw",
        "눈썹을 약간 길고 도톰하게, 아치는 부드럽게. 이마 폭이 넓어 보이도록 꼬리를 살짝 길게.",
        "Slightly longer, fuller brows with a gentle arch widen the narrow forehead.",
    ),
}

# ---------------------------------------------------------------------------
# Eyebrow "problem" conditions to be designed over
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BrowCondition:
    key: str
    ko: str
    prompt: str
    shape: bool = False   # True: an existing brow SHAPE to design over, not a lack of hair


BROW_CONDITIONS: Dict[str, BrowCondition] = {
    "sparse": BrowCondition(
        "sparse", "모량 부족(듬성듬성)",
        "very sparse, thin eyebrows with only a few scattered hairs and plenty of bare skin visible between them",
    ),
    "faint": BrowCondition(
        "faint", "연한 눈썹",
        "very faint, pale, low-contrast eyebrows that are barely visible against the skin",
    ),
    "patchy": BrowCondition(
        "patchy", "군데군데 빈 눈썹",
        "patchy eyebrows with uneven density and several bald gaps along their length",
    ),
    "missing_tail": BrowCondition(
        "missing_tail", "꼬리가 없는 눈썹",
        "eyebrows whose outer third (the tail) is missing entirely, so each brow stops short above the middle of the eye",
    ),
    "asymmetric": BrowCondition(
        "asymmetric", "비대칭 눈썹",
        "clearly asymmetric thin eyebrows: one brow sits higher and is shaped differently from the other, both sparse",
    ),
    "overplucked": BrowCondition(
        "overplucked", "과도하게 뽑은 얇은 눈썹",
        "over-plucked, extremely thin pencil-line eyebrows with an unclear, faded outline",
    ),
    "undefined": BrowCondition(
        "undefined", "형태가 불분명한 눈썹",
        "shapeless, blurry, ill-defined eyebrows with no clear outline, arch or tail",
    ),
    "scar_gap": BrowCondition(
        "scar_gap", "흉터로 끊긴 눈썹",
        "thin eyebrows with a small hairless scar gap cutting through one brow",
    ),
    "almost_none": BrowCondition(
        "almost_none", "거의 없는 눈썹",
        "almost no eyebrow hair at all, just faint traces on smooth skin over the brow bone",
    ),
    # ── existing brow shapes: the client already has brows, and a new design is drawn over them ──
    "arch": BrowCondition(
        "arch", "아치형 눈썹",
        "naturally arched eyebrows with a clear rounded peak about two thirds along the brow and a tapered tail, "
        "medium thickness and medium density",
        shape=True,
    ),
    "high_arch": BrowCondition(
        "high_arch", "높은 아치 눈썹",
        "high, steeply arched eyebrows whose peak sits high above the eye and close to the outer third, "
        "with a short slim tail",
        shape=True,
    ),
    "straight_flat": BrowCondition(
        "straight_flat", "일자 눈썹",
        "straight flat eyebrows running almost horizontally with no arch at all, even thickness from head to tail",
        shape=True,
    ),
    "half": BrowCondition(
        "half", "1/2 반토막 눈썹",
        "eyebrows that stop at their midpoint: the inner half has normal hair and the outer half is completely "
        "bare skin, so each brow looks cut in half",
        shape=True,
    ),
    "thin": BrowCondition(
        "thin", "얇은 눈썹",
        "naturally thin, narrow eyebrows: a continuous but very low line of hair, only a few millimetres tall",
        shape=True,
    ),
    "thick": BrowCondition(
        "thick", "두꺼운 눈썹",
        "naturally thick, wide and dense eyebrows with strong dark hair covering a tall brow area",
        shape=True,
    ),
    "spread": BrowCondition(
        "spread", "퍼진 눈썹",
        "spread-out eyebrows whose hairs fan outwards in several directions beyond the brow line, "
        "so the outline is wide, fuzzy and hard to read",
        shape=True,
    ),
}

# ---------------------------------------------------------------------------
# Ethnicity / appearance diversity
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ethnicity:
    key: str
    ko: str
    prompt: str
    weight: float  # sampling weight for random mode


ETHNICITIES: Dict[str, Ethnicity] = {
    "korean": Ethnicity("korean", "한국인", "Korean", 8.0),
    "japanese": Ethnicity("japanese", "일본인", "Japanese", 1.5),
    "chinese": Ethnicity("chinese", "중국인", "Chinese", 1.5),
    "southeast_asian": Ethnicity("southeast_asian", "동남아시아인", "Southeast Asian (for example Vietnamese, Thai or Filipino)", 1.5),
    "south_asian": Ethnicity("south_asian", "남아시아인", "South Asian (for example Indian or Pakistani)", 1.0),
    "middle_eastern": Ethnicity("middle_eastern", "중동인", "Middle Eastern", 1.0),
    "european": Ethnicity("european", "유럽계(백인)", "European with fair skin", 1.5),
    "mediterranean": Ethnicity("mediterranean", "남유럽/지중해계", "Southern European / Mediterranean with olive skin", 1.0),
    "african": Ethnicity("african", "아프리카계(흑인)", "Black African", 1.0),
    "latino": Ethnicity("latino", "라틴계", "Latin American", 1.0),
    "mixed": Ethnicity("mixed", "혼혈", "mixed-heritage", 1.0),
}

# ---------------------------------------------------------------------------
# Brow styles (for the restyle command)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BrowStyle:
    key: str
    ko: str
    prompt: str


BROW_STYLES: Dict[str, BrowStyle] = {
    "korean_natural": BrowStyle(
        "korean_natural", "자연 눈썹(기본형)",
        "natural Korean semi-permanent style: nearly straight with a very soft arch, medium thickness, tapered tail, natural hair density",
    ),
    "straight": BrowStyle(
        "straight", "일자눈썹",
        "straight flat brows with almost no arch, even thickness, softly squared head and a gently tapered tail",
    ),
    "soft_arch": BrowStyle(
        "soft_arch", "부드러운 아치형",
        "softly arched brows with a gentle, natural curve and a medium-thickness body",
    ),
    "angled_arch": BrowStyle(
        "angled_arch", "각진 아치형",
        "angled brows with a clearly defined arch peak about two-thirds along the brow and a straight tapered tail",
    ),
    "rounded": BrowStyle(
        "rounded", "둥근형",
        "softly rounded, curved brows with no sharp peak, following a smooth semicircular line",
    ),
    "high_arch": BrowStyle(
        "high_arch", "하이 아치",
        "dramatic high-arched brows with a lifted peak and a slim tapered tail",
    ),
    "puppy": BrowStyle(
        "puppy", "처진 눈썹(강아지상)",
        "gently downward-sloping brows with a soft rounded body and a tail that drops slightly below the head, youthful and friendly",
    ),
    "bold_thick": BrowStyle(
        "bold_thick", "볼드/두꺼운 눈썹",
        "thick, full, dense brows with well-defined edges and a strong natural shape",
    ),
    "feathered": BrowStyle(
        "feathered", "결눈썹(엠보/마이크로블레이딩)",
        "feathered hair-stroke (microblading) brows: crisp individual fine hair strokes following natural growth direction, slightly fuller than natural, soft natural outline",
    ),
    "ombre_powder": BrowStyle(
        "ombre_powder", "옴브레 파우더",
        "ombre powder brows: soft powdered shading with no hard outline, lighter and airy at the head, gradually darker and more defined toward the tail",
    ),
    "combo": BrowStyle(
        "combo", "콤보(결+파우더)",
        "combination brows: natural hair strokes at the head blending into soft powder shading through the arch and tail",
    ),
    "s_curve": BrowStyle(
        "s_curve", "S자형",
        "S-curve brows with a subtle dip at the head, a soft rise to the arch and a gently descending tail",
    ),
}

BROW_COLORS: Dict[str, Dict[str, str]] = {
    "match_hair": {"ko": "머리색에 맞춤", "prompt": "a colour that naturally matches the person's hair and skin tone"},
    "natural_black": {"ko": "내추럴 블랙", "prompt": "soft natural black-brown"},
    "dark_brown": {"ko": "다크 브라운", "prompt": "dark brown"},
    "medium_brown": {"ko": "미디엄 브라운", "prompt": "medium warm brown"},
    "ash_brown": {"ko": "애쉬 브라운", "prompt": "cool ash brown"},
    "light_brown": {"ko": "라이트 브라운", "prompt": "light brown"},
    "gray_brown": {"ko": "그레이 브라운(중장년)", "prompt": "soft grey-brown suitable for greying hair"},
}

# Convenience lists for argparse choices
AGE_CHOICES: List[str] = list(AGE_GROUPS) + ["random"]
GENDER_CHOICES: List[str] = list(GENDERS) + ["random"]
FACE_SHAPE_CHOICES: List[str] = list(FACE_SHAPES) + ["random"]
BROW_CONDITION_CHOICES: List[str] = list(BROW_CONDITIONS) + ["random"]
ETHNICITY_CHOICES: List[str] = list(ETHNICITIES) + ["random", "any"]
BROW_STYLE_CHOICES: List[str] = list(BROW_STYLES)
BROW_COLOR_CHOICES: List[str] = list(BROW_COLORS)
