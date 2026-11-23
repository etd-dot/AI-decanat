# Windows PowerShell 5.1+. Check is read-only apart from the local diagnostic report.
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Build', 'Package', 'CreateScene', 'PrepareCharacter', 'AssembleCharacter', 'BindCharacter', 'PreviewCharacter', 'Render', 'ExportAuthoring', 'ImportMotion', 'ImportOutfit', 'InstallOfficeAvatar', 'BindMotion', 'PreviewMotion', 'VerifyMotion', 'InspectFace', 'InstallFace', 'RepairFace', 'VerifyBlink', 'VerifyFace')]
    [string]$Action = 'Check',
    [string]$EngineRoot = $env:UE_ROOT,
    [switch]$Interactive,
    [switch]$NoHttpConnectionReuse,
    [switch]$WithACE,
    [ValidateRange(0, 64)]
    [int]$EditorCoreLimit = 0,
    [ValidateRange(1, 64)]
    [int]$MaxParallelActions = 2
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $projectRoot 'desktop\Decanat\Decanat.uproject'
$reportDirectory = Join-Path $projectRoot '.runtime\unreal'
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$issues = [System.Collections.Generic.List[string]]::new()

# Only select the candidate engine family; never silently upgrade the project.
$candidates = [System.Collections.Generic.List[string]]::new()
if ($EngineRoot) {
    $candidates.Add($EngineRoot)
} else {
    $launcherFile = Join-Path $env:ProgramData 'Epic\UnrealEngineLauncher\LauncherInstalled.dat'
    if (Test-Path -LiteralPath $launcherFile) {
        $launcher = Get-Content -LiteralPath $launcherFile -Raw | ConvertFrom-Json
        foreach ($installation in $launcher.InstallationList) {
            if ($installation.AppName -like 'UE_5.6*') { $candidates.Add($installation.InstallLocation) }
        }
    }
    foreach ($key in @('HKLM:\SOFTWARE\EpicGames\Unreal Engine\5.6',
                       'HKCU:\SOFTWARE\Epic Games\Unreal Engine\Builds')) {
        $entry = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($entry) {
            foreach ($property in $entry.PSObject.Properties) {
                if ($property.Name -eq 'InstalledDirectory' -or $property.Name -notlike 'PS*') {
                    if ($property.Value -is [string]) { $candidates.Add($property.Value) }
                }
            }
        }
    }
    $candidates.Add((Join-Path $env:ProgramFiles 'Epic Games\UE_5.6'))
    # The engine can be installed next to the checkout without Launcher registration.
    foreach ($directory in (Get-ChildItem -LiteralPath (Split-Path -Parent $projectRoot) -Directory -Filter 'UE_5.6*' -ErrorAction SilentlyContinue)) {
        $candidates.Add($directory.FullName)
    }
}
$engine = $null
foreach ($candidate in $candidates) {
    $versionFile = Join-Path $candidate 'Engine\Build\Build.version'
    if (!(Test-Path -LiteralPath $versionFile)) { continue }
    $version = Get-Content -LiteralPath $versionFile -Raw | ConvertFrom-Json
    if ($version.MajorVersion -eq 5 -and $version.MinorVersion -eq 6) {
        $engine = [ordered]@{
            root = (Resolve-Path -LiteralPath $candidate).Path
            version = '{0}.{1}.{2}' -f $version.MajorVersion, $version.MinorVersion, $version.PatchVersion
        }
        break
    }
}
if (!$engine) { $issues.Add('UE 5.6 not found. Install it in Epic Launcher or supply -EngineRoot / UE_ROOT.') }
if ($engine) {
    foreach ($relative in @('Engine\Build\BatchFiles\Build.bat',
                            'Engine\Binaries\Win64\UnrealEditor-Cmd.exe')) {
        if (!(Test-Path -LiteralPath (Join-Path $engine.root $relative))) {
            $issues.Add("Incomplete Unreal installation: $relative")
        }
    }
}

