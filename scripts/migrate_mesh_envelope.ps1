param([string]$Source = 'D:\Car2LEGO_3D')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$package = Join-Path $root 'src/car2lego3d/mesh_envelope'
if (Test-Path -LiteralPath $package) { throw 'Migration destination already exists.' }
$modules = @('appearance','carfit','checkpoints','config','coupe','details','dialogue',
    'envelope_suv','glazing','interior','layout','lego','optimization','part_index','quality',
    'recipes','reference_plan','requirements','research','reservation','sculpted_suv',
    'search','sectional','sequence','surfaces','suv','target','vehicle','wedge')
$shared = @('common','gpt_reference','multiview','native','studio','preview','web_access','bricklayer_audit')
New-Item -ItemType Directory -Path $package | Out-Null
$records = foreach ($module in $modules) {
    $origin = Join-Path $Source "src/car2lego3d/$module.py"
    $destination = Join-Path $package "$module.py"
    $content = [IO.File]::ReadAllText($origin)
    foreach ($name in $shared) { $content = $content.Replace("from .$name import", "from ..$name import") }
    [IO.File]::WriteAllText($destination, $content, [Text.UTF8Encoding]::new($false))
    [ordered]@{ source = "src/car2lego3d/$module.py"; destination = "src/car2lego3d/mesh_envelope/$module.py"
        source_sha256 = (Get-FileHash -LiteralPath $origin -Algorithm SHA256).Hash.ToLowerInvariant()
        migrated_sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() }
}
$tests = @('test_quality_search','test_native_gate','test_sequence','test_section_envelope',
    'test_wedge_layout','test_smooth_surfaces_interior','test_reference_details','test_dialogue_details')
$pattern = 'car2lego3d\.(' + (($modules | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')\b'
$testDirectory = Join-Path $root 'tests/mesh_envelope'
New-Item -ItemType Directory -Path $testDirectory | Out-Null
foreach ($name in $tests) {
    $path = Join-Path $root "tests/$name.py"
    $content = [IO.File]::ReadAllText($path)
    $content = [regex]::Replace($content, $pattern, 'car2lego3d.mesh_envelope.$1')
    $content = $content.Replace('from car2lego3d import details, dialogue', 'from car2lego3d.mesh_envelope import details, dialogue')
    $content = $content.Replace('from car2lego3d import dialogue', 'from car2lego3d.mesh_envelope import dialogue')
    [IO.File]::WriteAllText((Join-Path $testDirectory "$name.py"), $content, [Text.UTF8Encoding]::new($false))
    Remove-Item -LiteralPath $path
}
foreach ($name in @('analyze_assembly','inspect_body_cabin','inspect_wedge_junctions','observe_vehicle_layout',
        'optimize_cached_countach','prepare_build_trial')) {
    $path = Join-Path $root "scripts/$name.py"
    $content = [regex]::Replace([IO.File]::ReadAllText($path), $pattern, 'car2lego3d.mesh_envelope.$1')
    [IO.File]::WriteAllText($path, $content, [Text.UTF8Encoding]::new($false))
}
# Remove only exact, previously copied modules; pre-existing target code is retained.
foreach ($name in @('appearance','checkpoints','config','details','envelope_suv','interior','layout',
        'optimization','part_index','quality','reservation','search','sectional','sequence','target')) {
    $path = Join-Path $root "src/car2lego3d/$name.py"
    $origin = Join-Path $Source "src/car2lego3d/$name.py"
    if ((Get-FileHash -LiteralPath $path).Hash -ne (Get-FileHash -LiteralPath $origin).Hash) {
        throw "Previously copied file has changed: $path"
    }
    Remove-Item -LiteralPath $path
}
$records | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $root 'docs/mesh-envelope-source-map.json') -Encoding utf8
Write-Output "Migrated $($modules.Count) converter modules and $($tests.Count) test modules."
