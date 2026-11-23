"""Read one payer-supplied XV order. All output is strictly boolean."""
import getpass,json,re,subprocess,sys
SQL = """
SELECT (o.amount_fen=100 AND o.amount=1 AND o.duration_days=5 AND o.tier='vip' AND v.env=0 AND v.product_id='membership_diamond') AS product_matches,
(o.paid_at IS NOT NULL AND o.wechat_transaction_id IS NOT NULL) AS platform_payment_recorded,
v.delivery_confirmed AS delivery_confirmed,
(o.paid_at IS NOT NULL AND v.delivery_confirmed AND o.status='已发放' AND o.refunded_at IS NULL AND i.status='issued' AND i.duration_days=5 AND i.tier='vip' AND e.user_id=o.user_id AND e.kind='vip' AND e.source='order') AS issued_once,
(o.status='已退款' AND o.refunded_at IS NOT NULL AND v.refund_no IS NOT NULL AND v.wx_refund_no IS NOT NULL) AS verified_refund_recorded,
(o.status='已退款' AND o.refunded_at IS NOT NULL AND v.wx_refund_no IS NOT NULL AND i.status='issued' AND e.user_id=o.user_id AND e.source='order' AND e.kind='vip' AND e.expires_at<=o.refunded_at) AS refunded_grant_ended
FROM orders o JOIN virtual_orders v ON v.order_id=o.id
LEFT JOIN order_issuances i ON i.order_id=o.id LEFT JOIN entitlements e ON e.id=i.entitlement_id
WHERE o.product='membership_diamond' AND o.order_number=:number
"""
def runner(number):
    if not re.fullmatch('XV[0-9a-f]{28}',number):
        raise ValueError('请输入本人本次钻石订单的完整订单号。')
    app="""
import json,sys
from sqlalchemy import create_engine,text
from nadou.config import get_settings
with create_engine(get_settings().database_url).connect() as conn:
    conn.exec_driver_sql('SET TRANSACTION READ ONLY')
    result=conn.execute(text(QUERY),{'number':sys.stdin.readline().strip()}).mappings().one_or_none()
    checks={'matching_order':result is not None}
    if result:checks.update({k:bool(v) for k,v in result.items()})
    print(json.dumps(checks))
""".replace('QUERY',repr(SQL))
    remote="""
import json,os,subprocess,sys
from pathlib import Path
os.chdir('/home/nadou/nadouzaijia-deploy')
r=subprocess.run(['docker','compose','-f','compose.prod.yml','exec','-T','-e','PGOPTIONS=-c default_transaction_read_only=on','api','python','-c',APP],input=NUMBER.encode(),capture_output=True,timeout=15)
assert r.returncode==0
obj=json.loads(r.stdout);assert isinstance(obj,dict) and all(type(v) is bool for v in obj.values())
print(json.dumps(obj))
""".replace('APP]',repr(app)+']').replace('NUMBER.encode()',repr(number)+'.encode()')
    cmd=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8','nadou-prod','sudo -n python3 -']
    p=subprocess.run(cmd,input=remote.encode(),capture_output=True,timeout=45)
    if p.returncode:raise RuntimeError('只读核验暂未完成')
    result=json.loads(p.stdout);assert isinstance(result,dict) and all(type(v) is bool for v in result.values())
    return result
if __name__=='__main__':
    number=getpass.getpass('本人钻石订单号（输入隐藏）: ') if sys.stdin.isatty() else sys.stdin.readline().strip()
    print(json.dumps(runner(number),ensure_ascii=False))