$toolchains = @()
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path -LiteralPath $vswhere) {
    $installations = & $vswhere -products '*' -version '[17.8,18.0)' -format json | ConvertFrom-Json
    foreach ($installation in $installations) {
        if (!$installation.isComplete) { continue }
        $toolsDirectory = Join-Path $installation.installationPath 'VC\Tools\MSVC'
        foreach ($toolset in (Get-ChildItem -LiteralPath $toolsDirectory -Directory -ErrorAction SilentlyContinue)) {
            $compiler = Join-Path $toolset.FullName 'bin\Hostx64\x64\cl.exe'
            if ([version]$toolset.Name -ge [version]'14.38.33130' -and (Test-Path -LiteralPath $compiler)) {
                $toolchains += [ordered]@{ visual_studio = $installation.installationVersion; msvc = $toolset.Name; compiler = $compiler }
            }
        }
    }
}
if (!$toolchains.Count) { $issues.Add('Visual Studio 2022 >=17.8 with x64 MSVC >=14.38.33130 not found.') }

$sdkVersions = @()
$sdkRegistry = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows Kits\Installed Roots' -ErrorAction SilentlyContinue
if ($sdkRegistry -and $sdkRegistry.KitsRoot10) {
    $sdkRoot = $sdkRegistry.KitsRoot10
    foreach ($include in (Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'Include') -Directory -ErrorAction SilentlyContinue)) {
        if ($include.Name -notmatch '^10\.0\.\d+\.\d+$') { continue }
        $sdkVersion = $include.Name
        if ([version]$sdkVersion -lt [version]'10.0.19041.0') { continue }
        $required = @("Include\$sdkVersion\um\Windows.h", "Include\$sdkVersion\ucrt\stdio.h",
                      "Lib\$sdkVersion\um\x64\kernel32.lib", "Lib\$sdkVersion\ucrt\x64\ucrt.lib",
                      "bin\$sdkVersion\x64\rc.exe")
        $missing = @($required | Where-Object { !(Test-Path -LiteralPath (Join-Path $sdkRoot $_)) })
        if (!$missing.Count) { $sdkVersions += $sdkVersion }
    }
}
if (!$sdkVersions.Count) { $issues.Add('Complete x64 Windows SDK >=10.0.19041.0 not found.') }

$netFxSdk = $null
foreach ($registryRoot in @('HKLM:\SOFTWARE\Microsoft\Microsoft SDKs\NETFXSDK',
                            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Microsoft SDKs\NETFXSDK')) {
    foreach ($key in (Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue)) {
        $sdk = Get-ItemProperty -LiteralPath $key.PSPath
        if ($sdk.KitsInstallationFolder -and
            (Test-Path -LiteralPath (Join-Path $sdk.KitsInstallationFolder 'Include\um\metahost.h')) -and
            (Test-Path -LiteralPath (Join-Path $sdk.KitsInstallationFolder 'Lib\um\x64\mscoree.lib'))) {
            $netFxSdk = $sdk.KitsInstallationFolder
        }
    }
}
if (!$netFxSdk) { $issues.Add('.NET Framework SDK not found; install Microsoft.Net.Component.4.8.SDK in VS Build Tools.') }

$report = [ordered]@{
    checked_at_utc = [DateTime]::UtcNow.ToString('o')
    engine = $engine
    toolchains = @($toolchains)
    windows_sdks = @($sdkVersions)
    net_framework_sdk = $netFxSdk
    metahuman_core_data_present = ($engine -and (Test-Path -LiteralPath (Join-Path $engine.root 'Engine\Plugins\MetaHuman\MetaHumanCharacter\Content\Optional\Presets')))
    prerequisites_detected = ($issues.Count -eq 0)
    verification_scope = 'prerequisites_only; actual build results are in build-no-ace.json'
    issues = @($issues.ToArray())
}
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $reportDirectory 'environment.json') -Encoding UTF8
$report | ConvertTo-Json -Depth 6 | Write-Output
if ($issues.Count) { exit 1 }
if ($Action -eq 'Check') { exit 0 }

