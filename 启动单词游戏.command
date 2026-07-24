#!/bin/zsh
cd "$(dirname "$0")" || exit 1
IP_ADDRESS=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo ""
echo "WordQuest 本地学习服务器正在启动……"
echo "本机访问：http://localhost:3000"
if [[ -n "$IP_ADDRESS" ]]; then
  echo "同一 Wi-Fi 的其他设备访问：http://$IP_ADDRESS:3000"
fi
echo ""
echo "学生账号、课程、进度和单词本都保存在这台电脑。"
echo "所有学生统一使用服务器上的 Kokoro 英语发音。"
echo "请保持这个窗口打开。按 Control + C 可以停止服务器。"
echo ""
npm start &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null' EXIT INT TERM

for attempt in {1..30}; do
  if curl -s --max-time 1 "http://localhost:3000" >/dev/null; then
    break
  fi
  sleep 1
done

open "http://localhost:3000"
wait "$SERVER_PID"
