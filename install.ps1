# Installs (or updates) the Cylinder Text add-in for Fusion on Windows:
#
#     irm https://raw.githubusercontent.com/vystal/CylinderTextDeboss/main/install.ps1 | iex
#
# Downloads the latest version and puts it in Fusion's add-ins folder,
# replacing any copy already there.
& {
    $ErrorActionPreference = "Stop"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $addins = Join-Path $env:APPDATA "Autodesk\Autodesk Fusion 360\API\AddIns"
    $target = Join-Path $addins "CylinderTextDeboss"
    $work = Join-Path ([IO.Path]::GetTempPath()) ("CylinderTextDeboss-" + [Guid]::NewGuid())
    New-Item -ItemType Directory -Force $work | Out-Null
    try {
        $zip = Join-Path $work "download.zip"
        Invoke-WebRequest "https://github.com/vystal/CylinderTextDeboss/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath $work
        New-Item -ItemType Directory -Force $addins | Out-Null
        if (Test-Path $target) { Remove-Item -Recurse -Force $target }
        Move-Item (Join-Path $work "CylinderTextDeboss-main") $target
    } finally {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
    Write-Host "Installed to $target"
    Write-Host "In Fusion: Utilities > Add-Ins (Shift+S), select CylinderTextDeboss and click Run."
}
