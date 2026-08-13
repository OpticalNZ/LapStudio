# Publishing a release

Two separate things go to GitHub, and it is worth being clear which is which:

- **The source** goes in the repository, with `git push`. That is what someone
  gets when they clone it.
- **The zip users download** is a compiled bundle. Git never stores it, and
  `dist/` is deliberately in `.gitignore`. It is attached to a *Release*, which
  is a different part of GitHub from the code.

Both come from the same folder, but neither one contains the other. Some things
in the zip are not in the repository at all - `ffmpeg.exe` and AiM's DLLs are on
your machine and are not ours to publish as source.

---

## 1. Check the tests pass

From the project folder:

```
python jitter_test.py
python tests/ui_smoke.py
python tests/gsensor_check.py
python tests/check_release_inputs.py
python tests/motec_check.py
```

All five should end with `FAILURES: 0` or `PROBLEMS: 0`. The last one skips
politely if there is no `.ld` file to hand, which is not a failure.

`check_release_inputs.py` is the one that matters most before a release: it walks
the source, works out every module and image the running app touches, and
compares that against the build command. It is what stops a release shipping
without, say, the HUD backing images.

## 2. Set the version

Edit `version_info.txt` and change all four version numbers to the new one. They
appear as `filevers`, `prodvers`, `FileVersion` and `ProductVersion`. This is
what Windows shows in the file properties, and an executable with no version
information is treated more harshly by Defender than one with it.

## 3. Push the source

**You need a git client on this machine first.** The repository and its history
live in the project folder, but Windows has no way to send them anywhere without
one.

**GitHub Desktop** is the easy option: <https://desktop.github.com>. It signs in
through your browser, so there is no Personal Access Token to create, and it
pushes with a button. Install it, then **File, Add local repository**, choose the
LapStudio folder, and click **Push origin**. It will already show the waiting
commits.

**Command-line Git** is the other: <https://git-scm.com/download/win>, accept
every default.

Once either is installed, **double-click `push.bat`**. It shows what is about to
go up, warns you if anything is edited but not committed, waits for you to agree,
then pushes. It looks for GitHub Desktop's bundled copy of git as well as a
normal install, because GitHub Desktop does not put git on the PATH.

To do it by hand instead, you need a Command Prompt open in the project folder.
The quick way: open the LapStudio folder in File Explorer, click the address bar,
type `cmd` and press Enter. Then:

```
git status                  # should say nothing to commit, working tree clean
git push origin main
```

If it asks for a username and password, GitHub no longer accepts account
passwords. Use a Personal Access Token as the password - github.com, Settings,
Developer settings, Personal access tokens, with `repo` access - or install
GitHub Desktop and push from there.

## 4. Build the zip

```
make_release.bat 1.3.0
```

Substitute the real version. Leave the version off and it uses today's date.

It prints a summary at the end. **Read it before going further:**

```
   build:      folder
   signing:    UNSIGNED - expect antivirus warnings on first run
   UCRT:       bundled (runs on Windows 8.1 and 7 too)
   runs on:    Windows 10 or newer  (built with Python 3.14)
   ffmpeg:     bundled (shared + 7 DLLs, adds about 86 MB)
   AiM reader: bundled
```

If `ffmpeg` or `AiM reader` says anything other than bundled, the zip is
incomplete - users would find video export or `.drk` files broken. Fix that
before publishing rather than after.

The result is `dist\LapStudio 1.3.0.zip`, holding `LapStudio.exe`, `SETUP.md`
and an `_internal` folder with everything else.

**Folder build by default, on purpose.** A `--onefile` exe unpacks 250 MB to
`%TEMP%` on every launch and runs code from there, which is exactly the behaviour
Windows Defender flags as `Wacapew`. Pass `make_release.bat 1.3.0 onefile` if you
want the single file anyway, and expect the warning.

## 5. Test the zip as a user would

This step is the whole point of the two above, so do not skip it.

1. Copy the zip to a folder with nothing else in it
2. Unzip it and run `LapStudio.exe`
3. Load a CSV and export a few seconds - this proves ffmpeg is bundled
4. Load a `.drk` and export a few seconds - this proves the AiM reader is bundled

Ideally on a machine that has never had ffmpeg or RaceStudio installed, because
yours will quietly fall back on its own copies and hide a missing file.

## 6. Create the Release on GitHub

1. Go to <https://github.com/OpticalNZ/LapStudio/releases/new>
2. **Choose a tag** - type `v1.3.0` and pick "Create new tag on publish"
3. **Release title** - `LapStudio 1.3.0`
4. Write the notes (see below)
5. Drag `dist\LapStudio 1.3.0.zip` onto the attachment box
6. **Publish release**

The zip then appears under Assets on the release page, and the latest one is
what <https://github.com/OpticalNZ/LapStudio/releases/latest> points at.

### What to put in the notes

Say what changed, then the two things every user needs to know:

- **Windows 10 or newer**, because the embedded Python requires it
- **Windows will warn on first run.** The build is unsigned, so SmartScreen
  shows "Windows protected your PC". More info, then Run anyway. `SETUP.md`
  travels inside the zip and explains it at length.

## 7. Point people at it

The download link is
<https://github.com/OpticalNZ/LapStudio/releases/latest> - it always resolves to
the newest release, so it does not need updating each time.

---

## If something goes wrong

**`git push` rejected as out of date** - someone or something else changed the
repository. `git pull --rebase origin main`, check nothing broke, push again.

**The build fails** - capture it. `pause` waits on a prompt you cannot see when
output is redirected, so:

```
set LAPSTUDIO_NOPAUSE=1
make_release.bat 1.3.0 > build.log 2>&1
```

**Defender quarantines the exe** - it is a false positive on the packaging, not
the contents; `SETUP.md` covers reporting it to Microsoft. The only permanent fix
is a code signing certificate, and once you have one, set
`LAPSTUDIO_REQUIRE_SIGNING=1` so an unsigned build becomes a hard failure rather
than something you have to remember to check.
