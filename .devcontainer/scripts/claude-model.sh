#!/bin/bash
model=$(jq -re '.model // empty' ~/.claude/stats.json 2>/dev/null) || { echo "---"; exit 0; }
tmp="${model#claude-}"
echo "${tmp%%-*}"
