#!/bin/zsh
cd -- "${0:A:h}" || exit 2
# Finder does not inherit the Node path configured in an interactive terminal.
for candidate in "${FPILOT_NODE:-}" "$HOME/.local/node/bin/node" /opt/homebrew/bin/node /usr/local/bin/node node; do
  if [[ -n "$candidate" ]] && command -v "$candidate" >/dev/null 2>&1; then
    "$candidate" scripts/start.mjs "$@"
    result=$?
    if (( result != 0 )) && [[ -t 0 ]]; then read '?启动失败，查看上方原因。按回车关闭。'; fi
    exit $result
  fi
done
print -u2 '没有找到已有 Node。请设置 FPILOT_NODE；无需创建 Python 环境。'
if [[ -t 0 ]]; then read '?按回车关闭。'; fi
exit 2
