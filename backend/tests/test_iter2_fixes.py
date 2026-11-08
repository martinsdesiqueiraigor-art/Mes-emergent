# Iteration 2 retest: login input, progresso formula, roteiro API path, pendencias 401, burger RBAC, exports
import re
import pytest
import requests
from test_rbac_pcp import BASE_URL, login, OPERADOR, SUP, PCP


@pytest.fixture(scope='module')
def sup():
    return login(SUP)[0]


@pytest.fixture(scope='module')
def pcp():
    return login(PCP)[0]


@pytest.fixture(scope='module')
def opr():
    return login(OPERADOR)[0]


# Login template fixes
def test_login_input_text_and_no_hint():
    html = requests.get(f"{BASE_URL}/login").text
    m = re.search(r'<input[^>]*name="matricula"[^>]*>', html)
    assert m and 'type="text"' in m.group(0) and 'inputmode="numeric"' in m.group(0)
    assert 'Teste:' not in html and 'Senha: 123456' not in html


# PCP progresso column matches acompanhar
def test_pcp_progresso_matches_acompanhar(pcp):
    html = pcp.get(f"{BASE_URL}/pcp").text
    m = re.search(r'data-testid="pcp-progresso-OP-002">([\d.]+)%', html)
    assert m, 'pcp-progresso-OP-002 missing'
    acomp = pcp.get(f"{BASE_URL}/pcp/acompanhar/OP-002").text
    assert f"{m.group(1)}%" in acomp
    assert float(m.group(1)) == 50.0


def test_pcp_table_overflow_wrapper(pcp):
    assert 'overflow-x:auto' in pcp.get(f"{BASE_URL}/pcp").text


# Roteiro API moved
def test_roteiro_api_supervisor(sup):
    r = sup.get(f"{BASE_URL}/supervisor/api/roteiro/1")
    assert r.status_code == 200
    assert r.json()['id'] == 1


def test_roteiro_api_404(sup):
    assert sup.get(f"{BASE_URL}/supervisor/api/roteiro/99999999").status_code == 404


@pytest.mark.parametrize('who', ['opr', 'pcp'])
def test_roteiro_api_blocked(who, request):
    s = request.getfixturevalue(who)
    r = s.get(f"{BASE_URL}/supervisor/api/roteiro/1", allow_redirects=False)
    assert r.status_code == 302


def test_roteiro_api_anon():
    r = requests.get(f"{BASE_URL}/supervisor/api/roteiro/1", allow_redirects=False)
    assert r.status_code in (401, 302)


# pendencias count
def test_pendencias_anon_401():
    assert requests.get(f"{BASE_URL}/pendencias/count").status_code == 401


def test_pendencias_logged(sup):
    r = sup.get(f"{BASE_URL}/pendencias/count")
    assert r.status_code == 200 and isinstance(r.json()['count'], int)


# Burger menu only for supervisor
def test_burger_absent_for_operador(opr):
    html = opr.get(f"{BASE_URL}/operador").text
    assert 'id="burgerMenu"' not in html


def test_burger_absent_for_pcp(pcp):
    assert 'id="burgerMenu"' not in pcp.get(f"{BASE_URL}/pcp").text


def test_burger_present_for_supervisor(sup):
    assert 'id="burgerMenu"' in sup.get(f"{BASE_URL}/supervisor").text


# Exports
@pytest.mark.parametrize('kind', ['oee', 'pareto', 'aging', 'produtividade'])
@pytest.mark.parametrize('fmt', ['xlsx', 'pdf'])
def test_exports(sup, kind, fmt):
    r = sup.get(f"{BASE_URL}/supervisor/export/{kind}/{fmt}")
    assert r.status_code == 200 and len(r.content) > 200
    assert r.content[:4] == (b'%PDF' if fmt == 'pdf' else b'PK\x03\x04')


# RBAC regression
def test_pcp_routes(pcp):
    assert pcp.get(f"{BASE_URL}/pcp/acompanhar/OP-002").status_code == 200
    r = pcp.get(f"{BASE_URL}/operador", allow_redirects=False)
    assert r.status_code == 302 and '/pcp' in r.headers['Location']
    r = pcp.get(f"{BASE_URL}/supervisor", allow_redirects=False)
    assert r.status_code == 302
