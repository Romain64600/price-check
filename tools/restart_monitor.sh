#!/bin/bash
# Redémarrage du moniteur et du bot après un changement de code, sans couper un passage : attend la fin du passage en cours
# (jamais pendant un passage demandé depuis l'admin), vérifie que le bot ne traite rien, puis lit le recalcul des reports au
# démarrage dans le journal. Usage : sudo tools/restart_monitor.sh
# attend la fin du passage en cours (20 min max), puis redémarre le moniteur et le bot ; jamais pendant un passage demandé
for i in $(seq 1 120); do
  busy=$(python3 -c "
import json
d = json.load(open('/var/lib/price-check/status.json'))['modes']
print(int(any(s.get('running') or s.get('requested_by') for s in d.values())))")
  [ "$busy" = "0" ] && break
  sleep 10
done
[ "$busy" = "0" ] || { echo "passage toujours en cours après 20 min : pas de redémarrage"; exit 2; }
kids=$(pgrep -P "$(systemctl show -p MainPID --value price-check-bot)" | wc -l)
[ "$kids" = "0" ] || { echo "le bot traite un message : pas de redémarrage"; exit 3; }
date +%H:%M:%S
systemctl restart price-check price-check-bot
sleep 8
systemctl is-active price-check price-check-bot | paste -sd' '
start=$(date "+%Y-%m-%d %H:%M:%S" -d "-10 seconds")
for i in $(seq 1 60); do
  line=$(journalctl -u price-check --since "$start" --no-pager -o cat | grep "recalcul des reports" | tail -1)
  [ -n "$line" ] && break
  sleep 10
done
echo "${line:-pas de recalcul dans le journal}" | cut -c1-300
journalctl -u price-check --since "$start" --no-pager -o cat | grep -E "🧹|✅ Repaired" | cut -c1-400 | tail -3
