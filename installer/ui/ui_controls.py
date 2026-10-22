"""Small keyboard-accessible canvas control for the desktop setup shell."""
from __future__ import annotations

import tkinter as tk
from tkinter import font


class PillButton(tk.Canvas):
    """Rounded action button with the state interface used by ttk buttons."""

    def __init__(self, parent, *, text, command, primary=False):
        from installer.ui.ui_theme import COLORS, FONT
        self.label = text
        self.command = command
        self.primary = primary
        self.disabled = False
        self.hover = False
        self.palette = COLORS
        self.typeface = font.Font(root=parent, family=FONT, size=10)
        super().__init__(parent, height=42, width=self.typeface.measure(text) + 42,
                         background=COLORS['bg'], highlightthickness=0,
                         takefocus=True, cursor='hand2')
        self.bind('<Configure>', lambda _: self._draw())
        self.bind('<Enter>', lambda _: self._hover(True))
        self.bind('<Leave>', lambda _: self._hover(False))
        self.bind('<Button-1>', lambda _: self.focus_set())
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Return>', lambda _: self.invoke())
        self.bind('<space>', lambda _: self.invoke())
        self.bind('<FocusIn>', lambda _: self._draw())
        self.bind('<FocusOut>', lambda _: self._draw())
        self._draw()

    def _hover(self, value):
        self.hover = value
        self._draw()

    def _release(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if not self.disabled:
            return self.command()

    def state(self, statespec=None):
        if statespec is not None:
            self.configure(state='disabled' if 'disabled' in statespec else 'normal')
        return ('disabled',) if self.disabled else ()

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if not kwargs:
            return super().configure()
        if 'text' in kwargs:
            self.label = kwargs.pop('text')
            kwargs['width'] = self.typeface.measure(self.label) + 42
        if 'style' in kwargs:
            self.primary = kwargs.pop('style') == 'Primary.TButton'
        if 'state' in kwargs:
            self.disabled = kwargs.pop('state') == 'disabled'
            super().configure(takefocus=not self.disabled, cursor='' if self.disabled else 'hand2')
        if kwargs:
            super().configure(**kwargs)
        self._draw()

    config = configure

    def _draw(self):
        c = self.palette
        width = max(self.winfo_width(), int(self.cget('width')))
        height = int(self.cget('height'))
        fill = c['accent'] if self.primary else c['bg']
        ink = c['bg'] if self.primary else c['muted']
        outline = fill if self.primary else c['border_strong']
        if self.hover and not self.disabled:
            fill = c['accent_hover'] if self.primary else c['surface']
            ink = c['bg'] if self.primary else c['text']
        if self.disabled:
            fill, ink, outline = c['surface'], c['faint'], c['border']
        if self.focus_get() == self and not self.disabled:
            outline = c['text']
        self.delete('all')
        # Round-ended strokes make real capsules, including at Windows DPI scales.
        x1, x2, y = height / 2, width - height / 2, height / 2
        self.create_line(x1, y, x2, y, width=height - 4, fill=outline, capstyle='round')
        self.create_line(x1, y, x2, y, width=height - 6, fill=fill, capstyle='round')
        self.create_text(width / 2, height / 2, text=self.label, fill=ink, font=self.typeface)
