#!/usr/bin/env bash
# ./open_profile.sh <remote profile dir>   then open http://localhost:8800
ssh -L 8800:localhost:8800 chia-dev.us-west4-c.chia-codesign26oak-1951 \
    "cd $1 && python3 -m http.server 8800"
