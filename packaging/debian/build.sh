#!/usr/bin/env bash
# Build two amd64 .deb packages for Ubuntu 22.04 and 24.04:
#   packaging/dist/teleop-operator_<version>_amd64.deb
#   packaging/dist/teleop-robot_<version>_amd64.deb
# Unpacked layout is /opt/teleop plus /usr/bin/teleop-operator and
# /usr/bin/teleop-robot. This script does not install the packages
# and does not start the arm. ros2_ws source, build, and install are
# packed. The ros2-teleop-poc:humble image is not.
set -euo pipefail

VERSION=1.0.0
ARCH=amd64
IMAGE=ros2-teleop-poc:humble

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../.." && pwd)"
dist="${repo}/packaging/dist"
debian="${repo}/packaging/debian"
vendor="${debian}/vendor/websockets"

die() {
  echo "build.sh: $*" >&2
  exit 1
}

log() {
  echo "build.sh: $*" >&2
}

require_file() {
  [[ -f "$1" ]] || die "missing $1"
}

require_file "${vendor}/__init__.py"
require_file "${debian}/postinst"
require_file "${debian}/teleop-operator.wrapper"
require_file "${debian}/teleop-robot.wrapper"
require_file "${debian}/teleop-operator.desktop"
require_file "${debian}/vendor/LICENSE.websockets"
[[ -d "${repo}/mlink-transport" ]] || die "missing mlink-transport"
[[ -d "${repo}/turn" ]] || die "missing turn"
[[ -d "${repo}/video/so-arm" ]] || die "missing video/so-arm"

rsync_tree() {
  local src="$1" dest="$2"
  shift 2
  mkdir -p "${dest}"
  rsync -a \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    --exclude '.pytest_cache/' \
    --exclude '.venv/' \
    --exclude '.git/' \
    --exclude '*.pcap' \
    --exclude '*.pcapng' \
    "$@" \
    "${src}" "${dest}"
}

copy_common() {
  local dest="$1"
  rsync_tree "${vendor}/" "${dest}/vendor/websockets/" --exclude '*.so'
  install -m 644 "${debian}/vendor/LICENSE.websockets" \
    "${dest}/vendor/LICENSE.websockets"

  mkdir -p "${dest}/packaging"
  install -m 644 "${repo}/packaging/__init__.py" "${dest}/packaging/__init__.py"
  rsync_tree "${repo}/packaging/robot_app/" "${dest}/packaging/robot_app/"
  rsync_tree "${repo}/packaging/registry/" "${dest}/packaging/registry/"
  mkdir -p "${dest}/packaging/supervisor"
  install -m 644 "${repo}/packaging/supervisor/__init__.py" \
    "${dest}/packaging/supervisor/__init__.py"
  install -m 644 "${repo}/packaging/supervisor/robot_commands.py" \
    "${dest}/packaging/supervisor/robot_commands.py"
  install -m 644 "${repo}/packaging/supervisor/robot_exec.py" \
    "${dest}/packaging/supervisor/robot_exec.py"
  install -m 644 "${repo}/packaging/supervisor/robot_stop.py" \
    "${dest}/packaging/supervisor/robot_stop.py"
  mkdir -p "${dest}/packaging/run"
  chmod 1777 "${dest}/packaging/run"
  : > "${dest}/packaging/run/.keep"
  chmod 644 "${dest}/packaging/run/.keep"

  rsync_tree "${repo}/mlink-transport/" "${dest}/mlink-transport/"
  rsync_tree "${repo}/turn/" "${dest}/turn/" \
    --exclude 'local_op.yaml' \
    --exclude 'local_edge.yaml' \
    --exclude 'local.yaml' \
    --exclude 'generic.yaml' \
    --exclude 'no_turn.yaml' \
    --exclude 'coturn.env'

  mkdir -p "${dest}/teleoperation-prototype/config" \
    "${dest}/teleoperation-prototype/scripts"
  install -m 644 "${repo}/teleoperation-prototype/config/teleop.yaml" \
    "${dest}/teleoperation-prototype/config/teleop.yaml"
  install -m 644 "${repo}/teleoperation-prototype/config/cyclonedds.xml" \
    "${dest}/teleoperation-prototype/config/cyclonedds.xml"
  install -m 644 "${repo}/teleoperation-prototype/config/cyclonedds-localhost.xml" \
    "${dest}/teleoperation-prototype/config/cyclonedds-localhost.xml"
}

