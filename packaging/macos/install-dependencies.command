#!/bin/zsh
cd -- "${0:A:h}" || exit 2
source scripts/resolve-python.sh || exit 2
if "$FPILOT_PYTHON" -X utf8 -c 'import launch; raise SystemExit(0 if launch.doctor()["ready"] else 1)'; then
  printf '%s\n' '现有依赖可以使用，无需重复安装。'
  result=0
else
  "$FPILOT_PYTHON" -X utf8 -m pip install --only-binary=:all: -r requirements-runtime.txt
  result=$?
fi
if [[ -t 0 ]]; then read '?操作完成。按回车关闭。'; fi
exit $result
