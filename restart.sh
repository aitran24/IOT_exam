#!/bin/bash
systemctl --user restart photobooth.service

if [ "$1" == "--tunnel" ] || [ "$1" == "--all" ]; then
    systemctl --user restart photobooth-tunnel.service
    echo "Photobooth server & Ngrok tunnel restarted."
else
    echo "Photobooth backend restarted."
fi
