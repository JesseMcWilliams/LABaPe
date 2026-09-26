#_preseed_V1
# Rendered by tofu/modules/vm/libvirt/main.tf via templatefile() — see
# Claude_Docs/Design_Base-Images.md §3 (Debian family) and §4 (direct ISO boot).
# Real Debian (not Ubuntu) uses the classic debian-installer (d-i) with
# preseed, NOT subiquity/cloud-init — a genuinely different installer
# from ubuntu_lts/ubuntu_26 (iso/answer-files/debian-family/user-data-
# ubuntu-lts.yaml.tpl), even though both are "Debian-family" in the
# broad sense. Mechanically this is much closer to the RHEL kickstart
# template (iso/answer-files/rhel-family/ks-rocky9.cfg.tpl): a single
# file injected into the initrd via --initrd-inject and referenced by a
# kernel command-line argument, not a second NoCloud seed ISO — hence
# its own os_family ("debian_preseed") and its own create-iso-direct.sh
# case, rather than reusing "debian"'s NoCloud-based branch.
#
# UNVERIFIED against real infrastructure as of this writing — treat the
# following as the most likely first-attempt failure points, not
# settled fact (see Claude_Docs/Testing_Troubleshooting-Log.md if a real test round
# already happened and this comment wasn't updated):
#   - partman-auto/disk assumes /dev/vda (create-iso-direct.sh's
#     debian_preseed case pins --disk ...,bus=virtio specifically so
#     this holds — Anaconda-based kickstart doesn't need this since its
#     bootloader command auto-detects the disk, but d-i's grub-installer
#     and partman-auto both require an explicit device path).
#   - user-password-crypted "!" (a locked password, same convention as
#     the other two templates) combined with SSH key-only access
#     installed via late_command rather than a native preseed
#     "authorized-keys" question (d-i has no equivalent to subiquity's
#     ssh.authorized-keys or kickstart's sshkey; the reference example
#     preseed for trixie does this the same way, via late_command).

### Localization
d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us

### Network configuration
d-i netcfg/choose_interface select auto
%{ if addressing.mode == "static" ~}
d-i netcfg/disable_autoconfig boolean true
d-i netcfg/get_ipaddress string ${addressing.address}
d-i netcfg/get_netmask string ${cidrnetmask("${addressing.address}/${addressing.prefix_length}")}
d-i netcfg/get_gateway string ${addressing.gateway}
d-i netcfg/get_nameservers string ${addressing.gateway}
d-i netcfg/confirm_static boolean true
%{ else ~}
d-i netcfg/disable_autoconfig boolean false
%{ endif ~}
d-i netcfg/get_hostname string ${hostname}
d-i netcfg/get_domain string
d-i netcfg/wireless_wep string

### Mirror settings
d-i mirror/country string manual
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string

### Account setup
# Root login disabled; labape gets NOPASSWD sudo via late_command below
# (matching the other two templates' convention) rather than group
# membership + the account's own — nonexistent — password.
d-i passwd/root-login boolean false
d-i passwd/user-fullname string labape
d-i passwd/username string labape
d-i passwd/user-password-crypted password !
d-i passwd/user-default-groups string

### Clock and time zone setup
d-i clock-setup/utc boolean true
d-i time/zone string UTC
d-i clock-setup/ntp boolean true

### Partitioning
# Plain "regular" method (real msdos partition table, one bootable
# ext4 root partition) — not the "loop" whole-disk-no-partition-table
# approach some reference preseeds use, which needs its own libparted-
# bug workaround script; avoided here to keep this template simple, at
# the cost of one real (tiny) partition table, same trade-off the RHEL
# kickstart template already makes.
d-i partman-auto/disk string /dev/vda
d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true
d-i partman/mount_style select traditional

### Base system / apt
d-i apt-setup/cdrom/set-first boolean false

### Package selection
tasksel tasksel/first multiselect standard, ssh-server
d-i pkgsel/include string sudo%{ if syslog_host != "" } rsyslog%{ endif }
d-i pkgsel/upgrade select none
d-i pkgsel/update-policy select none
popularity-contest popularity-contest/participate boolean false

### Boot loader installation
d-i grub-installer/only_debian boolean true
d-i grub-installer/with_other_os boolean true
d-i grub-installer/bootdev string /dev/vda
d-i grub-installer/make_active boolean true

### Finishing up the installation
d-i finish-install/reboot_in_progress note

### SSH server hardening (key-only, matching the other two templates)
openssh-server openssh-server/permit-root-login boolean false
openssh-server openssh-server/password-authentication boolean false

### Preseeding other packages / late_command
# Runs just before the install finishes, with /target still mounted —
# used here for exactly what subiquity's late-commands and kickstart's
# %post cover: passwordless sudo for labape, and installing its SSH
# public key (no native preseed question for either).
d-i preseed/late_command string \
    in-target sh -c 'echo "labape ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/labape'; \
    in-target chmod 0440 /etc/sudoers.d/labape; \
    in-target mkdir -p /home/labape/.ssh; \
    in-target sh -c 'echo "${ssh_public_key}" > /home/labape/.ssh/authorized_keys'; \
    in-target chown -R 1000:1000 /home/labape/.ssh; \
    in-target chmod 700 /home/labape/.ssh; \
    in-target chmod 600 /home/labape/.ssh/authorized_keys%{ if syslog_host != "" ~}
; \
    in-target sh -c 'echo "*.* @${syslog_host}:1514" >> /etc/rsyslog.conf'; \
    in-target systemctl enable rsyslog
%{ endif ~}
