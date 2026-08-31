#!/bin/zsh
set -euo pipefail
umask 077

readonly project_root="${0:A:h:h:h}"
readonly label="com.regimeswitch.v13-5-qqq-paper-wheel"
readonly template="${project_root}/infra/launchd/${label}.plist.template"
readonly launch_agents_dir="${HOME}/Library/LaunchAgents"
readonly target_plist="${launch_agents_dir}/${label}.plist"
readonly rendered_plist="${target_plist}.new"
readonly runtime_root="${project_root}/artifacts/paper_wheel/v13_5_qqq"
readonly service_target="gui/$(/usr/bin/id -u)/${label}"
readonly native_python="${project_root}/.venv/bin/python"
readonly config_path="${project_root}/configs/paper/v13_5_qqq.yaml"

if [[ ! -f "${template}" ]]; then
  print -u2 "WHEEL_LAUNCHD_TEMPLATE_MISSING"
  exit 78
fi
if [[ ! -x "${native_python}" ]] || [[ "$("${native_python}" -c 'import platform; print(platform.machine())')" != "arm64" ]]; then
  print -u2 "WHEEL_NATIVE_RUNTIME_REQUIRED"
  exit 78
fi

cd "${project_root}"
"${native_python}" -m packages.paper_wheel.cli preflight --config "${config_path}"
"${native_python}" -m packages.paper_wheel.cli verify-arm --config "${config_path}"

/bin/mkdir -p "${launch_agents_dir}" "${runtime_root}"
/usr/bin/touch "${runtime_root}/launchd.stdout.log" "${runtime_root}/launchd.stderr.log"
/bin/chmod 600 "${runtime_root}/launchd.stdout.log" "${runtime_root}/launchd.stderr.log"
/usr/bin/sed "s|__PROJECT_ROOT__|${project_root}|g" "${template}" > "${rendered_plist}"
/usr/bin/plutil -lint "${rendered_plist}"
/bin/mv "${rendered_plist}" "${target_plist}"
/bin/chmod 600 "${target_plist}"

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
  /bin/launchctl bootout "${service_target}"
fi
/bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "${target_plist}"
/bin/launchctl enable "${service_target}"
/bin/launchctl kickstart -k "${service_target}"
/bin/launchctl print "${service_target}"
