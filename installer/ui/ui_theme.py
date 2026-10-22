"""Shared visual language for the Odessy desktop windows.

One palette, one type scale and three button weights, used by the installer,
maintenance, uninstall and launcher windows. Keeping the tokens here means the
windows stay visually consistent and restyling is a one-file change.
"""
from __future__ import annotations

FONT = 'Segoe UI'

COLORS = {
    'bg': '#F7F8FA',
    'surface': '#FFFFFF',
    'border': '#E4E7EC',
    'border_strong': '#D0D5DD',
    'text': '#101828',
    'muted': '#667085',
    'faint': '#98A2B3',
    'accent': '#2563EB',
    'accent_hover': '#1D4ED8',
    'accent_soft': '#EFF4FF',
    'accent_disabled': '#A9C2F8',
    'success': '#067647',
    'danger': '#B42318',
    'danger_soft': '#FEF3F2',
    'warning': '#B54708',
}


def configure(root):
    """Apply the Odessy ttk styles to *root* and return the Style object."""
    from tkinter import ttk
    from installer import brand
    s = ttk.Style(root)
    s.theme_use('clam')
    s.configure('.', font=(FONT, 10), foreground=COLORS['text'])
    s.configure('TFrame', background=COLORS['bg'])
    s.configure('Surface.TFrame', background=COLORS['surface'])
    s.configure('TLabel', background=COLORS['bg'], foreground=COLORS['text'])
    s.configure('Surface.TLabel', background=COLORS['surface'], foreground=COLORS['text'])
    s.configure('Hint.TLabel', background=COLORS['bg'], foreground=COLORS['muted'], font=(FONT, 9))
    s.configure('SurfaceHint.TLabel', background=COLORS['surface'], foreground=COLORS['muted'], font=(FONT, 9))
    s.configure('Field.TLabel', background=COLORS['surface'], foreground=COLORS['muted'], font=(FONT, 9))
    s.configure('Error.TLabel', background=COLORS['bg'], foreground=COLORS['danger'])
    s.configure('Success.TLabel', background=COLORS['bg'], foreground=COLORS['success'])
    s.configure('Title.TLabel', background=COLORS['bg'], foreground=COLORS['text'], font=(FONT, 20, 'bold'))
    s.configure('Section.TLabel', background=COLORS['bg'], foreground=COLORS['muted'], font=(FONT, 9, 'bold'))

    s.configure('Primary.TButton', background=COLORS['accent'], foreground='#FFFFFF',
                padding=(18, 10), borderwidth=0, relief='flat', focusthickness=0)
    s.map('Primary.TButton',
          background=[('disabled', COLORS['accent_disabled']), ('pressed', COLORS['accent_hover']),
                      ('active', COLORS['accent_hover'])],
          foreground=[('disabled', '#F5F8FF')])

    s.configure('Secondary.TButton', background=COLORS['surface'], foreground=COLORS['text'],
                padding=(14, 8), borderwidth=1, relief='flat', bordercolor=COLORS['border_strong'])
    s.map('Secondary.TButton',
          background=[('disabled', COLORS['bg']), ('pressed', COLORS['bg']), ('active', COLORS['bg'])],
          foreground=[('disabled', COLORS['faint'])],
          bordercolor=[('disabled', COLORS['border'])])

    s.configure('TButton', background=COLORS['surface'], padding=(12, 7), borderwidth=1,
                bordercolor=COLORS['border_strong'], relief='flat')
    s.map('TButton', background=[('active', COLORS['bg']), ('disabled', COLORS['bg'])])

    s.configure('TEntry', padding=8, fieldbackground=COLORS['surface'], background=COLORS['surface'],
                bordercolor=COLORS['border_strong'], lightcolor=COLORS['border_strong'],
                darkcolor=COLORS['border_strong'], insertcolor=COLORS['text'])
    s.map('TEntry', bordercolor=[('focus', COLORS['accent'])],
          lightcolor=[('focus', COLORS['accent'])], darkcolor=[('focus', COLORS['accent'])])

    s.configure('TCheckbutton', background=COLORS['bg'], foreground=COLORS['muted'], padding=(0, 2))
    s.configure('Surface.TCheckbutton', background=COLORS['surface'], foreground=COLORS['text'], padding=(0, 2))

    s.configure('Treeview', rowheight=34, background=COLORS['surface'],
                fieldbackground=COLORS['surface'], borderwidth=0, font=(FONT, 10))
    s.configure('Treeview.Heading', font=(FONT, 9, 'bold'), background=COLORS['bg'],
                foreground=COLORS['muted'], padding=(10, 8), borderwidth=0, relief='flat')
    s.map('Treeview.Heading', background=[('active', COLORS['bg'])])
    s.configure('Treeview', bordercolor=COLORS['border'])
    s.map('Treeview', background=[('selected', COLORS['accent_soft'])],
          foreground=[('selected', COLORS['text'])])
    s.configure('Horizontal.TProgressbar', background=COLORS['accent'], troughcolor=COLORS['border'],
                borderwidth=0, thickness=6)
    s.configure('TNotebook', background=COLORS['bg'], borderwidth=0, tabmargins=(0, 0, 0, 0))
    s.configure('TNotebook.Tab', background=COLORS['bg'], foreground=COLORS['muted'],
                padding=(14, 8), borderwidth=0, font=(FONT, 9, 'bold'))
    s.map('TNotebook.Tab', background=[('selected', COLORS['bg'])],
          foreground=[('selected', COLORS['text'])])
    s.configure('TSeparator', background=COLORS['border'])
    s.configure('Vertical.TScrollbar', background=COLORS['border'], troughcolor=COLORS['bg'],
                borderwidth=0, arrowcolor=COLORS['muted'])
    brand.window_icon(root)
    return s


