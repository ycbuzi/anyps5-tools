import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obsws


def main():
    if "--restart" in sys.argv and obsws.obs_pids():
        obsws.log("restart: closing OBS")
        obsws.close_obs()
    if not obsws.obs_pids():
        obsws.log("starting OBS")
        obsws.launch_obs()
    with obsws.connect(90) as obs:
        obsws.log("ready " + repr(obsws.ensure_all(obs)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        obsws.log("start failed: " + traceback.format_exc())
        sys.exit(1)
