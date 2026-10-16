# Рабочая среда ENV-01. Исходные каталоги используются только для чтения.
[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'Probe', 'Smuta', 'Version', 'UZDoom')]
    [string]$Action = 'Prepare',
    [string]$Ipk3
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$run = Join-Path $repo 'build/run'
$journal = Join-Path $run ('logs/' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
$sources = [ordered]@{
    smuta = Join-Path $repo 'originals/smuta_modern_20261013/smuta_modern'
    uzdoom = Join-Path $repo 'uzdoom'
}

function Get-Inventory {
    foreach ($name in $sources.Keys) {
        $root = $sources[$name]
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        foreach ($file in (Get-ChildItem -LiteralPath $root -File -Recurse | Sort-Object FullName)) {
            if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Ссылка вместо исходного файла: $($file.FullName)"
            }
            [pscustomobject][ordered]@{
                Source = $name
                Path = $file.FullName.Substring($root.Length + 1).Replace('\', '/')
                Length = $file.Length
                SHA256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
    }
}

function Invoke-LocalProcess([string]$Name, [string]$Exe, [string[]]$Arguments, [int]$TimeoutSeconds) {
    $cwd = Split-Path -Parent $Exe
    # Start-Process соединяет ArgumentList строкой; пути заключаем в кавычки явно.
    $quoted = @($Arguments | ForEach-Object {
        if ($_ -match '["\r\n]') { throw 'Недопустимый символ в аргументе запуска.' }
        '"' + $_ + '"'
    })
    $start = @{
        FilePath = $Exe; WorkingDirectory = $cwd; PassThru = $true; WindowStyle = 'Hidden'
    }
    # Игровой запуск должен оставаться обычным интерактивным запуском.
    # Скрытое окно и принудительная остановка не проверяют работоспособность игры.
    if ($TimeoutSeconds -eq 0) {
        $start.Remove('WindowStyle')
    } else {
        $start.RedirectStandardOutput = Join-Path $journal "$Name.stdout.txt"
        $start.RedirectStandardError = Join-Path $journal "$Name.stderr.txt"
    }
    if ($quoted.Count) { $start.ArgumentList = $quoted }
    $result = [ordered]@{ Name = $Name; Executable = $Exe; WorkingDirectory = $cwd; Arguments = $Arguments }
    $errorLog = Join-Path $cwd 'ERROR/ERROR.TXT'
    if ($Name -eq 'smuta' -and (Test-Path -LiteralPath $errorLog)) {
        $result.ErrorLogHashBefore = (Get-FileHash -LiteralPath $errorLog).Hash
        Copy-Item -LiteralPath $errorLog -Destination (Join-Path $journal 'smuta-error-before.txt')
    }
    $process = $null
    try {
        $process = Start-Process @start
        $null = $process.Handle
        $result.ProcessId = $process.Id
        if ($TimeoutSeconds -gt 0) {
            $exited = $process.WaitForExit($TimeoutSeconds * 1000)
            $process.Refresh()
            $result.ExitedBeforeTimeout = $exited
            $result.WindowTitle = $process.MainWindowTitle
            $result.Responding = $process.Responding
            if (-not $exited) {
                $closed = $process.CloseMainWindow()
                $result.CloseRequested = $closed
                if (-not $process.WaitForExit(2000)) {
                    $process.Kill()
                    $process.WaitForExit()
                    $result.ForcedStop = $true
                }
            }
        } else {
            $process.WaitForExit()
        }
        $result.ExitCode = $process.ExitCode
        if ($Name -eq 'smuta' -and (Test-Path -LiteralPath $errorLog)) {
            $result.ErrorLogHashAfter = (Get-FileHash -LiteralPath $errorLog).Hash
            $result.ErrorLogChanged = $result.ErrorLogHashBefore -ne $result.ErrorLogHashAfter
            Copy-Item -LiteralPath $errorLog -Destination (Join-Path $journal 'smuta-error.txt')
        }
    } catch {
        $result.Error = $_.Exception.Message
        throw
    } finally {
        [pscustomobject]$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $journal "$Name.json") -Encoding UTF8
        if ($process) { $process.Dispose() }
    }
}

New-Item -ItemType Directory -Path $journal -Force | Out-Null
$before = @(Get-Inventory)
$before | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $journal 'sources-before.json') -Encoding UTF8
$report = [ordered]@{
    TimestampUtc = [DateTime]::UtcNow.ToString('o'); Action = $Action
    OS = [Environment]::OSVersion.VersionString
    OS64Bit = [Environment]::Is64BitOperatingSystem
    PowerShell = $PSVersionTable.PSVersion.ToString()
    MissingSources = @($sources.Keys | Where-Object { -not (Test-Path -LiteralPath $sources[$_]) })
}
try {
    foreach ($name in $sources.Keys) {
        if ($report.MissingSources -contains $name) { continue }
        $destination = Join-Path $run $name
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        # Без перезаписи: повторная подготовка сохраняет INI, SAVE и результаты замеров.
        foreach ($directory in (Get-ChildItem -LiteralPath $sources[$name] -Directory -Recurse)) {
            if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Ссылки в источнике не поддерживаются.' }
            New-Item -ItemType Directory -Path (Join-Path $destination $directory.FullName.Substring($sources[$name].Length + 1)) -Force | Out-Null
        }
        foreach ($entry in @($before | Where-Object Source -eq $name)) {
            $target = Join-Path $destination $entry.Path
            if (-not (Test-Path -LiteralPath $target)) {
                Copy-Item -LiteralPath (Join-Path $sources[$name] $entry.Path) -Destination $target
            }
            # Конфигурации и результаты могут меняться; исполняемые файлы и ресурсы — нет.
            if ($entry.Path -match '(?i)\.(exe|dll|dat|pk3|spr|cmd|ogg)$') {
                if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.SHA256) {
                    throw "Рабочая копия отличается от источника: $target. Используйте новый каталог build/run после архивации замеров."
                }
            }
        }
    }
    foreach ($relative in @('smuta/SAVE', 'smuta/BMP', 'smuta/ERROR', 'smuta/TEST', 'uzdoom/saves', 'uzdoom/screenshots')) {
        New-Item -ItemType Directory -Path (Join-Path $run $relative) -Force | Out-Null
    }
    $engine = Join-Path $run 'uzdoom/uzdoom.exe'
    if (Test-Path -LiteralPath $sources.uzdoom) {
        $report.UZDoomFileVersion = (Get-Item -LiteralPath $engine).VersionInfo.FileVersion
    }
    $engineArgs = @('-config', (Join-Path $run 'uzdoom/uzdoom.ini'),
        '-savedir', (Join-Path $run 'uzdoom/saves'), '-shotdir', (Join-Path $run 'uzdoom/screenshots'),
        '-noautoload', '-noautoexec', '+logfile', (Join-Path $journal 'uzdoom.log'))
    if ($Action -in @('Probe', 'Version')) {
        if ($report.MissingSources -contains 'uzdoom') { throw 'UZDoom недоступен: отсутствует локальный дистрибутив.' }
        Invoke-LocalProcess 'uzdoom-version' $engine (@('-version') + $engineArgs) 10
    }
    if ($Action -eq 'Smuta') {
        if ($report.MissingSources -contains 'smuta') { throw 'Эталон Смуты недоступен.' }
        Invoke-LocalProcess 'smuta' (Join-Path $run 'smuta/SMUTA.EXE') @() 0
    }
    if ($Action -eq 'UZDoom') {
        if ($report.MissingSources -contains 'uzdoom') { throw 'UZDoom недоступен.' }
        if (-not $Ipk3 -or -not (Test-Path -LiteralPath $Ipk3 -PathType Leaf)) { throw 'Укажите существующий самостоятельный пакет через -Ipk3.' }
        $package = (Resolve-Path -LiteralPath $Ipk3).Path
        if ([IO.Path]::GetExtension($package) -ne '.ipk3') { throw 'Нужен самостоятельный .ipk3.' }
        Invoke-LocalProcess 'uzdoom' $engine (@('-iwad', $package) + $engineArgs) 0
    }
    $report.Completed = $true
} catch {
    $report.Error = $_.Exception.Message
    throw
} finally {
    $after = @(Get-Inventory)
    $after | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $journal 'sources-after.json') -Encoding UTF8
    $difference = @(Compare-Object ($before | ConvertTo-Json -Depth 4) ($after | ConvertTo-Json -Depth 4))
    $report.SourcesUnchanged = $difference.Count -eq 0
    $report | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $journal 'environment.json') -Encoding UTF8
    Write-Output "Журнал среды: $journal"
    if ($difference.Count) { throw 'Исходные файлы изменились; смотрите манифесты журнала.' }
}
