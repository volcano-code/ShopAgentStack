"""Response contract regressions. These doubles are not hosted business acceptance."""
import json
from pathlib import Path
import pytest
from tools.shop_demo import smoke
from tools.shop_demo.runtime import DemoError


@pytest.mark.parametrize('body', [{'code': 200, 'message': 'registered'}, {'code': 200, 'data': None}])
def test_empty_registration_success_is_explicitly_supported(monkeypatch, body):
    monkeypatch.setattr(smoke, 'request', lambda *a, **kw: (200, body))
    assert smoke.business('http://127.0.0.1', '/api/portal/sso/register', allow_empty=True) is None


@pytest.mark.parametrize('body', [{'code': 200}, {'code': 200, 'data': None}])
def test_read_response_must_still_contain_data(monkeypatch, body):
    monkeypatch.setattr(smoke, 'request', lambda *a, **kw: (200, body))
    with pytest.raises(DemoError, match='data missing'):
        smoke.business('http://127.0.0.1', '/api/portal/cart/list')


@pytest.mark.parametrize('status,body', [(403, None), (200, {'code': 403}), (200, None),
                                        (200, []), (200, {'data': {}}), (503, {'code': 200})])
def test_optional_data_does_not_mask_failed_business_status(monkeypatch, status, body):
    monkeypatch.setattr(smoke, 'request', lambda *a, **kw: (status, body))
    with pytest.raises(DemoError, match='business request rejected'):
        smoke.business('http://127.0.0.1', '/api/portal/sso/register', allow_empty=True)


@pytest.mark.parametrize('data', [[], 0, False, {'id': 1}])
def test_required_data_accepts_valid_empty_collection_or_scalar(monkeypatch, data):
    monkeypatch.setattr(smoke, 'request', lambda *a, **kw: (200, {'code': 200, 'data': data}))
    assert smoke.business('http://127.0.0.1', '/api/portal/cart/list') == data


def test_failure_locations_contain_no_exception_values_or_locals():
    private_value = 'SYNTHETIC-PRIVATE-DO-NOT-PUBLISH'
    try:
        raise KeyError(private_value)
    except KeyError as error:
        locations = smoke.failure_locations(error)
    assert locations
    encoded = json.dumps(locations)
    assert private_value not in encoded
    assert all(set(row) == {'file', 'function', 'line'} for row in locations)
    assert all(not Path(row['file']).is_absolute() for row in locations)
    assert locations[-1]['function'] == 'test_failure_locations_contain_no_exception_values_or_locals'


def test_actual_registration_controller_returns_no_payload():
    root = smoke.ROOT
    controller = (root / 'services/commerce/mall-portal/src/main/java/com/macro/mall/portal/controller/UmsMemberController.java').read_text()
    config = (root / 'services/commerce/mall-portal/src/main/java/com/macro/mall/portal/config/JacksonConfig.java').read_text()
    assert 'CommonResult.success(null,"注册成功")' in controller
    assert 'JsonInclude.Include.NON_NULL' in config
