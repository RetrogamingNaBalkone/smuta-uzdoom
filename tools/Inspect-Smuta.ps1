# Static inspection only. Never executes or modifies the supplied game files.
[CmdletBinding()]
param(
    [string]$SourceDirectory = (Join-Path $PSScriptRoot '../originals/smuta_modern_20261013/smuta_modern'),
    [switch]$Catalog
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path
$archivePath = Join-Path $sourceRoot 'SMUTA.DAT'
$bytes = [IO.File]::ReadAllBytes($archivePath)
if (-not [BitConverter]::IsLittleEndian) { throw 'Requires a little-endian host.' }
if ($bytes.Length -lt 2) { throw 'Truncated archive header.' }
$count = [BitConverter]::ToUInt16($bytes, 0)
$headerEnd = 2 + 30 * $count
if ($headerEnd -gt $bytes.Length) { throw 'Truncated archive directory.' }
$encoding = [Text.Encoding]::GetEncoding(1251)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $entries = @(for ($index = 0; $index -lt $count; $index++) {
        $recordOffset = 2 + 30 * $index
        $nameField = $encoding.GetString($bytes, $recordOffset, 16)
        $terminator = $nameField.IndexOf([char]0)
        if ($terminator -lt 0) { throw "Unterminated name in entry $index." }
        $name = $nameField.Substring(0, $terminator)
        $length = [BitConverter]::ToUInt32($bytes, $recordOffset + 16)
        $offset = [BitConverter]::ToUInt32($bytes, $recordOffset + 20)
        if ($offset -lt $headerEnd -or [long]$offset + $length -gt $bytes.Length) {
            throw "Invalid range in entry $index."
        }
        $signature = [Text.Encoding]::ASCII.GetString($bytes, $offset, [Math]::Min(4, $length))
        $kind = 'unclassified'
        if ($signature -eq 'RIFF' -and $length -ge 12 -and
            [Text.Encoding]::ASCII.GetString($bytes, $offset + 8, 4) -eq 'WAVE') {
            $kind = 'riff-wave'
        } elseif ($signature.StartsWith('BM')) {
            $kind = 'bmp'
        } elseif ($name -match '^(LEVEL\d+-\d+|DEMO\d*|TEST)$') {
            $kind = 'level-text-candidate'
        } elseif ($name -match '\.SPR$') {
            $kind = 'sprite-candidate'
        } elseif ($name -match '\.FL$') {
            $kind = 'animation-candidate'
        }
        [pscustomobject][ordered]@{
            Index = $index
            Name = $name
            DirectoryOffset = $recordOffset
            Offset = $offset
            Length = $length
            Kind = $kind
            SHA256 = [BitConverter]::ToString($sha.ComputeHash($bytes, $offset, $length)).Replace('-', '').ToLowerInvariant()
            RawDirectoryHex = [BitConverter]::ToString($bytes, $recordOffset, 30)
        }
    })
    if ($Catalog) {
        $entries
        return
    }
    $nonContiguous = @(for ($i = 1; $i -lt $entries.Count; $i++) {
        if ($entries[$i].Offset -ne ([long]$entries[$i - 1].Offset + $entries[$i - 1].Length)) { $i }
    })
    $lastEnd = $headerEnd
    if ($entries.Count -gt 0) {
        $lastEnd = ($entries | ForEach-Object { [long]$_.Offset + $_.Length } | Measure-Object -Maximum).Maximum
    }
    [pscustomobject][ordered]@{
        Archive = $archivePath
        ArchiveSHA256 = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
        ArchiveBytes = $bytes.Length
        EntryCount = $count
        DirectoryEnd = $headerEnd
        NonContiguousEntries = $nonContiguous.Count
        TrailingBytes = $bytes.Length - $lastEnd
        DuplicateNames = @($entries | Group-Object Name | Where-Object Count -gt 1 | ForEach-Object {
            [pscustomobject]@{ Name = $_.Name; Indices = @($_.Group.Index) }
        })
        Kinds = @($entries | Group-Object Kind | ForEach-Object {
            [pscustomobject]@{ Kind = $_.Name; Count = $_.Count }
        })
    }
} finally {
    $sha.Dispose()
}
