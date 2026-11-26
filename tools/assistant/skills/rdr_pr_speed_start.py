import sys
sys.path.insert(0, r'C:\AnyPS5 Games\_tools\assistant')
import work_skills as w
w.console_job('Red Dead Redemption (PPSA30528)', 'offline speed test of 4 open pull requests on it (builds + runs, about 2 hours, no AI)', r'''powershell -NoProfile -ExecutionPolicy Bypass -File 'C:\AnyPS5 Games\_tools\assistant\skills\rdr_pr_speed.ps1' ''')