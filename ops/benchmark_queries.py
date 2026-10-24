#!/usr/bin/env python3
"""Compare list readers on fictional data in an isolated *_test database."""

import argparse
import ast
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent

PROBE = r"""
import json, secrets, statistics, sys, time, types
from fastapi.encoders import jsonable_encoder
from sqlalchemy import event, select, text
from app import main
from app.auth import hasher
from app.config import cipher
from app.db import Session, engine
from app.models import Account, AccountGroup, AccountNote, AccountUser, Base, Claim, Event, Group, Settings, User

args = json.load(sys.stdin)
if not engine.url.database.endswith('_test'):
    raise SystemExit('Refusing to benchmark or reset a non-test database')
with Session.begin() as db:
    tables = ', '.join('"'+t.name+'"' for t in Base.metadata.sorted_tables)
    db.execute(text('TRUNCATE '+tables+' RESTART IDENTITY CASCADE'))
    db.add(Settings(id=1))
    password_hash = hasher.hash(secrets.token_urlsafe(32))
    users = [User(username='benchmark'+str(i), display_name='Fictional user '+str(i), role='admin' if i==0 else 'user', must_change_password=False, password_hash=password_hash) for i in range(4)]
    groups = [Group(name='Fictional group '+str(i)) for i in range(2)]
    db.add_all([*users,*groups]);db.flush()
    encrypted = cipher().encrypt(b'Fictional-Benchmark-Credential').decode()
    accounts = [Account(email='benchmark'+str(i)+'@example.test', tier='5x', password_encrypted=encrypted, auth_password_encrypted=encrypted, mail_tool='mailcom', quota=i%101) for i in range(args['accounts'])]
    db.add_all(accounts);db.flush()
    for i,a in enumerate(accounts):
        db.add_all(AccountGroup(account_id=a.id,group_id=g.id) for g in groups)
        db.add(AccountUser(account_id=a.id,user_id=users[0].id))
        db.add(AccountNote(account_id=a.id,author_id=users[0].id,content='Fictional benchmark note'))
        db.add(Claim(account_id=a.id,user_id=users[(i%3)+1].id))
        for j in range(5):db.add(Event(account_id=a.id,actor_id=users[(i+j)%4].id,kind='quota_updated',details={'quota':j}))

sql=[]
@event.listens_for(engine,'before_cursor_execute')
def record(conn,cursor,statement,parameters,context,executemany):
    if statement.lstrip().upper().startswith(('SELECT','WITH')):sql.append(statement)

def measure(function):
    samples=[];payload=None
    for iteration in range(args['repeat']+1):
        with Session() as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            user=db.scalar(select(User).where(User.username=='benchmark0'))
            sql.clear();start=time.perf_counter();cpu=time.process_time()
            rows=function(user=user,db=db,scope='all')
            payload=jsonable_encoder(rows)
            json.dumps(payload)
            sample={'queries':len(sql),'wall_ms':(time.perf_counter()-start)*1000,'cpu_ms':(time.process_time()-cpu)*1000}
            if iteration:samples.append(sample)
    return {key:round(statistics.median(sample[key] for sample in samples),2) for key in samples[0]},payload

report={'accounts':args['accounts'],'samples':args['repeat'],'measurement':'handler and JSON serialization; authentication excluded'}
report['current'],current=measure(main.accounts)
if args.get('baseline_domain'):
    domain=types.ModuleType('app._benchmark_baseline');domain.__package__='app'
    exec(compile(args['baseline_domain'],'<baseline-domain>','exec'),domain.__dict__)
    namespace=dict(vars(main),d=domain)
    exec(compile(args['baseline_accounts'],'<baseline-accounts>','exec'),namespace)
    report['baseline'],baseline=measure(namespace['accounts'])
    def normalize(rows):
        return {row['id']:{key:sorted(value) if key in {'group_ids','user_ids','current_users'} else value for key,value in row.items()} for row in rows}
    assert normalize(current)==normalize(baseline),'Reader response changed'
    report['responses_equivalent']=True
    report['latency_reduction_percent']=round(100*(1-report['current']['wall_ms']/report['baseline']['wall_ms']),1)
print(json.dumps(report,indent=2))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=int, default=58)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument(
        "--baseline-ref", help="Git revision to compare with the working tree"
    )
    parser.add_argument(
        "--reset-test-data",
        action="store_true",
        required=True,
        help="Explicitly allow replacement of fictional test data",
    )
    args = parser.parse_args()
    if not 1 <= args.accounts <= 1000 or not 1 <= args.repeat <= 20:
        parser.error("accounts must be 1–1000; repeat must be 1–20")
    payload = {"accounts": args.accounts, "repeat": args.repeat}
    if args.baseline_ref:

        def source(path):
            return subprocess.check_output(
                ["git", "show", f"{args.baseline_ref}:{path}"], cwd=ROOT, text=True
            )

        payload["baseline_domain"] = source("backend/app/domain.py")
        module = source("backend/app/main.py")
        function = next(
            node
            for node in ast.parse(module).body
            if isinstance(node, ast.FunctionDef) and node.name == "accounts"
        )
        payload["baseline_accounts"] = ast.get_source_segment(module, function)
    command = [
        str(ROOT / "ops/compose.sh"),
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "-e",
        "DB_NAME=account_manager_test",
        "-v",
        f"{ROOT / 'backend'}:/app:ro",
        "api",
        "python",
        "-c",
        PROBE,
    ]
    subprocess.run(command, cwd=ROOT, input=json.dumps(payload), text=True, check=True)


if __name__ == "__main__":
    main()
