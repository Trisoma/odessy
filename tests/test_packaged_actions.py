import pytest

def test_package_requires_verified_context_before_mutation(monkeypatch):
    from odessy.setup.native_bridge import manage
    from odessy.setup import package_context
    monkeypatch.setattr(package_context,'context',lambda **kwargs: (_ for _ in ()).throw(ValueError('damaged package')))
    with pytest.raises(ValueError,match='damaged package'):
        manage({'action':'package-review','root':'C:/test','payload':'C:/payload','selection':{'role':'combined'}})
