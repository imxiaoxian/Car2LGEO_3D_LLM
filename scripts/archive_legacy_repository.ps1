param(
    [string]$Destination = 'D:\lego-project-archives\Car2LEGO_3D-20261029'
)
$ErrorActionPreference = 'Stop'
$source = 'D:\Car2LEGO_3D'
$target = 'D:\Car2LGEO_3D_LLM'
if (Test-Path -LiteralPath $Destination) { throw 'Use a new archive destination.' }
New-Item -ItemType Directory -Path $Destination | Out-Null
$trees = @('D:\Car2LEGO_3D', 'D:\Car2LEGO_3D-seed3d', 'D:\Car2LEGO_3D-worktrees\gpt61-blender')
$inventory = foreach ($relative in (git -C $source ls-files --cached --others --exclude-standard)) {
    $original = Join-Path $source $relative
    if (-not (Test-Path -LiteralPath $original -PathType Leaf)) { continue }
    $hash = (Get-FileHash -LiteralPath $original -Algorithm SHA256).Hash.ToLowerInvariant()
    $merged = Join-Path $target $relative
    $targetHash = if (Test-Path -LiteralPath $merged -PathType Leaf) {
        (Get-FileHash -LiteralPath $merged -Algorithm SHA256).Hash.ToLowerInvariant()
    } else { $null }
    [ordered]@{ path = $relative; source_sha256 = $hash; target_sha256 = $targetHash
        status = if (-not $targetHash) { 'missing' } elseif ($hash -eq $targetHash) { 'identical' } else { 'different' } }
}
$inventory | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Destination 'migration-inventory.json') -Encoding utf8
git -C $source bundle create (Join-Path $Destination 'all-refs.bundle') --all
if ($LASTEXITCODE -ne 0) { throw 'Git bundle creation failed.' }
git -C $source bundle verify (Join-Path $Destination 'all-refs.bundle') 2>&1 | Out-File (Join-Path $Destination 'bundle-verification.txt')
if ($LASTEXITCODE -ne 0) { throw 'Git bundle verification failed.' }
$records = foreach ($tree in $trees) {
    $name = Split-Path $tree -Leaf
    git -C $tree status --porcelain=v1 --untracked-files=all | Out-File (Join-Path $Destination ($name + '-status.txt')) -Encoding utf8
    $patch = Join-Path $Destination ($name + '-dirty.patch')
    git -C $tree diff --binary HEAD --output $patch
    if ($LASTEXITCODE -ne 0) { throw "Cannot save dirty patch: $tree" }
    $archive = Join-Path $Destination ($name + '-working-files.tar')
    # Keep runs, untracked sources and .env locally; omit only reproducible dependencies/caches.
    tar -cf $archive --exclude=.git --exclude=.venv --exclude=node_modules --exclude=.next --exclude=__pycache__ --exclude=.pytest_cache --exclude=models --exclude=external -C $tree .
    if ($LASTEXITCODE -ne 0) { throw "Cannot archive: $tree" }
    tar -tf $archive | Out-File (Join-Path $Destination ($name + '-archive-files.txt')) -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Cannot read archive: $tree" }
    [ordered]@{ source = $tree; archive = $archive
        archive_sha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        head = (git -C $tree rev-parse HEAD)
        deferred_resources = @('.venv', 'models', 'external') | Where-Object { Test-Path -LiteralPath (Join-Path $tree $_) } }
}
[ordered]@{ schema = 'legacy-repository-backup.v1'; source = $source; target = $target
    created_utc = (Get-Date).ToUniversalTime().ToString('o'); worktrees = @($records)
    bundle_sha256 = (Get-FileHash -LiteralPath (Join-Path $Destination 'all-refs.bundle') -Algorithm SHA256).Hash.ToLowerInvariant()
    confidential = 'Working archives may contain .env; never publish these archives.'
    deletion_performed = $false; restoration_verified = $false
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Destination 'backup.json') -Encoding utf8
Write-Output $Destination
