#!/bin/sh
# Make yanhe-agent ready to run: install uv when it is missing, then fetch Python and the script's dependencies, from
# whichever is faster here, the official sources or the Chinese mirrors, and from the other when that fails. Prints
# the path to write in place of `uv` until the harness restarts. YANHE_AGENT_SETUP_MIRRORS=1 uses the mirrors only.
case $(uname -s) in
  MINGW* | MSYS* | CYGWIN*) exec powershell -NoProfile -ExecutionPolicy Bypass -File "$(dirname "$0")/setup.ps1" ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
pypi=https://pypi.tuna.tsinghua.edu.cn/simple
only_mirrors=${YANHE_AGENT_SETUP_MIRRORS:-}
temp=$(mktemp -d)
trap 'rm -rf "$temp"' EXIT

fetch() {  # url file
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --max-time 300 -o "$2" "$1"
  else
    wget -q -T 300 -O "$2" "$1"
  fi
}

speed() {  # url: bytes per second over the first 2 MB, or 0 when nothing arrives within 15 seconds
  command -v curl >/dev/null 2>&1 || { echo 0; return; }
  curl -fsL -r 0-2097151 --max-time 15 -A yanhe-agent-setup -o /dev/null -w '%{speed_download}' "$1" 2>/dev/null || echo 0
}

case $(uname -s)-$(uname -m) in
  Darwin-arm64) tag='macosx_[0-9_]*_arm64' asset=aarch64-apple-darwin ;;
  Darwin-*) tag='macosx_[0-9_]*_x86_64' asset=x86_64-apple-darwin ;;
  *-aarch64 | *-arm64) tag='manylinux_[0-9_]*_aarch64' asset=aarch64-unknown-linux-gnu ;;
  *) tag='manylinux_[0-9_]*_x86_64' asset=x86_64-unknown-linux-gnu ;;
esac

# The newest uv wheel for this machine on the Tsinghua mirror, whose paths match PyPI's
wheel=
if fetch "$pypi/uv/" "$temp/index.html"; then
  # by version, as 0.9.9 sorts after 0.13.3 as text
  wheel=$(grep -o "href=\"[^\"#]*/uv-[0-9.]*-[^\"#/]*$tag[^\"#/]*\.whl" "$temp/index.html" | sed 's/^href="//' |
    awk -F'/uv-' '{ split($2, v, "-"); split(v[1], n, "."); printf "%09d%09d%09d %s\n", n[1], n[2], n[3], $0 }' |
    sort | tail -1 | cut -d' ' -f2)
  case $wheel in
    '') ;;
    http*) ;;
    /*) wheel="https://pypi.tuna.tsinghua.edu.cn$wheel" ;;
    *) wheel="https://pypi.tuna.tsinghua.edu.cn/packages/${wheel#*/packages/}" ;;
  esac
fi

# Which is faster here: GitHub and PyPI, or the Tsinghua mirror? Both serve the same uv wheel.
if [ -n "$only_mirrors" ]; then
  mirrors_first=1
else
  github=$(speed "https://github.com/astral-sh/uv/releases/latest/download/uv-$asset.tar.gz")
  pypi_org=0
  mirror=0
  if [ -n "$wheel" ]; then
    pypi_org=$(speed "https://files.pythonhosted.org/packages/${wheel#*/packages/}")
    mirror=$(speed "$wheel")
  fi
  mirrors_first=$(awk -v g="$github" -v p="$pypi_org" -v m="$mirror" 'BEGIN {
    o = (g < p) ? g : p
    printf "Download speed: official sources %.1f MB/s, Chinese mirrors %.1f MB/s; starting with the %s.\n", o / 1048576, m / 1048576, (m > o) ? "mirrors" : "official sources" > "/dev/stderr"
    print (m > o) ? 1 : ""
  }')
fi

install_official() {  # dir
  for url in https://astral.sh/uv/install.sh https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh; do
    fetch "$url" "$temp/installer.sh" && UV_INSTALL_DIR=$1 sh "$temp/installer.sh" >&2 && [ -x "$1/uv" ] && return 0
    echo "Could not install from $url" >&2
  done
  return 1
}

install_from_mirror() {  # dir: the uv wheel carries the uv binary, and a wheel is a zip file
  [ -n "$wheel" ] && fetch "$wheel" "$temp/uv.whl" || return 1
  mkdir -p "$1" "$temp/wheel"
  if command -v unzip >/dev/null 2>&1; then
    unzip -q -o "$temp/uv.whl" -d "$temp/wheel" || return 1
  else
    python3 -m zipfile -e "$temp/uv.whl" "$temp/wheel" || return 1
  fi
  for name in uv uvx; do
    cp "$temp"/wheel/uv-*.data/scripts/$name "$1/$name" && chmod +x "$1/$name" || return 1
  done
  if [ -z "${UV_NO_MODIFY_PATH:-}" ]; then  # put the folder on PATH, as the official installer does
    UV_TOOL_BIN_DIR=$1 "$1/uv" tool update-shell >&2
  fi
}

found=
if [ -z "$only_mirrors" ] && command -v uv >/dev/null 2>&1; then
  uv=$(command -v uv)
  found=1
else
  dir=${UV_INSTALL_DIR:-$HOME/.local/bin}
  uv=$dir/uv
  if ! [ -x "$uv" ]; then
    echo 'Downloading uv, about 20 MB.' >&2
    if [ -n "$only_mirrors" ]; then
      ways=mirror
    elif [ -n "$mirrors_first" ]; then
      ways='mirror official'
    else
      ways='official mirror'
    fi
    for way in $ways; do
      if [ "$way" = official ]; then
        install_official "$dir" && break
      else
        install_from_mirror "$dir" && break
        echo 'Could not install from the Tsinghua mirror' >&2
      fi
    done
  fi
  if ! [ -x "$uv" ]; then
    echo 'Could not download uv. In a sandbox, run this script again with network access; otherwise see https://docs.astral.sh/uv/getting-started/installation/' >&2
    exit 1
  fi
fi

# The first run fetches Python and the dependencies; later runs use the cache
echo 'Fetching Python and the dependencies, about 100 MB the first time.' >&2
if [ -n "$only_mirrors" ]; then
  sources=mirrors
elif [ -n "$mirrors_first" ]; then
  sources='mirrors official'
else
  sources='official mirrors'
fi
ready=
for source in $sources; do
  if [ "$source" = mirrors ]; then
    UV_PYTHON_INSTALL_MIRROR=https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone \
      UV_DEFAULT_INDEX=$pypi "$uv" run --quiet "$here/yanhe_agent.py" --help >"$temp/run.log" 2>&1 && ready=1
  else
    "$uv" run --quiet "$here/yanhe_agent.py" --help >"$temp/run.log" 2>&1 && ready=1
  fi
  [ -n "$ready" ] && break
  tail -5 "$temp/run.log" >&2
  echo "Could not fetch them from the $source." >&2
done
if [ -z "$ready" ]; then
  echo 'Could not fetch Python or the dependencies. In a sandbox, run this script again with network access.' >&2
  exit 1
fi
echo "uv is ready: $uv"
[ -n "$found" ] || echo "New sessions find it as uv; until the harness restarts, write $uv in place of uv."
