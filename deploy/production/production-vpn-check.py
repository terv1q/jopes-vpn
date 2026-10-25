"""Disposable production subscription check; never prints subscription credentials."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

SOURCE = r"""
import asyncio,base64,json,os,secrets
from datetime import UTC,datetime,timedelta
from urllib.parse import urlsplit,parse_qs,unquote
import httpx
from jopesvpn.config import Settings

async def run(cleanup_only):
 s=Settings.from_env(); prefix=os.environ.get('OPS_TEST_USER_PREFIX','ops_probe_')
 tag='OPS_TEST'; now=datetime.now(UTC)
 if not prefix.startswith('ops_probe_') or len(prefix)>20: raise ValueError('Unsafe test prefix')
 headers={'Authorization':'Bearer '+s.panel_api_token}
 result={'test_only':True,'subscriptions':{},'cleanup':False,'outbounds':[]}
 async with httpx.AsyncClient(headers=headers,verify=s.panel_ca_bundle or True,timeout=15) as api:
  base=s.panel_api_url.rstrip('/')
  if cleanup_only:
   r=await api.get(base+'/api/users?size=1000');r.raise_for_status()
   users=r.json()['response']['users']; deleted=0
   for user in users:
    if user.get('tag')!=tag or not user.get('username','').startswith(prefix) or not user.get('description','').startswith('Jopes VPN automatic probe:'): continue
    if datetime.fromisoformat(user['expireAt'].replace('Z','+00:00'))>now: continue
    r=await api.delete(base+'/api/users/'+str(user['id']));r.raise_for_status();deleted+=1
   return {'deleted_expired_test_users':deleted}
  name=prefix+secrets.token_hex(5)
  duration=int(os.environ.get('OPS_TEST_DURATION_MINUTES','15'))
  if not 5<=duration<=30: raise ValueError('Invalid test duration')
  body={'username':name,'expireAt':(now+timedelta(minutes=duration)).isoformat(),'status':'ACTIVE','tag':tag,'trafficLimitBytes':16*1024**2,'description':'Jopes VPN automatic probe: disposable, expires in '+str(duration)+' minutes','activeInternalSquads':[s.panel_internal_squad_uuid]}
  uid=None
  try:
   r=await api.post(base+'/api/users',json=body);r.raise_for_status()
   user=r.json()['response'];uid=str(user['id'])
   if user['username']!=name or user.get('tag')!=tag: raise ValueError('Test identity mismatch')
   async with httpx.AsyncClient(verify=s.panel_ca_bundle or True,timeout=15,follow_redirects=False) as sub:
    for label,agent in [('xray','v2rayN/7.0'),('happ','Happ/2.0'),('singbox','sing-box/1.12.0')]:
     r=await sub.get(user['subscriptionUrl'],headers={'User-Agent':agent})
     result['subscriptions'][label]={'http':r.status_code,'bytes':len(r.content),'content_type':r.headers.get('content-type','').split(';')[0]}
     r.raise_for_status()
     text=r.text.strip()
     if '://' not in text:
      try:text=base64.b64decode(text+'='*((-len(text))%4)).decode()
      except (ValueError,UnicodeError): pass
     try: parsed_json=json.loads(text)
     except ValueError: parsed_json=None
     if isinstance(parsed_json,list):
      parsed_json={'outbounds':[outbound for cfg in parsed_json if isinstance(cfg,dict) for outbound in cfg.get('outbounds',[])]}
     if isinstance(parsed_json,dict) and isinstance(parsed_json.get('outbounds'),list):
      entries=[entry for entry in parsed_json['outbounds'] if entry.get('protocol')=='vless']
      result['subscriptions'][label]['json_valid']=True
      result['subscriptions'][label]['protocols']=sorted({entry.get('protocol',entry.get('type','unknown')) for entry in parsed_json['outbounds']})
      if label=='happ':
       for entry in parsed_json['outbounds']:
        if entry.get('protocol')=='hysteria':
         entry['label']='hysteria2';result['outbounds'].append(entry)
      if label=='xray':
       for entry in entries:
        entry['label']=entry.get('streamSettings',{}).get('network','tcp');result['outbounds'].append(entry)
      continue
     if label=='singbox':
      try: result['subscriptions'][label]['json_valid']=isinstance(json.loads(text),dict)
      except ValueError: result['subscriptions'][label]['json_valid']=False
     else:
      uris=[line.strip() for line in text.splitlines() if line.startswith(('vless://','hysteria2://','hy2://'))]
      result['subscriptions'][label]['protocols']=sorted({urlsplit(line).scheme for line in uris})
      if not uris:
       error=ValueError('No supported subscription entries');error.safe_subscription=result['subscriptions'];raise error
      for uri in uris:
       parsed=urlsplit(uri);query={k:v[0] for k,v in parse_qs(parsed.query).items()}
       if not parsed.hostname or not parsed.port or not parsed.username: raise ValueError('Incomplete subscription URI')
       if label!='xray' or parsed.scheme!='vless': continue
       security=query.get('security');network=query.get('type','tcp')
       stream={'network':network,'security':security}
       if security=='reality':
        if not query.get('pbk') or not query.get('sni'): raise ValueError('Incomplete REALITY settings')
        stream['realitySettings']={'serverName':query['sni'],'fingerprint':query.get('fp','chrome'),'publicKey':query['pbk'],'shortId':query.get('sid','')}
       elif security=='tls':stream['tlsSettings']={'serverName':query.get('sni',parsed.hostname)}
       if network=='xhttp':stream['xhttpSettings']={'path':query.get('path','/'),'host':query.get('host',parsed.hostname),'mode':query.get('mode','auto')}
       if network not in ('tcp','xhttp'): continue
       result['outbounds'].append({'label':network,'protocol':'vless','settings':{'vnext':[{'address':parsed.hostname,'port':parsed.port,'users':[{'id':unquote(parsed.username),'encryption':'none','flow':query.get('flow','')}]}]},'streamSettings':stream})
  except Exception:
   if uid is not None:
    r=await api.get(base+'/api/users/'+uid);r.raise_for_status();actual=r.json()['response']
    if actual.get('tag')==tag and actual.get('username')==name:
     r=await api.delete(base+'/api/users/'+uid);r.raise_for_status();uid=None
   raise
  finally:
   if uid is not None:
    r=await api.get(base+'/api/users/'+uid);r.raise_for_status();actual=r.json()['response']
    if actual.get('tag')!=tag or actual.get('username')!=name: raise ValueError('Refusing to remove non-test user')
    # Return test-only configs while the parent process tests the transport. It owns final deletion.
    result['test_uuid']=uid;result['test_name']=name
  return result

