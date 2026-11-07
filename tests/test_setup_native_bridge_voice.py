"""The packaged bridge must pass the chosen features through untouched.

The wake word is a separate opt-in stored outside the voice root. If the bridge
dropped `features`, a wake-only request would silently plan and install every
artifact — the two selections would disagree about what the user asked for.
"""
import pytest
from odessy.setup import native_bridge


def _dispatch(monkeypatch,calls):
    def fake(parameters,*,cancelled=lambda: False):
        calls.append(dict(parameters))
        return {'ok':True}
    monkeypatch.setattr('odessy.setup.voice_install.dispatch',fake)


@pytest.mark.parametrize('action',['voice-plan','voice-status','voice-python-install'])
def test_voice_actions_forward_the_selected_features(monkeypatch,action):
    calls=[]
    _dispatch(monkeypatch,calls)
    native_bridge.manage({'action':action,'root':'/tmp/ignored','features':['wake']})
    assert calls==[{'action':action,'features':['wake']}]


def test_voice_install_forwards_features_without_the_confirm_free_shape(monkeypatch,tmp_path):
    calls=[]
    _dispatch(monkeypatch,calls)
    native_bridge.manage({'action':'voice-install','features':['wake'],'confirm':'plan-1',
                          'install_root':'/tmp/root'})
    assert calls[0]['features']==['wake']
    assert calls[0]['confirm']=='plan-1'


def test_voice_python_install_normalises_the_install_root(monkeypatch,tmp_path):
    calls=[]
    _dispatch(monkeypatch,calls)
    root=tmp_path/'runtime'
    native_bridge.manage({'action':'voice-python-install','features':['voice','wake'],
                          'install_root':str(root)})
    assert calls[0]['install_root']==str(root)
    assert calls[0]['features']==['voice','wake']


@pytest.mark.parametrize('features',[[],['voice','wake','extra'],['voice',7],'wake',{}])
def test_an_unusable_feature_list_is_refused(monkeypatch,features):
    calls=[]
    _dispatch(monkeypatch,calls)
    with pytest.raises(ValueError):
        native_bridge.manage({'action':'voice-plan','features':features})
    assert calls==[]
