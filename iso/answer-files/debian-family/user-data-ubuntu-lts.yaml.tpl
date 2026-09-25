#cloud-config
# Rendered by tofu/modules/vm/libvirt/main.tf via templatefile() — see
# docs/base-images.md §3 (Debian family) and §4 (direct ISO boot).
# First-ever Debian-family answer file (M5's deferred workstation-
# support pass) — subiquity's autoinstall schema, NOT generic
# cloud-config and NOT kickstart. Mirrors
# iso/answer-files/rhel-family/ks-rocky9.cfg.tpl's bootstrap contract
# (labape SSH-key-only sudo-capable user, static-or-dhcp networking,
# unattended reboot) but every mechanism differs, since this is a
# completely different installer (subiquity, not Anaconda).
#
# UNVERIFIED against real infrastructure as of this writing — treat the
# following as the most likely first-attempt failure points, not
# settled fact (see docs/troubleshooting-log.md if a real test round
# already happened and this comment wasn't updated):
#   - identity.password accepting a bare "!" (the traditional shadow
#     "no hash can ever match" placeholder, same convention `passwd -l`
#     produces) rather than requiring a real crypt() hash.
#   - late-commands writing to /target/... directly (not auto-chrooted
#     — late-commands run in the LIVE installer environment) rather
#     than needing an explicit `curtin in-target --target=/target --`
#     wrapper.
#   - YAML indentation surviving the %%{ if ~}/%%{ endif ~} directives
#     below intact — this template's equivalent of the Windows
#     answer-file XML-comment bug (docs/troubleshooting-log.md), i.e.
#     render via `tofu console` + templatefile() and eyeball the output
#     before ever booting a VM with it.
autoinstall:
  version: 1
  interactive-sections: []
  locale: en_US.UTF-8
  keyboard:
    layout: us

  identity:
    hostname: ${hostname}
    username: labape
    password: "!"

  ssh:
    install-server: true
    allow-pw: false
    authorized-keys:
      - "${ssh_public_key}"

  user-data:
    ssh_pwauth: false

%{ if addressing.mode == "static" ~}
  network:
    version: 2
    ethernets:
      all-en:
        match:
          name: "en*"
        addresses: ["${addressing.address}/${addressing.prefix_length}"]
        routes:
          - to: default
            via: ${addressing.gateway}
        nameservers:
          addresses: ["${addressing.gateway}"]
%{ else ~}
  network:
    version: 2
    ethernets:
      all-en:
        match:
          name: "en*"
        dhcp4: true
%{ endif ~}

  # Explicit, fully-specified (action-list) storage config — confirmed
  # necessary via real testing (docs/troubleshooting-log.md): the
  # higher-level `storage.layout: {name: direct}` shorthand, which per
  # Canonical's own docs should mean "whole disk, no LVM, no
  # encryption," was STILL followed by a mandatory, unskippable LUKS
  # "Passphrase must be set" screen on this Ubuntu 24.04.3 build — the
  # `layout:` directive was seemingly not honored at all (possibly a
  # real subiquity bug/regression on this point release). Bypassing it
  # entirely with an explicit action list avoids subiquity's "guided
  # storage" flow altogether, which is where that unwanted encryption
  # prompt lives. msdos (MBR), not gpt, to match this VM's legacy BIOS
  # boot (virt-install's default here) without needing a separate
  # bios_grub partition.
  storage:
    config:
      - type: disk
        id: disk0
        ptable: msdos
        match:
          size: largest
      - type: partition
        id: root-partition
        device: disk0
        size: -1
      - type: format
        id: root-fs
        fstype: ext4
        volume: root-partition
      - type: mount
        id: root-mount
        device: root-fs
        path: /

  # geoip: false + an explicit primary mirror — confirmed necessary via
  # real testing (docs/troubleshooting-log.md): subiquity's default
  # geoip-based mirror auto-selection hung indefinitely on "The mirror
  # location is being tested" even though archive.ubuntu.com itself was
  # directly reachable (confirmed via curl from a VM on the same
  # network) — the geoip lookup service subiquity contacts to pick a
  # country mirror was the unreachable part, not the mirror itself.
  # Pinning a known-good mirror sidesteps that lookup entirely.
  apt:
    geoip: false
    primary:
      - arches: [amd64]
        uri: "http://archive.ubuntu.com/ubuntu/"

  packages:
    - openssh-server
%{ if syslog_host != "" ~}
    - rsyslog
%{ endif ~}

  late-commands:
    - "echo 'labape ALL=(ALL) NOPASSWD:ALL' > /target/etc/sudoers.d/labape"
    - "chmod 0440 /target/etc/sudoers.d/labape"
%{ if syslog_host != "" ~}
    - "echo '*.* @${syslog_host}:1514' >> /target/etc/rsyslog.conf"
    - "curtin in-target --target=/target -- systemctl enable rsyslog"
%{ endif ~}

  shutdown: reboot
