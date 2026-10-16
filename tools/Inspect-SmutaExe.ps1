# Статическое исследование: исходники открываются только на чтение.
[CmdletBinding()]
param(
    [string]$SourceDirectory = (Join-Path $PSScriptRoot '../originals/smuta_modern_20261013/smuta_modern'),
    [string]$Objdump,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '../build/research/smuta-exe')
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $SourceDirectory).Path
$exePath = Join-Path $root 'SMUTA.EXE'
$datPath = Join-Path $root 'SMUTA.DAT'
$exe = [IO.File]::ReadAllBytes($exePath)
$dat = [IO.File]::ReadAllBytes($datPath)
$sha = [Security.Cryptography.SHA256]::Create()
function Get-Hash([byte[]]$Bytes, [int]$Offset = 0, [int]$Length = -1) {
    if ($Length -lt 0) { $Length = $Bytes.Length }
    [BitConverter]::ToString($sha.ComputeHash($Bytes, $Offset, $Length)).Replace('-', '').ToLowerInvariant()
}
function Assert-Range([byte[]]$Bytes, [long]$Offset, [long]$Length) {
    if ($Offset -lt 0 -or $Length -lt 0 -or $Offset + $Length -gt $Bytes.Length) {
        throw "Недопустимый диапазон: $Offset + $Length / $($Bytes.Length)."
    }
}
function Read-U16([int]$Offset) { Assert-Range $exe $Offset 2; [BitConverter]::ToUInt16($exe, $Offset) }
function Read-U32([int]$Offset) { Assert-Range $exe $Offset 4; [BitConverter]::ToUInt32($exe, $Offset) }
try {
    $exeHash = Get-Hash $exe
    $datHash = Get-Hash $dat
    if ($exeHash -ne '1b7f8e5d010e225139f3a03f3721aff39d5e0b8c1c4ef42106c14002ea6405bb' -or
        $datHash -ne '6e13c4b65e4530291b11e3d4cd6280d874c489837e7e74c5f53b686ca69a74e8') {
        throw 'Неизвестный EXE/DAT: адреса и выводы относятся только к зафиксированному эталону.'
    }
    if ((Read-U16 0) -ne 0x5a4d) { throw 'Нет MZ.' }
    $pe = Read-U32 0x3c
    if ((Read-U32 $pe) -ne 0x4550 -or (Read-U16 ($pe + 4)) -ne 0x14c) { throw 'Ожидается PE i386.' }
    $optional = $pe + 24
    if ((Read-U16 $optional) -ne 0x10b) { throw 'Ожидается PE32.' }
    $imageBase = Read-U32 ($optional + 28)
    $sections = @(for ($i = 0; $i -lt (Read-U16 ($pe + 6)); $i++) {
        $p = $optional + (Read-U16 ($pe + 20)) + 40 * $i
        Assert-Range $exe $p 40
        $raw = Read-U32 ($p + 20)
        $size = Read-U32 ($p + 16)
        Assert-Range $exe $raw $size
        [pscustomobject][ordered]@{
            Name = [Text.Encoding]::ASCII.GetString($exe, $p, 8).Trim([char]0)
            RVA = Read-U32 ($p + 12); RawOffset = $raw; RawSize = $size
        }
    })
    function Get-FileOffset([uint32]$VA, [int]$Length = 1) {
        foreach ($section in $sections) {
            $delta = [long]$VA - $imageBase - $section.RVA
            if ($delta -ge 0 -and $delta + $Length -le $section.RawSize) {
                return [int]($section.RawOffset + $delta)
            }
        }
        throw ('VA {0:X8} не имеет полного файлового диапазона.' -f $VA)
    }
    $dispatchOffset = Get-FileOffset 0x41dde8 56
    $vtables = @(0x436b28,0x436930,0x436af8,0x436ac4,0x436a94,0x436964,0x4368d0,
        0x4368a0,0x436900,0x43686c,0x436a60,0,0x4369e4,0x436998)
    $classes = @(for ($type = 0; $type -le 13; $type++) {
        $branch = Read-U32 ($dispatchOffset + 4 * $type)
        if ($type -eq 11) {
            if ($branch -ne 0x41e20a) { throw 'Изменилась ветка типа 11.' }
            [pscustomobject][ordered]@{ Type = $type; Class = $null; FactoryBranchVA = ('{0:X8}' -f $branch); VTableVA = $null; LoadVA = $null; EventVA = $null }
            continue
        }
        $v = $vtables[$type]
        $vp = Get-FileOffset $v 40
        $rtti = Read-U32 (Get-FileOffset ($v - 12) 4)
        $nameOffset = Get-FileOffset ($rtti + 48)
        $end = $nameOffset
        while ($true) {
            Assert-Range $exe $end 1
            if ($exe[$end] -eq 0) { break }
            $end++
        }
        [pscustomobject][ordered]@{
            Type = $type
            Class = [Text.Encoding]::ASCII.GetString($exe, $nameOffset, $end - $nameOffset)
            FactoryBranchVA = ('{0:X8}' -f $branch)
            VTableVA = ('{0:X8}' -f $v)
            LoadVA = ('{0:X8}' -f (Read-U32 ($vp + 4)))
            EventVA = ('{0:X8}' -f (Read-U32 ($vp + 32)))
        }
    })
    # Все варианты проверяются отдельно; полные сетки и тексты не экспортируются в метаданные.
    $entries = @(& (Join-Path $PSScriptRoot 'Inspect-Smuta.ps1') -SourceDirectory $root -Catalog)
    $encoding = [Text.Encoding]::GetEncoding(1251)
    $levels = @(foreach ($entry in $entries | Where-Object Name -Match '^LEVEL\d+-[123]$') {
        $lines = $encoding.GetString($dat, [int]$entry.Offset, [int]$entry.Length) -split '\r\n|\n|\r'
        $section = ''; $definitions = @(); $grids = @{}; $gridRows = @(); $gridName = ''
        $counts = @{}
        for ($lineIndex = 0; $lineIndex -lt $lines.Count; $lineIndex++) {
            $line = $lines[$lineIndex]
            if ($line -match '^\s*\[([^\]]+)\]') {
                if ($gridName) { $grids[$gridName] = @($gridRows); $gridName = ''; $gridRows = @() }
                $section = $Matches[1]
                if ($section -in @('TMap','FCMap')) { $gridName = $section }
                continue
            }
            if ($gridName) {
                # Строки сетки не заключены в кавычки; пробелы значимы.
                if ($line.Length -gt 0 -and -not $line.StartsWith('#')) { $gridRows += $line }
            } elseif ($section -eq 'Game Object' -and $line -match '^[ \t]*([^ \t])[ \t]+(-?\d+)[ \t]+') {
                $type = [int]$Matches[2]
                if ($type -lt 0 -or $type -gt 13 -or $type -eq 11) { throw "Неизвестный тип $type в $($entry.Name):$($lineIndex + 1)." }
                $counts[$type] = 1 + [int]$counts[$type]
                $definitions += $line
            }
        }
        if ($gridName) { $grids[$gridName] = @($gridRows) }
        $gridMetadata = @(foreach ($name in @('TMap','FCMap')) {
            $rows = @($grids[$name])
            if ($rows.Count -ne 64 -or @($rows | Where-Object Length -ne 64).Count) { throw "Повреждена сетка $name в $($entry.Name)." }
            [pscustomobject][ordered]@{ Name = $name; Width = 64; Height = 64; SHA256 = Get-Hash ($encoding.GetBytes(($rows -join "`n"))) }
        })
        [pscustomobject][ordered]@{
            Index = [int]$entry.Index; Name = $entry.Name; Offset = [int]$entry.Offset
            Length = [int]$entry.Length; SHA256 = $entry.SHA256
            DefinitionCount = $definitions.Count
            DefinitionsSHA256 = Get-Hash ($encoding.GetBytes(($definitions -join "`n")))
            Types = @($counts.Keys | Sort-Object | ForEach-Object { [pscustomobject]@{ Type = $_; Count = $counts[$_] } })
            Grids = $gridMetadata
        }
    })
    if ($levels.Count -ne 30) { throw 'Ожидается 30 вариантов уровней.' }
    $disassembler = $null
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    if ($Objdump) {
        $Objdump = (Resolve-Path -LiteralPath $Objdump).Path
        $disassembler = (@(& $Objdump --version) | Select-Object -First 3) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось получить версию objdump.' }
        & $Objdump -d --x86-asm-syntax=intel $exePath | Set-Content -Encoding UTF8 (Join-Path $OutputDirectory 'disasm.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Ошибка дизассемблирования.' }
    }
    $report = [pscustomobject][ordered]@{
        SchemaVersion = 1; Game = 'smuta'; ExeSHA256 = $exeHash; DatSHA256 = $datHash
        Engine = 'UZDoom не запускается этим инструментом; исследуется исходный EXE'
        Method = 'Статический разбор PE, таблицы диспетчеризации, RTTI и метаданных DAT; без запуска игры'
        ImageBase = ('{0:X8}' -f $imageBase); Disassembler = $disassembler
        Sections = $sections; Classes = $classes; Levels = $levels
    }
    $report | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $OutputDirectory 'metadata.json')
    $classes
} finally { $sha.Dispose() }