try: print(json.dumps(asyncio.run(run(__CLEANUP__))))
except Exception as error:
 import traceback
 print(json.dumps({'failed':type(error).__name__,'subscriptions':getattr(error,'safe_subscription',{}),'http_status':getattr(getattr(error,'response',None),'status_code',None),'lines':[frame.lineno for frame in traceback.extract_tb(error.__traceback__) if frame.filename=='<stdin>']}));raise SystemExit(1) from None
"""


def container_code(code: str) -> dict:
    result = subprocess.run(
        ["docker", "exec", "-i", "jopesvpn-production-bot", "python", "-"],
        input=code,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        detail = json.loads(result.stdout)
        print(json.dumps({"container_failure": detail}))
        raise RuntimeError("Container probe failed")
    return json.loads(result.stdout)


def run(cleanup_only: bool) -> dict:
    data = container_code(SOURCE.replace("__CLEANUP__", repr(cleanup_only)))
    if cleanup_only:
        return data
    uid = data.pop("test_uuid")
    name = data.pop("test_name")
    outbounds = data.pop("outbounds")
    try:
        with tempfile.TemporaryDirectory(prefix="jopesvpn-probe-") as directory:
            staging = Path(directory)
            binary = staging / "xray"
            subprocess.run(
                [
                    "docker",
                    "cp",
                    "jopesvpn-production-node:/usr/local/bin/xray",
                    str(binary),
                ],
                capture_output=True,
                check=True,
            )
            binary.chmod(0o700)
            data["transports"] = {}
            for outbound in outbounds:
                label = outbound.pop("label")
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                config = staging / "client.json"
                config.write_text(
                    json.dumps(
                        {
                            "log": {"loglevel": "none"},
                            "inbounds": [
                                {
                                    "listen": "127.0.0.1",
                                    "port": port,
                                    "protocol": "http",
                                    "settings": {},
                                }
                            ],
                            "outbounds": [outbound],
                        }
                    )
                )
                config.chmod(0o600)
                validation = subprocess.run(
                    [str(binary), "run", "-test", "-c", str(config)],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                if validation.returncode:
                    data["transports"][label] = {"config_valid": False}
                    continue
                process = subprocess.Popen(
                    [str(binary), "run", "-c", str(config)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    time.sleep(1)
                    opener = build_opener(
                        ProxyHandler(
                            {
                                "http": f"http://127.0.0.1:{port}",
                                "https": f"http://127.0.0.1:{port}",
                            }
                        )
                    )
                    with opener.open("https://example.com", timeout=20) as response:
                        data["transports"][label] = {
                            "config_valid": True,
                            "https_status": response.status,
                            "bytes": len(response.read(4096)),
                        }
                except OSError as error:
                    data["transports"][label] = {
                        "config_valid": True,
                        "failed": type(error).__name__,
                    }
                finally:
                    process.terminate()
                    process.wait(timeout=10)
    finally:
        code = """import asyncio,json,httpx
from jopesvpn.config import Settings
async def cleanup():
 s=Settings.from_env()
 async with httpx.AsyncClient(headers={'Authorization':'Bearer '+s.panel_api_token},verify=s.panel_ca_bundle or True,timeout=15) as client:
  url=s.panel_api_url.rstrip('/')+'/api/users/'+__UUID__
  r=await client.get(url);r.raise_for_status();user=r.json()['response']
  if user.get('tag')!='OPS_TEST' or user.get('username')!=__NAME__: raise ValueError('Not a test user')
  r=await client.delete(url);r.raise_for_status()
 print(json.dumps({'cleanup':True}))
asyncio.run(cleanup())
""".replace("__UUID__", repr(uid)).replace("__NAME__", repr(name))
        data["cleanup"] = container_code(code)["cleanup"]
    ports = subprocess.run(
        ["ss", "-lun"], capture_output=True, text=True, check=True
    ).stdout
    data["hysteria_udp_listener"] = ":443 " in ports
    return data


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.cleanup_only)))
    except Exception as error:  # noqa: BLE001 -- hide test credentials and provider URLs
        print(
            json.dumps(
                {
                    "probe_failed": type(error).__name__,
                    "test_access_expires_in_minutes": 15,
                }
            )
        )
        raise SystemExit(1) from None
