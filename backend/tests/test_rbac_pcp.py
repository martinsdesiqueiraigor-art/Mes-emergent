# RBAC (operador / supervisor / pcp) + PCP acompanhamento por fase - MES/APS Flask
import os
import re
import sqlite3
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    with open('/app/frontend/.env') as fh:
        for line in fh:
            if line.startswith('REACT_APP_BACKEND_URL='):
                BASE_URL = line.split('=', 1)[1].strip().rstrip('/')
DB = '/app/backend/producao.db'

OPERADOR = ('1025', '123456')
SUP = ('TEST_SUP', 'teste123')
PCP = ('TEST_PCP', 'teste123')

SUP_ONLY_GET = ['/supervisor', '/supervisor/fila', '/supervisor/relatorios', '/supervisor/gerenciar',
                '/supervisor/saude', '/supervisor/historico', '/supervisor/export/oee/pdf',
                '/relatorio/csv/hoje']


def login(cred):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/login", data={'matricula': cred[0], 'senha': cred[1]}, allow_redirects=False)
    assert r.status_code == 302, r.text[:200]
    return s, r.headers.get('Location', '')


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def nav_html(html):
    m = re.search(r'<div class="nav-tabs">(.*?)</div>', html, re.S)
    return m.group(1) if m else ''


@pytest.fixture(scope='module')
def op_s():
    return login(OPERADOR)[0]


@pytest.fixture(scope='module')
def sup_s():
    return login(SUP)[0]


@pytest.fixture(scope='module')
def pcp_s():
    return login(PCP)[0]


@pytest.fixture(scope='module')
def any_op():
    c = db()
    r = c.execute("SELECT num_op FROM ordens_producao WHERE num_op='OP-002'").fetchone()
    return r['num_op'] if r else c.execute("SELECT num_op FROM ordens_producao LIMIT 1").fetchone()['num_op']


# --- Login
class TestLogin:
    def test_login_page(self):
        r = requests.get(f"{BASE_URL}/login")
        assert r.status_code == 200 and 'name="matricula"' in r.text and 'name="senha"' in r.text

    def test_bad_password(self):
        r = requests.post(f"{BASE_URL}/login", data={'matricula': '1024', 'senha': 'x'}, allow_redirects=False)
        assert r.status_code == 302 and '/login' in r.headers['Location']

    def test_anonymous_pcp_redirects_login(self):
        r = requests.get(f"{BASE_URL}/pcp", allow_redirects=False)
        assert r.status_code == 302 and '/login' in r.headers['Location']


# --- Operador RBAC
class TestOperador:
    def test_nav_only_op_manual(self, op_s):
        r = op_s.get(f"{BASE_URL}/operador")
        assert r.status_code == 200
        nav = nav_html(r.text)
        assert 'nav-pcp' not in nav
        assert '/supervisor' not in nav
        assert 'href="/operador"' in nav and 'href="/manual"' in nav

    @pytest.mark.parametrize('path', ['/pcp', '/pcp/acompanhar/OP-002'] + SUP_ONLY_GET)
    def test_blocked_get(self, op_s, path):
        r = op_s.get(f"{BASE_URL}{path}", allow_redirects=False)
        assert r.status_code == 302, f"{path} -> {r.status_code}"
        assert r.headers['Location'].endswith('/operador')
        follow = op_s.get(f"{BASE_URL}/operador")
        assert 'Acesso restrito ao supervisor' in follow.text

    @pytest.mark.parametrize('path', ['/pcp/nova', '/supervisor/operador/reset/1026'])
    def test_blocked_post(self, op_s, path):
        r = op_s.post(f"{BASE_URL}{path}", data={}, allow_redirects=False)  # must be denied before handler runs
        assert r.status_code == 403

    @pytest.mark.parametrize('path', ['/operador', '/manual'])
    def test_allowed(self, op_s, path):
        assert op_s.get(f"{BASE_URL}{path}").status_code == 200

    def test_anexo_not_blocked(self, op_s, any_op):
        r = op_s.get(f"{BASE_URL}/anexo/{any_op}", allow_redirects=False)
        # 200 when attachment exists; otherwise redirects to /operador w/o 'restrito'
        assert r.status_code in (200, 302)
        if r.status_code == 302:
            assert '/operador' in r.headers['Location']

    def test_my_counters(self, op_s):
        c = db()
        row = c.execute("""SELECT COALESCE(SUM(qtd_boas),0) b, COALESCE(SUM(refugo),0) r FROM apontamentos
            WHERE operador='1025' AND DATE(data_hora_inicio)=DATE('now') AND qtd_boas IS NOT NULL""").fetchone()
        html = op_s.get(f"{BASE_URL}/operador").text
        m1 = re.search(r'data-testid="meu-boas-hoje".*?</div>\s*</div>', html, re.S)
        m2 = re.search(r'data-testid="meu-refugo-hoje".*?</div>\s*</div>', html, re.S)
        assert m1 and m2
        assert str(row['b']) in re.sub('<[^>]+>', ' ', m1.group(0))
        assert str(row['r']) in re.sub('<[^>]+>', ' ', m2.group(0))


