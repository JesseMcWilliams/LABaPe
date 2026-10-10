# cloud-init network config v2 for a cloned VM. Matched by MAC (fixed per
# VM by the vm module). A name glob ("e*") worked with netplan, but Rocky's
# sysconfig renderer wrote a profile for a device literally named after the
# config key and the NIC fell back to DHCP, so non-netplan systems also get
# set-name: eth0. Ubuntu (netplan) doesn't: on 26.04 the rename fails (the
# NIC is already up from dracut), netplan's eth0-named .network then
# matches nothing, and dracut's catch-all DHCP default wins.
version: 2
ethernets:
  eth0:
    match:
      macaddress: "${mac_address}"
%{ if set_name ~}
    set-name: eth0
%{ endif ~}
%{ if addressing.mode == "static" ~}
    addresses: ["${addressing.address}/${addressing.prefix_length}"]
    routes:
      # 0.0.0.0/0, not "default": netplan accepts "default", but
      # cloud-init's own v2 parser (Rocky 9's 24.4, rendering to
      # NetworkManager) rejects it and skips network config entirely.
      - to: 0.0.0.0/0
        via: ${addressing.gateway}
    nameservers:
      addresses: ["${addressing.gateway}"]
%{ else ~}
    dhcp4: true
%{ endif ~}
