#!/bin/sh
# Can the power LED actually dim, or is it on/off only?
# Compares the kernel sysfs path against talking to the EC directly.
# Watch the power button LED while this runs.
D=/sys/class/leds/chromeos:white:power

echo "== 1. what the EC claims it supports =="
sudo ectool led power query

echo
echo "== 2. kernel sysfs path =="
echo none > $D/trigger
for v in 100 60 30 10 3; do
  echo $v > $D/brightness
  printf "   sysfs brightness=%-4s" "$v"; sleep 2; echo "(watched)"
done

echo
echo "== 3. ectool direct to the EC =="
for v in 100 60 30 10 3; do
  sudo ectool led power white=$v >/dev/null 2>&1 \
    && printf "   ectool white=%-4s" "$v" \
    || printf "   ectool white=%-4s REJECTED" "$v"
  sleep 2; echo "(watched)"
done

echo
echo "restoring firmware control"
sudo ectool led power auto >/dev/null 2>&1
echo chromeos-auto > $D/trigger
echo "done"
