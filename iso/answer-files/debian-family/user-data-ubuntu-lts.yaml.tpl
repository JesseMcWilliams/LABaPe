#cloud-config
# Rendered by tofu/modules/vm/libvirt/main.tf via templatefile() — see
# Claude_Docs/Design_Base-Images.md §3 (Debian family) and §4 (direct ISO boot).
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
# settled fact (see Claude_Docs/Testing_Troubleshooting-Log.md if a real test round
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
#     answer-file XML-comment bug (Claude_Docs/Testing_Troubleshooting-Log.md), i.e.
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

  # layout: direct = whole disk, one partition, no LVM, no encryption;
  # subiquity adds whatever bootloader partition the firmware needs.
  # An earlier "layout ignored, LUKS passphrase demanded" finding was a
  # misdiagnosis: the seed was never found (bad ds= path), so subiquity
  # ran fully interactive (Claude_Docs/Testing_Troubleshooting-Log.md).
  # A hand-written storage: config: list is rejected once the config is
  # really applied ("did not create needed bootloader partition").
  storage:
    layout:
      name: direct
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
