[CmdletBinding()]
param(
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$versionOutput = & $PythonCommand -c 'import sys; sys.path.insert(0, sys.argv[1]); from app_version import VERSION; print(VERSION)' $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the MarbleScape application version."
}
$appVersion = ($versionOutput | Select-Object -Last 1).Trim()
if ($appVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw "Invalid MarbleScape application version: $appVersion"
}
$releaseRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot "release")
)
$sourcePackageDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $releaseRoot "MarbleScape-source")
)
$windowsPackageDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $releaseRoot "MarbleScape-windows-x64")
)
$sourceArchive = Join-Path $releaseRoot "MarbleScape-source.zip"
$windowsArchive = Join-Path $releaseRoot ("MarbleScape-windows-x64_v" + $appVersion + ".zip")
$checksumsPath = Join-Path $releaseRoot "SHA256SUMS.txt"
$rootExecutable = Join-Path $projectRoot "marblescape.exe"
$thirdPartyLicenceNames = @(
    "ALTGRAPH-0.17.5.txt",
    "CERTIFI-2026.7.22.txt",
    "COPERNICUS-BROWSER-MIT.txt",
    "PEFILE-2024.8.26.txt",
    "PILLOW-12.3.0.txt",
    "PYINSTALLER-6.22.2.txt",
    "PYINSTALLER-HOOKS-CONTRIB-2026.7.txt",
    "PYSTRAY-0.19.5-GPL.txt",
    "PYSTRAY-0.19.5-LGPL.txt",
    "PYTHON-3.14.txt",
    "PYWIN32-CTYPES-0.2.3.txt",
    "SIX-1.17.0.txt",
    "TCL-TK-8.6.txt"
)

function Assert-ChildPath {
    param(
        [string]$Parent,
        [string]$Candidate
    )

    $parentFullPath = [System.IO.Path]::GetFullPath($Parent).TrimEnd("\")
    $candidateFullPath = [System.IO.Path]::GetFullPath($Candidate)
    if (
        $candidateFullPath.Equals(
            $parentFullPath,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not $candidateFullPath.StartsWith(
            $parentFullPath + "\",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Unsafe child path: $candidateFullPath"
    }
}

function Assert-NotReparsePoint {
    param(
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Release paths must not be reparse points: $Path"
        }
    }
}

Assert-ChildPath -Parent $projectRoot -Candidate $releaseRoot
Assert-ChildPath -Parent $projectRoot -Candidate $rootExecutable
Assert-ChildPath -Parent $releaseRoot -Candidate $sourcePackageDirectory
Assert-ChildPath -Parent $releaseRoot -Candidate $windowsPackageDirectory

$tempRoot = [System.IO.Path]::GetFullPath(
    [System.IO.Path]::GetTempPath()
).TrimEnd("\")
$tempBuildRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $tempRoot (
        "MarbleScape-PyInstaller-" + [guid]::NewGuid().ToString("N")
    ))
)
Assert-ChildPath -Parent $tempRoot -Candidate $tempBuildRoot

$distPath = Join-Path $tempBuildRoot "dist"
$workPath = Join-Path $tempBuildRoot "work"
$specPath = Join-Path $tempBuildRoot "spec"

function Copy-PublicFile {
    param(
        [string]$SourceName,
        [string]$DestinationDirectory,
        [string]$DestinationName = ""
    )

    $sourcePath = Join-Path $projectRoot $SourceName
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Required release file not found: $sourcePath"
    }
    Assert-NotReparsePoint -Path $sourcePath

    if (-not $DestinationName) {
        $DestinationName = [System.IO.Path]::GetFileName($SourceName)
    }

    Copy-Item -LiteralPath $sourcePath -Destination (
        Join-Path $DestinationDirectory $DestinationName
    )
}

function Write-ReleaseArchive {
    param(
        [string]$SourceDirectory,
        [string]$ArchivePath
    )

    $archiveCode = @'
from pathlib import Path
import sys
import zipfile

source = Path(sys.argv[1])
archive_path = Path(sys.argv[2])
with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED,
                     compresslevel=9, allowZip64=True) as archive:
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise RuntimeError(f"Release file must not be a link: {item}")
        if item.is_file():
            archive.write(item, item.relative_to(source.parent).as_posix())
'@
    $archiveHelperPath = Join-Path $tempBuildRoot "archive_release.py"
    [System.IO.File]::WriteAllText(
        $archiveHelperPath,
        $archiveCode,
        [System.Text.UTF8Encoding]::new($false)
    )
    & $PythonCommand $archiveHelperPath $SourceDirectory $ArchivePath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create release archive: $ArchivePath"
    }
}

