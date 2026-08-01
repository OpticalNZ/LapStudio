# LapStudio checks

`jitter_test.py` (project root) verifies the fixed digit-pitch text engine and
that no dash's ink moves when only the values change.

`tests/ui_smoke.py` builds the whole Tk UI headlessly against the stub tkinter
in `tests/stubs`, then walks every tab, every overlay setting and the preview
scrubber, rendering each preview for real through PIL. Run it from the project
root:

    python tests/ui_smoke.py

The stubs stand in for tkinter so the build path can be executed without a
display. They are only for testing - the app itself needs real tkinter.

`tests/check_release_inputs.py` walks the imports from the app entry point,
finds every font, image and module the running program loads, and checks each
one appears in `make_release.bat`. A PyInstaller build fails silently - a
missing asset only surfaces when a user picks the one dash that needed it - so
run this before cutting a release:

    python tests/check_release_inputs.py
