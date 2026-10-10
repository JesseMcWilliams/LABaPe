#cloud-config
# First-boot identity for a VM cloned from a template (image_source =
# "packer_template"), rendered by tofu/modules/vm/libvirt/main.tf and
# delivered on a CIDATA-labeled CD (cloud-init NoCloud). The template was
# finalized with `cloud-init clean`, so this runs as a new instance.
# Same account convention as the ISO answer files: `labape`, SSH key only,
# passwordless sudo (Claude_Docs/Reference_Credentials.md §5).
hostname: ${hostname}
preserve_hostname: false
users:
  - name: labape
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - "${ssh_public_key}"
ssh_pwauth: false
# New host keys per clone (default, stated for clarity).
ssh_deletekeys: true
# Clones may get a bigger disk than the template; grow / to fill it.
# growpart/resize_rootfs cover a root filesystem on a plain partition
# (Ubuntu, Debian); runcmd below covers Rocky's LVM root, which they don't.
# (No dollar-brace syntax in the script: this file goes through
# templatefile(), which would treat it as interpolation.)
growpart:
  mode: auto
  devices: ["/"]
resize_rootfs: true
runcmd:
  - |
    root="$(findmnt -no SOURCE /)"
    if lvs "$root" >/dev/null 2>&1; then
      vg="$(lvs --noheadings -o vg_name "$root" | tr -d ' ')"
      for pv in $(pvs --noheadings -o pv_name -S vg_name="$vg"); do
        part="$(basename "$pv")"
        growpart "/dev/$(lsblk -ndo pkname "$pv")" "$(cat "/sys/class/block/$part/partition")" || true
        pvresize "$pv"
      done
      lvextend -r -l +100%FREE "$root" || true
    fi
