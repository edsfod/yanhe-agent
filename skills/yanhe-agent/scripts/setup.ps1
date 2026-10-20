# Make yanhe-agent ready to run: install uv when it is missing, then fetch Python and the script's dependencies, from
# whichever is faster here, the official sources or the Chinese mirrors, and from the other when that fails. Prints
# the path to write in place of `uv` until the harness restarts. YANHE_AGENT_SETUP_MIRRORS=1 uses the mirrors only.
$ErrorActionPreference = 'Stop'
# A PowerShell 7 harness passes its module path down, and Windows PowerShell cannot load its own modules from it
$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine')
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 'Tls12'
$pypi = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
$onlyMirrors = $env:YANHE_AGENT_SETUP_MIRRORS -eq '1'
function Say($text) { [Console]::Error.WriteLine($text) }

function Invoke-Native($exe, [string[]]$arguments, [int]$show = 0) {
  # Windows PowerShell turns each stderr line of a program into an error, which 'Stop' would end the script on
  $ErrorActionPreference = 'Continue'
  $output = & $exe @arguments 2>&1
  $output | Select-Object -Last $show | ForEach-Object { Say "$_" }
  $LASTEXITCODE
}

function Measure-Speed($url) {
  # Bytes per second over the first 2 MB, or 0 when nothing arrives within 15 seconds
  try {
    $request = [Net.HttpWebRequest]::Create($url)
    $request.AddRange(0, 2097151)
    $request.UserAgent = 'yanhe-agent-setup'  # the Tsinghua mirror refuses requests without one
    $request.Timeout = 15000
    $request.ReadWriteTimeout = 15000
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $response = $request.GetResponse()
    $stream = $response.GetResponseStream()
    $buffer = New-Object byte[] 65536
    $total = 0
    while ($total -lt 2097152 -and $watch.Elapsed.TotalSeconds -lt 15 -and ($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
      $total += $read
    }
    $response.Close()
    $total / [Math]::Max($watch.Elapsed.TotalSeconds, 0.001)
  } catch {
    0
  }
}

function Find-Wheel {
  # The URL of the newest uv wheel for this machine on the Tsinghua mirror, whose paths match PyPI's
  $tag = if ($arch -eq 'aarch64') { 'win_arm64' } else { 'win_amd64' }
  $index = (Invoke-WebRequest "$pypi/uv/" -UseBasicParsing -TimeoutSec 30).Content
  $wheel = [regex]::Matches($index, "href=""([^""#]+/uv-(\d+(?:\.\d+)*)-[^""#/]+-$tag\.whl)") |
    Sort-Object { [version]($_.Groups[2].Value + '.0' * [Math]::Max(0, 3 - $_.Groups[2].Value.Split('.').Count)) } |
    Select-Object -Last 1  # by version, as 0.9.9 sorts after 0.13.3 as text
  if (-not $wheel) { throw "no uv wheel for $tag" }
  [Uri]::new([Uri]"$pypi/uv/", $wheel.Groups[1].Value).AbsoluteUri
}

function Install-Official($dir) {
  foreach ($url in 'https://astral.sh/uv/install.ps1', 'https://github.com/astral-sh/uv/releases/latest/download/uv-installer.ps1') {
    try {
      $installer = Invoke-RestMethod $url -TimeoutSec 60
      $env:UV_INSTALL_DIR = $dir
      & ([scriptblock]::Create($installer)) *>&1 | ForEach-Object { Say $_ }
      return
    } catch {
      Say "Could not install from ${url}: $($_.Exception.Message)"
    }
  }
}

function Install-FromMirror($dir, $wheel) {
  # The uv wheel carries uv.exe; a wheel is a zip file, so no Python is needed to unpack it
  $zip = Join-Path ([IO.Path]::GetTempPath()) 'yanhe-agent-uv.whl.zip'
  Invoke-WebRequest $wheel -OutFile $zip -UseBasicParsing -TimeoutSec 300
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $archive = [IO.Compression.ZipFile]::OpenRead($zip)
  try {
    New-Item -ItemType Directory -Force $dir | Out-Null
    foreach ($entry in $archive.Entries | Where-Object { $_.FullName -match '\.data/scripts/uvx?\.exe$' }) {
      [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $dir $entry.Name), $true)
    }
  } finally {
    $archive.Dispose()
    Remove-Item $zip -ErrorAction SilentlyContinue
  }
  if (-not $env:UV_NO_MODIFY_PATH) {  # put $dir on the user's PATH, as the official installer does
    $env:UV_TOOL_BIN_DIR = $dir
    Invoke-Native (Join-Path $dir 'uv.exe') @('tool', 'update-shell') 5 | Out-Null
  }
}

