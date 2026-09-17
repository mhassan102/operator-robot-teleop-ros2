#!/usr/bin/env bash
# Start mlink-op or mlink-edge in the foreground.
# Default YAML is the Orin lab pair (eth+wifi). --remote-laptop picks the
# Tailscale control-only pair. Never passes --reflect (that echoes; it will
# not drive the robot) or --control (that is mlink-ping).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

usage() {
  echo "Usage: $0 op|edge [--remote-laptop]" >&2
  echo "  default:     config/lab-{op,edge}.yaml (Orin eth+wifi)" >&2
  echo "  --remote-laptop: config/lab-{op,edge}-remote-laptop.yaml" >&2
  echo "  Does not pass --reflect or --control." >&2
}

role=""
remote_laptop=0
for arg in "$@"; do
  case "${arg}" in
    op|edge)
      if [[ -n "${role}" ]]; then
        echo "ERROR: role already set to ${role}" >&2
        usage
        exit 2
      fi
      role="${arg}"
      ;;
    --remote-laptop)
      remote_laptop=1
      ;;
    --reflect)
      echo "ERROR: --reflect echoes; it will not drive the robot. Omit it." >&2
      exit 2
      ;;
    --control|--control=*)
      echo "ERROR: --control is for mlink-ping, not teleop. Omit it." >&2
      exit 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: ${arg}" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${role}" ]]; then
  usage
  exit 2
fi

if (( remote_laptop )); then
  config="config/lab-${role}-remote-laptop.yaml"
  path_name="tailscale0"
else
  config="config/lab-${role}.yaml"
  path_name="orin-eth-wifi"
fi

echo "role=${role}"
echo "config=${config}"
echo "path=${path_name}"
echo "reflect=no"
echo "control=no"

if [[ "${MLINK_PARSE_ONLY:-}" == "1" ]]; then
  exit 0
fi

if [[ ! -f "${config}" ]]; then
  echo "ERROR: missing ${config}" >&2
  exit 1
fi

export PYTHONPATH="${root}${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m "${role}" --config "${config}"
