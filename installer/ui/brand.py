"""Pixel-sailboat brand mark.

The project is spelled *Odessy* rather than *Odyssey*, so the mark is a small
pixel boat: a nod to the voyage without borrowing the written name. The art is
a character grid so it stays crisp at 16/32/64 px and needs no image assets at
runtime.
"""
from __future__ import annotations
from pathlib import Path

# '.' transparent, 'H' hull (accent), 'S' sail (white), 'M' mast (deep navy)
PIXELS = (
    '................',
    '.......M........',
    '.......MS.......',
    '.......MSS......',
    '.......MSSS.....',
    '.......MSSSS....',
    '.......MSSSSS...',
    '.......MSSSSSS..',
    '.......MSSSSS...',
    '.......MSSSS....',
    '.......MSSS.....',
    '.......MSS......',
    '.......M........',
    '..HHHHHHHHHHHH..',
    '...HHHHHHHHHH...',
    '.....HHHHHH.....',
)

PALETTE = {
    'H': '#12233F',
    'S': '#5B8DEF',
    'M': '#12233F',
}


def _rgba(char):
    hex_colour = PALETTE.get(char)
    if hex_colour is None:
        return (0, 0, 0, 0)
    red, green, blue = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return (red, green, blue, 255)


ROWS = tuple(tuple(_rgba(char) for char in row) for row in PIXELS)


def window_icon(root, size=16):
    """Attach the pixel boat as the window/taskbar icon.

    Tk's PhotoImage has no alpha channel, so transparent pixels are composited
    onto a light surface colour; otherwise Tk renders them opaque black.
    Returns the PhotoImage or None when unsupported.
    """
    import tkinter as tk
    background = (255, 255, 255)
    try:
        image = tk.PhotoImage(width=size, height=size)
        for y in range(size):
            for x in range(size):
                red, green, blue, alpha = ROWS[y][x]
                if alpha == 0:
                    red, green, blue = background
                image.put('#%02x%02x%02x' % (red, green, blue), (x, y))
        root.iconphoto(True, image)
        # Keep a reference: Tk discards images with no Python-side referent.
        root._odessy_icon = image
        return image
    except Exception:
        return None


def ico_bytes():
    """Render the mark to an .ico payload for the Windows EXE build."""
    from PIL import Image
    base = Image.new('RGBA', (16, 16))
    for y, row in enumerate(ROWS):
        for x, pixel in enumerate(row):
            base.putpixel((x, y), pixel)
    frames = [base.resize((size, size), Image.NEAREST) for size in (16, 32, 48, 64)]
    import io
    buffer = io.BytesIO()
    frames[-1].save(buffer, format='ICO', sizes=[(s, s) for s in (16, 32, 48, 64)])
    return buffer.getvalue()


def icon_path():
    """Path to the checked-in icon used by the PyInstaller build.

    The icon lives in `installer/assets/` (shared build input), not inside the
    `installer/ui/` package.
    """
    return Path(__file__).resolve().parent.parent / 'assets' / 'odessy-boat.ico'


if __name__ == '__main__':
    target = icon_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ico_bytes())
    print('Wrote', target, target.stat().st_size, 'bytes')
