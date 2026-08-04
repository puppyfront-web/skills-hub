"""Prompt builders for all 7 image types + video prompts.

Each builder returns the final prompt string. Templates are split into two
groups: non-apparel (4 types) and apparel (3 types). Apparel builders accept
a `category` argument that selects sub-templates for upper/lower/dress/
outerwear/shoes/hat.

Keyed by `TYPE` string used in generate.py --type.
"""
from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Type registry (also mirrored in SKILL.md and collection.py)
# ---------------------------------------------------------------------------

NON_APPAREL_TYPES = ("white-bg", "scene", "selling-point", "aplus")
APPAREL_TYPES = ("model-wear", "multi-model", "flat-lay")
# Cross-cutting types — usable in both apparel and non-apparel collections.
SHARED_TYPES = ("banner",)
ALL_IMAGE_TYPES = NON_APPAREL_TYPES + APPAREL_TYPES + SHARED_TYPES

TYPE_LABELS = {
    "white-bg": "白底主图",
    "scene": "场景图",
    "selling-point": "卖点图",
    "aplus": "A+详情图",
    "model-wear": "模特试穿图",
    "multi-model": "多模特展示图",
    "flat-lay": "平铺/挂拍图",
    "banner": "橱窗图",
}

# Each image type's default size (sent to OpenAI as `size`).
TYPE_SIZES = {
    "white-bg": "1024x1024",
    "scene": "1024x1024",
    "selling-point": "1024x1024",
    "aplus": "1536x1024",   # wide
    "model-wear": "1024x1024",
    "multi-model": "1024x1024",
    "flat-lay": "1024x1024",
    "banner": "1536x1024",  # wide; gpt-image-2 max is 1536 long edge
}

# Which types use /images/edits (need a reference image) vs /images/generations.
# A+ 详情图 is pure text-to-image (no reference image);
# 橱窗图 banner supports both — has reference image → use edits (composition guidance),
# no reference image → use generations.
TYPES_USING_EDITS = set(ALL_IMAGE_TYPES) - {"aplus"}

# Apparel categories.
APPAREL_CATEGORIES = ("upper", "lower", "dress", "outerwear", "shoes", "hat")
CATEGORY_LABELS = {
    "upper": "上装",
    "lower": "下装",
    "dress": "连衣裙",
    "outerwear": "外套",
    "shoes": "鞋",
    "hat": "帽",
}


# ---------------------------------------------------------------------------
# Non-apparel (4 types)
# ---------------------------------------------------------------------------

def white_bg_prompt(product_hint: Optional[str] = None) -> str:
    """Pure white background studio shot — Taobao main image standard."""
    base = (
        "Professional e-commerce product photo. "
        "Place the product on a pure white (#FFFFFF) seamless background. "
        "Product centered, occupying ~70% of the frame. "
        "Soft studio softbox lighting from upper-left, gentle gradient highlight. "
        "Crisp shadow directly beneath the product, very subtle, no harsh edges. "
        "Sharp focus on the whole product, depth of field flat. "
        "High detail, photorealistic, 8k product photography. "
        "Do NOT add any text, logo, watermark, or border. "
        "Taobao/Tmall main image style."
    )
    if product_hint:
        base += f"\nProduct category hint: {product_hint}"
    return base


def scene_prompt(scene_desc: str, product_hint: Optional[str] = None) -> str:
    """Lifestyle scene shot — needs scene description from user."""
    if not scene_desc:
        scene_desc = "minimalist modern living room, warm natural light, soft bokeh background"
    base = (
        f"Lifestyle product photography. "
        f"Place the product naturally in this scene: {scene_desc}. "
        "Composition follows rule-of-thirds; product occupies 30-50% of frame, "
        "clearly the focal point. "
        "Realistic ambient lighting consistent with the scene. "
        "Shallow depth of field with the product in sharp focus and background softly blurred. "
        "Photorealistic, high detail, aspirational mood. "
        "Do NOT overlay any text or watermark. "
        "Taobao/Tmall scene image style."
    )
    if product_hint:
        base += f"\nProduct category hint: {product_hint}"
    return base


