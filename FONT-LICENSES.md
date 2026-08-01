# Bundled font licences

LapStudio itself is MIT licensed (see `LICENSE`). The two font files distributed
with it are not — both are under the **SIL Open Font License, Version 1.1**,
which permits bundling and redistribution provided the licence travels with them
and the fonts are not sold on their own.

| File | Family | Licence | Source |
|------|--------|---------|--------|
| `BigShoulders-Bold.ttf` | Big Shoulders Display | SIL OFL 1.1 | <https://fonts.google.com/specimen/Big+Shoulders+Display> |
| `Poppins-Bold.ttf` | Poppins | SIL OFL 1.1 | <https://fonts.google.com/specimen/Poppins> |

The full text of the SIL Open Font License 1.1 is available at
<https://openfontlicense.org/> and accompanies each family at the links above.

Under the OFL the reserved font names may not be used to promote modified
versions; these files are unmodified copies of the upstream releases.

## Not bundled

Two other pieces of software the app can use are deliberately **not** included in
this repository, because they are not ours to redistribute:

- **ffmpeg** — required for video export. Licensed GPL or LGPL depending on the
  build. Download from <https://ffmpeg.org/download.html>.
- **AiM `MatLabXRK` DLL** and its dependencies — required only to read AiM
  `.drk` / `.xrk` logs. Supplied with AiM RaceStudio 3.
