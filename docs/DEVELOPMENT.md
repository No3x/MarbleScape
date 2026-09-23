# Development and release builds

Planned work, release builds, tests, schema history and the invariants the
regression tests enforce.

[Back to the main README](../README.md)

## Contents

- [Planned features](#planned-features)
- [Building release archives](#building-release-archives)
- [Tests](#tests)
- [Schema history](#schema-history)
- [Invariants](#invariants)

## Planned features

- Support for additional imagery providers.
- More customization options for image composition.

These ideas are planned, but scope and release dates may change.

## Building release archives

This step is intended for maintainers, not for users of the prebuilt package.

### Build commands

On 64-bit Windows with 64-bit x86 Python and PowerShell 7, install the tested
build dependencies and run the release script:

```powershell
python -m pip install -r requirements-build.txt
pwsh -NoProfile -File .\build_release.ps1
```

To build and smoke-test only `marblescape.exe` in the project root, without
replacing existing release archives or checksums:

```powershell
pwsh -NoProfile -File .\build_release.ps1 -ExeOnly
```

### What the archives contain

The script creates separate source and Windows release-candidate archives in
`release`, plus a SHA-256 checksum file. Creating them does not clear them for
publication; complete the
[distribution review](LEGAL_AND_ATTRIBUTION.md#outstanding-distribution-review)
before publishing a new binary.

- **Source archive:** `MarbleScape-X.Y.Z-source.zip` with application modules, build scripts, tests, documentation,
  icon assets required by the build, licences and corresponding third-party
  source.
- **Windows package:** `MarbleScape-X.Y.Z-windows-x64.zip` with the executable,
  the matching application source ZIP under `source/`, and the verified upstream
  pystray source under `third_party/` (also in the source archive). See
  [Third-Party Notices](../THIRD_PARTY_NOTICES.md#rebuilding-with-a-modified-pystray)
  for rebuilding with a modified tray library.
- **Both archives** contain the README's example pictures: every WebP in
  `assets/examples` (the README picture and the single pictures it links to).
  Full-size example PNGs stay local and are never committed or packaged.
- **Never included:** the local virtual environment, configuration, credentials,
  profiles, shortcuts, downloaded images, caches, History images, generated
  binaries and unused local assets.

`build_release.ps1` lists every packaged file explicitly; new modules and data
files must be added there. Extract the Windows archive to use a new build;
building does not replace a running installation.

### Icon, version and signing

- The icon is MarbleScape's own drawing (a circle and an equator ellipse in
  #00C3FF): `python build_icon.py --draw` draws the PNG sizes anew. The build
  regenerates `assets/icons/marblescape.ico` from those PNGs with `build_icon.py`. The ICO contains the 16, 32, 48, 64, 128 and 256 px images; the
  tray can also use the 512 px PNG. The EXE embeds its icon and tray assets.
- `app_version.py` defines the version used by `--version`, the HTTP User-Agent
  and the EXE's Windows file/product version metadata, which
  `build_windows_version.py` generates. `X.Y.Z` in the package name comes from it.
- The executable is not code-signed, so Windows may show an
  unrecognized-publisher warning.

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
