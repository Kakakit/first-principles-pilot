# Sourced from a launcher in the release root. Reuse an existing interpreter.
fpilot_resolve_python() {
  local candidate configured
  if [ -z "${FPILOT_PYTHON:-}" ]; then
    for candidate in python3.11 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11 python3 /usr/bin/python3; do
      if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert (3,9) <= sys.version_info[:2] < (3,13) and sys.maxsize > 2**32' >/dev/null 2>&1; then
        FPILOT_PYTHON="$(command -v "$candidate")"
        break
      fi
    done
    if [ -n "${FPILOT_PYTHON:-}" ] && [ -f fpilot.local.json ]; then
      configured="$("$FPILOT_PYTHON" -c 'import json; print(json.load(open("fpilot.local.json", encoding="utf-8-sig")).get("python", ""))')" || return 2
      if [ -n "$configured" ]; then FPILOT_PYTHON="$configured"; fi
    fi
  fi
  if [ -z "${FPILOT_PYTHON:-}" ] || ! "$FPILOT_PYTHON" -c 'import sys; assert (3,9) <= sys.version_info[:2] < (3,13) and sys.maxsize > 2**32' >/dev/null 2>&1; then
    printf '%s\n' '没有找到可用的已有 Python（3.9–3.12，64 位，推荐 3.11）。请设置 FPILOT_PYTHON 为实际解释器路径。' >&2
    return 2
  fi
  export FPILOT_PYTHON
}
fpilot_resolve_python
