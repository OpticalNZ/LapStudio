from tkinter import Widget


class Style:
    def theme_use(self, *a):
        pass

    def configure(self, *a, **k):
        pass

    def map(self, *a, **k):
        pass

    def layout(self, *a, **k):
        return []

    def lookup(self, *a, **k):
        return ""

    def element_create(self, *a, **k):
        pass

    def theme_create(self, *a, **k):
        pass


class Notebook(Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._tabs = []
        self._sel = 0

    def add(self, child, **kw):
        self._tabs.append((child, kw))

    def select(self, tab=None):
        if tab is None:
            return str(self._tabs[self._sel][0]) if self._tabs else ""
        for i, (c, _k) in enumerate(self._tabs):
            if c is tab or str(c) == str(tab):
                self._sel = i
        return None

    def index(self, *a):
        return self._sel

    def tabs(self):
        return [str(c) for c, _k in self._tabs]

    def tab(self, *a, **k):
        return {}


class Combobox(Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._val = ""

    def set(self, v):
        self._val = v
        tv = self.kw.get("textvariable")
        if tv is not None:
            tv.set(v)

    def get(self):
        return self._val

    def current(self, i=None):
        return 0


class Scrollbar(Widget):
    pass


class Progressbar(Widget):
    pass


class Scale(Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._v = 0.0

    def set(self, v):
        self._v = float(v)

    def get(self):
        return self._v


class Separator(Widget):
    pass


class Frame(Widget):
    pass


class Label(Widget):
    pass


class Button(Widget):
    pass


class Entry(Widget):
    pass


class Checkbutton(Widget):
    pass


class Treeview(Widget):
    pass


class Sizegrip(Widget):
    pass


class Panedwindow(Widget):
    pass