def selling_point_prompt(
    selling_points: str,
    product_hint: Optional[str] = None,
) -> str:
    """Marketing image with overlaid selling-point text.

    Uses gpt-image-1's text rendering capability. selling_points is a
    user-supplied string of comma-separated Chinese phrases.
    """
    if not selling_points:
        selling_points = "热销爆款, 品质保证"
    base = (
        "Taobao/Tmall marketing main image with bold typography overlay. "
        f"The product is the hero, occupying 60-70% of the frame on a clean "
        f"complementary background (light pastel or soft gradient). "
        f"Overlay these selling-point phrases as design elements:\n"
        f"{selling_points}\n"
        "Render the text in modern bold Chinese sans-serif typography, "
        "vibrant accent colors (red/orange for emphasis), clean layout with "
        "rounded badge shapes or ribbon banners. Text must be legible and "
        "correctly spelled. "
        "Add 1-2 small decorative icons related to the product. "
        "Photorealistic product + flat vector-style typography overlay. "
        "Eye-catching e-commerce design."
    )
    if product_hint:
        base += f"\nProduct category hint: {product_hint}"
    return base


def aplus_prompt(
    selling_points: str,
    product_hint: Optional[str] = None,
    brand: Optional[str] = None,
) -> str:
    """Wide A+ detail-page banner. Text-to-image (no reference image)."""
    if not selling_points:
        selling_points = "产品优势一, 产品优势二, 产品优势三"
    base = (
        "Wide horizontal Taobao/Tmall A+ detail-page banner (aspect ratio ~3:2). "
        "Design a structured marketing layout with the product hero on the left/center "
        "and 2-3 selling-point callouts arranged as numbered cards on the right. "
        "Overlay these selling-point phrases:\n"
        f"{selling_points}\n"
        "Render text in modern bold Chinese sans-serif, large legible sizes. "
        "Use a cohesive color palette (3-4 colors max), clean typography hierarchy. "
        "Add subtle decorative elements (line art, badges, icons) relevant to the product. "
        "Light, professional background gradient. "
        "Photorealistic product render + flat graphic design overlay. "
        "Premium e-commerce detail page aesthetic."
    )
    if brand:
        base += f"\nBrand name to subtly feature: {brand}"
    if product_hint:
        base += f"\nProduct category hint: {product_hint}"
    return base


