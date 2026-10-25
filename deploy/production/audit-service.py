"""Read-only service audit. Raw ticket bodies and logs never leave the VPS."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SOURCE = r'''
import json,subprocess,pathlib,re,shutil
def run(args,**kw):
 r=subprocess.run(args,capture_output=True,text=True,**kw)
 if r.returncode: raise RuntimeError('Audit command failed')
 return r.stdout
code="""import sqlite3,json,re
db=sqlite3.connect('file:/app/data/bot-state.sqlite3?mode=ro',uri=True); db.row_factory=sqlite3.Row
patterns={'connection':r'не.*подключ|не.*работ|connect.*fail','disconnect':r'обрыв|отключ|disconnect','speed':r'медлен|скорост|slow','import':r'импорт|подписк.*добав|import','payment':r'оплат|платеж|платёж|payment','quota':r'лимит|трафик|quota','device':r'устройств|happ'}
symptoms={key:[] for key in patterns}
for row in db.execute(\"select t.ticket_id,group_concat(m.body,' ') as body from support_tickets t join support_ticket_messages m using(ticket_id) where m.sender_role='user' group by t.ticket_id\"):
 for key,pattern in patterns.items():
  if re.search(pattern,row['body'],re.I): symptoms[key].append(row['ticket_id'])
data={'tickets_by_status':dict(db.execute('select status,count(*) from support_tickets group by status').fetchall()),'ticket_categories':dict(db.execute('select category,count(*) from support_tickets group by category').fetchall()),'ticket_symptoms':symptoms,'known_users':db.execute('select count(*) from user_preferences').fetchone()[0],'referrals':db.execute('select count(*) from referrals').fetchone()[0],'free_pending':db.execute('select count(*) from free_daily_reports where sent_at is null and abandoned_at is null').fetchone()[0],'orders':[dict(r) for r in db.execute('select telegram_id,plan_code,status,provider,created_at,paid_at,fulfilled_at,term_days from orders')],'preferences':[dict(r) for r in db.execute('select telegram_id,first_name,last_name,username from user_preferences')]}
print(json.dumps(data))
"""
data=json.loads(run(['docker','exec','-i','jopesvpn-production-bot','python','-'],input=code))
env=dict(x.split('=',1) for x in json.loads(run(['docker','inspect','--format','{{json .Config.Env}}','jopesvpn-production-db'])))
sql="SELECT row_to_json(t) FROM (SELECT username,telegram_id,created_at,expire_at,status,tag,round(extract(epoch from (expire_at-created_at))/86400,2) AS duration_days FROM users WHERE tag='PLAN_UNLIMITED_7' ORDER BY created_at) t"
rows=run(['docker','exec','jopesvpn-production-db','psql','-U',env['POSTGRES_USER'],'-d',env['POSTGRES_DB'],'-At','-c',sql])
users=[json.loads(line) for line in rows.splitlines() if line]
for user in users:
 user['orders']=[r for r in data['orders'] if r['telegram_id']==user['telegram_id']]
 user['profile']=next((r for r in data['preferences'] if r['telegram_id']==user['telegram_id']),None)
data.pop('orders'); data.pop('preferences'); data['ambiguous_users']=users
sql="SELECT count(*),count(*) FILTER (WHERE status='ACTIVE'),count(*) FILTER (WHERE tag like 'PLAN_%') FROM users"
counts=list(map(int,run(['docker','exec','jopesvpn-production-db','psql','-U',env['POSTGRES_USER'],'-d',env['POSTGRES_DB'],'-At','-c',sql]).strip().split('|')))
data['panel_users']=dict(zip(('total','active','plan_tagged'),counts))
patterns={'telegram_network':r'TelegramNetworkError','telegram_server':r'TelegramServerError','telegram_timeout':r'Request timeout','panel_unavailable':r'PanelUnavailable','quota':r'LIMITED','tls':r'TLS handshake|tls handshake|certificate.*(?:error|fail)','connection_reset':r'connection reset|connection refused','critical':r'ERROR|Traceback|CRITICAL'}
data['logs']={}
for name in ('bot','node','panel','proxy','ticket-forum'):
 r=subprocess.run(['docker','logs','--since','168h','jopesvpn-production-'+name],capture_output=True,text=True)
 text=r.stdout+r.stderr
 data['logs'][name]={key:len(re.findall(pattern,text,re.I if key!='critical' else 0)) for key,pattern in patterns.items()}
mem={line.split(':')[0]:int(line.split()[1]) for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
disk=shutil.disk_usage('/opt/jopesvpn')
data['resources']={'ram_total_mb':mem['MemTotal']//1024,'available_mb':mem['MemAvailable']//1024,'swap_used_mb':(mem['SwapTotal']-mem['SwapFree'])//1024,'disk_used_percent':round(100*disk.used/disk.total,1),'disk_free_gb':round(disk.free/1024**3,1)}
print(json.dumps(data))
'''


def audit(directory: Path) -> dict:
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "jopesvpn",
            "python3 -",
        ],
        input=SOURCE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Read-only audit failed")
    data = json.loads(result.stdout)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "service-audit.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Проверка PLAN_UNLIMITED_7",
        "",
        "Только чтение. Даты из Panel; срок от создания не доказывает купленный план.",
        "",
        "| Аккаунт Panel | Telegram ID | Создан (UTC) | Доступ до (UTC) | Дней от создания | Статус | Факты заказов |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for user in data["ambiguous_users"]:
        facts = (
            "; ".join(
                f"{r['plan_code']}: {r['status']}, {r['term_days']} дн., {r['provider']}, {r['created_at']}"
                for r in user["orders"]
            )
            or "Заказов бота нет; исходный план не восстановлен"
        )
        lines.append(
            f"| {user['username']} | {user['telegram_id'] or 'не задан'} | {user['created_at']} | {user['expire_at']} | {user['duration_days']} | {user['status']} | {facts} |"
        )
    lines += [
        "",
        "Предложение: сохранить TAG и доступы; использовать подтверждённые заказы для индивидуальной сверки. При отсутствии заказа длительный срок может быть ручной выдачей или продлением, но это не доказано.",
    ]
    (directory / "PLAN_TAG_AUDIT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return {key: value for key, value in data.items() if key != "ambiguous_users"}


if __name__ == "__main__":
    print(json.dumps(audit(Path("docs/service-reliability")), ensure_ascii=False))