# Build.bat invokes the engine's bundled .NET/UnrealBuildTool. An installed system
# dotnet or a compiler on PATH alone is not evidence that the project can build.
$previousACE = $env:DECANAT_WITH_ACE
$previousCharacterAction = $env:DECANAT_CHARACTER_ACTION
$previousCharacterInteractive = $env:DECANAT_CHARACTER_INTERACTIVE
$previousConnectionFile = $env:DECANAT_CONNECTION_FILE
$commandletScripts = @{
    PrepareCharacter = 'prepare_metahuman.py'; BindCharacter = 'bind_metahuman.py'
    ImportMotion = 'import_avatar_motion.py'; ImportOutfit = 'import_avatar_outfit.py'
    InstallOfficeAvatar = 'install_office_avatar.py'; BindMotion = 'bind_avatar_motion.py'
}
$editorScripts = @{
    Render = 'capture_unreal_scene.py'; PreviewCharacter = 'preview_metahuman.py'
    ExportAuthoring = 'export_avatar_authoring.py'; PreviewMotion = 'preview_avatar_motion.py'
    VerifyMotion = 'verify_office_motion.py'; AssembleCharacter = 'prepare_metahuman.py'
    InspectFace = 'inspect_avatar_face.py'; InstallFace = 'install_avatar_face.py'
    RepairFace = 'repair_face_mapping.py'; VerifyBlink = 'verify_avatar_blink.py'
    VerifyFace = 'verify_avatar_face.py'
}
try {
    if ($Action -in @('InspectFace', 'InstallFace', 'RepairFace', 'VerifyFace') -and !$WithACE) {
        throw 'This face action requires -WithACE and the installed local provider.'
    }
    $env:DECANAT_WITH_ACE = if ($WithACE) { '1' } else { '0' }
    $variant = if ($WithACE) { 'with-ace' } else { 'no-ace' }
    if ($WithACE -and !(Test-Path -LiteralPath (Join-Path $projectRoot 'desktop\Decanat\Plugins\NV_ACE_Reference\NV_ACE_Reference.uplugin'))) {
        throw 'Install the verified NVIDIA ACE UE 5.6 plugin before using -WithACE.'
    }
    $buildArguments = @('DecanatEditor', 'Win64', 'Development', "-Project=$project",
                        '-WaitMutex', '-NoHotReloadFromIDE', '-DisableAdaptiveUnity', "-MaxParallelActions=$MaxParallelActions",
                        "-Log=$(Join-Path $reportDirectory ("build-$variant.log"))")
    & (Join-Path $engine.root 'Engine\Build\BatchFiles\Build.bat') @buildArguments
    $buildExitCode = $LASTEXITCODE
    [ordered]@{ finished_at_utc = [DateTime]::UtcNow.ToString('o'); engine = $engine;
        target = 'DecanatEditor Win64 Development'; ace_enabled = [bool]$WithACE; exit_code = $buildExitCode
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $reportDirectory ("build-$variant.json")) -Encoding UTF8
    if ($buildExitCode -ne 0) { exit $buildExitCode }
    if ($Action -eq 'Package') {
        $gameArguments = @('Decanat', 'Win64', 'Development', "-Project=$project",
                          '-WaitMutex', '-NoHotReloadFromIDE', '-DisableAdaptiveUnity', "-MaxParallelActions=$MaxParallelActions",
                          "-Log=$(Join-Path $reportDirectory ("build-game-$variant.log"))")
        & (Join-Path $engine.root 'Engine\Build\BatchFiles\Build.bat') @gameArguments
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $archive = Join-Path $reportDirectory ("package-$variant")
        $cookCoreLimit = if ($EditorCoreLimit -gt 0) { $EditorCoreLimit } else { 4 }
        $packageStartedUtc = [DateTime]::UtcNow
        $packageArguments = @('BuildCookRun', "-project=$project", '-installed', '-nop4',
                             '-platform=Win64', '-clientconfig=Development', '-skipbuild', '-nocompileeditor',
                             '-cook', '-stage', '-pak', '-archive', "-archivedirectory=$archive",
                             '-map=/Game/Decanat/Office', '-unattended', '-utf8output',
                             "-AdditionalCookerOptions=-corelimit=$cookCoreLimit")
        & (Join-Path $engine.root 'Engine\Build\BatchFiles\RunUAT.bat') @packageArguments
        $packageExitCode = $LASTEXITCODE
        $executable = Join-Path $archive 'Windows\Decanat\Binaries\Win64\Decanat.exe'
        [ordered]@{ started_at_utc = $packageStartedUtc.ToString('o'); finished_at_utc = [DateTime]::UtcNow.ToString('o');
            engine = $engine; target = 'Decanat Win64 Development'; ace_enabled = [bool]$WithACE;
            exit_code = $packageExitCode; executable = $executable; runtime_verified = $false
        } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $reportDirectory ("package-$variant.json")) -Encoding UTF8
        if ($packageExitCode -ne 0) { exit $packageExitCode }
        if (!(Test-Path -LiteralPath $executable)) { throw 'Packaging exited without the actual game executable.' }
        Write-Output "Packaged Development build ($variant): $executable. Runtime checks remain required."
    } elseif ($Action -eq 'CreateScene') {
        $mapFile = Join-Path $projectRoot 'desktop\Decanat\Content\Decanat\Office.umap'
        if (Test-Path -LiteralPath $mapFile) { throw 'Office.umap already exists; open the existing scene in the editor.' }
        $sceneScript = Join-Path $PSScriptRoot 'create_unreal_scene.py'
        # The Python commandlet propagates Python failures to the process exit code.
        # The generated environment is not a render or approval of a MetaHuman.
        & (Join-Path $engine.root 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe') $project `
            '-run=pythonscript' "-script=$sceneScript" '-unattended' '-nullrhi' '-nosplash' `
            "-abslog=$(Join-Path $reportDirectory 'create-scene.log')"
        $sceneExitCode = $LASTEXITCODE
        if ($sceneExitCode -ne 0) { exit $sceneExitCode }
        if (!(Test-Path -LiteralPath $mapFile)) { throw 'Editor exited without saving Office.umap; inspect create-scene.log.' }
        Write-Output "Office.umap created. Open Unreal Editor to inspect the scene and create the real MetaHuman."
    } elseif ($commandletScripts.ContainsKey($Action)) {
        $env:DECANAT_CHARACTER_ACTION = 'prepare'
        $characterScript = Join-Path $PSScriptRoot $commandletScripts[$Action]
        $characterLog = if ($Action -eq 'PrepareCharacter') { 'prepare-character.log' } else { $Action + '.log' }
        & (Join-Path $engine.root 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe') $project `
            '-run=pythonscript' "-script=$characterScript" '-unattended' '-nullrhi' '-nosplash' `
            "-abslog=$(Join-Path $reportDirectory $characterLog)"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } elseif ($editorScripts.ContainsKey($Action)) {
        $runStartedUtc = [DateTime]::UtcNow
        if ($Action -eq 'AssembleCharacter') {
            $env:DECANAT_CHARACTER_ACTION = 'complete'
            $env:DECANAT_CHARACTER_INTERACTIVE = if ($Interactive) { '1' } else { '0' }
            $editorScript = Join-Path $PSScriptRoot 'prepare_metahuman.py'
            # Authoring the character does not need Office; avoid locking its map
            # file while Epic authorization is pending in another editor window.
            $startupMap = '/Engine/Maps/Entry'
        } else {
            $editorScript = Join-Path $PSScriptRoot $editorScripts[$Action]
            $startupMap = if ($Action -eq 'ExportAuthoring') { '/Engine/Maps/Entry' } else { '/Game/Decanat/Office' }
        }
        if ($Action -in @('VerifyMotion', 'VerifyBlink', 'VerifyFace')) { $env:DECANAT_CONNECTION_FILE = $null }
        $editorArguments = @($project, $startupMap, "-ExecutePythonScript=$editorScript", '-nosplash',
                             '-d3d12', '-windowed', '-ResX=1280', '-ResY=720',
                             "-abslog=$(Join-Path $reportDirectory ($Action + '.log'))")
        if (!$Interactive) { $editorArguments += '-unattended' }
        if ($NoHttpConnectionReuse) { $editorArguments += '-noreuseconn' }
        if ($EditorCoreLimit -gt 0) { $editorArguments += "-corelimit=$EditorCoreLimit" }
        $editorExecutable = if ($Interactive) { 'UnrealEditor.exe' } else { 'UnrealEditor-Cmd.exe' }
        $attempt = [ordered]@{
            started_at_utc = $runStartedUtc.ToString('o')
            action = $Action
            interactive = [bool]$Interactive
            http_connection_reuse = !$NoHttpConnectionReuse
            process_state = 'running'
            exit_code = $null
        }
        $attemptFile = Join-Path $reportDirectory ($Action + '-attempt.json')
        $attempt | ConvertTo-Json | Set-Content -LiteralPath $attemptFile -Encoding UTF8
        & (Join-Path $engine.root ('Engine\Binaries\Win64\' + $editorExecutable)) @editorArguments
        $editorExitCode = $LASTEXITCODE
        $attempt.process_state = if ($editorExitCode -eq 0) { 'exited' } else { 'failed' }
        $attempt.exit_code = $editorExitCode
        $attempt.finished_at_utc = [DateTime]::UtcNow.ToString('o')
        $attempt | ConvertTo-Json | Set-Content -LiteralPath $attemptFile -Encoding UTF8
        if ($editorExitCode -ne 0) { exit $editorExitCode }
        if ($Action -in @('Render', 'PreviewCharacter')) {
            $renderName = if ($Action -eq 'PreviewCharacter') { 'metahuman-preview.png' } else { 'office.png' }
            $render = Get-Item -LiteralPath (Join-Path $reportDirectory $renderName) -ErrorAction SilentlyContinue
            if (!$render -or $render.LastWriteTimeUtc -lt $runStartedUtc) { throw 'No new render saved; inspect Render.log.' }
        } elseif ($Action -eq 'PreviewMotion') {
            foreach ($pose in @('seated', 'standing', 'rising', 'sitting')) {
                $render = Get-Item -LiteralPath (Join-Path $reportDirectory ("motion-$pose.png")) -ErrorAction SilentlyContinue
                if (!$render -or $render.LastWriteTimeUtc -lt $runStartedUtc) { throw "No new $pose render; inspect PreviewMotion.log." }
            }
        } elseif ($Action -eq 'VerifyMotion') {
            $verificationFile = Get-Item -LiteralPath (Join-Path $reportDirectory 'office-motion-verification.json') -ErrorAction SilentlyContinue
            if (!$verificationFile -or $verificationFile.LastWriteTimeUtc -lt $runStartedUtc) { throw 'No fresh motion verification report.' }
            $verification = Get-Content -LiteralPath $verificationFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            if (!$verification.engine_contact_and_boundary_checks_passed -or !$verification.pie_passed) { throw ("Motion verification failed: " + $verification.issue) }
        } elseif ($Action -in @('VerifyBlink', 'VerifyFace')) {
            $verificationName = if ($Action -eq 'VerifyBlink') { 'avatar-blink-verification.json' } else { 'avatar-face-verification.json' }
            $verificationFile = Get-Item -LiteralPath (Join-Path $reportDirectory $verificationName) -ErrorAction SilentlyContinue
            if (!$verificationFile -or $verificationFile.LastWriteTimeUtc -lt $runStartedUtc) { throw 'No fresh face verification report.' }
            $verification = Get-Content -LiteralPath $verificationFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            if (!$verification.passed) { throw ("Face verification failed: " + $verification.issue) }
        } elseif ($Action -eq 'ExportAuthoring') {
            foreach ($file in @('body.fbx', 'outfit.fbx', 'skeleton.json')) {
                if (!(Test-Path -LiteralPath (Join-Path $reportDirectory ("authoring/$file")))) { throw "Authoring export missing: $file" }
            }
        } elseif ($Action -eq 'AssembleCharacter') {
            $characterReportFile = Join-Path $reportDirectory 'metahuman.json'
            if ((Get-Item -LiteralPath $characterReportFile).LastWriteTimeUtc -lt $runStartedUtc) {
                throw 'No new character report saved; inspect AssembleCharacter.log.'
            }
            $characterReport = Get-Content -LiteralPath $characterReportFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($characterReport.stage -ne 'assembly_output_found_inspection_required') {
                throw ("MetaHuman assembly incomplete: " + $characterReport.issue + '. Inspect AssembleCharacter.log.')
            }
        }
    }
} finally {
    $env:DECANAT_WITH_ACE = $previousACE
    $env:DECANAT_CHARACTER_ACTION = $previousCharacterAction
    $env:DECANAT_CHARACTER_INTERACTIVE = $previousCharacterInteractive
    $env:DECANAT_CONNECTION_FILE = $previousConnectionFile
}