def banner_prompt(
    selling_points: Optional[str] = None,
    product_hint: Optional[str] = None,
    brand: Optional[str] = None,
    has_reference_image: bool = True,
    slogan: Optional[str] = None,
) -> str:
    """橱窗图 / 店铺首页大图 banner. Wide 3:2 ratio, high-impact visual.

    Difference vs A+:
      - A+ 详情图 is for detail-page hero (information-dense, structured cards)
      - 橱窗图 is for shop homepage / event banner (single bold focal point,
        one slogan, lots of negative space, brand-forward)

    has_reference_image controls whether the prompt asks the model to feature
    the uploaded product (edits mode) or compose freely (generations mode).
    """
    parts = [
        "Wide horizontal Taobao/Tmall shop homepage banner (橱窗图, aspect ratio 3:2).",
        "High-impact hero composition with a SINGLE bold focal point and generous "
        "negative space (at least 30% of canvas empty for layout breathing room).",
    ]
    if has_reference_image:
        parts.append(
            "Feature the product from the reference image as the hero — preserve its "
            "color, shape, and details exactly. Place it off-center (rule of thirds) "
            "for dynamic composition."
        )
    else:
        parts.append(
            "Compose the hero product from scratch based on the category hint."
        )
    parts.append(
        "Background: premium editorial-quality — soft directional lighting, "
        "gradient or atmospheric depth, NO clutter. Mood should feel aspirational "
        "and brand-forward, NOT information-dense."
    )
    if slogan:
        parts.append(
            f"Overlay this brand slogan as the dominant typography element "
            f"(large, bold, modern Chinese sans-serif, 2-4 characters ideal):\n{slogan}"
        )
    elif selling_points:
        parts.append(
            "Overlay these selling points as restrained typography (smaller than A+ style, "
            "1-3 phrases max, supporting not dominating):\n"
            f"{selling_points}"
        )
    else:
        parts.append(
            "Typography should be minimal — at most a single short slogan or the brand name. "
            "Do NOT pack the banner with text."
        )
    if brand:
        parts.append(f"Subtly feature the brand name: {brand}")
    parts.append(
        "Cinematic e-commerce campaign aesthetic — think flagship store hero, "
        "not detail page. Photorealistic product + clean graphic design overlay."
    )
    if product_hint:
        parts.append(f"Product category hint: {product_hint}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Apparel (3 types × N categories)
# ---------------------------------------------------------------------------

# Per-category model-wear base prompts. Each describes how the garment should
# appear on the model.
_CATEGORY_WEAR_TEMPLATES = {
    "upper": (
        "Asian female model, 25 years old, slim build, neutral expression, "
        "wearing THIS top (preserved exactly: color, pattern, fabric texture, "
        "cut, neckline, sleeve length). Front-facing three-quarter pose, "
        "hands relaxed at sides. Modern studio background (light gray seamless), "
        "soft even lighting. Full upper-body framing from head to hips."
    ),
    "lower": (
        "Asian female model, 25 years old, slim build, wearing THESE bottoms "
        "(preserved exactly: color, wash, fabric, cut, length). Front-facing "
        "full-body pose. Plain fitted top in neutral color so bottoms stand out. "
        "Modern studio background (light gray seamless), soft even lighting. "
        "Full-body framing head to ankle."
    ),
    "dress": (
        "Asian female model, 25 years old, slim build, wearing THIS dress "
        "(preserved exactly: color, pattern, fabric, cut, neckline, hem length, "
        "sleeve style). Elegant three-quarter turn pose. Modern studio background "
        "(light gray seamless), soft beauty lighting. Full-body framing head to toe."
    ),
    "outerwear": (
        "Asian female model, 28 years old, slim build, wearing THIS outerwear "
        "piece open over a plain neutral top (preserved exactly: color, fabric, "
        "cut, lapel, buttons/zipper, length). Confident standing pose. "
        "Modern studio background (light gray seamless), soft even lighting. "
        "Full-body framing head to knee."
    ),
    "shoes": (
        "Asian female model's feet (or clean invisible-mannequin styling), "
        "wearing THESE shoes (preserved exactly: color, material, shape, sole, "
        "laces/buckles). Front three-quarter view, one foot slightly forward. "
        "Neutral wooden floor surface, soft studio lighting, shallow depth of field. "
        "Lower-leg framing."
    ),
    "hat": (
        "Asian female model, 25 years old, wearing THIS hat (preserved exactly: "
        "color, material, shape, brim, details). Three-quarter face framing, "
        "neutral expression, head-and-shoulders composition. Soft studio lighting, "
        "light gray seamless background."
    ),
}


def model_wear_prompt(category: str, model_desc: Optional[str] = None) -> str:
    """Single model wearing the apparel item."""
    if category not in _CATEGORY_WEAR_TEMPLATES:
        raise ValueError(f"未知服饰品类: {category}. 允许: {list(APPAREL_CATEGORIES)}")
    template = _CATEGORY_WEAR_TEMPLATES[category]
    base = (
        "Professional apparel e-commerce photo. "
        f"{template} "
        "Ultra-realistic, high-resolution fashion photography, "
        "Taobao/Tmall main image style. "
        "CRITICAL: preserve every visual detail of the input garment exactly — "
        "do NOT change color, pattern, fabric, or cut. "
        "Do NOT add text, logo, or watermark."
    )
    if model_desc:
        # Replace default "Asian female model, 25 years old, slim build" prefix.
        base = base.replace(
            "Asian female model, 25 years old, slim build",
            model_desc,
            1,
        )
    return base


def multi_model_prompt(
    category: str,
    variations: str,
) -> str:
    """Multiple models / scenes showing the same garment.

    variations: user-supplied comma-separated list, e.g.
      "Asian female 25yo studio, Caucasian male 30yo street, African female 28yo cafe"
    """
    if category not in _CATEGORY_WEAR_TEMPLATES:
        raise ValueError(f"未知服饰品类: {category}")
    if not variations:
        variations = (
            "Asian female 25yo in studio, "
            "Caucasian male 30yo in street setting, "
            "African female 28yo in cafe setting"
        )
    base = (
        "Professional apparel e-commerce photo set. "
        f"Show THIS {CATEGORY_LABELS[category]} worn by multiple models in "
        f"different settings to showcase versatility. Variations:\n{variations}\n"
        "Arrange as a clean 2x2 or 1x3 grid, each shot in its own setting. "
        "CRITICAL: the garment must be visually identical across all shots "
        "(same color, pattern, fabric, cut). "
        "Photorealistic fashion editorial quality. "
        "Do NOT overlay text or watermark. "
        "Taobao/Tmall multi-scene detail image style."
    )
    return base


def flat_lay_prompt(category: str, style: str = "folded") -> str:
    """Flat-lay / hanging shot without a model."""
    if category not in _CATEGORY_WEAR_TEMPLATES:
        raise ValueError(f"未知服饰品类: {category}")
    style = style or "folded"
    style_desc = {
        "folded": "neatly folded, stacked at a slight angle",
        "hanging": "hanging on a minimal wooden hanger against a clean wall",
        "steamed": "laid flat and freshly steamed, wrinkle-free",
        "laid": "laid flat, fully visible, shot directly from above",
    }.get(style, "neatly folded")

    base = (
        "Professional apparel e-commerce flat-lay photo. "
        f"Show THIS {CATEGORY_LABELS[category]} {style_desc}. "
        "Top-down (or slight three-quarter) camera angle. "
        "Background: clean white or light wood surface with subtle texture. "
        "Soft, even studio lighting — no harsh shadows. "
        "CRITICAL: preserve every visual detail of the input garment exactly "
        "(color, pattern, fabric texture, cut). "
        "Photorealistic, high detail. "
        "Taobao/Tmall flat-lay main image style. "
        "Do NOT add text, logo, or watermark."
    )
    return base


# ---------------------------------------------------------------------------
# Video prompts (CogVideoX)
# ---------------------------------------------------------------------------

DEFAULT_VIDEO_PROMPT = (
    "Smooth cinematic product showcase: the product rotates slowly, "
    "soft directional light sweeps across the surface revealing texture and details, "
    "subtle depth-of-field shifts. Elegant, premium e-commerce video aesthetic. "
    "Camera: gentle orbit, 30-degree rotation over the clip duration."
)


def build_video_prompt(custom: Optional[str] = None, product_hint: Optional[str] = None) -> str:
    """Build a video generation prompt for CogVideoX."""
    base = custom if (custom and custom.strip()) else DEFAULT_VIDEO_PROMPT
    if product_hint:
        base = f"Product: {product_hint}. {base}"
    return base


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def build_prompt(
    image_type: str,
    *,
    scene_desc: Optional[str] = None,
    selling_points: Optional[str] = None,
    product_hint: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    model_desc: Optional[str] = None,
    variations: Optional[str] = None,
    style: Optional[str] = None,
    slogan: Optional[str] = None,
    has_reference_image: bool = True,
) -> str:
    """Dispatch to the correct builder based on image_type."""
    if image_type == "white-bg":
        return white_bg_prompt(product_hint)
    if image_type == "scene":
        return scene_prompt(scene_desc or "", product_hint)
    if image_type == "selling-point":
        return selling_point_prompt(selling_points or "", product_hint)
    if image_type == "aplus":
        return aplus_prompt(selling_points or "", product_hint, brand)
    if image_type == "model-wear":
        if not category:
            raise ValueError("model-wear 需要 --category 参数")
        return model_wear_prompt(category, model_desc)
    if image_type == "multi-model":
        if not category:
            raise ValueError("multi-model 需要 --category 参数")
        return multi_model_prompt(category, variations or "")
    if image_type == "flat-lay":
        if not category:
            raise ValueError("flat-lay 需要 --category 参数")
        return flat_lay_prompt(category, style or "folded")
    if image_type == "banner":
        return banner_prompt(
            selling_points, product_hint, brand,
            has_reference_image=has_reference_image, slogan=slogan,
        )
    raise ValueError(f"未知类型: {image_type}")


if __name__ == "__main__":
    # Smoke test: print all default prompts.
    for t in ALL_IMAGE_TYPES:
        kwargs = {}
        if t in ("model-wear", "multi-model", "flat-lay"):
            kwargs["category"] = "upper"
        try:
            p = build_prompt(t, **kwargs)
            print(f"=== {t} ({TYPE_LABELS[t]}) ===")
            print(p)
            print()
        except Exception as e:
            print(f"ERROR {t}: {e}")