copy_ros2_ws() {
  local dest="$1/teleoperation-prototype/ros2_ws"
  local label="$2"
  local ws="${repo}/teleoperation-prototype/ros2_ws"
  [[ -d "${ws}/src" ]] || die "missing ${ws}/src"
  log "${label}: copying ros2_ws"
  rsync_tree "${ws}/src/" "${dest}/src/"
  if [[ -f "${ws}/install/setup.bash" && -d "${ws}/install/teleop_demo_msgs" && -d "${ws}/build" ]]; then
    rsync_tree "${ws}/install/" "${dest}/install/"
    rsync_tree "${ws}/build/" "${dest}/build/"
  else
    log "${label}: ros2_ws install is absent; Start will build it from the packaged source"
  fi
}

copy_operator_payload() {
  local dest="$1"
  copy_common "${dest}"
  rsync_tree "${repo}/packaging/operator_app/" "${dest}/packaging/operator_app/"
  install -m 644 "${repo}/packaging/supervisor/operator_commands.py" \
    "${dest}/packaging/supervisor/operator_commands.py"
  install -m 644 "${repo}/packaging/supervisor/operator_exec.py" \
    "${dest}/packaging/supervisor/operator_exec.py"
  install -m 755 "${repo}/teleoperation-prototype/scripts/start_operator_mlink.sh" \
    "${dest}/teleoperation-prototype/scripts/start_operator_mlink.sh"
  install -m 644 "${repo}/teleoperation-prototype/compose.operator-mlink.yaml" \
    "${dest}/teleoperation-prototype/compose.operator-mlink.yaml"
  rsync_tree "${repo}/teleoperation-prototype/web/" \
    "${dest}/teleoperation-prototype/web/"
  copy_ros2_ws "${dest}" teleop-operator
}

copy_robot_payload() {
  local dest="$1"
  copy_common "${dest}"
  install -m 755 "${repo}/teleoperation-prototype/scripts/start_robot_mlink.sh" \
    "${dest}/teleoperation-prototype/scripts/start_robot_mlink.sh"
  install -m 755 "${repo}/teleoperation-prototype/scripts/stop_mlink.sh" \
    "${dest}/teleoperation-prototype/scripts/stop_mlink.sh"
  install -m 644 "${repo}/teleoperation-prototype/compose.robot-mlink.yaml" \
    "${dest}/teleoperation-prototype/compose.robot-mlink.yaml"
  install -m 644 "${repo}/teleoperation-prototype/compose.robot-mlink.real-arm.yaml" \
    "${dest}/teleoperation-prototype/compose.robot-mlink.real-arm.yaml"
  rsync_tree "${repo}/video/so-arm/" "${dest}/video/so-arm/" \
    --exclude 'logs/' \
    --exclude 'run/' \
    --exclude 'bin/'
  mkdir -p "${dest}/video/so-arm/logs" "${dest}/video/so-arm/run"
  chmod 1777 "${dest}/video/so-arm/logs" "${dest}/video/so-arm/run"
  : > "${dest}/video/so-arm/logs/.keep"
  : > "${dest}/video/so-arm/run/.keep"
  chmod 644 "${dest}/video/so-arm/logs/.keep" "${dest}/video/so-arm/run/.keep"
  if [[ -f "${repo}/video/bin/mediamtx" ]]; then
    log "teleop-robot: copying mediamtx"
    mkdir -p "${dest}/video/so-arm/bin"
    install -m 755 "${repo}/video/bin/mediamtx" \
      "${dest}/video/so-arm/bin/mediamtx"
  fi
  copy_ros2_ws "${dest}" teleop-robot
}

write_control() {
  local path="$1" package="$2" depends="$3" conflicts="$4" summary="$5"
  local installed
  installed="$(du -sk "${path}/opt" "${path}/usr" | awk '{s += $1} END {print s}')"
  cat > "${path}/DEBIAN/control" <<EOF
Package: ${package}
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: Teleop Packaging <packaging@localhost>
Depends: ${depends}
Conflicts: ${conflicts}
Section: utils
Priority: optional
Installed-Size: ${installed}
Description: ${summary}
 Files install under /opt/teleop with a command in /usr/bin.
 Ubuntu 22.04 and 24.04, amd64. This package does not start the arm
 and does not contain the ros2-teleop-poc:humble image.
EOF
}

write_doc() {
  local path="$1" package="$2"
  local doc="${path}/usr/share/doc/${package}"
  mkdir -p "${doc}"
  install -m 644 "${debian}/vendor/LICENSE.websockets" "${doc}/LICENSE.websockets"
  cat > "${doc}/copyright" <<EOF
${package} for Ubuntu 22.04 and 24.04 (amd64).

The programs are Python files under /opt/teleop plus a short command
in /usr/bin. They are not one compiled binary.

websockets 16.1.1 is vendored at /opt/teleop/vendor/websockets so the
apps do not pip-install at install time. Its license is
/usr/share/doc/${package}/LICENSE.websockets.
EOF
}

