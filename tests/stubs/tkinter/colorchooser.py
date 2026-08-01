def __getattr__(name):
    def _f(*a, **k):
        return ""
    return _f
