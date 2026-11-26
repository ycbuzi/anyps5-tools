@echo off
title Paste the Claude sign-in code into the Quest
echo.
echo  1. Sign in on the page, then copy the code it shows.
echo  2. Click in THIS window, press Ctrl+V (or right-click), then press Enter.
echo.
set /p CODE= Code:
if "%CODE%"=="" (
  echo  No code was pasted. Close this window and try again.
  pause
  goto :eof
)
ssh quest "tmux send-keys -t claude -l '%CODE%'; sleep 1; tmux send-keys -t claude C-m; sleep 8; tmux capture-pane -p -t claude | grep -v '^\s*$' | tail -6"
echo.
echo  Above is what Claude Code on the Quest shows now.
echo  If it says the code is invalid or expired, authorize again for a new code and run this again.
pause
