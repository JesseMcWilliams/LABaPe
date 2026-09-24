#version=RHEL9
# Rendered by tofu/modules/vm/libvirt/main.tf via templatefile() — see
# docs/base-images.md §3 (RHEL family) and §4 (direct ISO boot).
#
# Bootstrap account: "labape" — this design's fixed name for the
# SSH-key-only bootstrap user (docs/credentials.md §5). Not meant for
# ongoing use once Ansible has configured the host; it's the initial
# connection point only.

text
reboot

lang en_US.UTF-8
keyboard --vckeymap=us --xlayouts='us'
timezone UTC --utc

%{ if syslog_host != "" ~}
# Streams Anaconda's own install-time log to a remote syslog receiver
# in real time (kickstart's native `logging` command, UDP 514) —
# a much more direct troubleshooting channel than inferring install
# progress from VHD growth or a Hyper-V uptime-counter reset (see
# README's Known Gaps, M2). Off by default (empty syslog_host); no
# effect on the working libvirt path unless explicitly opted into.
logging --host=${syslog_host} --port=1514 --level=debug
%{ endif ~}

%{ if addressing.mode == "static" ~}
# No dedicated DNS field exists in environment.yml yet — defaulting to
# the gateway is a reasonable assumption for a small/lab LAN (it's what
# this design's own control-machine setup uses) and static addressing
# has no other source for a resolver at all otherwise.
network --bootproto=static --ip=${addressing.address} --netmask=${cidrnetmask("${addressing.address}/${addressing.prefix_length}")} --gateway=${addressing.gateway} --nameserver=${addressing.gateway} --hostname=${hostname} --activate
%{ else ~}
network --bootproto=dhcp --hostname=${hostname} --activate
%{ endif ~}

rootpw --lock
sshkey --username=labape "${ssh_public_key}"
user --name=labape --groups=wheel

# Bootstrap-only, sudo without a password prompt so Ansible's first
# connection (which authenticates as `labape` via the SSH key above,
# not a password) can still escalate.
%packages
@^minimal-environment
openssh-server
%{ if syslog_host != "" ~}
rsyslog
%{ endif ~}
%{ if debug_disk ~}
dosfstools
%{ endif ~}
%end

%post
echo 'labape ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/labape
chmod 0440 /etc/sudoers.d/labape

systemctl enable sshd

%{ if syslog_host != "" ~}
# Keep logs flowing to the same receiver *after* install too — covers
# exactly the "guest looks healthy but I can't tell why sshd isn't
# answering" gap the install-time `logging` command above doesn't
# reach, since that one stops at reboot.
echo '*.* @${syslog_host}:1514' >> /etc/rsyslog.conf
systemctl enable rsyslog
%{ endif ~}

%{ if debug_disk ~}
# Debugging aid, off by default (README's Known Gaps, M2) — network
# (syslog forwarding) and serial console both came up completely empty
# investigating the SSH-reachability gap, despite the guest clearly
# being healthy, so this channel deliberately depends on neither: a
# oneshot systemd unit mounts the small extra FAT-formatted disk
# tofu/modules/vm/hyperv/main.tf attaches when debug_disk is set,
# writes a live diagnostic dump there every boot, and unmounts — FAT is
# natively readable from the Windows/Hyper-V host side (Mount-VHD)
# without touching the network or a serial port at all. Found by size,
# not by device name -- see the comment further down.
cat > /usr/local/bin/labape-debug-dump.sh <<'DUMPEOF'
#!/bin/bash
set -uo pipefail
mount_point="$(mktemp -d)"
# Do NOT assume the debug disk is /dev/sdb -- Hyper-V's synthetic IDE
# enumeration order is not guaranteed stable across boots (confirmed
# hands-on: an install once saw this exact 64 MiB disk come up as sda,
# with the OS's own 40 GiB root disk as sdb). Hardcoding the device
# name here would risk sfdisk/mkfs.vfat running against the live root
# disk instead. Match on its known exact size
# (tofu/modules/vm/hyperv/main.tf hyperv_vhd.debug: size = 67108864)
# instead, and bail out rather than guess if that doesn't turn up
# exactly one disk.
debug_dev="$(lsblk -dn -o NAME,SIZE --bytes | awk '$2 == 67108864 {print "/dev/" $1}')"
if [ "$(echo "$debug_dev" | wc -l)" != "1" ] || [ -z "$debug_dev" ]; then
  echo "labape-debug-dump: could not uniquely identify the debug disk by size, aborting" >&2
  rmdir "$mount_point"
  exit 1
fi
if [ ! -b "$${debug_dev}1" ]; then
  echo 'start=2048, type=c' | sfdisk "$debug_dev" >/dev/null 2>&1
  partprobe "$debug_dev" >/dev/null 2>&1 || true
  udevadm settle >/dev/null 2>&1 || true
fi
mkfs.vfat -n LABAPEDBG "$${debug_dev}1" >/dev/null 2>&1
mount "$${debug_dev}1" "$mount_point"
{
  echo "=== date ==="; date
  echo "=== systemctl status sshd ==="; systemctl status sshd --no-pager -l
  echo "=== sshd-keygen units ==="; systemctl list-units 'sshd-keygen*' --no-pager -l
  echo "=== systemctl list-units --failed ==="; systemctl list-units --failed --no-pager -l
  echo "=== ip addr ==="; ip addr
  echo "=== ip route ==="; ip route
  echo "=== ss -tlnp ==="; ss -tlnp
  echo "=== firewall-cmd --list-all ==="; firewall-cmd --list-all
  echo "=== journalctl -b --no-pager ==="; journalctl -b --no-pager
  echo "=== dmesg ==="; dmesg
} > "$mount_point/debug-report.txt" 2>&1
sync
umount "$mount_point"
rmdir "$mount_point"
DUMPEOF
chmod +x /usr/local/bin/labape-debug-dump.sh

cat > /etc/systemd/system/labape-debug-dump.service <<'UNITEOF'
[Unit]
Description=LABaPe debug diagnostic dump (README Known Gaps, M2)
After=network-online.target sshd.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/labape-debug-dump.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNITEOF
systemctl enable labape-debug-dump.service
%{ endif ~}

%{ if management_source != "" ~}
# Firewall scoping — docs/credentials.md §7. Restrict SSH to the
# control machine's address/CIDR instead of leaving it open to the
# whole (bridged, DESIGN.md §14) LAN segment.
firewall-cmd --permanent --zone=public --remove-service=ssh || true
firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="${management_source}" service name="ssh" accept'
firewall-cmd --reload
%{ endif ~}
%end

# console=ttyS0 mirrors kernel/systemd boot messages to the first serial
# port alongside the normal VGA console (tty0) — costs nothing on
# backends that don't wire up a serial device, and was the only way to
# actually see what a stuck/failed boot was doing on the Hyper-V backend
# (DESIGN.md §6, no VNC-equivalent framebuffer readily available there
# the way libvirt's virsh screenshot gave the Windows-answer-file saga).
bootloader --location=mbr --append="console=ttyS0,115200n8 console=tty0"
zerombr
clearpart --all --initlabel
autopart --type=lvm
