@echo off
rem Builds the assistant's AnyPS5 workspace (D:\AnyPS5\wt-assistant -> D:\AnyPS5\build\assistant): the libraries it
rem changes most often. Runs as the one-off scheduled task "AnyPS5 assistant build", so it keeps going without Claude.
rem 4 jobs at a time: more ran out of memory next to the AI models.
set PATH=D:\AnyPS5-tools\mingw-extract\mingw64\bin;%PATH%
ninja -C D:\AnyPS5\build\assistant -j 4 core/libs/libs/libSceHttp2.prx > D:\AnyPS5\build\assistant-build.log 2>&1
echo done %ERRORLEVEL% >> D:\AnyPS5\build\assistant-build.log
