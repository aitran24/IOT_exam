#!/bin/bash
echo '=== PHOTOBOOTH SERVICES STATUS ==='
systemctl --user status photobooth.service --no-pager | head -n 5
echo ''
systemctl --user status photobooth-tunnel.service --no-pager | head -n 5
echo ''
echo '=== PERMANENT PUBLIC URL (NGROK) ==='
echo 'https://tinsmith-cattail-murmuring.ngrok-free.dev'
echo ''
