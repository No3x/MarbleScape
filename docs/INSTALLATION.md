# Installation and startup

Installation, source operation, Linux usage, and the Windows tray controls.

[Back to the main README](../README.md)

## Run the Windows executable

A reviewed prebuilt package supports 64-bit Windows 10 and 11 and does not
require a separate Python installation. Publish a newly built package only
after completing the distribution review documented below.

1. Download a published, reviewed versioned archive such as
   `MarbleScape-windows-x64_v1.3.0.zip` and
   `SHA256SUMS.txt` from
   [GitHub Releases](https://github.com/Gittegatt/MarbleScape/releases).
2. Extract the complete ZIP archive into a user-writable folder. Do not run the
   application from inside the ZIP archive.
3. Optionally verify the download before starting it:

   ```powershell
   Get-FileHash .\MarbleScape-windows-x64_v1.3.0.zip -Algorithm SHA256
   Get-Content .\SHA256SUMS.txt
   ```

   The displayed ZIP hash must match the corresponding line in
   `SHA256SUMS.txt`.
4. Run `marblescape.exe`. The executable is not code-signed, so Windows may show
   an unrecognized-publisher warning. Continue only after obtaining the file
   from a trusted release and verifying its checksum.
5. Find the MarbleScape icon in the Windows notification area. It may be inside
   the overflow menu behind the up-arrow. Right-click the icon and select
   `Settings...` to configure the image.

The tray icon appears immediately. The first wallpaper is applied after the
selected source has been checked and its image download has completed. Use
`Open image folder` to inspect the generated image, `Start with Windows` to
enable per-user startup, and `Exit` to stop the application.
On startup, MarbleScape checks the latest public GitHub release in the
background. If a newer release exists, a small window offers **Skip this
version** and **Open GitHub**. Skipping suppresses that release on later starts;
the next newer release can still be shown.

## Run from source

Source operation requires:

- Python 3.11 or newer;
- Pillow 10.0 or newer;
- pystray 0.19.5 or newer;
- Windows 10 or 11 for tray and wallpaper integration.

Install the dependencies and start the application:

```powershell
python -m pip install -r requirements.txt
python marblescape_download.py
```

On a fresh installation, MarbleScape creates `marblescape_config.toml` from the
`marblescape_config.example.toml` template. On Windows,
`start.bat` performs the same source-based start and keeps a console window open
for messages.

Useful command-line options:

```powershell
python marblescape_download.py --list-layers geo
python marblescape_download.py --export-layers marblescape_layers.json
python marblescape_download.py --validate-config
python marblescape_download.py --print-urls
python marblescape_download.py --once
python marblescape_download.py --version
```

## Linux usage

Linux supports image downloading and local composition, but not the Windows
tray menu, Windows startup integration, or automatic wallpaper application.

```bash
python3 -m pip install -r requirements.txt
python3 marblescape_download.py --once
```

Remove `--once` to keep checking for new imagery until the process is stopped.
The output location is controlled by `linux_root` in
`marblescape_config.toml`.
