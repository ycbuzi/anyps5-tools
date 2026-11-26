#!/bin/bash
export LD_PRELOAD=/root/qtest/qprof/libqprof.so QPROF_OUT=$(cat /root/qtest/qprof/current-out) QPROF_MATCH=" ./celeste"
export QPROF_START=${QPROF_START:-90} QPROF_SECS=${QPROF_SECS:-60} QPROF_US=${QPROF_US:-2000} BOX64_DYNAREC_PERFMAP=$(cat /root/qtest/qprof/perfmap 2>/dev/null || echo 1)
export QPROF_FIND=34b3ff40 QPROF_GETX64=353e5e84
exec /root/run-game.sh "$@"