check_postinst() {
  local script="$1" package="$2"
  local tmp out code
  tmp="$(mktemp -d)"
  set +e
  out="$(PATH="${tmp}" "${script}" configure 2>&1)"
  code=$?
  set -e
  [[ "${code}" -ne 0 ]] || die "${package} postinst exited 0 when docker is missing"
  grep -q "${package}: docker is missing" <<<"${out}" \
    || die "${package} postinst did not name missing docker: ${out}"

  cat > "${tmp}/docker" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "image" ] && [ "${2:-}" = "inspect" ]; then
  exit 1
fi
exit 0
EOF
  chmod 755 "${tmp}/docker"
  set +e
  out="$(PATH="${tmp}" "${script}" configure 2>&1)"
  code=$?
  set -e
  [[ "${code}" -ne 0 ]] || die "${package} postinst exited 0 when the image is missing"
  grep -q "${package}: image ${IMAGE} is missing" <<<"${out}" \
    || die "${package} postinst did not name the missing image: ${out}"
  PATH="${tmp}" "${script}" abort-upgrade
  rm -rf "${tmp}"
}

import_check() {
  local dest="$1"
  shift
  PYTHONPATH="${dest}/vendor:${dest}" /usr/bin/python3 - "$@" <<'PY'
import websockets
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
import packaging.robot_app.cli
if not websockets.__version__ or connect is None or ConnectionClosed is None:
    raise SystemExit(1)
PY
}

pack_one() {
  local package="$1" depends="$2" conflicts="$3" summary="$4"
  local stage
  stage="$(mktemp -d)"
  log "${package}: staging files"
  mkdir -p "${stage}/DEBIAN" "${stage}/opt/teleop" "${stage}/usr/bin" \
    "${stage}/usr/share/doc/${package}"

  if [[ "${package}" == "teleop-operator" ]]; then
    copy_operator_payload "${stage}/opt/teleop"
    install -m 755 "${debian}/teleop-operator.wrapper" \
      "${stage}/usr/bin/teleop-operator"
    mkdir -p "${stage}/usr/share/applications"
    install -m 644 "${debian}/teleop-operator.desktop" \
      "${stage}/usr/share/applications/teleop-operator.desktop"
    log "${package}: checking the staged tree"
    import_check "${stage}/opt/teleop"
    PYTHONPATH="${stage}/opt/teleop/vendor:${stage}/opt/teleop" \
      /usr/bin/python3 -c "import packaging.operator_app.cli"
  else
    copy_robot_payload "${stage}/opt/teleop"
    install -m 755 "${debian}/teleop-robot.wrapper" \
      "${stage}/usr/bin/teleop-robot"
    log "${package}: checking the staged tree"
    import_check "${stage}/opt/teleop"
  fi

  find "${stage}/opt/teleop" -type f -name '*.sh' -exec chmod 755 {} +
  sed "s/@PKG@/${package}/g" "${debian}/postinst" > "${stage}/DEBIAN/postinst"
  chmod 755 "${stage}/DEBIAN/postinst"
  if grep -q '@PKG@' "${stage}/DEBIAN/postinst"; then
    die "${package} postinst still has a placeholder"
  fi
  if grep -E 'start_robot_mlink|start_operator_mlink|start_daemon|docker compose|so-arm/start' \
    "${stage}/DEBIAN/postinst" >/dev/null; then
    die "${package} postinst starts a stack"
  fi
  check_postinst "${stage}/DEBIAN/postinst" "${package}"
  write_doc "${stage}" "${package}"
  write_control "${stage}" "${package}" "${depends}" "${conflicts}" "${summary}"

  (
    cd "${stage}"
    find opt usr -type f -print0 | sort -z | xargs -0 md5sum
  ) > "${stage}/DEBIAN/md5sums"

  local out="${dist}/${package}_${VERSION}_${ARCH}.deb"
  rm -f "${out}"
  log "${package}: packing"
  dpkg-deb --root-owner-group --build "${stage}" "${out}" >&2
  rm -rf "${stage}"
  echo "${out}"
}

