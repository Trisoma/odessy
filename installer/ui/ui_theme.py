"""Shared visual language for the Odessy desktop windows.

One palette, one type scale and three button weights, used by the installer,
maintenance, uninstall and launcher windows. Keeping the tokens here means the
windows stay visually consistent and restyling is a one-file change.
"""
from __future__ import annotations

FONT = 'Segoe UI'

COLORS = {
    'bg': '#080808',
    'surface': '#141414',
    'border': '#252525',
    'border_strong': '#393939',
    'text': '#F0F0F0',
    'muted': '#B8B8B8',
    'faint': '#777777',
    'accent': '#ECECEC',
    'accent_hover': '#FFFFFF',
    'accent_soft': '#292929',
    'accent_disabled': '#424242',
    'success': '#99D5AC',
    'danger': '#FF9A9A',
    'danger_soft': '#2B1717',
    'warning': '#E8C791',
}


def configure(root):
    """Apply the Odessy ttk styles to *root* and return the Style object."""
    from tkinter import ttk
    from installer.ui import brand
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
    s.configure('Hero.TLabel', background=COLORS['bg'], foreground=COLORS['text'], font=(FONT, 23, 'bold'))
    s.configure('Body.TLabel', background=COLORS['bg'], foreground=COLORS['muted'], font=(FONT, 11))
    s.configure('Section.TLabel', background=COLORS['bg'], foreground=COLORS['muted'], font=(FONT, 9, 'bold'))

    s.configure('Primary.TButton', background=COLORS['accent'], foreground=COLORS['bg'],
                padding=(18, 10), borderwidth=0, relief='flat', focusthickness=0)
    s.map('Primary.TButton',
          background=[('disabled', COLORS['accent_disabled']), ('pressed', COLORS['accent_hover']),
                      ('active', COLORS['accent_hover'])],
          foreground=[('disabled', COLORS['faint'])])

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
    s.map('TCheckbutton', background=[('active', COLORS['bg'])],
          foreground=[('disabled', COLORS['faint']), ('active', COLORS['text'])],
          indicatorbackground=[('selected', COLORS['text']), ('!selected', COLORS['surface'])])
    s.configure('Surface.TCheckbutton', background=COLORS['surface'], foreground=COLORS['text'], padding=(0, 2))

    s.configure('Treeview', rowheight=29, background=COLORS['surface'], foreground=COLORS['text'],
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
    """A raised surface with a hairline border; returns the inner frame."""
    import tkinter as tk
    outer = tk.Frame(parent, background=COLORS['surface'],
                     highlightthickness=1, highlightbackground=COLORS['border'],
                     highlightcolor=COLORS['border'], bd=0, **kwargs)
    return outer


def pill_button(parent, text, command, *, primary=False):
    from installer.ui.ui_controls import PillButton
    return PillButton(parent, text=text, command=command, primary=primary)


def sparkle(parent, *, size=52, success=False):
    """Resolution-independent illustration, with no image runtime dependency."""
    import tkinter as tk
    c = tk.Canvas(parent, width=size, height=size, background=COLORS['bg'], highlightthickness=0)
    ink = COLORS['text']
    if success:
        c.create_oval(6, 6, size - 6, size - 6, outline=ink, width=2)
        c.create_line(size * .28, size * .51, size * .44, size * .66,
                      size * .73, size * .35, fill=ink, width=2.5,
                      capstyle='round', joinstyle='round')
    else:
        points = (.46,.18,.55,.39,.76,.48,.55,.57,.46,.80,.37,.57,.15,.48,.37,.39,.46,.18)
        c.create_line(*(n * size for n in points), fill=ink, width=2.3, smooth=True)
        c.create_oval(size*.72, size*.12, size*.83, size*.23, outline=ink, width=2)
    return c


def header(root, *, title='Odessy', caption='Windows x64'):
    """Quiet brand header with a compact platform badge."""
    import tkinter as tk
    bar = tk.Frame(root, background=COLORS['bg'], height=70)
    bar.grid(row=0, column=0, sticky='ew')
    bar.grid_propagate(False)
    mark = tk.Canvas(bar, width=30, height=30, background=COLORS['bg'], highlightthickness=0)
    mark.create_oval(6, 8, 24, 22, outline=COLORS['text'], width=1.5)
    mark.create_oval(10, 4, 20, 26, outline=COLORS['text'], width=1.5)
    mark.create_oval(13, 12, 17, 16, fill=COLORS['text'], outline='')
    mark.pack(side='left', padx=(24, 8), pady=18)
    label = tk.Frame(bar, background=COLORS['bg'])
    label.pack(side='left')
    tk.Label(label, text=title, background=COLORS['bg'], foreground=COLORS['text'],
             font=(FONT, 13, 'bold'), anchor='w').pack(anchor='w')
    tk.Label(label, text='Local AI. Under your control.', background=COLORS['bg'],
             foreground=COLORS['muted'], font=(FONT, 8), anchor='w').pack(anchor='w')
    tk.Label(bar, text='  ' + caption + '  ', background=COLORS['surface'],
             foreground=COLORS['muted'], font=(FONT, 9), padx=5, pady=4).pack(side='right', padx=26)
    return bar


class Stepper:
    """Responsive segmented progress; completed steps retain their labels."""
    def __init__(self, root, labels, row):
        import tkinter as tk
        self.canvas = tk.Canvas(root, height=46, background=COLORS['bg'], highlightthickness=0)
        self.canvas.grid(row=row, column=0, sticky='ew', padx=24)
        self.labels = tuple(labels)
        self.index = 0
        self.completed = 0
        self.canvas.bind('<Configure>', lambda _: self._draw())

    def set(self, index, completed=None):
        self.index = index
        if completed is not None:
            self.completed = completed
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete('all')
        width = max(c.winfo_width(), 1)
        count = len(self.labels)
        cell = width / max(count, 1)
        for i, label in enumerate(self.labels):
            x = cell * i
            c.create_line(x + 3, 5, x + cell - 3, 5, width=4, capstyle='round',
                          fill=COLORS['text'] if i <= self.index else COLORS['border'])
            c.create_text(x + cell / 2, 27, text=f'{i + 1}  {label}',
                          fill=COLORS['text'] if i == self.index else COLORS['faint'],
                          font=(FONT, 8, 'bold' if i == self.index else 'normal'))
