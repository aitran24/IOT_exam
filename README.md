# Photobooth – tuần 1

Chạy trên Pi (qua SSH, hiện lên màn TFT):

    cd ~/photobooth
    DISPLAY=:0 python3 main.py

Thử trên máy tính không có webcam/màn TFT:

    PB_WINDOW=1 PB_FAKE_CAM=1 PB_FAST=1 python3 main.py

Biến môi trường: PB_CAM (số hoặc đường dẫn webcam), PB_PUBLIC_URL (địa chỉ gốc trong QR).
Thoát: 5 chạm nhanh vào góc trái trên ở màn chờ, hoặc Ctrl+C trong terminal SSH.
