param(
    [Parameter(Mandatory=$true)][string]$RendererBundle,
    [Parameter(Mandatory=$true)][string]$Client,
    [string]$Blender = 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe',
    [string]$Evidence,
    [switch]$WaitForOpen
)
$ErrorActionPreference = 'Stop'
$blenderExe = (Resolve-Path -LiteralPath $Blender).Path
$bundleDirectory = (Resolve-Path -LiteralPath $RendererBundle).Path
$clientPath = (Resolve-Path -LiteralPath $Client).Path
$entry = Join-Path $PSScriptRoot 'launch_demo.py'
# This demo gets its own configuration; the user's existing Blender is untouched.
$configDirectory = Join-Path ([IO.Path]::GetTempPath()) ('auroraview-blender-' + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $configDirectory | Out-Null
$oldConfig = $env:BLENDER_USER_CONFIG
try {
    $env:BLENDER_USER_CONFIG = $configDirectory
    $arguments = @('--factory-startup', '--python-exit-code', '1', '--python',
                   ('"' + $entry + '"'), '--', '--renderer-bundle', ('"' + $bundleDirectory + '"'),
                   '--client', ('"' + $clientPath + '"'))
    if ($Evidence) { $arguments += @('--evidence', ('"' + [IO.Path]::GetFullPath($Evidence) + '"')) }
    if ($WaitForOpen) { $arguments += '--wait-for-open' }
    # Visible by explicit user request: this is the interactive Blender demo.
    $logs = @{ }
    if ($Evidence) {
        $logBase = [IO.Path]::GetFullPath($Evidence)
        New-Item -ItemType Directory -Force -Path (Split-Path $logBase) | Out-Null
        $logs.RedirectStandardOutput = $logBase + '.stdout.log'
        $logs.RedirectStandardError = $logBase + '.stderr.log'
    }
    $process = Start-Process -FilePath $blenderExe -ArgumentList $arguments @logs -PassThru
    Write-Output ('Blender demo PID=' + $process.Id)
} finally {
    $env:BLENDER_USER_CONFIG = $oldConfig
}
