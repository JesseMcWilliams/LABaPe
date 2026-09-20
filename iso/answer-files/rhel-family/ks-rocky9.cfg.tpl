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
%end

%post
echo 'labape ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/labape
chmod 0440 /etc/sudoers.d/labape

systemctl enable sshd

%{ if management_source != "" ~}
# Firewall scoping — docs/credentials.md §7. Restrict SSH to the
# control machine's address/CIDR instead of leaving it open to the
# whole (bridged, DESIGN.md §14) LAN segment.
firewall-cmd --permanent --zone=public --remove-service=ssh || true
firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="${management_source}" service name="ssh" accept'
firewall-cmd --reload
%{ endif ~}
%end

bootloader --location=mbr
zerombr
clearpart --all --initlabel
autopart --type=lvm
