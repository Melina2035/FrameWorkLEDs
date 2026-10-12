#!/bin/sh
# One-time setup: let your user adjust the Framework EC LEDs without root.
#
# Writes a udev rule so the sysfs attributes are group-writable, then applies
# the same permissions right now so you don't have to reboot.
set -e

GROUP=wheel
RULE=/etc/udev/rules.d/60-framework-leds.rules
DEVS="chromeos:white:power chromeos:multicolor:charging"
ATTRS="brightness multi_intensity trigger"

echo "Installing $RULE (group: $GROUP)"
sudo tee "$RULE" >/dev/null <<EOF
# Framework 13 EC LEDs: let group $GROUP set brightness, colour and trigger.
# Installed by ~/ledpanel/install.sh
ACTION=="add", SUBSYSTEM=="leds", KERNEL=="chromeos:white:power", RUN+="/bin/sh -c 'chgrp $GROUP /sys/class/leds/%k/brightness /sys/class/leds/%k/multi_intensity /sys/class/leds/%k/trigger && chmod g+w /sys/class/leds/%k/brightness /sys/class/leds/%k/multi_intensity /sys/class/leds/%k/trigger'"
ACTION=="add", SUBSYSTEM=="leds", KERNEL=="chromeos:multicolor:charging", RUN+="/bin/sh -c 'chgrp $GROUP /sys/class/leds/%k/brightness /sys/class/leds/%k/multi_intensity /sys/class/leds/%k/trigger && chmod g+w /sys/class/leds/%k/brightness /sys/class/leds/%k/multi_intensity /sys/class/leds/%k/trigger'"
EOF

sudo udevadm control --reload

echo "Applying permissions to the live devices"
for d in $DEVS; do
  [ -d "/sys/class/leds/$d" ] || { echo "  skip $d (not present)"; continue; }
  for a in $ATTRS; do
    sudo chgrp "$GROUP" "/sys/class/leds/$d/$a"
    sudo chmod g+w "/sys/class/leds/$d/$a"
  done
  echo "  $d ok"
done

echo
echo "Done. Launch the panel with:  ~/ledpanel/run.sh"