def ghost_button(parent, text, command, *, padx=0):
    """Flat, low-emphasis action used for maintenance and destructive paths."""
    import tkinter as tk
    button = tk.Button(parent, text=text, command=command, font=(FONT, 9),
                       background=COLORS['bg'], foreground=COLORS['muted'],
                       activebackground=COLORS['border'], activeforeground=COLORS['text'],
                       relief='flat', borderwidth=0, highlightthickness=0,
                       cursor='hand2', padx=6, pady=4)
    if padx:
        button.pack_configure(padx=padx)
    return button


def link_button(parent, text, command):
    """Accent-coloured text button for secondary in-page navigation."""
    import tkinter as tk
    return tk.Button(parent, text=text, command=command, font=(FONT, 9, 'bold'),
                     background=COLORS['bg'], foreground=COLORS['accent'],
                     activebackground=COLORS['bg'], activeforeground=COLORS['accent_hover'],
                     relief='flat', borderwidth=0, highlightthickness=0,
                     cursor='hand2', padx=0, pady=0)


def card(parent, **kwargs):
    """A white surface with a hairline border; returns the inner frame."""
    import tkinter as tk
    outer = tk.Frame(parent, background=COLORS['surface'],
                     highlightthickness=1, highlightbackground=COLORS['border'],
                     highlightcolor=COLORS['border'], bd=0, **kwargs)
    return outer


def header(root, *, title='Odessy', caption='Windows x64  ·  Preview'):
    """Thin application header: brand mark, wordmark and an optional caption."""
    import tkinter as tk
    from installer import brand
    bar = tk.Frame(root, background=COLORS['surface'], height=56)
    bar.grid(row=0, column=0, sticky='ew')
    bar.grid_propagate(False)
    mark = tk.Canvas(bar, width=30, height=30, background=COLORS['surface'], highlightthickness=0)
    for y, row in enumerate(brand.ROWS):
        for x, (red, green, blue, alpha) in enumerate(row):
            if alpha == 0:
                continue
            mark.create_rectangle(x + 7, y + 7, x + 8, y + 8,
                                  fill='#%02x%02x%02x' % (red, green, blue), outline='')
    mark.pack(side='left', padx=(24, 10), pady=13)
    tk.Label(bar, text=title, background=COLORS['surface'], foreground=COLORS['text'],
             font=(FONT, 14, 'bold')).pack(side='left')
    tk.Label(bar, text=caption, background=COLORS['surface'], foreground=COLORS['faint'],
             font=(FONT, 9)).pack(side='right', padx=24)
    tk.Frame(bar, background=COLORS['border'], height=1).place(relx=0, rely=1, relwidth=1, anchor='sw')
    return bar


class Stepper:
    """Horizontal three-step track drawn on a canvas.

    Parameters are just the step labels; ``set(index, completed)`` repaints.
    """

    def __init__(self, root, labels, row):
        import tkinter as tk
        self.canvas = tk.Canvas(root, height=64, background=COLORS['bg'], highlightthickness=0)
        self.canvas.grid(row=row, column=0, sticky='ew')
        self.labels = tuple(labels)
        self.index = 0
        self.completed = 0
        self.canvas.bind('<Configure>', lambda _event: self._draw())

    def set(self, index, completed=None):
        self.index = index
        if completed is not None:
            self.completed = completed
        self._draw()

    def _draw(self):
        c = self.canvas
        width = c.winfo_width() or 900
        c.delete('all')
        count = len(self.labels)
        margin = 40
        usable = max(width - margin * 2, count * 120)
        step = usable / max(count - 1, 1)
        xs = [margin + step * i for i in range(count)]
        y = 24
        for i in range(count - 1):
            done = i < self.index
            c.create_line(xs[i] + 13, y, xs[i + 1] - 13, y, width=2,
                          fill=COLORS['accent'] if done else COLORS['border'])
        for i, (x, label) in enumerate(zip(xs, self.labels)):
            if i < self.index:
                fill, glyph, ring, text = COLORS['accent'], '✓', COLORS['accent'], COLORS['muted']
            elif i == self.index:
                fill, glyph, ring, text = COLORS['accent'], str(i + 1), COLORS['accent_soft'], COLORS['text']
            else:
                fill, glyph, ring, text = COLORS['surface'], str(i + 1), COLORS['border'], COLORS['faint']
            c.create_oval(x - 13, y - 13, x + 13, y + 13, fill=fill, outline=ring, width=2)
            c.create_text(x, y, text=glyph,
                          fill='#FFFFFF' if i <= self.index else COLORS['faint'],
                          font=(FONT, 9, 'bold'))
            c.create_text(x, y + 30, text=label, fill=text,
                          font=(FONT, 9, 'bold' if i == self.index else 'normal'))
