# Development and release builds

Planned work and the process used to create local source and Windows release archives.

[Back to the main README](../README.md)

## Planned features

- Support for additional imagery providers.
- More customization options for image composition.

These ideas are planned, but scope and release dates may change.

## Building release archives

This step is intended for maintainers, not for users of the prebuilt package.
On 64-bit Windows with 64-bit x86 Python and PowerShell 7, install the tested
build dependencies and run the release script:

```powershell
python -m pip install -r requirements-build.txt
pwsh -NoProfile -File .\build_release.ps1
```

The script creates separate source and Windows release-candidate archives in
`release`, plus a SHA-256 checksum file. Creating these local archives does not
clear them for publication; complete the distribution review described below
before publishing a new binary. Local configuration, shortcuts, downloaded
images, caches, and unused local assets are never copied.

The build regenerates `assets/icons/marblescape.ico` from the supplied PNGs
using `build_icon.py`. The ICO contains the original 16, 32, 48, 64, 128 and
256 px images; the tray can additionally use the 512 px PNG. Keep these
assets alongside the source when building. The EXE embeds its icon and tray
assets, so no separate icon installation is needed.

`app_version.py` defines the application version used by `--version`, the
HTTP User-Agent, and the EXE's Windows file/product version metadata.
The build generates that metadata with `build_windows_version.py`.

The executable is inside `MarbleScape-windows-x64_vX.Y.Z.zip`, where `X.Y.Z`
comes from `app_version.py`; extract that archive
to use the new build. Building does not replace an already running installation.

The build script includes the matching application source ZIP under
`source/` inside the Windows package and the verified upstream pystray source
under `third_party/` in both packages. See
[Third-Party Notices](../THIRD_PARTY_NOTICES.md#rebuilding-with-a-modified-pystray)
for rebuilding with a modified tray library.

The Windows executable is not code-signed. Windows may therefore display an
unrecognized-publisher warning.

## Publishing a GitHub release

Pushing a version tag such as `v1.2.0` starts the **Create release** GitHub
Actions workflow. It checks that the tag version matches `app_version.py`,
builds the release archives on a 64-bit Windows runner, and publishes a GitHub
Release named `MarbleScape v1.2.0` with these assets:

- `MarbleScape-source.zip`
- `MarbleScape-windows-x64.zip`
- `SHA256SUMS.txt`

GitHub generates the release notes from commits. Create the tag only after the
release contents have been reviewed locally; the workflow publishes the binary
automatically.
