#!/bin/zsh
set -euo pipefail
umask 077

readonly project_root="${0:A:h:h:h}"
readonly native_python="${project_root}/.venv/bin/python"
readonly config_path="${project_root}/configs/paper/v13_5_qqq.yaml"

if [[ ! -x "${native_python}" ]]; then
  print -u2 "WHEEL_NATIVE_PYTHON_UNAVAILABLE"
  exit 78
fi
if [[ "$("${native_python}" -c 'import platform; print(platform.machine())')" != "arm64" ]]; then
  print -u2 "WHEEL_NATIVE_PYTHON_NOT_ARM64"
  exit 78
fi
if [[ ! -f "${config_path}" ]]; then
  print -u2 "WHEEL_CONFIG_UNAVAILABLE"
  exit 78
fi

readonly local_day="$(TZ=America/New_York /bin/date +%u)"
readonly local_hhmm="$(TZ=America/New_York /bin/date +%H%M)"
if (( local_day > 5 )) || (( 10#${local_hhmm} < 830 )) || (( 10#${local_hhmm} > 1630 )); then
  exit 0
fi

cd "${project_root}"
export REGIMESWITCH_SECRETS_DIR="${REGIMESWITCH_SECRETS_DIR:-/Users/lipengyuan/.config/great_secrets}"
export PAPER_API_BASE_URL="https://paper-api.alpaca.markets"

exec "${native_python}" -m packages.paper_wheel.cli run-once --config "${config_path}"
