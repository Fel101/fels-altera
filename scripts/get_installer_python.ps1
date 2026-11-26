# Based on the Voice Lab installer kit. Stage the SHA-256-pinned NuGet CPython
# package, including venv and pip, without registering Python in Windows.
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$Package
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Destination = [IO.Path]::GetFullPath($Destination)
$downloaded = -not $Package
if ($downloaded) {
    $Package = Join-Path (Split-Path -Parent $Destination) 'python.3.12.10.nupkg'
}
try {
    if ($downloaded) {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg' -OutFile $Package
    }
    $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $Package).Hash
    if ($digest -ne '0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8') {
        throw 'The Python package checksum did not match. Fetch the pinned package and try again.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Package)
    try {
        foreach ($entry in $archive.Entries) {
            if (-not $entry.FullName.StartsWith('tools/') -or $entry.FullName.EndsWith('/')) { continue }
            $target = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName.Substring(6)))
            if (-not $target.StartsWith("$Destination\", [StringComparison]::OrdinalIgnoreCase)) {
                throw 'A file in the Python package would leave its staging directory.'
            }
            [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target)
        }
    } finally {
        $archive.Dispose()
    }
} finally {
    if ($downloaded -and (Test-Path -LiteralPath $Package)) {
        Remove-Item -LiteralPath $Package -Force
    }
}