assert_archive() {
  local deb="$1" bin_path="$2"
  local listing depends
  [[ -s "${deb}" ]] || die "empty file ${deb}"
  listing="$(dpkg-deb -c "${deb}")"
  [[ -n "${listing}" ]] || die "empty archive ${deb}"
  grep -q " ${bin_path}$" <<<"${listing}" \
    || die "${deb} is missing ${bin_path}"
  if grep -E '(^|/)(local_op\.yaml|local_edge\.yaml|coturn\.env)$|ros2_ws/log/|ros2-teleop-poc' \
    <<<"${listing}" >/dev/null; then
    die "${deb} contains a secret, workspace logs, or the ROS image"
  fi
  depends="$(dpkg-deb -f "${deb}" Depends)"
  echo "${deb}"
  echo "Depends: ${depends}"
  dpkg-deb -I "${deb}"
  dpkg-deb -c "${deb}"
}

log "building teleop-operator and teleop-robot ${VERSION} (${ARCH})"
mkdir -p "${dist}"
rm -f "${dist}/teleop-operator_"*"_${ARCH}.deb" "${dist}/teleop-robot_"*"_${ARCH}.deb"

operator_deb="$(pack_one \
  teleop-operator \
  "python3, python3-pyqt5, python3-pyqt5.qtwebengine, python3-yaml" \
  teleop-robot \
  "Teleop operator window")"
robot_deb="$(pack_one \
  teleop-robot \
  "python3, python3-yaml" \
  teleop-operator \
  "Teleop robot terminal")"

log "checking both packages"
op_depends="$(dpkg-deb -f "${operator_deb}" Depends)"
robot_depends="$(dpkg-deb -f "${robot_deb}" Depends)"
has_dep() {
  local padded=", ${1},"
  [[ "${padded}" == *", ${2},"* ]]
}
has_dep "${op_depends}" python3 || die "operator Depends missing python3"
has_dep "${op_depends}" python3-pyqt5 || die "operator Depends missing python3-pyqt5"
has_dep "${op_depends}" python3-pyqt5.qtwebengine \
  || die "operator Depends missing python3-pyqt5.qtwebengine"
has_dep "${robot_depends}" python3 || die "robot Depends missing python3"
if [[ "${robot_depends,,}" == *pyqt* ]]; then
  die "robot package depends on PyQt: ${robot_depends}"
fi

op_list="$(dpkg-deb -c "${operator_deb}")"
robot_list="$(dpkg-deb -c "${robot_deb}")"
grep -q ' \./usr/bin/teleop-operator$' <<<"${op_list}" \
  || die "operator command missing"
grep -q ' \./usr/share/applications/teleop-operator.desktop$' <<<"${op_list}" \
  || die "operator desktop file missing"
grep -q ' \./usr/bin/teleop-robot$' <<<"${robot_list}" \
  || die "robot command missing"
if grep -E 'teleop-operator|operator_app|applications/' <<<"${robot_list}" >/dev/null; then
  die "robot package contains the operator GUI"
fi
if grep -q 'mediamtx' <<<"${op_list}"; then
  die "operator package contains mediamtx"
fi
if [[ -f "${repo}/video/bin/mediamtx" ]]; then
  grep -q ' \./opt/teleop/video/so-arm/bin/mediamtx$' <<<"${robot_list}" \
    || die "robot package is missing video/so-arm/bin/mediamtx"
fi
for listing in "${op_list}" "${robot_list}"; do
  grep -q ' \./opt/teleop/teleoperation-prototype/ros2_ws/src/' <<<"${listing}" \
    || die "package is missing ros2_ws/src"
  grep -q ' \./opt/teleop/teleoperation-prototype/ros2_ws/install/setup.bash$' <<<"${listing}" \
    || die "package is missing ros2_ws/install/setup.bash"
  grep -q ' \./opt/teleop/teleoperation-prototype/ros2_ws/install/teleop_demo_msgs/' <<<"${listing}" \
    || die "package is missing ros2_ws/install/teleop_demo_msgs"
  grep -q 'teleop_demo_msgs_s__rosidl_typesupport_c.cpython-310-x86_64-linux-gnu.so' <<<"${listing}" \
    || die "package is missing the teleop_demo_msgs type-support library"
done

assert_archive "${operator_deb}" "./usr/bin/teleop-operator" >/dev/null
assert_archive "${robot_deb}" "./usr/bin/teleop-robot" >/dev/null
dpkg-deb --fsys-tarfile "${operator_deb}" | tar -xO ./usr/bin/teleop-operator \
  | grep -q '/usr/bin/python3' || die "operator wrapper is not distro Python"
dpkg-deb --fsys-tarfile "${robot_deb}" | tar -xO ./usr/bin/teleop-robot \
  | grep -q '/usr/bin/python3' || die "robot wrapper is not distro Python"

echo "built ${operator_deb}"
echo "built ${robot_deb}"
