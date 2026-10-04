#!/bin/zsh
cd -- "${0:A:h}" || exit 2
source scripts/resolve-python.sh || exit 2
"$FPILOT_PYTHON" -X utf8 launch.py --check "$@" > startup-check.json 2>&1
result=$?
cat startup-check.json
if [[ -t 0 ]]; then read '?检查完成。按回车关闭。'; fi
exit $result
