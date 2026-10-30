param([string]$Archive = 'D:\lego-project-archives\Car2LEGO_3D-20261029')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$backupPath = Join-Path $Archive 'backup.json'
$backup = Get-Content -LiteralPath $backupPath -Raw | ConvertFrom-Json
$restore = Join-Path $Archive 'restore-check'
New-Item -ItemType Directory -Path $restore -Force | Out-Null
$gitDirectory = Join-Path $restore 'repository.git'
if (-not (Test-Path -LiteralPath $gitDirectory)) {
    git clone --quiet --bare (Join-Path $Archive 'all-refs.bundle') $gitDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Bundle restore failed.' }
}
$trees = foreach ($tree in $backup.worktrees) {
    $actualHash = (Get-FileHash -LiteralPath $tree.archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $tree.archive_sha256) { throw 'Archive checksum changed.' }
    git "--git-dir=$gitDirectory" cat-file -e "$($tree.head)^{commit}"
    if ($LASTEXITCODE -ne 0) { throw 'Archived worktree commit is missing.' }
    $destination = Join-Path $restore (Split-Path $tree.source -Leaf)
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    tar -xf $tree.archive -C $destination
    if ($LASTEXITCODE -ne 0) { throw 'Working files restore failed.' }
    $count = 0
    foreach ($file in Get-ChildItem -LiteralPath $destination -Recurse -File -Force) {
        $relative = [IO.Path]::GetRelativePath($destination, $file.FullName)
        $original = Join-Path $tree.source $relative
        if (-not (Test-Path -LiteralPath $original -PathType Leaf) -or
            (Get-FileHash -LiteralPath $original).Hash -ne (Get-FileHash -LiteralPath $file.FullName).Hash) {
            throw "Restored working file differs: $relative"
        }
        $count++
    }
    [ordered]@{ source = $tree.source; restored = $destination; files_verified = $count; head = $tree.head }
}
$resources = @(
    @('D:\Car2LEGO_3D\models', (Join-Path $root 'models')),
    @('D:\Car2LEGO_3D\external\Hunyuan3D-2', (Join-Path $root 'external/Hunyuan3D-2')),
    @('D:\Car2LEGO_3D\external\TripoSR', (Join-Path $root 'external/TripoSR')),
    @('D:\Car2LEGO_3D-worktrees\gpt61-blender\external', (Join-Path $Archive 'historical-external'))
)
$preserved = foreach ($pair in $resources) {
    $original, $destination = $pair
    if (-not (Test-Path -LiteralPath $original -PathType Container)) { throw "Missing resource: $original" }
    if (Test-Path -LiteralPath $destination) { throw "Resource destination already exists: $destination" }
    robocopy $original $destination /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Resource copy failed: $original" }
    $count = 0
    foreach ($file in Get-ChildItem -LiteralPath $original -Recurse -File -Force) {
        $relative = [IO.Path]::GetRelativePath($original, $file.FullName)
        $copied = Join-Path $destination $relative
        if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $copied).Hash) {
            throw "Resource checksum differs: $relative"
        }
        $count++
    }
    [ordered]@{ source = $original; destination = $destination; files_verified = $count }
}
$sourceZip = 'D:\Car2LEGO_3D\external\hunyuan3d-source.zip'
Copy-Item -LiteralPath $sourceZip -Destination (Join-Path $Archive 'hunyuan3d-source.zip')
if ((Get-FileHash -LiteralPath $sourceZip).Hash -ne (Get-FileHash -LiteralPath (Join-Path $Archive 'hunyuan3d-source.zip')).Hash) {
    throw 'Source zip checksum differs.'
}
& 'D:\Car2LEGO_3D\.venv\Scripts\python.exe' -c 'import importlib.metadata as m; print("\n".join(sorted(d.metadata["Name"]+"=="+d.version for d in m.distributions())))' |
    Set-Content -LiteralPath (Join-Path $Archive 'legacy-environment.txt')
if ($LASTEXITCODE -ne 0) { throw 'Cannot record legacy environment.' }
[ordered]@{ schema = 'legacy-restoration-check.v1'; checked_utc = (Get-Date).ToUniversalTime().ToString('o')
    worktrees = @($trees); preserved_resources = @($preserved); status = 'pass' } |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Archive 'restoration-verification.json') -Encoding utf8
$backup.restoration_verified = $true
$backup | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $backupPath -Encoding utf8
Write-Output 'Archive restoration and resource preservation passed.'
