"""Minimal stand-in for tkinter: enough to execute a UI build path headlessly
and surface real errors (missing attributes, bad parents, wrong call signatures)
without needing a display or the Tk libraries."""

TkVersion = 8.6


class TclError(Exception):
    pass


LEFT = "left"; RIGHT = "right"; TOP = "top"; BOTTOM = "bottom"
END = "end"; N = "n"; S = "s"; E = "e"; W = "w"; NW = "nw"; NSEW = "nsew"
HORIZONTAL = "horizontal"; VERTICAL = "vertical"; BOTH = "both"; X = "x"; Y = "y"
DISABLED = "disabled"; NORMAL = "normal"


class _Var:
    _registry = []

    def __init__(self, master=None, value=None, name=None):
        self._v = value if value is not None else self._default
        self._traces = []
        _Var._registry.append(self)

    def get(self):
        return self._v

    def set(self, v):
        self._v = v
        for cb in list(self._traces):
            cb()

    def trace_add(self, mode, cb):
        self._traces.append(lambda *a: cb("", "", mode))
        return f"trace{len(self._traces)}"

    trace = trace_add

    def trace_remove(self, *a):
        pass


class StringVar(_Var):
    _default = ""


class BooleanVar(_Var):
    _default = False


class IntVar(_Var):
    _default = 0


class DoubleVar(_Var):
    _default = 0.0


class Widget:
    """One permissive widget class standing in for every Tk widget."""
    _counter = [0]

    def __init__(self, master=None, cnf=None, **kw):
        self.master = master
        self.children = []
        self.kw = dict(kw)
        Widget._counter[0] += 1
        self._name = f"{type(self).__name__.lower()}{Widget._counter[0]}"
        self._grid = None
        self._cls = type(self).__name__
        self._bindings = {}
        self.image = None
        if master is not None and hasattr(master, "children"):
            master.children.append(self)

    # geometry
    def grid(self, **kw):
        self._grid = kw
        return self

    def pack(self, **kw):
        self._grid = kw
        return self

    def place(self, **kw):
        self._grid = kw
        return self

    def grid_remove(self):
        self._grid = None

    def grid_forget(self):
        self._grid = None

    def pack_forget(self):
        self._grid = None

    def grid_propagate(self, flag=True):
        pass

    def pack_propagate(self, flag=True):
        pass

    def grid_configure(self, **kw):
        self._grid = dict(self._grid or {}, **kw)

    def columnconfigure(self, i, **kw):
        pass

    def rowconfigure(self, i, **kw):
        pass

    grid_columnconfigure = columnconfigure
    grid_rowconfigure = rowconfigure

    # config
    def configure(self, cnf=None, **kw):
        self.kw.update(kw)
        return self.kw

    config = configure

    def cget(self, k):
        return self.kw.get(k)

    def __setitem__(self, k, v):
        self.kw[k] = v

    def __getitem__(self, k):
        return self.kw.get(k)

    def keys(self):
        return list(self.kw)

    # events
    def bind(self, seq=None, fn=None, add=None):
        self._bindings.setdefault(seq, []).append(fn)

    def bind_all(self, seq=None, fn=None, add=None):
        self._bindings.setdefault(seq, []).append(fn)

    def unbind(self, *a):
        pass

    def bindtags(self, *a):
        return ()

    # winfo
    def winfo_class(self):
        return self._cls

    def winfo_children(self):
        return list(self.children)

    def winfo_width(self):
        return 0

    def winfo_height(self):
        return 0

    def winfo_reqwidth(self):
        return 0

    def winfo_reqheight(self):
        return 0

    def winfo_exists(self):
        return True

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def winfo_containing(self, *a):
        return None

    def winfo_toplevel(self):
        return self

    def __str__(self):
        return self._name

    # scheduling
    def after(self, ms, fn=None, *a):
        return "timer"

    def after_cancel(self, tid):
        pass

    def after_idle(self, fn=None, *a):
        return "timer"

    def update(self):
        pass

    def update_idletasks(self):
        pass

    def focus_set(self):
        pass

    def option_add(self, *a, **k):
        pass

    def destroy(self):
        pass

    def title(self, *a):
        pass

    def geometry(self, *a):
        pass

    def minsize(self, *a):
        pass

    def maxsize(self, *a):
        pass

    def resizable(self, *a):
        pass

    def iconbitmap(self, *a):
        pass

    def protocol(self, *a):
        pass

    def mainloop(self):
        pass

    def wm_attributes(self, *a):
        pass

    def clipboard_get(self):
        return ""

    def clipboard_clear(self):
        pass

    def clipboard_append(self, *a):
        pass

    # text / canvas / listbox odds and ends
    def insert(self, *a, **k):
        pass

    def delete(self, *a, **k):
        pass

    def see(self, *a):
        pass

    def get(self, *a, **k):
        return ""

    def yview(self, *a):
        pass

    def yview_scroll(self, *a):
        pass

    def xview(self, *a):
        pass

    def create_window(self, *a, **k):
        return 1

    def create_line(self, *a, **k):
        return 1

    def create_text(self, *a, **k):
        return 1

    def create_rectangle(self, *a, **k):
        return 1

    def create_image(self, *a, **k):
        return 1

    def itemconfig(self, *a, **k):
        pass

    def coords(self, *a, **k):
        return (0, 0, 0, 0)

    def bbox(self, *a):
        return (0, 0, 100, 100)

    def select_range(self, *a):
        pass

    def selection_range(self, *a):
        pass

    def set(self, *a, **k):
        pass

    def state(self, *a):
        return ()

    def invoke(self):
        pass

    def deselect(self):
        pass

    def select(self):
        pass

    def tag_configure(self, *a, **k):
        pass

    def tag_add(self, *a, **k):
        pass

    def edit_reset(self, *a):
        pass


class Tk(Widget):
    def __init__(self, *a, **k):
        super().__init__(None)


class Toplevel(Widget):
    pass


class Frame(Widget):
    pass


class LabelFrame(Widget):
    pass


class Label(Widget):
    pass


class Button(Widget):
    pass


class Entry(Widget):
    pass


class Checkbutton(Widget):
    pass


class Radiobutton(Widget):
    pass


class Canvas(Widget):
    pass


class Text(Widget):
    pass


class Listbox(Widget):
    pass


class Scrollbar(Widget):
    pass


class Scale(Widget):
    pass


class Menu(Widget):
    pass


class PhotoImage(Widget):
    pass


class Spinbox(Widget):
    pass


class Message(Widget):
    pass


class OptionMenu(Widget):
    pass


class StringVarCompat(StringVar):
    pass
