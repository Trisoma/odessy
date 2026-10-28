import json
from odessy.setup.native_bridge import pair


def test_discovery_is_structured_and_read_only(tmp_path):
    binary=tmp_path/'odessy-link';binary.write_bytes(b'fixture')
    calls=[]
    def run(argv):
        calls.append(argv)
        return json.dumps({'schema_version':1,'devices':[{'name':'Laptop','address':'192.168.1.5:7443',
            'advertised_fingerprint':'a'*64,'usable':True,'trusted':False}]})
    result=pair({'action':'discover','link':str(binary)},runner=run)
    assert calls[0][1:]==['discover','--seconds','2','--json']
    assert result['devices'][0]['address']=='192.168.1.5:7443'
    assert result['devices'][0]['trusted'] is False
    assert result['grants_changed'] is False