# Validate the source bundle before replacing any existing release files.
& $PythonCommand (Join-Path $projectRoot "verify_pystray_source.py")
if ($LASTEXITCODE -ne 0) {
    throw "The installed pystray library does not match the supplied corresponding source."
}

Assert-NotReparsePoint -Path $releaseRoot
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null

foreach ($directory in @(
    $sourcePackageDirectory,
    $windowsPackageDirectory
)) {
    Assert-NotReparsePoint -Path $directory
    if (Test-Path -LiteralPath $directory) {
        Assert-ChildPath -Parent $releaseRoot -Candidate $directory
        Remove-Item -LiteralPath $directory -Recurse -Force
    }
    New-Item -ItemType Directory -Path $directory | Out-Null
}

foreach ($archive in @(
    $sourceArchive,
    $windowsArchive,
    $checksumsPath
)) {
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
}

New-Item -ItemType Directory -Path (
    $distPath,
    $workPath,
    $specPath
) -Force | Out-Null

try {
    & $PythonCommand -c (
        "import certifi, PIL, PyInstaller, pystray; " +
        "print('Build dependencies are available.')"
    )
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Build dependencies are missing. Install them with " +
            "'python -m pip install -r requirements-build.txt'."
        )
    }

    & $PythonCommand -c (
        'import sys,tomllib;from pathlib import Path;' +
        'sys.path.insert(0,sys.argv[1]);' +
        'from marblescape_source_defaults import AUTO_RESOLUTION_PROVIDERS,DEFAULT_SOURCE_PROFILES;' +
        'settings=tomllib.loads((Path(sys.argv[1])/"marblescape_config.example.toml").read_text(encoding="utf-8"));' +
        'wrong=[name for name in AUTO_RESOLUTION_PROVIDERS if settings.get("sources",{}).get(name,{}).get("resolution")!="auto" or DEFAULT_SOURCE_PROFILES[name]["resolution"]!="auto"];' +
        'print("Non-automatic source defaults: "+", ".join(wrong) if wrong else "All source resolution defaults are automatic.");' +
        'sys.exit(bool(wrong))'
    ) $projectRoot
    if ($LASTEXITCODE -ne 0) {
        throw "The release configuration must default every source resolution to auto."
    }

    $pythonArchitecture = & $PythonCommand -c 'import platform,struct;print(platform.machine()+chr(58)+str(struct.calcsize(bytes((80,)))*8))'
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to determine the Python architecture."
    }
    $pythonArchitecture = ($pythonArchitecture | Select-Object -Last 1).Trim()
    if ($pythonArchitecture -notmatch '^(AMD64|x86_64):64$') {
        throw (
            "The windows-x64 package requires 64-bit x86 Python; found " +
            "$pythonArchitecture."
        )
    }

    & $PythonCommand (Join-Path $projectRoot "build_icon.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Application icon generation failed."
    }
    $versionResource = Join-Path $tempBuildRoot "windows-version.txt"
    & $PythonCommand (Join-Path $projectRoot "build_windows_version.py") $versionResource
    if ($LASTEXITCODE -ne 0) {
        throw "Windows version resource generation failed."
    }
    $applicationIcon = Join-Path $projectRoot "assets\icons\marblescape.ico"
    $trayIconNames = @(
        "marblescape_16.png",
        "marblescape_32.png",
        "marblescape_48.png",
        "marblescape_64.png",
        "marblescape_128.png",
        "marblescape_256.png",
        "marblescape_512.png"
    )
    $trayIconPaths = foreach ($iconName in $trayIconNames) {
        $iconPath = Join-Path $projectRoot ("assets\icons\" + $iconName)
        if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
            throw "Tray icon asset not found: $iconPath"
        }
        $iconPath
    }
    if (-not (Test-Path -LiteralPath $applicationIcon -PathType Leaf)) {
        throw "Application icon not found: $applicationIcon"
    }

    $pyinstallerArguments = @(
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--log-level",
        "WARN",
        "--onefile",
        "--windowed",
        "--name",
        "marblescape",
        "--icon",
        $applicationIcon,
        "--version-file",
        $versionResource,
        "--distpath",
        $distPath,
        "--workpath",
        $workPath,
        "--specpath",
        $specPath,
        "--hidden-import",
        "pystray._win32"
    )
    foreach ($iconPath in $trayIconPaths) {
        $pyinstallerArguments += @(
            "--add-data",
            ($iconPath + ";assets/icons")
        )
    }
    $pyinstallerArguments += @(
        "--add-data",
        ($applicationIcon + ";assets/icons")
    )
    $pyinstallerArguments += @(
        "--add-data",
        ((Join-Path $projectRoot "marblescape_copernicus_catalog.json") + ";.")
    )
    $pyinstallerArguments += (
        Join-Path $projectRoot "marblescape_download.py"
    )
    & $PythonCommand @pyinstallerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    $builtExecutable = Join-Path $distPath "marblescape.exe"
    if (-not (Test-Path -LiteralPath $builtExecutable -PathType Leaf)) {
        throw "Built executable not found: $builtExecutable"
    }

    $smokeTest = Start-Process `
        -FilePath $builtExecutable `
        -ArgumentList "--help" `
        -WorkingDirectory $distPath `
        -WindowStyle Hidden `
        -PassThru
    if (-not $smokeTest.WaitForExit(30000)) {
        Stop-Process -Id $smokeTest.Id -Force -ErrorAction SilentlyContinue
        throw "The built executable smoke test timed out after 30 seconds."
    }
    $smokeTest.Refresh()
    if ($smokeTest.ExitCode -ne 0) {
        throw (
            "The built executable failed its command-line smoke test with " +
            "exit code $($smokeTest.ExitCode)."
        )
    }

    # Publish the same smoke-tested binary in the project root on every build.
    Copy-Item -LiteralPath $builtExecutable -Destination $rootExecutable -Force

    foreach ($file in @(
        ".gitignore",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "build_release.ps1",
        "build_icon.py",
        "app_version.py",
        "build_windows_version.py",
        "verify_pystray_source.py",
        "marblescape_config.example.toml",
        "marblescape_download.py",
        "marblescape_download_progress.py",
        "marblescape_noaa.py",
        "marblescape_himawari.py",
        "marblescape_slider.py",
        "marblescape_worldview.py",
        "marblescape_eumetsat.py",
        "marblescape_catalogues.py",
        "marblescape_catalogue_activity.py",
        "marblescape_copernicus.py",
        "marblescape_copernicus_mosaics.py",
        "marblescape_copernicus_settings.py",
        "marblescape_copernicus_catalog.json",
        "marblescape_source_layout.py",
        "marblescape_source_defaults.py",
        "marblescape_source_settings.py",
        "marblescape_cache.py",
        "marblescape_profiles.py",
        "marblescape_profile_settings.py",
        "marblescape_time.py",
        "requirements.txt",
        "requirements-build.txt",
        "start.bat"
    )) {
        Copy-PublicFile -SourceName $file -DestinationDirectory $sourcePackageDirectory
    }
    $sourceAssetsDirectory = Join-Path $sourcePackageDirectory "assets\icons"
    New-Item -ItemType Directory -Path $sourceAssetsDirectory | Out-Null
    foreach ($iconPath in $trayIconPaths) {
        Copy-Item -LiteralPath $iconPath -Destination $sourceAssetsDirectory
    }
    Copy-Item -LiteralPath $applicationIcon -Destination $sourceAssetsDirectory

    $projectLicence = Join-Path $projectRoot "LICENSE"
    $thirdPartyLicences = Join-Path $projectRoot "licenses"
    if (-not (Test-Path -LiteralPath $projectLicence -PathType Leaf)) {
        throw "Project licence not found: $projectLicence"
    }
    Assert-NotReparsePoint -Path $projectLicence
    if (-not (Test-Path -LiteralPath $thirdPartyLicences -PathType Container)) {
        throw "Third-party licence directory not found: $thirdPartyLicences"
    }
    Assert-NotReparsePoint -Path $thirdPartyLicences

    $licenceItems = @(
        Get-ChildItem -LiteralPath $thirdPartyLicences -Force
    )
    $unexpectedLicenceItems = @(
        $licenceItems | Where-Object {
            -not $_.PSIsContainer -and
            $thirdPartyLicenceNames -notcontains $_.Name
        }
    ) + @(
        $licenceItems | Where-Object { $_.PSIsContainer }
    )
    if ($unexpectedLicenceItems.Count -gt 0) {
        $unexpectedNames = (
            $unexpectedLicenceItems | ForEach-Object { $_.Name }
        ) -join ", "
        throw "Unexpected item in third-party licence directory: $unexpectedNames"
    }

    $sourceLicenceDirectory = Join-Path $sourcePackageDirectory "licenses"
    $windowsLicenceDirectory = Join-Path $windowsPackageDirectory "licenses"
    New-Item -ItemType Directory -Path (
        $sourceLicenceDirectory,
        $windowsLicenceDirectory
    ) | Out-Null

    foreach ($licenceName in $thirdPartyLicenceNames) {
        $licencePath = Join-Path $thirdPartyLicences $licenceName
        if (-not (Test-Path -LiteralPath $licencePath -PathType Leaf)) {
            throw "Required third-party licence not found: $licencePath"
        }
        Assert-NotReparsePoint -Path $licencePath
        Copy-Item -LiteralPath $licencePath -Destination $sourceLicenceDirectory
        Copy-Item -LiteralPath $licencePath -Destination $windowsLicenceDirectory
    }

    Copy-Item -LiteralPath $projectLicence -Destination $sourcePackageDirectory

    Copy-Item -LiteralPath $builtExecutable -Destination (
        Join-Path $windowsPackageDirectory "marblescape.exe"
    )
    foreach ($file in @(
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "marblescape_config.example.toml"
    )) {
        Copy-PublicFile -SourceName $file -DestinationDirectory $windowsPackageDirectory
    }
    Copy-PublicFile -SourceName "marblescape_config.example.toml" -DestinationDirectory $windowsPackageDirectory -DestinationName "marblescape_config.toml"
    Copy-Item -LiteralPath $projectLicence -Destination $windowsPackageDirectory

    foreach ($packageDirectory in @($sourcePackageDirectory, $windowsPackageDirectory)) {
        $documentationSource = Join-Path $projectRoot "docs"
        if (-not (Test-Path -LiteralPath $documentationSource -PathType Container)) {
            throw "Documentation directory not found: $documentationSource"
        }
        Assert-NotReparsePoint -Path $documentationSource
        Copy-Item -LiteralPath $documentationSource -Destination $packageDirectory -Recurse

        $examplesDirectory = Join-Path $packageDirectory "assets\examples"
        New-Item -ItemType Directory -Path $examplesDirectory -Force | Out-Null
        foreach ($exampleName in @(
            "example_marblescape_globe_geocolor.webp",
            "example_marblescape_globe_truecolor.webp",
            "example_marblescape_europe_geocolor.webp",
            "example_marblescape_europe_truecolor.webp"
        )) {
            Copy-Item -LiteralPath (Join-Path $projectRoot ("assets\examples\" + $exampleName)) -Destination $examplesDirectory
        }
    }
    foreach ($packageDirectory in @($sourcePackageDirectory, $windowsPackageDirectory)) {
        $thirdPartyDirectory = Join-Path $packageDirectory "third_party"
        New-Item -ItemType Directory -Path $thirdPartyDirectory | Out-Null
        foreach ($sourceName in @("third_party\README.md", "third_party\pystray-0.19.5-source.zip")) {
            Copy-PublicFile -SourceName $sourceName -DestinationDirectory $thirdPartyDirectory
        }
    }
    Write-ReleaseArchive -SourceDirectory $sourcePackageDirectory -ArchivePath $sourceArchive
    # Keep the exact application source and build instructions with the binary.
    $correspondingSourceDirectory = Join-Path $windowsPackageDirectory "source"
    New-Item -ItemType Directory -Path $correspondingSourceDirectory | Out-Null
    Copy-Item -LiteralPath $sourceArchive -Destination $correspondingSourceDirectory
    Write-ReleaseArchive -SourceDirectory $windowsPackageDirectory -ArchivePath $windowsArchive

    $checksumLines = foreach ($archive in @(
        $sourceArchive,
        $windowsArchive
    )) {
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $archive
        "{0}  {1}" -f (
            $hash.Hash.ToLowerInvariant(),
            [System.IO.Path]::GetFileName($archive)
        )
    }
    [System.IO.File]::WriteAllLines(
        $checksumsPath,
        $checksumLines,
        [System.Text.UTF8Encoding]::new($false)
    )

    Remove-Item -LiteralPath $sourcePackageDirectory -Recurse -Force
    Remove-Item -LiteralPath $windowsPackageDirectory -Recurse -Force

    foreach ($oldArchive in @(Get-ChildItem -LiteralPath $releaseRoot -File -Filter "MarbleScape-windows-x64*.zip")) {
        if ($oldArchive.FullName -ne $windowsArchive) {
            Assert-ChildPath -Parent $releaseRoot -Candidate $oldArchive.FullName
            Assert-NotReparsePoint -Path $oldArchive.FullName
            Remove-Item -LiteralPath $oldArchive.FullName -Force
        }
    }

    Write-Host "Release archives created:"
    Write-Host "  $sourceArchive"
    Write-Host "  $windowsArchive"
    Write-Host "  $checksumsPath"
}
finally {
    for (
        $attempt = 1;
        $attempt -le 5 -and (Test-Path -LiteralPath $tempBuildRoot);
        $attempt++
    ) {
        try {
            Assert-ChildPath -Parent $tempRoot -Candidate $tempBuildRoot
            Remove-Item -LiteralPath $tempBuildRoot -Recurse -Force -ErrorAction Stop
        }
        catch {
            if ($attempt -eq 5) {
                Write-Warning (
                    "Unable to remove temporary build directory: " +
                    $tempBuildRoot
                )
            }
            else {
                Start-Sleep -Milliseconds 750
            }
        }
    }
}
