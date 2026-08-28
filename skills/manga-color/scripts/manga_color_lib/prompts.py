from __future__ import annotations


def build_clean_prompt(character_hint: str = "", feedback: str = "") -> str:
    target = (
        f"Target character: {character_hint.strip()}."
        if character_hint.strip()
        else "Target character: the single clearest main character in the panel."
    )
    correction = (
        f"\nCorrection requested after review: {feedback.strip()}"
        if feedback.strip()
        else ""
    )
    return f"""
Edit the attached manga panel; do not recreate or redesign the character.
{target}

Keep only the target character's complete visible body, including hair, face,
clothing, worn accessories, and objects directly attached to the body. Remove
the original background, all other characters, dialogue boxes, speech bubbles,
captions, text, sound effects, page numbers, and every other written element.
Replace everything outside the target character with pure white (#FFFFFF).

Preserve every visible character line. Keep the exact proportions, pose,
perspective, facial features, expression, hairstyle, clothing outlines, line
weight, and line connections. Do not thicken, thin, move, stylize, or redraw
visible lines. If writing overlaps a character line, reconstruct only the
shortest local segment required to continue or close that existing contour.
Do not invent large hidden anatomy or redesign clothing.

Output clean black-and-white line art only. No gray background, text, extra
objects, decorations, or shadows. Keep the same composition on a vertical
9:16 canvas at 1152x2048 with a pure white opaque background.{correction}
""".strip()


def build_color_prompt(
    palette_notes: str = "",
    feedback: str = "",
    *,
    allow_inferred_palette: bool = False,
    character_hint: str = "",
) -> str:
    if palette_notes.strip():
        palette = f"User palette instructions, which take priority: {palette_notes.strip()}"
    elif allow_inferred_palette:
        target = character_hint.strip() or "the confirmed target character/version"
        palette = (
            f"The user explicitly allowed palette inference for {target}. Infer the canonical, "
            "most recognizable palette; do not silently switch costumes or variants."
        )
    else:
        palette = "Use the supplied color references as the authoritative palette."
    correction = (
        f"\nCorrection requested after review: {feedback.strip()}"
        if feedback.strip()
        else ""
    )
    return f"""
Edit the attached images. Image 1 is the approved black-and-white line art and
is the immutable source of geometry and composition. Images 2 and later are
color references only. {palette}

Color the character inside the existing contours. Match the reference hair,
eyes, skin, clothing, accessories, and materials. Add soft, natural shading and
highlights that suit the manga style. Do not copy the reference pose,
expression, composition, background, text, or lighting direction.

Do not change the character's position, scale, pose, facial features,
expression, hairstyle, clothing outlines, or any line structure. Do not add
new black outlines. Prefer a clean color-and-shading underlay so the approved
line layer can be deterministically overlaid afterward. Keep the background
pure white (#FFFFFF), opaque, and empty. Add no text, people, objects, or
decorations. Output the exact same 1152x2048 vertical 9:16 composition.{correction}
""".strip()
