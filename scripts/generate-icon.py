#!/usr/bin/env python3
"""Generate the OpenInstax app icon as a 1024x1024 PNG."""

from PIL import Image, ImageDraw
import os
import subprocess
import sys

SIZE = 1024
PADDING = 100


def draw_icon():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background: rounded rectangle with gradient-like blue
    bg_margin = 40
    bg_radius = 200
    draw.rounded_rectangle(
        [bg_margin, bg_margin, SIZE - bg_margin, SIZE - bg_margin],
        radius=bg_radius,
        fill=(30, 110, 220),  # Vibrant blue
    )

    # Subtle lighter overlay on top half for depth
    overlay = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(
        [bg_margin, bg_margin, SIZE - bg_margin, SIZE // 2 + 50],
        radius=bg_radius,
        fill=(255, 255, 255, 30),
    )
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Camera/printer icon - simplified Instax shape
    # Main body (white rounded rect representing the printer)
    body_l, body_t = 200, 220
    body_r, body_b = 824, 720
    body_radius = 60
    draw.rounded_rectangle(
        [body_l, body_t, body_r, body_b],
        radius=body_radius,
        fill=(255, 255, 255),
    )

    # Lens circle (dark)
    cx, cy = SIZE // 2, 420
    lens_r = 100
    draw.ellipse(
        [cx - lens_r, cy - lens_r, cx + lens_r, cy + lens_r],
        fill=(30, 110, 220),
        outline=(20, 80, 180),
        width=6,
    )

    # Inner lens highlight
    inner_r = 60
    draw.ellipse(
        [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
        fill=(40, 130, 240),
    )

    # Lens glint
    glint_r = 20
    draw.ellipse(
        [cx - 30 - glint_r, cy - 30 - glint_r, cx - 30 + glint_r, cy - 30 + glint_r],
        fill=(255, 255, 255, 180),
    )

    # Photo slot (bottom of printer - where photo comes out)
    slot_margin = 40
    slot_l = body_l + slot_margin + 30
    slot_r = body_r - slot_margin - 30
    slot_t = 620
    slot_b = 640
    draw.rounded_rectangle(
        [slot_l, slot_t, slot_r, slot_b],
        radius=6,
        fill=(200, 210, 230),
    )

    # Photo coming out of printer
    photo_l = body_l + 80
    photo_r = body_r - 80
    photo_t = 650
    photo_b = 820
    photo_radius = 12

    # Photo shadow
    draw.rounded_rectangle(
        [photo_l + 4, photo_t + 4, photo_r + 4, photo_b + 4],
        radius=photo_radius,
        fill=(0, 0, 0, 40),
    )

    # Photo white border (Instax style)
    draw.rounded_rectangle(
        [photo_l, photo_t, photo_r, photo_b],
        radius=photo_radius,
        fill=(255, 255, 255),
    )

    # Photo image area (colorful mini landscape)
    img_margin = 20
    img_l = photo_l + img_margin
    img_r = photo_r - img_margin
    img_t = photo_t + img_margin
    img_b = photo_b - img_margin - 20  # Extra bottom margin like real Instax

    # Sky
    draw.rectangle([img_l, img_t, img_r, img_b], fill=(135, 200, 250))
    # Hills
    draw.polygon(
        [(img_l, img_b), (img_l + 80, img_t + 60), (img_l + 160, img_b)],
        fill=(80, 180, 100),
    )
    draw.polygon(
        [(img_l + 100, img_b), (img_l + 200, img_t + 40), (img_r, img_b)],
        fill=(60, 160, 80),
    )
    # Sun
    sun_r = 18
    draw.ellipse(
        [img_r - 50 - sun_r, img_t + 20 - sun_r, img_r - 50 + sun_r, img_t + 20 + sun_r],
        fill=(255, 220, 80),
    )

    # Small viewfinder circle on top-right of printer body
    vf_cx, vf_cy = body_r - 100, body_t + 50
    vf_r = 18
    draw.ellipse(
        [vf_cx - vf_r, vf_cy - vf_r, vf_cx + vf_r, vf_cy + vf_r],
        fill=(200, 210, 230),
        outline=(180, 190, 210),
        width=3,
    )

    return img


def create_iconset(png_path, output_dir):
    """Create .iconset and convert to .icns using iconutil."""
    iconset_dir = os.path.join(output_dir, "AppIcon.iconset")
    os.makedirs(iconset_dir, exist_ok=True)

    img = Image.open(png_path)

    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for size in sizes:
        resized = img.resize((size, size), Image.LANCZOS)
        resized.save(os.path.join(iconset_dir, f"icon_{size}x{size}.png"))
        if size <= 512:
            resized2x = img.resize((size * 2, size * 2), Image.LANCZOS)
            resized2x.save(os.path.join(iconset_dir, f"icon_{size}x{size}@2x.png"))

    icns_path = os.path.join(output_dir, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset_dir, "-o", icns_path], check=True)

    # Cleanup iconset directory
    import shutil
    shutil.rmtree(iconset_dir)

    return icns_path


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    resources_dir = os.path.join(project_dir, "macos", "Resources")
    os.makedirs(resources_dir, exist_ok=True)

    png_path = os.path.join(resources_dir, "AppIcon.png")
    icon = draw_icon()
    icon.save(png_path)
    print(f"PNG saved: {png_path}")

    icns_path = create_iconset(png_path, resources_dir)
    print(f"ICNS saved: {icns_path}")
