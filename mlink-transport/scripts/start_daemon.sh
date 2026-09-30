#!/usr/bin/env bash
# Start mlink-op or mlink-edge in the foreground.
# Default YAML is the Orin lab pair (eth+wifi). --remote-laptop picks the
# Tailscale control-only pair. --ice picks the one-NIC ICE pair (no Tailscale).
# --ice-config PATH keeps that ICE pair and uses PATH instead of
# config/lab-{op,edge}-ice.yaml. Never passes --reflect (that echoes; it will
# not drive the robot) or --control (that is mlink-ping).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

usage() {
  echo "Usage: $0 op|edge [--remote-laptop | --ice [--ice-config PATH]]" >&2
  echo "  default:     config/lab-{op,edge}.yaml (Orin eth+wifi)" >&2
  echo "  --remote-laptop: config/lab-{op,edge}-remote-laptop.yaml" >&2
  echo "  --ice:       config/lab-{op,edge}-ice.yaml (nominated ICE socket)" >&2
  echo "  --ice-config PATH: ICE transport, yaml at PATH (not lab-*-ice.yaml)" >&2
  echo "  Does not pass --reflect or --control." >&2
}

role=""
remote_laptop=0
ice=0
ice_config=""
ice_config_set=0
args=("$@")
index=0
while (( index < ${#args[@]} )); do
  arg="${args[$index]}"
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
    --ice)
      ice=1
      ;;
    --ice-config)
      index=$((index + 1))
      if (( index >= ${#args[@]} )); then
        echo "ERROR: --ice-config requires a path" >&2
        usage
        exit 2
      fi
      ice=1
      ice_config_set=1
      ice_config="${args[$index]}"
      ;;
    --ice-config=*)
      ice=1
      ice_config_set=1
      ice_config="${arg#--ice-config=}"
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
  index=$((index + 1))
done

if [[ -z "${role}" ]]; then
  usage
  exit 2
fi

if (( ice_config_set )) && [[ -z "${ice_config}" || "${ice_config}" == -* ]]; then
  echo "ERROR: --ice-config requires a path" >&2
  usage
  exit 2
fi

if (( remote_laptop && ice )); then
  echo "ERROR: pass either --remote-laptop or --ice, not both." >&2
  exit 2
fi

if (( ice )); then
  if (( ice_config_set )); then
    config="${ice_config}"
  else
    config="config/lab-${role}-ice.yaml"
  fi
  path_name="ice"
elif (( remote_laptop )); then
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
