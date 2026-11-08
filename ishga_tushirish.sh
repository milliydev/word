#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
# O'z raqamingizni yozing:
export ADMIN_PHONE="+998901234567"
python server.py
