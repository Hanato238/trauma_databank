#!/bin/bash
jq -re '
  (.input_tokens // 0) as $i |
  (.output_tokens // 0) as $o |
  ($i + $o) as $total |
  (.turn_count // 0) as $t |
  (if $total > 999 then
    (($total / 1000 * 10 | round) / 10 | tostring) + "k"
  else
    ($total | tostring)
  end) + " " + ($t | tostring) + "t"
' ~/.claude/stats.json 2>/dev/null || echo "0t"
