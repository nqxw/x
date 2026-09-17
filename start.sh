#!/bin/bash
echo "[selfbot] Installing dependencies..."
pip install -r requirements.txt
echo "[selfbot] Starting..."
python3 selfbot.py
