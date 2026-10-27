#!/usr/bin/env bash
# Set up a USB drive as Barktracker's backup drive: mounts it at /mnt/barktracker-backup on every
# boot (and lets Barktracker re-mount it after you unplug and replug it). Nothing is erased.
#
#   ./scripts/setup-usb-backup.sh             list the USB drives plugged in
#   ./scripts/setup-usb-backup.sh /dev/sda1   use that partition
#
# The drive needs a filesystem already. exFAT or FAT32 (how most USB sticks come) and ext4 all
# work; ext4 is the most robust if the Pi loses power during a backup. To start fresh with ext4
# (THIS ERASES THE DRIVE):  sudo mkfs.ext4 -L BARKBACKUP /dev/sda1
set -euo pipefail
MNT=/mnt/barktracker-backup
USER_NAME="${SUDO_USER:-$USER}"
DEV="${1:-}"

if [[ -z "$DEV" ]]; then
  echo "USB drives plugged in:"
  lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT,TRAN,MODEL -p | awk 'NR==1 || /usb/ || prev ~ /usb/ {print} {prev=$0}'
  echo
  echo "Run again with the partition to use, e.g.:  $0 /dev/sda1"
  exit 0
fi

[[ -b "$DEV" ]] || { echo "$DEV is not a disk partition"; exit 1; }
ROOTDEV=$(findmnt -n -o SOURCE /)
if [[ "$DEV" == "$ROOTDEV" || "$ROOTDEV" == "$DEV"* ]]; then
  echo "$DEV holds the system (SD card); choose the USB drive"; exit 1
fi
UUID=$(sudo blkid -s UUID -o value "$DEV" || true)
FS=$(sudo blkid -s TYPE -o value "$DEV" || true)
if [[ -z "$UUID" || -z "$FS" ]]; then
  echo "$DEV has no filesystem. Format it first (erases it):  sudo mkfs.ext4 -L BARKBACKUP $DEV"; exit 1
fi

U=$(id -u "$USER_NAME"); G=$(id -g "$USER_NAME")
case "$FS" in
  ext4|ext3|ext2|btrfs|xfs) OPTS="defaults,nofail,noatime,users,x-systemd.device-timeout=10s" ;;
  vfat|exfat)               OPTS="defaults,nofail,noatime,users,uid=$U,gid=$G,umask=022,x-systemd.device-timeout=10s" ;;
  ntfs|ntfs3)               FS=ntfs3; OPTS="defaults,nofail,noatime,users,uid=$U,gid=$G,umask=022,x-systemd.device-timeout=10s" ;;
  *) echo "Filesystem $FS isn't supported here; use ext4, exFAT or FAT32"; exit 1 ;;
esac

# Unmount it wherever the desktop may have auto-mounted it
for m in $(findmnt -n -o TARGET -S "$DEV" || true); do sudo umount "$m"; done

sudo mkdir -p "$MNT"
if grep -q "UUID=$UUID" /etc/fstab; then
  echo "==> /etc/fstab already has this drive"
else
  sudo cp /etc/fstab "/etc/fstab.bak-$(date +%Y%m%d%H%M%S)"
  sudo sed -i "\|[[:space:]]$MNT[[:space:]]|d" /etc/fstab        # replace an older backup drive
  echo "UUID=$UUID  $MNT  $FS  $OPTS  0  2" | sudo tee -a /etc/fstab >/dev/null
  echo "==> Added to /etc/fstab (a copy of the old one is next to it)"
fi
sudo systemctl daemon-reload
sudo mount "$MNT"
case "$FS" in ext*|btrfs|xfs) sudo chown "$USER_NAME:" "$MNT" ;; esac
df -h "$MNT"
echo
echo "Done. In the dashboard: Settings > Backup and restore > Backup folder:  $MNT"
echo "(or add to config.yaml:  backup: {path: $MNT})"
