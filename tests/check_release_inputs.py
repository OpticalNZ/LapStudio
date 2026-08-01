"""
check_release_inputs.py — does make_release.bat bundle everything the app needs?

A PyInstaller build fails silently: a missing font, image or module only shows up
when a user picks the one dash that needed it. This walks the source instead,
works out what the running app touches, and compares that against the build
command in make_release.bat.

    python tests/check_release_inputs.py

Exits non-zero if anything the app needs at runtime is not in the build.
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Reached from the GUI at runtime. Dead scripts (render_ecu_v3, wheel_render) and
# the test suite are deliberately excluded - they are not in the release.
ENTRY = "ecu_overlay_app.py"

# Files the app locates by name relative to itself. Anything matching this that
# actually exists in the project must be in the bundle.
ASSET_RE = re.compile(r'''["']([\w\-]+\.(?:ttf|otf|png|jpg|npy))["']''')

PROBLEMS = []


def reachable_modules():
    """Local modules reachable from the entry point, following imports."""
    local = {os.path.splitext(f)[0] for f in os.listdir(ROOT) if f.endswith(".py")}
    seen, todo = set(), [os.path.splitext(ENTRY)[0]]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = os.path.join(ROOT, mod + ".py")
        if not os.path.isfile(path):
            continue
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError as e:
            PROBLEMS.append(f"{mod}.py does not parse: {e}")
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                root = n.split(".")[0]
                if root in local:
                    todo.append(root)
    return seen


def referenced_assets(modules):
    """Asset filenames mentioned by those modules that exist in the project."""
    found = {}
    for mod in modules:
        p = os.path.join(ROOT, mod + ".py")
        if not os.path.isfile(p):
            continue
        for m in ASSET_RE.finditer(open(p, encoding="utf-8").read()):
            name = m.group(1)
            if os.path.isfile(os.path.join(ROOT, name)):
                found.setdefault(name, set()).add(mod + ".py")
    return found


def build_script():
    p = os.path.join(ROOT, "make_release.bat")
    if not os.path.isfile(p):
        PROBLEMS.append("make_release.bat is missing")
        return ""
    return open(p, encoding="utf-8", errors="replace").read()


def main():
    mods = reachable_modules()
    assets = referenced_assets(mods)
    script = build_script()

    print(f"modules reachable from {ENTRY}: {len(mods)}")
    for m in sorted(mods):
        print("   ", m)

    print(f"\nassets those modules load: {len(assets)}")
    for a in sorted(assets):
        listed = a in script
        print(f"    {a:26s} {'in build' if listed else 'MISSING FROM BUILD'}"
              f"   ({', '.join(sorted(assets[a]))})")
        if not listed:
            PROBLEMS.append(f"asset {a} is loaded at runtime but not bundled")

    # xrk_helper is launched as a separate process, so it must be bundled as a
    # data file as well as a module.
    if "xrk_helper.py;." not in script:
        PROBLEMS.append("xrk_helper.py is not bundled as a data file")

    # Every reachable local module should survive the build. They are normally
    # found by the import scan; pinning them means a refactor cannot drop one.
    print("\nmodules pinned with --hidden-import:")
    for m in sorted(mods):
        if m == os.path.splitext(ENTRY)[0]:
            continue
        pinned = f"--hidden-import {m}" in script
        print(f"    {m:26s} {'pinned' if pinned else 'not pinned (relies on the import scan)'}")
        if not pinned:
            PROBLEMS.append(f"module {m} is not pinned in the build")

    # External binaries
    print("\nexternal binaries:")
    for group, files in (
        ("ffmpeg", ["ffmpeg.exe", "avcodec-62.dll", "avformat-62.dll",
                    "avfilter-11.dll", "avutil-60.dll", "swscale-9.dll",
                    "swresample-6.dll", "avdevice-62.dll"]),
        ("AiM reader", ["MatLabXRK-2022-64-ReleaseU.dll", "libxml2-2.dll",
                        "libiconv-2.dll", "libz.dll", "pthreadVC2_x64.dll",
                        "msvcr90.dll"]),
    ):
        for f in files:
            here = os.path.isfile(os.path.join(ROOT, f))
            listed = f in script
            state = ("bundled" if (here and listed) else
                     "in build list, file not in folder" if listed and not here else
                     "PRESENT BUT NOT IN BUILD" if here else "absent")
            print(f"    {group:11s} {f:32s} {state}")
            if here and not listed:
                PROBLEMS.append(f"{f} is in the folder but the build ignores it")

    print()
    if PROBLEMS:
        print(f"PROBLEMS: {len(PROBLEMS)}")
        for p in PROBLEMS:
            print("  -", p)
        return 1
    print("PROBLEMS: 0  - the build command covers everything the app loads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
