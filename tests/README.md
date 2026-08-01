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
