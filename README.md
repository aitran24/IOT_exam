# 3.2" TFT ST7789V (240x320) on Raspberry Pi

Controller is ST7789V (not ILI9341), using the mipi-dbi-spi overlay.

## Install
1. Get mipi-dbi-cmd: https://github.com/notro/panel-mipi-dbi
2. sudo python3 mipi-dbi-cmd /lib/firmware/st7789.bin firmware/st7789.txt
3. Append config/config.txt.snippet to /boot/firmware/config.txt
4. sudo shutdown now, unplug power ~10 s, replug

## Notes
- Landscape: MADCTL 0x36 = 0x60
- Touch: swapxy=1,invy=1
- tools/ramp.py: color ramp test; tools/live.py: webcam preview; tools/tune.py: touch color tuning
