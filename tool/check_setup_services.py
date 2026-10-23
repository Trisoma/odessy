"""Opt-in real native user-service qualification with a harmless short-lived task."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from odessy.setup.services import ServiceManager,ServiceSpec


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accept-native-user-service',action='store_true')
    parser.add_argument('--report-dir',required=True)
    args=parser.parse_args()
    if not args.accept_native_user_service:parser.error('explicit native service consent flag required')
    root=Path(args.report_dir).resolve()/('service-'+uuid.uuid4().hex);root.mkdir(parents=True)
    spec=ServiceSpec(uuid.uuid4().hex,(sys.executable,'-c','import time; time.sleep(10)'),str(root))
    manager=ServiceManager(root);registered=False;report={'accepted':False,'root':str(root)}
    try:
        report['registration']=manager.install(spec);registered=True
        report['start']=manager.action(spec,'start');report['status']=manager.action(spec,'status')
        report['stop']=manager.action(spec,'stop');report['remove']=manager.action(spec,'remove');registered=False
        report['accepted']=True
    except Exception:report['error']='Native qualification failed; check permissions and inspect retained ownership record. No elevation was attempted.'
    finally:
        if registered:
            try:report['cleanup']=manager.action(spec,'remove')
            except Exception:report['cleanup_required']=True
        (root/'acceptance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2));return 0 if report['accepted'] else 2

if __name__=='__main__':raise SystemExit(main())
