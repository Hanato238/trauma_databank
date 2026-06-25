#!/bin/bash
# Claude Code Stop hook — ~/.claude/stats.json を更新する
payload=$(cat)
stats_file="$HOME/.claude/stats.json"

model=$(echo "$payload" | jq -r '.model // "unknown"' 2>/dev/null)
input_tokens=$(echo "$payload" | jq -r '.usage.input_tokens // 0' 2>/dev/null)
output_tokens=$(echo "$payload" | jq -r '.usage.output_tokens // 0' 2>/dev/null)
cache_read=$(echo "$payload" | jq -r '.usage.cache_read_input_tokens // 0' 2>/dev/null)

turn_count=$(jq -r '.turn_count // 0' "$stats_file" 2>/dev/null)
turn_count=$((${turn_count:-0} + 1))

jq -n \
  --arg model "$model" \
  --argjson input_tokens "${input_tokens:-0}" \
  --argjson output_tokens "${output_tokens:-0}" \
  --argjson cache_read "${cache_read:-0}" \
  --argjson turn_count "$turn_count" \
  '{model: $model, input_tokens: $input_tokens, output_tokens: $output_tokens, cache_read: $cache_read, turn_count: $turn_count}' \
  > "$stats_file"
