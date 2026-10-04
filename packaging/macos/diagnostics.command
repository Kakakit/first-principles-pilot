#!/bin/zsh
cd -- "${0:A:h}" || exit 2
source scripts/resolve-python.sh || exit 2
"$FPILOT_PYTHON" -X utf8 scripts/collect_diagnostics.py
result=$?
if [[ -t 0 ]]; then read '?诊断完成。按回车关闭。'; fi
exit $result
