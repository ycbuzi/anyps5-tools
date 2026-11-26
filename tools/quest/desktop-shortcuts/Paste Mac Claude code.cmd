@echo off
title Paste the Claude sign-in code into the MacBook
echo.
echo  1. Sign in on the page in Chrome, then copy the code it shows.
echo  2. Click in THIS window, press Ctrl+V (or right-click), then press Enter.
echo.
set /p CODE= Code:
if "%CODE%"=="" (
  echo  No code was pasted. Close this window and try again.
  pause
  goto :eof
)
ssh macbook "screen -S claudelogin -p 0 -X stuff '%CODE%'; sleep 1; screen -S claudelogin -p 0 -X stuff $'\r'; sleep 8; export PATH=$HOME/.local/bin:$PATH; claude auth status | grep -E 'loggedIn|email'"
echo.
echo  Above is what Claude Code on the MacBook shows now.
pause
