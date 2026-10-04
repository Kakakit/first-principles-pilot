#!/bin/zsh
cd -- "${0:A:h}" || exit 2
source scripts/resolve-python.sh || exit 2
"$FPILOT_PYTHON" -X utf8 launch.py "$@"
result=$?
if (( result != 0 )) && [[ -t 0 ]]; then read '?启动失败，查看上方原因。按回车关闭。'; fi
exit $result
