#!/bin/sh
# Does the power LED dim, or is it on/off only?
# Everything is logged to ~/ledpanel/probe.log
D=/sys/class/leds/chromeos:white:power
LOG=~/ledpanel/probe.log
exec > >(tee "$LOG") 2>&1

echo "=== valid EC led names (error text lists them) ==="
sudo ectool led bogus query 2>&1 | head -5
echo
echo "=== EC capability query: power ==="
sudo ectool led power query 2>&1
echo
echo "=== EC capability query: battery ==="
sudo ectool led battery query 2>&1

echo
echo "=== CONTROL: does the LED respond at all? ==="
echo "Watch the power button. It should blink 3 times, ~1.5s each."
echo none > $D/trigger
for i in 1 2 3; do
  echo 0   > $D/brightness; sleep 1.5
  echo 100 > $D/brightness; sleep 1.5
done
echo "   -> did it blink?  (if NO, sysfs control isn't reaching the LED at all)"

echo
echo "=== TEST A: dimming via kernel sysfs ==="
echo "Alternating full(100) and dim(8). If dimming works you'll see it PULSE"
echo "bright/dim. If it's on/off only, it will just sit there steady."
for i in 1 2 3 4; do
  echo 100 > $D/brightness; sleep 1.5
  echo 8   > $D/brightness; sleep 1.5
done
echo "   -> pulsed, or steady?"

echo
echo "=== TEST B: dimming via ectool, straight to the EC ==="
for i in 1 2 3 4; do
  sudo ectool led power white=100 >/dev/null 2>&1; sleep 1.5
  sudo ectool led power white=8   >/dev/null 2>&1; sleep 1.5
done
echo "   -> pulsed, or steady?"
echo "   (last ectool exit status: $?)"

echo
echo "=== restoring firmware control ==="
sudo ectool led power auto 2>&1
echo chromeos-auto > $D/trigger
echo "done - log saved to $LOG"
