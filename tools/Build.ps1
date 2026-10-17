# Единый вход ENV-02. Все результаты остаются в build/ или dist/.
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Test', 'Extract', 'Check', 'Pack', 'Build', 'Text', 'IR', 'Validate', 'Levels', 'Graphics', 'Animations', 'AuxGraphics', 'Sounds', 'Standalone')]
    [string]$Task = 'Test',
    [string]$Source,
    [string]$Output,
    [string]$Stage,
    [string]$Archive,
    [string]$IR,
    [ValidateSet('raw', 'research', 'release')]
    [string]$Mode = 'raw'
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Source) { $Source = Join-Path $repo 'originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT' }
if (-not $Output) {
    if ($Task -eq 'Standalone') { $Output = Join-Path $repo 'build/uzd-01/smuta-uzd01.ipk3' }
    elseif ($Task -eq 'Text') { $Output = Join-Path $repo 'build/decoded/smuta-text' }
    elseif ($Task -eq 'IR') { $Output = Join-Path $repo 'build/ir/smuta' }
    elseif ($Task -eq 'Validate') { $Output = Join-Path $repo 'build/ir/smuta-validation.json' }
    elseif ($Task -eq 'Levels') { $Output = Join-Path $repo 'build/research/smuta-levels' }
    elseif ($Task -eq 'Graphics') { $Output = Join-Path $repo 'build/decoded/smuta-graphics' }
    elseif ($Task -eq 'Animations') { $Output = Join-Path $repo 'build/decoded/smuta-animations' }
    elseif ($Task -eq 'AuxGraphics') { $Output = Join-Path $repo 'build/decoded/smuta-auxgraphics' }
    elseif ($Task -eq 'Sounds') { $Output = Join-Path $repo 'build/decoded/smuta-sounds' }
    else { $Output = Join-Path $repo 'build/extracted/smuta' }
}
$venv = Join-Path $repo 'build/tools-venv'
$python = Join-Path $venv 'Scripts/python.exe'

function Invoke-Python([string[]]$PythonArguments) {
    & $python -B @PythonArguments
    if ($LASTEXITCODE -ne 0) { throw "Python завершился с кодом $LASTEXITCODE" }
}

# Не позволяем Setup писать через junction в локальное окружение.
foreach ($relative in @('build', 'build/tools-venv')) {
    $path = Join-Path $repo $relative
    if (Test-Path -LiteralPath $path) {
        if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Ссылка вместо рабочего каталога: $path"
        }
    }
}

if ($Task -eq 'Setup') {
    if (Test-Path -LiteralPath $venv) { throw 'Окружение уже существует; используйте его или сохраните под другим именем.' }
    & py -3.14 -B -m venv --without-pip $venv
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать окружение Python 3.14.' }
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Сначала выполните tools/Build.ps1 -Task Setup.' }
Invoke-Python -PythonArguments @('-c', 'import sys; assert sys.version_info[:2] == (3, 14), sys.version; print(sys.version)')
if ($Task -eq 'Setup') { return }
if ($Task -eq 'Pack' -or ($Task -eq 'Build' -and ($Stage -or $Archive))) {
    if (-not $Stage -or -not $Archive) { throw 'Для упаковки обязательны -Stage и -Archive.' }
}

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $repo 'tools'
    if ($Task -eq 'Standalone') {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'standalone', '--output', $Output)
    }
    if ($Task -in @('Test', 'Build')) {
        Invoke-Python -PythonArguments @('-m', 'unittest', 'discover', '-s', (Join-Path $repo 'tests'), '-v')
    }
    if ($Task -in @('Extract', 'Build')) {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'extract', '--source', $Source, '--output', $Output)
    }
    if ($Task -in @('Check', 'Build')) {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'check', '--source', $Source, '--output', $Output)
    }
    if ($Task -eq 'Text') {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'text', '--source', $Source, '--output', $Output)
    }
    if ($Task -eq 'IR') {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'ir', '--source', $Source, '--output', $Output)
    }
    if ($Task -eq 'Levels') {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'levels', '--source', $Source, '--output', $Output)
    }
    if ($Task -eq 'Graphics') {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'graphics', '--source', $Source, '--output', $Output)
    }
    if ($Task -eq 'Animations') {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'animations', '--source', $Source, '--output', $Output)
    }
    if ($Task -eq 'AuxGraphics') {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'auxgraphics', '--source', $Source, '--output', $Output)
    }
    if ($Task -eq 'Sounds') {
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'sounds', '--source', $Source, '--output', $Output)
    }
    if ($Task -eq 'Validate') {
        if (-not $IR) { $IR = Join-Path $repo 'build/ir/smuta/ir.json' }
        if ($Mode -eq 'raw') { $Mode = 'research' }
        Invoke-Python -PythonArguments @('-m', 'smuta_build', 'validate', '--source', $Source, '--ir', $IR, '--output', $Output, '--mode', $Mode)
    }
    if ($Task -eq 'Pack' -or ($Task -eq 'Build' -and $Stage)) {
        $packArguments = @('-m', 'smuta_build', 'pack', '--source', $Stage, '--output', $Archive, '--mode', $Mode)
        if ($IR) { $packArguments += @('--ir', $IR, '--dat-source', $Source) }
        Invoke-Python -PythonArguments $packArguments
    }
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