# Which is faster here: GitHub and PyPI, or the Tsinghua mirror? Both serve the same uv wheel.
try { $wheel = Find-Wheel } catch { $wheel = $null }
if ($onlyMirrors) {
  $mirrorsFirst = $true
} else {
  $github = Measure-Speed "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip"
  $pypiOrg = if ($wheel) { Measure-Speed ('https://files.pythonhosted.org/packages/' + ($wheel -split '/packages/', 2)[1]) } else { 0 }
  $official = [Math]::Min($github, $pypiOrg)
  $mirror = if ($wheel) { Measure-Speed $wheel } else { 0 }
  $mirrorsFirst = $mirror -gt $official
  $first = if ($mirrorsFirst) { 'mirrors' } else { 'official sources' }
  Say ('Download speed: official sources {0:N1} MB/s, Chinese mirrors {1:N1} MB/s; starting with the {2}.' -f ($official / 1MB), ($mirror / 1MB), $first)
}

$found = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($found -and -not $onlyMirrors) {
  $uv = $found.Source
} else {
  $dir = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR } else { Join-Path $HOME '.local\bin' }
  $uv = Join-Path $dir 'uv.exe'
  if (-not (Test-Path $uv)) {
    Say 'Downloading uv, about 20 MB.'
    $ways = if ($onlyMirrors) { @('mirror') } elseif ($mirrorsFirst) { @('mirror', 'official') } else { @('official', 'mirror') }
    foreach ($way in $ways) {
      if ($way -eq 'official') {
        Install-Official $dir
      } elseif ($wheel) {
        try { Install-FromMirror $dir $wheel } catch { Say "Could not install from the Tsinghua mirror: $($_.Exception.Message)" }
      }
      if (Test-Path $uv) { break }
    }
  }
  if (-not (Test-Path $uv)) {
    Say 'Could not download uv. In a sandbox, run this script again with network access; otherwise see https://docs.astral.sh/uv/getting-started/installation/'
    exit 1
  }
}

# The first run fetches Python and the dependencies; later runs use the cache
$script = Join-Path $PSScriptRoot 'yanhe_agent.py'
Say 'Fetching Python and the dependencies, about 100 MB the first time.'
$sources = if ($onlyMirrors) { @('mirrors') } elseif ($mirrorsFirst) { @('mirrors', 'official sources') } else { @('official sources', 'mirrors') }
$ready = $false
foreach ($source in $sources) {
  if ($source -eq 'mirrors') {
    $env:UV_PYTHON_INSTALL_MIRROR = 'https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone'
    $env:UV_DEFAULT_INDEX = $pypi
  } else {
    Remove-Item Env:UV_PYTHON_INSTALL_MIRROR, Env:UV_DEFAULT_INDEX -ErrorAction SilentlyContinue
  }
  $ready = (Invoke-Native $uv @('run', '--quiet', $script, '--help') 5) -eq 0
  if ($ready) { break }
  Say "Could not fetch them from the $source."
}
if (-not $ready) {
  Say 'Could not fetch Python or the dependencies. In a sandbox, run this script again with network access.'
  exit 1
}
$path = $uv.Replace('\', '/')
"uv is ready: $path"
if (-not $found) { "New sessions find it as uv; until the harness restarts, write $path in place of uv." }
