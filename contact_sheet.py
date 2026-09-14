"""
Contact sheet generator — composites up to 8 scene-iteration snapshots into one
numbered grid image, so the whole VLM review loop for a scene can be checked
at a glance instead of opening 8 separate files.
"""

import pathlib

from PIL import Image, ImageDraw, ImageFont

MAX_FRAMES = 8
COLS = 4
CELL_PAD = 8
LABEL_H = 46
NUMBER_BADGE = 34


def _font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def build_contact_sheet(frame_paths, out_path, captions=None, title=None):
    """frame_paths: list of up to 8 image file paths, in iteration order (1..N).
    captions: optional list of short strings (e.g. "verdict=fix conf=0.8"), same length.
    Frames are numbered 1..N regardless of how many are supplied (<=8)."""
    frame_paths = list(frame_paths)[:MAX_FRAMES]
    if not frame_paths:
        raise ValueError("build_contact_sheet requires at least one frame")
    captions = (captions or [""] * len(frame_paths))[: len(frame_paths)]

    rows = (len(frame_paths) + COLS - 1) // COLS
    thumb_w, thumb_h = 300, 169  # 16:9, matches the 1280x720 stage aspect

    cell_w = thumb_w + CELL_PAD * 2
    cell_h = thumb_h + CELL_PAD * 2 + LABEL_H
    title_h = 40 if title else 0

    sheet_w = cell_w * COLS
    sheet_h = cell_h * rows + title_h
    sheet = Image.new("RGB", (sheet_w, sheet_h), "#0d0f14")
    draw = ImageDraw.Draw(sheet)

    if title:
        draw.text((CELL_PAD, 10), title, fill="#e8e8e8", font=_font(22))

    number_font = _font(20)
    caption_font = _font(14)

    for i, (path, caption) in enumerate(zip(frame_paths, captions)):
        row, col = divmod(i, COLS)
        x0 = col * cell_w
        y0 = row * cell_h + title_h

        img = Image.open(path).convert("RGB").resize((thumb_w, thumb_h))
        sheet.paste(img, (x0 + CELL_PAD, y0 + CELL_PAD))

        # numbered badge, top-left of the thumbnail
        badge_x, badge_y = x0 + CELL_PAD + 4, y0 + CELL_PAD + 4
        draw.ellipse(
            [badge_x, badge_y, badge_x + NUMBER_BADGE, badge_y + NUMBER_BADGE],
            fill="#f2c14e",
        )
        num_text = str(i + 1)
        bbox = draw.textbbox((0, 0), num_text, font=number_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (badge_x + NUMBER_BADGE / 2 - tw / 2, badge_y + NUMBER_BADGE / 2 - th / 2 - bbox[1]),
            num_text, fill="#1a1a1a", font=number_font,
        )

        # caption strip below thumbnail
        cap_y = y0 + CELL_PAD + thumb_h + 6
        draw.text((x0 + CELL_PAD, cap_y), caption[:60], fill="#c9c9c9", font=caption_font)
        if len(caption) > 60:
            draw.text((x0 + CELL_PAD, cap_y + 16), caption[60:120], fill="#c9c9c9", font=caption_font)

        draw.rectangle([x0 + 2, y0 + 2, x0 + cell_w - 2, y0 + cell_h - 2], outline="#333844", width=1)

    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return str(out_path)
