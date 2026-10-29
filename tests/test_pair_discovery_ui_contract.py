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


def test_one_device_has_one_row_and_prefers_pairing_ipv4(tmp_path):
    binary=tmp_path/'odessy-link';binary.write_bytes(b'fixture')
    peers=[{'name':'Laptop','address':address,'purpose':purpose,
      'advertised_fingerprint':'a'*64,'usable':True,'trusted':False}
      for address,purpose in [('[2001:db8::1]:7443','execution'),
        ('10.1.1.1:60095','pairing'),('[2001:db8::1]:60095','pairing'),
        ('10.1.1.1:7443','execution')]]
    result=pair({'action':'discover','link':str(binary)},runner=lambda _:json.dumps({'schema_version':1,'devices':peers}))
    assert len(result['devices'])==1
    assert result['devices'][0]['address']=='10.1.1.1:60095'
    assert result['devices'][0]['trusted'] is False
