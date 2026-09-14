"""画像処理ユーティリティ。表紙および本文挿絵の最適化を行う。"""

from __future__ import annotations

import io
import os
import tempfile
from typing import BinaryIO

from PIL import Image


def process_cover_image(uploaded_file: BinaryIO | None) -> str | None:
    """アップロードされた表紙画像を480x800にリサイズ・中央クロップ・白黒化して一時ファイルに保存する。"""
    if uploaded_file is None:
        return None

    # 拡張子は常に .jpg で保存する
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")

    img = Image.open(uploaded_file)
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    target_width = 480
    target_height = 800

    orig_width, orig_height = img.size
    orig_aspect = orig_width / orig_height
    target_aspect = target_width / target_height

    if orig_aspect > target_aspect:
        new_height = target_height
        new_width = int(target_height * orig_aspect)
    else:
        new_width = target_width
        new_height = int(target_width / orig_aspect)

    resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    left = (new_width - target_width) / 2
    top = (new_height - target_height) / 2
    right = (new_width + target_width) / 2
    bottom = (new_height + target_height) / 2

    cropped_img = resized_img.crop((left, top, right, bottom))
    gray_img = cropped_img.convert("L")
    gray_img.save(tmp.name, format="JPEG", quality=85)
    tmp.close()

    return tmp.name


def process_illustration_image(
    image_data: bytes | BinaryIO,
    max_width: int = 800,
    max_height: int = 1200,
    grayscale: bool = False,
    quality: int = 85,
    rotation: int = 0,
) -> bytes:
    """本文挿絵をアスペクト比維持で縮小し、カラー（または白黒）のJPEGバイト列へ変換する。
    
    クロップ（切り抜き）は行わず、イラストが見切れないように画面内に収める。
    透明チャンネルを持つ画像は白背景で合成する。
    xteink等の横変え・縦読み端末用に回転オプション（90度左回転など）に対応。
    """
    if isinstance(image_data, bytes):
        fp = io.BytesIO(image_data)
    else:
        fp = image_data

    with Image.open(fp) as img:
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(background, img).convert("RGB")
        elif img.mode != "RGB" and not grayscale:
            img = img.convert("RGB")

        if rotation == 90:
            img = img.transpose(Image.Transpose.ROTATE_90)
        elif rotation == 270:
            img = img.transpose(Image.Transpose.ROTATE_270)
        elif rotation == 180:
            img = img.transpose(Image.Transpose.ROTATE_180)

        orig_w, orig_h = img.size
        limit_w, limit_h = (max_height, max_width) if rotation in (90, 270) else (max_width, max_height)
        if orig_w > limit_w or orig_h > limit_h:
            ratio = min(limit_w / orig_w, limit_h / orig_h)
            new_size = (int(orig_w * ratio), int(orig_h * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        if grayscale:
            img = img.convert("L")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue()
