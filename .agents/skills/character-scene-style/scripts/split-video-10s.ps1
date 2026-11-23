<#
.SYNOPSIS
固定时长（默认10秒）粗切视频与首尾源帧提取辅助脚本。
注意：本脚本仅供单镜固定时长粗切初始化使用；正式项目按真实镜头切点分段（不要机械按10秒拆开完整镜头）及一段多镜（各镜头首帧+1张片段尾帧）提取时，请按实际确认的源时间区间与《待生图帧清单.json》执行。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputVideo,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$FfmpegPath = '',

    [string]$SourceAnalysisDir = '',

    [int]$SegmentTime = 10
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $InputVideo -PathType Leaf)) {
    throw "Input video not found: $InputVideo"
}

if ([string]::IsNullOrWhiteSpace($FfmpegPath) -or -not (Test-Path -LiteralPath $FfmpegPath -PathType Leaf)) {
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($cmd) {
        $FfmpegPath = $cmd.Source
    } else {
        $fallbacks = @(
            'C:\ffmpeg\bin\ffmpeg.exe',
            'C:\Program Files\ffmpeg\bin\ffmpeg.exe'
        )
        $FfmpegPath = $fallbacks | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
}

if ([string]::IsNullOrWhiteSpace($FfmpegPath) -or -not (Test-Path -LiteralPath $FfmpegPath -PathType Leaf)) {
    throw "FFmpeg not found. Pass -FfmpegPath with its full path."
}

$segmentsDirectory = Join-Path $OutputRoot '原视频段落'
$framesDirectory = Join-Path $OutputRoot '首尾及关键帧'
if ([string]::IsNullOrWhiteSpace($SourceAnalysisDir)) {
    $epName = Split-Path $OutputRoot -Leaf
    $projectRoot = Split-Path (Split-Path $OutputRoot -Parent) -Parent
    if ($projectRoot -and (Test-Path -LiteralPath (Join-Path $projectRoot '中间文件'))) {
        $SourceAnalysisDir = Join-Path $projectRoot (Join-Path '中间文件\原片分析' $epName)
    } else {
        $SourceAnalysisDir = Join-Path $OutputRoot '原片源帧分析'
    }
}
New-Item -ItemType Directory -Force -Path $segmentsDirectory, $framesDirectory, $SourceAnalysisDir | Out-Null

$segmentPattern = Join-Path $segmentsDirectory 'segment_%02d.mp4'
& $FfmpegPath -hide_banner -loglevel error -y -i $InputVideo `
    -map 0 -c copy -f segment -segment_time $SegmentTime -reset_timestamps 1 `
    -segment_start_number 1 $segmentPattern
if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg segmenting failed with exit code $LASTEXITCODE"
}

$segments = Get-ChildItem -LiteralPath $segmentsDirectory -Filter 'segment_*.mp4' | Sort-Object Name
foreach ($segment in $segments) {
    if ($segment.BaseName -notmatch 'segment_(?<number>\d+)$') {
        continue
    }
    $segName = $segment.BaseName
    $segmentFrameDirectory = Join-Path $framesDirectory $segName
    New-Item -ItemType Directory -Force -Path $segmentFrameDirectory | Out-Null
    $firstFrame = Join-Path $SourceAnalysisDir ("${segName}_镜头01_首帧_源.jpg")
    $lastFrame = Join-Path $SourceAnalysisDir ("${segName}_镜头01_尾帧_源.jpg")

    & $FfmpegPath -hide_banner -loglevel error -y -i $segment.FullName -frames:v 1 $firstFrame
    if ($LASTEXITCODE -ne 0) {
        throw "First-frame extraction failed for $($segment.Name)"
    }

    & $FfmpegPath -hide_banner -loglevel error -y -sseof -0.10 -i $segment.FullName -frames:v 1 $lastFrame
    if ($LASTEXITCODE -ne 0) {
        throw "Last-frame extraction failed for $($segment.Name)"
    }
}

[PSCustomObject]@{
    InputVideo = (Resolve-Path -LiteralPath $InputVideo).Path
    SegmentCount = $segments.Count
    SegmentsDirectory = $segmentsDirectory
    FramesDirectory = $framesDirectory
    SourceAnalysisDirectory = $SourceAnalysisDir
}