# --- Supervisor
class TestSupervisor:
    def test_nav_full(self, sup_s):
        nav = nav_html(sup_s.get(f"{BASE_URL}/operador").text)
        assert 'nav-pcp' in nav and '/supervisor/fila' in nav and '/supervisor/gerenciar' in nav

    @pytest.mark.parametrize('path', ['/pcp', '/operador', '/manual', '/supervisor', '/supervisor/fila',
                                      '/supervisor/relatorios', '/supervisor/gerenciar', '/supervisor/saude',
                                      '/supervisor/historico', '/pcp/acompanhar/OP-002'])
    def test_access(self, sup_s, path):
        r = sup_s.get(f"{BASE_URL}{path}", allow_redirects=False)
        assert r.status_code == 200, f"{path} -> {r.status_code}"

    @pytest.mark.parametrize('kind', ['oee', 'pareto', 'aging', 'produtividade'])
    @pytest.mark.parametrize('fmt', ['xlsx', 'pdf'])
    def test_exports(self, sup_s, kind, fmt):
        r = sup_s.get(f"{BASE_URL}/supervisor/export/{kind}/{fmt}", allow_redirects=False)
        assert r.status_code == 200 and len(r.content) > 100

    def test_relatorios_chartjs(self, sup_s):
        assert 'chart' in sup_s.get(f"{BASE_URL}/supervisor/relatorios").text.lower()


# --- PCP role
class TestPCPRole:
    def test_operador_redirects_pcp(self, pcp_s):
        r = pcp_s.get(f"{BASE_URL}/operador", allow_redirects=False)
        assert r.status_code == 302 and r.headers['Location'].endswith('/pcp')

    def test_nav(self, pcp_s):
        nav = nav_html(pcp_s.get(f"{BASE_URL}/pcp").text)
        assert 'nav-pcp' in nav and 'href="/operador"' not in nav and '/supervisor' not in nav

    @pytest.mark.parametrize('path', ['/pcp', '/pcp/acompanhar/OP-002', '/manual'])
    def test_allowed(self, pcp_s, path):
        assert pcp_s.get(f"{BASE_URL}{path}", allow_redirects=False).status_code == 200

    @pytest.mark.parametrize('path', ['/supervisor', '/supervisor/fila', '/supervisor/relatorios', '/relatorio/csv/hoje'])
    def test_blocked(self, pcp_s, path):
        r = pcp_s.get(f"{BASE_URL}{path}", allow_redirects=False)
        assert r.status_code == 302


# --- PCP tela / acompanhamento
class TestPCPScreens:
    def test_pcp_table(self, sup_s):
        c = db()
        ops = [r['num_op'] for r in c.execute("SELECT num_op FROM ordens_producao WHERE status_geral != 'Concluída'")]
        html = sup_s.get(f"{BASE_URL}/pcp").text
        for op in ops:
            assert f'data-testid="pcp-previsao-{op}"' in html
            assert f'data-testid="pcp-acompanhar-{op}"' in html
            etapas = [r[0] for r in c.execute("SELECT numero_etapa FROM op_etapas_execucao WHERE num_op=?", (op,))]
            for e in etapas:
                assert f'data-testid="pcp-fase-chip-{op}-{e}"' in html

    def test_acompanhar_metrics(self, sup_s, any_op):
        c = db()
        op = c.execute("SELECT * FROM ordens_producao WHERE num_op=?", (any_op,)).fetchone()
        et = c.execute("SELECT * FROM op_etapas_execucao WHERE num_op=?", (any_op,)).fetchall()
        trabalho = (op['meta_total'] or 0) * len(et)
        saldo = sum(e['saldo_residual'] or 0 for e in et)
        exp = round(max(0, min(100, (trabalho - saldo) / trabalho * 100)), 1) if trabalho else 0
        html = sup_s.get(f"{BASE_URL}/pcp/acompanhar/{any_op}").text
        assert f'{exp}%' in html
        for tid in ['acompanhar-previsao', 'acompanhar-historico', 'acompanhar-voltar-btn']:
            assert f'data-testid="{tid}"' in html
        for e in et:
            assert f'data-testid="fase-{e["numero_etapa"]}"' in html
        if 'fase-parada-' in html:
            assert 'data-testid="acompanhar-alerta-parada"' in html

    def test_acompanhar_previsao_formula(self, sup_s, any_op):
        c = db()
        op = c.execute("SELECT * FROM ordens_producao WHERE num_op=?", (any_op,)).fetchone()
        rows = c.execute("""SELECT e.saldo_residual s, e.status_execucao st, r.tempo_ciclo_minutos t FROM op_etapas_execucao e
            LEFT JOIN roteiros_padrao r ON r.numero_desenho=? AND r.numero_etapa=e.numero_etapa WHERE e.num_op=?""",
                         (op['numero_desenho'], any_op)).fetchall()
        minutos = sum((r['s'] or 0) * (r['t'] or 0) for r in rows if r['st'] != 'Concluída')
        html = sup_s.get(f"{BASE_URL}/pcp/acompanhar/{any_op}").text
        assert f"{round(minutos/60, 1)}h máq." in html

    def test_acompanhar_not_found(self, sup_s):
        r = sup_s.get(f"{BASE_URL}/pcp/acompanhar/NAOEXISTE", allow_redirects=False)
        assert r.status_code == 302 and r.headers['Location'].endswith('/pcp')
