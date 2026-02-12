#!/bin/bash
# Usage: ./quickpush.sh myproject

# mkdir "$1"
# cd "$1"
# echo '# My Python Project' > README.md
# touch main.py
git init
git add .
git commit -m "Initial commit"
gh repo create "$1" --private --source=. --push

