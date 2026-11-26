#!/bin/bash
# One-time setup of azlinux (run as blu; uses sudo). Re-runnable. Files come from _tools/assistant/azlinux on the PC,
# copied to ~/x64-work/bin first. Installs swap, ccache, the AnyPS5 clone, systemd units for VNC :1 and the hourly
# PR runner (both enabled, so they come back after a Spot eviction + restart), XFCE autostart and desktop launchers.
set -eu
W=$HOME/x64-work; BIN=$W/bin
mkdir -p "$W/logs" "$W/prtests" ~/.vnc ~/.config/autostart
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends ccache > /dev/null
if [ ! -f /swapfile ]; then
  sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile > /dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
fi
if [ ! -d ~/AnyPS5/.git ]; then
  git clone -q https://github.com/boykopovar/AnyPS5.git ~/AnyPS5
  git -C ~/AnyPS5 remote add fork https://github.com/ycbuzi/AnyPS5.git
fi
chmod +x "$BIN"/*.sh
printf '#!/bin/sh\nunset SESSION_MANAGER DBUS_SESSION_BUS_ADDRESS\nexec dbus-launch --exit-with-session startxfce4\n' > ~/.vnc/xstartup
chmod +x ~/.vnc/xstartup
sudo tee /etc/systemd/system/anyps5-vnc.service > /dev/null <<EOF
[Unit]
Description=AnyPS5 VNC desktop
After=network.target
[Service]
User=blu
ExecStartPre=-/usr/bin/vncserver -kill :1
ExecStart=/usr/bin/vncserver :1 -fg -localhost yes -geometry 1280x800 -depth 24 -SecurityTypes VncAuth -xstartup /home/blu/.vnc/xstartup
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/systemd/system/x64-pr-auto.service > /dev/null <<EOF
[Unit]
Description=AnyPS5 azlinux PR runner (unattended; ~/x64-work/bin/x64-pr-auto.sh)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=blu
ExecStart=/bin/bash /home/blu/x64-work/bin/x64-pr-auto.sh
Nice=10
TimeoutStartSec=12h
EOF
sudo tee /etc/systemd/system/x64-pr-auto.timer > /dev/null <<EOF
[Unit]
Description=Run the azlinux PR runner hourly at :20 and 10 minutes after boot
[Timer]
OnCalendar=*:20
OnBootSec=10min
Persistent=true
[Install]
WantedBy=timers.target
EOF
printf '[Desktop Entry]\nType=Application\nName=AnyPS5 x64 live\nExec=xfce4-terminal --maximize --title="AnyPS5 Azure x64" -x /home/blu/x64-work/bin/live.sh\n' > ~/.config/autostart/anyps5-live.desktop
sudo systemctl daemon-reload
sudo systemctl enable -q --now anyps5-vnc.service x64-pr-auto.timer
bash "$BIN/desktop-launchers.sh" x64
echo setup done
