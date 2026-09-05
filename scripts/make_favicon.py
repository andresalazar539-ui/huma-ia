"""
scripts/make_favicon.py — gera o favicon da HUMA a partir da marca (HumaMark).

A marca do Cockpit é um círculo com um quarto em terracota. No tamanho de
aba o traço fino some, então aqui o círculo ganha fundo papel + traço
mais grosso — legível em aba clara e escura. Roda 1x, os PNG/ICO ficam
versionados em huma/static/brand/.

    python scripts/make_favicon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "huma" / "static" / "brand"
OUT.mkdir(parents=True, exist_ok=True)

INK = (28, 23, 20, 255)          # --ink
PAPER = (246, 242, 236, 255)     # --paper
TERRACOTTA = (200, 85, 61, 255)  # --terracotta

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <circle cx="32" cy="32" r="28" fill="#F6F2EC" stroke="#1C1714" stroke-width="4"/>
  <path d="M32 6 A26 26 0 0 1 58 32 L32 32 Z" fill="#C8553D"/>
</svg>
"""


def render(size: int) -> Image.Image:
    """Desenha a marca com supersampling 8x (bordas suaves em 16px)."""
    scale = 8
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = big * 0.06
    stroke = max(1, round(big * 0.07))
    box = (pad, pad, big - pad, big - pad)
    draw.ellipse(box, fill=PAPER, outline=INK, width=stroke)
    inner = (pad + stroke / 2, pad + stroke / 2, big - pad - stroke / 2, big - pad - stroke / 2)
    draw.pieslice(inner, start=270, end=360, fill=TERRACOTTA)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    (OUT / "favicon.svg").write_text(SVG, encoding="utf-8")
    sizes = {16: "favicon-16.png", 32: "favicon-32.png", 180: "apple-touch-icon.png", 512: "icon-512.png"}
    for size, name in sizes.items():
        render(size).save(OUT / name, format="PNG")
    ico_frames = [render(s) for s in (16, 32, 48)]
    ico_frames[0].save(
        OUT / "favicon.ico", format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=ico_frames[1:],
    )
    print("favicon gerado em", OUT)


if __name__ == "__main__":
    main()
