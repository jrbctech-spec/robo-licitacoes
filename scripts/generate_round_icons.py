#!/usr/bin/env python3
"""
generate_round_icons.py
Gera ícones e logos circulares com transparência (RGBA),
suavização de bordas (anti-aliasing via supersampling) e profundidade elegante.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter


def generate_assets():
    base_dir = Path(__file__).resolve().parent.parent
    assets_dir = base_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    logo_path = assets_dir / "logo.png"
    if not logo_path.exists():
        print(f"Erro: {logo_path} não encontrado.")
        return

    # Carrega imagem base
    logo_src = Image.open(logo_path).convert("RGBA")
    w, h = logo_src.size
    cx, cy = w // 2, h // 2
    r_gold = min(cx, cy) - 6  # Raio do anel dourado

    # Máscara supersampleada para anti-aliasing perfeito
    scale = 4
    dim = (2 * r_gold) * scale
    mask_hi = Image.new("L", (dim, dim), 0)
    d = ImageDraw.Draw(mask_hi)
    d.ellipse([0, 0, dim, dim], fill=255)
    smooth_mask = mask_hi.resize((2 * r_gold, 2 * r_gold), Image.Resampling.LANCZOS)

    # Recorte circular
    crop_box = (cx - r_gold, cy - r_gold, cx + r_gold, cy + r_gold)
    cropped = logo_src.crop(crop_box)

    badge = Image.new("RGBA", (2 * r_gold, 2 * r_gold), (0, 0, 0, 0))
    badge.paste(cropped, (0, 0))
    badge.putalpha(smooth_mask)

    # 1. Logo de alta resolução (1000x1000 transparente)
    logo_out = Image.new("RGBA", (1000, 1000), (0, 0, 0, 0))
    badge_988 = badge.resize((988, 988), Image.Resampling.LANCZOS)
    logo_out.paste(badge_988, (6, 6), badge_988)
    logo_out.save(assets_dir / "logo.png", format="PNG", optimize=True)

    # 2. Ícone do desktop (512x512 com sombra e anti-aliasing)
    icon_out = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    badge_size = 480
    badge_icon = badge.resize((badge_size, badge_size), Image.Resampling.LANCZOS)
    margin = (512 - badge_size) // 2

    # Sombra ambiente suave para profundidade
    shadow = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    d_s = ImageDraw.Draw(shadow)
    d_s.ellipse([margin + 2, margin + 6, margin + badge_size - 2, margin + badge_size + 6], fill=(0, 0, 0, 110))
    shadow_soft = shadow.filter(ImageFilter.GaussianBlur(radius=6))

    icon_out.paste(shadow_soft, (0, 0), shadow_soft)
    icon_out.paste(badge_icon, (margin, margin), badge_icon)
    icon_out.save(assets_dir / "icon.png", format="PNG", optimize=True)

    # 3. Ícone ICO multi-resolução para Windows/Wine
    icon_out.save(
        assets_dir / "icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )

    print("✅ Novos ícones redondos gerados com sucesso em assets/")


if __name__ == "__main__":
    generate_assets()
