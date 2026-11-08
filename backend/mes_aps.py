# ============================================
# 🏭 MES/APS - VERSÃO COMPLETA
# Melhorias 1 a 7 implementadas!
# ============================================
# ✅ 1. Progresso Negativo Corrigido
# ✅ 2. Persistência de Dados
# ✅ 3. Backup Automático
# ✅ 4. Menu Burger no Supervisor
# ✅ 5. Gerenciamento de Roteiros
# ✅ 6. Impressão da Ordem de Produção
# ✅ 7. Consulta de OP com Apontamentos
# ============================================

import sqlite3
import os
import secrets
import hashlib
import csv
import io
import shutil
import json
import bcrypt
import time
import requests
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify, send_file

# --- CONFIGURAÇÕES ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "producao.db"))
BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(BASE_DIR, "backups"))
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
DEBUG = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
MAX_BACKUPS = int(os.environ.get("MAX_BACKUPS", "30"))

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ============================================
# FUNÇÕES DE SEGURANÇA
# ============================================

def gerar_hash_senha(senha):
    """Gera hash bcrypt (novo padrão)."""
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

def _is_bcrypt(h):
    return isinstance(h, str) and h.startswith(("$2a$", "$2b$", "$2y$"))

def verificar_senha(senha_digitada, senha_hash):
    """Verifica bcrypt; se hash for SHA256 legado, valida e devolve flag para upgrade."""
    if not senha_hash:
        return False
    if _is_bcrypt(senha_hash):
        try:
            return bcrypt.checkpw(senha_digitada.encode(), senha_hash.encode())
        except Exception:
            return False
    # legado (SHA256 hex)
    return hashlib.sha256(senha_digitada.encode()).hexdigest() == senha_hash

def upgrade_senha_se_legado(matricula, senha_digitada, senha_hash_atual):
    """Se senha estiver em SHA256 e for validada, converte para bcrypt."""
    if not _is_bcrypt(senha_hash_atual):
        novo_hash = gerar_hash_senha(senha_digitada)
        query("UPDATE operadores SET senha = ? WHERE matricula = ?",
              (novo_hash, matricula), commit=True)

# Dono do app: entra como supervisor automaticamente no 1º login Google
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "martinsdesiqueiraigor@gmail.com")
EMERGENT_AUTH_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('matricula'):
            flash('Por favor, faça login primeiro.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def load_session_from_cookie():
    """Fallback global: restaura sessão Flask a partir do cookie session_token (Google Sign-In)."""
    if session.get('matricula'):
        return
    tok = request.cookies.get('session_token')
    if not tok:
        return
    r = query("SELECT * FROM user_sessions WHERE session_token = ?", (tok,), one=True)
    if not r:
        return
    try:
        exp = datetime.fromisoformat(r['expires_at'])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return
        session['matricula'] = r['matricula']
        op = query("SELECT nome, turno, papel FROM operadores WHERE matricula = ?", (r['matricula'],), one=True)
        if op:
            session['nome'] = op['nome']
            session['turno'] = op['turno']
            session['papel'] = op['papel'] or 'operador'
    except Exception:
        pass

AREAS_SUPERVISOR = ('/pcp', '/supervisor', '/relatorio')


@app.before_request
def exigir_supervisor():
    if not request.path.startswith(AREAS_SUPERVISOR) or not session.get('matricula'):
        return
    op = query("SELECT papel, ativo FROM operadores WHERE matricula = ?", (session['matricula'],), one=True)
    papel = (op['papel'] if op else None) or 'operador'
    session['papel'] = papel
    if papel == 'supervisor' or (papel == 'pcp' and request.path.startswith('/pcp')):
        return
    log_audit('acesso_negado', 'rota', request.path, f"{session['matricula']} tentou {request.method}")
    if request.method != 'GET':
        return ('Acesso restrito ao supervisor', 403)
    flash('Acesso restrito ao supervisor', 'danger')
    return redirect(url_for('operador'))

# ============================================
# FUNÇÕES DE BACKUP
# ============================================

def criar_backup():
    """Cria um backup do banco de dados"""
    if not os.path.exists(DB_NAME):
        return
    
    # Criar diretório de backups se não existir
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    
    # Nome do backup com timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = os.path.join(BACKUP_DIR, f"producao_{timestamp}.db")
    
    # Copiar arquivo
    shutil.copy2(DB_NAME, backup_file)
    print(f"📦 Backup criado: {backup_file}")
    
    # Limpar backups antigos (manter apenas os últimos MAX_BACKUPS)
    backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
    if len(backups) > MAX_BACKUPS:
        for old_file in backups[:-MAX_BACKUPS]:
            os.remove(os.path.join(BACKUP_DIR, old_file))
            print(f"🗑️ Backup antigo removido: {old_file}")

def listar_backups():
    """Lista todos os backups disponíveis"""
    if not os.path.exists(BACKUP_DIR):
        return []
    backups = [f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')]
    backups.sort(reverse=True)
    return backups

def restaurar_backup(nome_arquivo):
    """Restaura um backup"""
    backup_path = os.path.join(BACKUP_DIR, nome_arquivo)
    if not os.path.exists(backup_path):
        return False
    
    # Fazer backup do atual antes de restaurar
    criar_backup()
    
    # Restaurar
    shutil.copy2(backup_path, DB_NAME)
    return True

# ============================================
# FUNÇÕES DE BANCO DE DADOS
# ============================================

def query(sql, params=(), commit=False, one=False):
    try:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            cursor = conn.cursor()
            cursor.execute(sql, params)
            if commit:
                conn.commit()
                return cursor.lastrowid
            result = cursor.fetchall()
            return result[0] if one and result else result
    except Exception as e:
        print(f"Erro: {e}")
        return None

def log_audit(acao, entidade, entidade_id="", detalhes=""):
    """Registra ação no log de auditoria."""
    try:
        query("""INSERT INTO audit_log (usuario, acao, entidade, entidade_id, detalhes)
                 VALUES (?, ?, ?, ?, ?)""",
              (session.get('matricula', 'system'), acao, entidade, str(entidade_id), detalhes),
              commit=True)
    except Exception as e:
        print(f"[audit] erro: {e}")

# ============================================
# INICIALIZAÇÃO DO BANCO (COM PERSISTÊNCIA)
# ============================================

def _ensure_upgrades():
    """Aplica upgrades idempotentes ao banco: WAL + auditoria + Fase 2/3/4."""
    try:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT, acao TEXT, entidade TEXT, entidade_id TEXT,
                detalhes TEXT, data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS configuracoes (
                chave TEXT PRIMARY KEY, valor TEXT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                matricula TEXT,
                session_token TEXT UNIQUE,
                expires_at TEXT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS google_vinculos (
                email TEXT PRIMARY KEY,
                matricula TEXT,
                vinculado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS op_anexos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                num_op TEXT,
                nome_arquivo TEXT,
                storage_path TEXT,
                content_type TEXT,
                enviado_por TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            # defaults
            for k, v in [("shift_end_time", "22:00"),
                         ("min_stoppage_comment_min", "15"),
                         ("oee_target_default", "0.75"),
                         ("backup_retention_days", "30")]:
                conn.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES (?, ?)", (k, v))
            # colunas novas em apontamentos
            for coldef in [
                ("justificativa_sucata", "TEXT"),
                ("status_aprovacao", "TEXT DEFAULT 'ok'"),
                ("auto_fechado", "INTEGER DEFAULT 0"),
                ("aprovador", "TEXT"),
                ("data_hora_aprovacao", "TIMESTAMP"),
                ("nota_aprovacao", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE apontamentos ADD COLUMN {coldef[0]} {coldef[1]}")
                except sqlite3.OperationalError:
                    pass
            # coluna nova em operadores (papel)
            try:
                conn.execute("ALTER TABLE operadores ADD COLUMN papel TEXT DEFAULT 'operador'")
            except sqlite3.OperationalError:
                pass
            # comentario em paradas
            try:
                conn.execute("ALTER TABLE paradas_maquina ADD COLUMN comentario TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()
    except Exception as e:
        print(f"[upgrade] {e}")

def get_config(chave, default=None):
    r = query("SELECT valor FROM configuracoes WHERE chave = ?", (chave,), one=True)
    return r['valor'] if r else default

def set_config(chave, valor):
    query("INSERT INTO configuracoes (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
          (chave, str(valor)), commit=True)

def init_db():
    """Inicializa o banco de dados apenas se não existir"""
    
    # Sempre garante tabelas novas (WAL, audit_log) mesmo em DB existente
    _ensure_upgrades()

    # Verificar se o banco já existe (dados de teste apenas na criação inicial)
    if os.path.exists(DB_NAME):
        print("📂 Banco de dados existente encontrado. Carregando dados...")
        return
    
    print("🚀 Criando banco de dados pela primeira vez...")
    
    # Criar diretório de backups
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    
    # Criar tabelas
    query("""CREATE TABLE IF NOT EXISTS operadores (
        matricula TEXT PRIMARY KEY,
        nome TEXT,
        turno TEXT,
        senha TEXT,
        ativo INTEGER DEFAULT 1
    )""", commit=True)
    
    query("""CREATE TABLE IF NOT EXISTS maquinas (
        codigo_maquina TEXT PRIMARY KEY,
        descricao TEXT,
        status_operacional TEXT DEFAULT 'Ativa'
    )""", commit=True)
    
    query("""CREATE TABLE IF NOT EXISTS desenhos (
        numero_desenho TEXT PRIMARY KEY,
        nome_peca TEXT
    )""", commit=True)
    
    query("""CREATE TABLE IF NOT EXISTS roteiros_padrao (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_desenho TEXT,
        numero_etapa INTEGER,
        descricao_etapa TEXT,
        codigo_maquina_padrao TEXT,
        tempo_ciclo_minutos REAL,
        FOREIGN KEY(numero_desenho) REFERENCES desenhos(numero_desenho)
    )""", commit=True)
    
    query("""CREATE TABLE IF NOT EXISTS ordens_producao (
        num_op TEXT PRIMARY KEY,
        numero_desenho TEXT,
        meta_total INTEGER,
        status_geral TEXT DEFAULT 'Planejada',
        data_criacao DATE DEFAULT CURRENT_DATE
    )""", commit=True)
    
    query("""CREATE TABLE IF NOT EXISTS op_etapas_execucao (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        num_op TEXT,
        numero_etapa INTEGER,
        descricao_etapa TEXT,
        saldo_residual INTEGER,
        status_execucao TEXT DEFAULT 'Pendente',
        FOREIGN KEY(num_op) REFERENCES ordens_producao(num_op)
    )""", commit=True)
    
    query("""CREATE TABLE IF NOT EXISTS apontamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        num_op TEXT,
        numero_etapa INTEGER,
        operador TEXT,
        codigo_maquina TEXT,
        qtd_boas INTEGER,
        refugo INTEGER DEFAULT 0,
        data_hora_inicio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        data_hora_fim TIMESTAMP,
        status TEXT DEFAULT 'Ativo',
        FOREIGN KEY(num_op) REFERENCES ordens_producao(num_op),
        FOREIGN KEY(operador) REFERENCES operadores(matricula)
    )""", commit=True)
    
    query("""CREATE TABLE IF NOT EXISTS paradas_maquina (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operador TEXT,
        motivo TEXT,
        status TEXT DEFAULT 'Ativa',
        data_hora_inicio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        data_hora_fim TIMESTAMP,
        FOREIGN KEY(operador) REFERENCES operadores(matricula)
    )""", commit=True)
    
    print("📦 Carregando dados de teste (apenas uma vez)...")
    
    senha_hash = gerar_hash_senha("123456")
    
    # Operadores
    operadores = [
        ('1024', 'Carlos Silva', 'Turno A', senha_hash, 1),
        ('1025', 'Maria Santos', 'Turno A', senha_hash, 1),
        ('1026', 'João Pereira', 'Turno B', senha_hash, 1),
    ]
    for mat, nome, turno, senha, ativo in operadores:
        query("INSERT OR IGNORE INTO operadores VALUES (?, ?, ?, ?, ?)", 
              (mat, nome, turno, senha, ativo), commit=True)
    
    # Máquinas
    maquinas = [
        ('SR-01', 'Serra Fita', 'Ativa'),
        ('TR-01', 'Torno Mecânico', 'Ativa'),
        ('AC-01', 'Acabamento', 'Ativa'),
        ('CNC-01', 'Centro de Usinagem', 'Ativa'),
        ('SOL-01', 'Solda Robotizada', 'Ativa'),
    ]
    for cod, desc, status in maquinas:
        query("INSERT OR IGNORE INTO maquinas VALUES (?, ?, ?)", 
              (cod, desc, status), commit=True)
    
    # Desenhos
    desenhos = [
        ('001', 'Eixo Motor'),
        ('002', 'Engrenagem'),
        ('003', 'Carcaça Alumínio'),
        ('004', 'Suporte'),
        ('005', 'Parafuso'),
    ]
    for num, nome in desenhos:
        query("INSERT OR IGNORE INTO desenhos VALUES (?, ?)", 
              (num, nome), commit=True)
    
    # Roteiros
    roteiros = [
        ('001', 10, 'Corte', 'SR-01', 1.5),
        ('001', 20, 'Torneamento', 'TR-01', 4.0),
        ('001', 30, 'Acabamento', 'AC-01', 2.5),
        ('002', 10, 'Corte', 'SR-01', 2.0),
        ('002', 20, 'Usinagem CNC', 'CNC-01', 3.5),
        ('003', 10, 'Corte', 'SR-01', 1.0),
        ('003', 20, 'Solda', 'SOL-01', 5.0),
        ('004', 10, 'Corte', 'SR-01', 1.5),
        ('004', 20, 'Acabamento', 'AC-01', 2.0),
        ('005', 10, 'Corte', 'SR-01', 0.5),
    ]
    for ds, et, desc, maq, tempo in roteiros:
        query("""INSERT OR IGNORE INTO roteiros_padrao 
            (numero_desenho, numero_etapa, descricao_etapa, codigo_maquina_padrao, tempo_ciclo_minutos)
            VALUES (?, ?, ?, ?, ?)""", (ds, et, desc, maq, tempo), commit=True)
    
    # Ordens de Produção
    ops = [
        ('OP-001', '001', 100, 'Em Andamento', '2026-08-27'),
        ('OP-002', '002', 50, 'Em Andamento', '2026-08-27'),
        ('OP-003', '003', 200, 'Em Andamento', '2026-08-27'),
    ]
    for op, ds, meta, status, data in ops:
        query("""INSERT OR IGNORE INTO ordens_producao 
            (num_op, numero_desenho, meta_total, status_geral, data_criacao)
            VALUES (?, ?, ?, ?, ?)""", (op, ds, meta, status, data), commit=True)
    
    # Etapas
    etapas = [
        ('OP-001', 10, 'Corte', 100, 'Pendente'),
        ('OP-001', 20, 'Torneamento', 100, 'Pendente'),
        ('OP-001', 30, 'Acabamento', 100, 'Pendente'),
        ('OP-002', 10, 'Corte', 50, 'Pendente'),
        ('OP-002', 20, 'Usinagem CNC', 50, 'Pendente'),
        ('OP-003', 10, 'Corte', 200, 'Pendente'),
        ('OP-003', 20, 'Solda', 200, 'Pendente'),
    ]
    for op, et, desc, saldo, status in etapas:
        query("""INSERT OR IGNORE INTO op_etapas_execucao 
            (num_op, numero_etapa, descricao_etapa, saldo_residual, status_execucao)
            VALUES (?, ?, ?, ?, ?)""", (op, et, desc, saldo, status), commit=True)
    
    # Criar backup inicial
    criar_backup()
    
    print("✅ Banco de dados inicializado com sucesso!")
    print("👤 Usuários: 1024, 1025, 1026")
    print("🔑 Senha: 123456")

# ============================================
# TEMPLATES (VERSÃO COMPLETA ATUALIZADA)
# ============================================

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>🏭 MES/APS</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            background: #0a0e17; 
            color: #e0e0e0; 
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            padding: 10px;
            min-height: 100vh;
        }
        .container { max-width: 480px; margin: 0 auto; }
        
        .header {
            text-align: center;
            padding: 15px 0;
            border-bottom: 2px solid #2196F3;
            margin-bottom: 15px;
        }
        .header h2 { color: #fff; font-size: 20px; }
        .header .sub { color: #78909c; font-size: 12px; }
        
        .nav-tabs {
            display: flex;
            background: #111927;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 15px;
            border: 1px solid #1a2a3a;
        }
        .nav-tabs a {
            flex: 1;
            text-align: center;
            padding: 10px 5px;
            text-decoration: none;
            color: #78909c;
            font-size: 11px;
            font-weight: bold;
        }
        .nav-tabs a.active {
            background: #1a3a4a;
            color: #fff;
        }
        
        .user-bar {
            background: #1a237e;
            padding: 10px 15px;
            border-radius: 10px;
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }
        .user-bar .name { font-weight: bold; color: #fff; }
        .user-bar .matricula { color: #90caf9; font-size: 12px; }
        .user-bar .turno { color: #81c784; font-size: 12px; margin-left: 8px; }
        .btn-logout {
            background: #c62828;
            color: #fff;
            border: none;
            padding: 5px 15px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 12px;
        }
        
        .card {
            background: #111927;
            padding: 15px;
            border-radius: 10px;
            border: 1px solid #1a2a3a;
            margin-bottom: 15px;
        }
        .card h3 { 
            color: #4fc3f7; 
            font-size: 15px; 
            margin-bottom: 12px;
        }
        .card h4 {
            color: #78909c;
            font-size: 13px;
            margin: 10px 0 8px 0;
        }
        
        .form-group { margin-bottom: 12px; }
        .form-group label {
            display: block;
            font-size: 12px;
            color: #90caf9;
            font-weight: bold;
            margin-bottom: 4px;
        }
        .form-group input,
        .form-group select {
            width: 100%;
            padding: 10px;
            border-radius: 6px;
            border: 1px solid #1a2a3a;
            background: #0d1b2a;
            color: #e0e0e0;
            font-size: 14px;
        }
        .form-group input:focus,
        .form-group select:focus {
            outline: none;
            border-color: #2196F3;
        }
        
        .btn {
            padding: 12px;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: bold;
            cursor: pointer;
            width: 100%;
        }
        .btn-success { background: #1b5e20; color: #fff; }
        .btn-danger { background: #c62828; color: #fff; }
        .btn-primary { background: #1a3a4a; color: #fff; }
        .btn-warning { background: #e65100; color: #fff; }
        .btn-info { background: #01579b; color: #fff; }
        .btn-secondary { background: #1e3a5f; color: #e3f2fd; text-decoration: none; display: inline-block; border: 1px solid #2c5282; }
        .btn-sm { padding: 6px 12px; font-size: 12px; width: auto; }
        .btn-xs { padding: 3px 8px; font-size: 10px; width: auto; }
        
        .table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .table th {
            background: #0d1b2a;
            color: #78909c;
            padding: 8px;
            text-align: left;
            border-bottom: 1px solid #1a2a3a;
        }
        .table td { padding: 8px; border-bottom: 1px solid #1a2a3a; }
        .table tr:hover { background: #0d1b2a; }
        
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: bold;
        }
        .badge-success { background: #1b5e20; color: #81c784; }
        .badge-danger { background: #b71c1c; color: #ef9a9a; }
        .badge-warning { background: #e65100; color: #ffcc80; }
        .badge-info { background: #01579b; color: #81d4fa; }
        
        .alert {
            padding: 10px 15px;
            border-radius: 6px;
            margin-bottom: 12px;
            font-size: 13px;
        }
        .alert-success { background: #1b5e20; color: #81c784; }
        .alert-danger { background: #b71c1c; color: #ef9a9a; }
        .alert-warning { background: #e65100; color: #ffcc80; }
        .alert-info { background: #01579b; color: #81d4fa; }
        
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
        .flex { display: flex; gap: 10px; flex-wrap: wrap; }
        .flex-1 { flex: 1; }
        .text-muted { color: #78909c; font-size: 12px; }
        .mt-10 { margin-top: 10px; }
        .mb-10 { margin-bottom: 10px; }
        
        .metric-card {
            background: #0d1b2a;
            padding: 12px;
            border-radius: 8px;
            text-align: center;
            border: 1px solid #1a2a3a;
        }
        .metric-card .value { font-size: 24px; font-weight: bold; color: #4fc3f7; }
        .metric-card .label { font-size: 11px; color: #78909c; }
        
        .progress-bar {
            background: #0d1b2a;
            border-radius: 4px;
            height: 6px;
            width: 100%;
            overflow: hidden;
        }
        .progress-bar .fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.5s ease;
            background: #4fc3f7;
        }
        
        /* Menu Burger */
        .burger-menu {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
            animation: fadeIn 0.3s ease;
        }
        .burger-menu.active { display: block; }
        .burger-content {
            background: #111927;
            width: 280px;
            height: 100%;
            padding: 20px;
            overflow-y: auto;
            animation: slideIn 0.3s ease;
        }
        .burger-content .close-btn {
            background: none;
            border: none;
            color: #78909c;
            font-size: 24px;
            cursor: pointer;
            float: right;
        }
        .burger-content h3 {
            color: #4fc3f7;
            font-size: 16px;
            margin: 15px 0 10px 0;
            border-bottom: 1px solid #1a2a3a;
            padding-bottom: 5px;
        }
        .burger-content a {
            display: block;
            padding: 10px 12px;
            color: #e0e0e0;
            text-decoration: none;
            border-radius: 6px;
            margin-bottom: 2px;
            font-size: 13px;
        }
        .burger-content a:hover {
            background: #1a2a3a;
        }
        .burger-content a .icon { margin-right: 10px; }
        
        .burger-toggle {
            background: none;
            border: none;
            color: #fff;
            font-size: 24px;
            cursor: pointer;
            padding: 5px;
        }
        
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes slideIn { from { transform: translateX(-100%); } to { transform: translateX(0); } }
        
        /* Impressão */
        @media print {
            body { background: #fff; color: #000; padding: 0; }
            .container { max-width: 100%; }
            .header { border-color: #000; }
            .header h2 { color: #000; }
            .nav-tabs, .user-bar, .btn, .no-print { display: none !important; }
            .card { border: 1px solid #000; background: #fff; }
            .card h3 { color: #000; }
            .table th { background: #eee; color: #000; }
            .table td { color: #000; }
            .badge { border: 1px solid #000; }
        }
        
        @media (max-width: 400px) {
            .grid-2, .grid-3 { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <h2>🏭 MES / APS</h2>
                    <div class="sub">Sistema de Gestão da Produção</div>
                </div>
                {% if aba == 'supervisor' %}
                <button class="burger-toggle" onclick="toggleMenu()">☰</button>
                {% endif %}
            </div>
        </div>
        
        {% if session.get('matricula') %}
        <div class="user-bar">
            <div>
                <span class="name">👤 {{ session.get('nome', 'Operador') }}</span>
                <span class="matricula">#{{ session.get('matricula', '') }}</span>
                <span class="turno">{{ session.get('turno', '') }}</span>
            </div>
            <a href="/logout" class="btn-logout">🚪 Sair</a>
        </div>
        {% endif %}
        
        {% if session.get('papel') == 'supervisor' %}
        <!-- Menu Burger -->
        <div class="burger-menu" id="burgerMenu">
            <div class="burger-content">
                <button class="close-btn" onclick="toggleMenu()">✕</button>
                <h2 style="color:#fff;margin-bottom:15px;">🏭 MES/APS</h2>
                
                <h3>📦 Cadastros</h3>
                <a href="/supervisor#operador"><span class="icon">👤</span> Operador</a>
                <a href="/supervisor#maquina"><span class="icon">🏭</span> Máquina</a>
                <a href="/supervisor#desenho"><span class="icon">📐</span> Desenho</a>
                <a href="/supervisor#roteiro"><span class="icon">⚙️</span> Roteiro</a>
                
                <h3>📊 Consultas</h3>
                <a href="/supervisor/consulta"><span class="icon">🔍</span> Consultar OP</a>
                <a href="/supervisor#operadores"><span class="icon">👥</span> Operadores</a>
                <a href="/supervisor#maquinas"><span class="icon">🏭</span> Máquinas</a>
                <a href="/supervisor#desenhos"><span class="icon">📐</span> Desenhos</a>
                
                <h3>📈 Relatórios</h3>
                <a href="/supervisor#relatorios"><span class="icon">📄</span> Exportar CSV</a>
                <a href="/supervisor#relatorios"><span class="icon">📊</span> Produtividade</a>
                
                <h3>💾 Sistema</h3>
                <a href="/supervisor/backup"><span class="icon">💾</span> Gerenciar Backups</a>
                
                <hr style="border-color:#1a2a3a;margin:15px 0;">
                <a href="/operador" style="color:#4fc3f7;">⬅️ Voltar ao Operador</a>
                <a href="/logout" style="color:#ef9a9a;">🚪 Sair</a>
            </div>
        </div>
        {% endif %}
        
        <div class="nav-tabs">
            {% if session.get('papel') != 'pcp' %}<a href="/operador" class="{% if aba == 'operador' %}active{% endif %}">🧑‍🔧 OP</a>{% endif %}
            {% if session.get('papel') in ('supervisor', 'pcp') %}<a href="/pcp" class="{% if aba == 'pcp' %}active{% endif %}" data-testid="nav-pcp">📋 PCP</a>{% endif %}
            {% if session.get('papel') == 'supervisor' %}
            <a href="/supervisor" class="{% if aba == 'supervisor' %}active{% endif %}">👮 SUP</a>
            <a href="/supervisor/fila" class="{% if aba == 'fila' %}active{% endif %}" data-testid="nav-fila">✅ Fila <span id="badge-fila" data-testid="badge-fila" style="display:none;background:#ef4444;color:#fff;border-radius:999px;padding:1px 7px;font-size:10px;font-weight:bold;animation:softPulse 1.2s ease-in-out infinite;"></span></a>
            <a href="/supervisor/relatorios" class="{% if aba == 'rel' %}active{% endif %}">📊 Relat.</a>
            <a href="/supervisor/saude" class="{% if aba == 'saude' %}active{% endif %}">❤️ Saúde</a>
            <a href="/supervisor/gerenciar" class="{% if aba == 'ger' %}active{% endif %}">⚙️ Gerenciar</a>
            <a href="/supervisor/historico" class="{% if aba == 'hist' %}active{% endif %}">📚 Histórico</a>
            {% endif %}
            <a href="/manual" class="{% if aba == 'manual' %}active{% endif %}">📖 Manual</a>
        </div>
        <style>@keyframes softPulse{0%,100%{opacity:1}50%{opacity:.45}}</style>
        
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        {{ conteudo|safe }}
    </div>
    
    <script>
        function toggleMenu() {
            document.getElementById('burgerMenu').classList.toggle('active');
        }
        
        // Fechar menu ao clicar fora
        document.addEventListener('click', function(e) {
            const menu = document.getElementById('burgerMenu');
            if (menu && menu.classList.contains('active') && !e.target.closest('.burger-content') && !e.target.closest('.burger-toggle')) {
                menu.classList.remove('active');
            }
        });

        // Cronômetro visível (Fase 2)
        function updateChronos() {
            document.querySelectorAll('.crono').forEach(function(el){
                var s = el.dataset.start;
                if(!s) return;
                var iso = s.replace(' ', 'T') + 'Z';
                var t0 = new Date(iso).getTime();
                if(isNaN(t0)) return;
                var diff = Math.max(0, Math.floor((Date.now() - t0)/1000));
                var h = String(Math.floor(diff/3600)).padStart(2,'0');
                var m = String(Math.floor((diff%3600)/60)).padStart(2,'0');
                var sec = String(diff%60).padStart(2,'0');
                el.textContent = h+':'+m+':'+sec;
            });
        }
        setInterval(updateChronos, 1000);
        updateChronos();

        // Badge de pendências em tempo real (poll 30s) + alerta sonoro p/ supervisor
        var isSup = {{ 'true' if (session.get('papel') == 'supervisor') else 'false' }};
        var lastPend = null;
        function beepPendencia(){
            try{
                var ctx = new (window.AudioContext || window.webkitAudioContext)();
                var o = ctx.createOscillator(), g = ctx.createGain();
                o.connect(g); g.connect(ctx.destination);
                o.type = 'sine'; o.frequency.value = 880;
                g.gain.setValueAtTime(0.15, ctx.currentTime);
                g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.6);
                o.start(); o.stop(ctx.currentTime + 0.6);
            }catch(e){}
        }
        function updateBadgeFila(){
            fetch('/pendencias/count').then(function(r){return r.json()})
            .then(function(j){
                var b=document.getElementById('badge-fila');
                if(b){
                    if(j.count>0){b.textContent=j.count;b.style.display='inline-block';}
                    else{b.style.display='none';}
                }
                // Toca apenas quando surge pendência NOVA e o usuário é supervisor
                if(isSup && lastPend !== null && j.count > lastPend){ beepPendencia(); }
                lastPend = j.count;
            }).catch(function(){});
        }
        setInterval(updateBadgeFila, 30000);
        updateBadgeFila();
    </script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</body>
</html>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<div class="card" style="max-width: 350px; margin: 30px auto;">
    <h3 style="text-align:center;">🔐 Login</h3>
    <p style="text-align:center; color:#78909c; font-size:12px; margin-bottom:15px;">
        Digite sua matrícula e senha
    </p>
    <form method="POST">
        <div class="form-group">
            <label>Matrícula</label>
            <input type="text" inputmode="numeric" name="matricula" placeholder="Ex: 1024" required>
        </div>
        <div class="form-group">
            <label>Senha</label>
            <input type="password" name="senha" placeholder="Digite sua senha" required>
        </div>
        <button type="submit" class="btn btn-primary">🚀 Entrar</button>
    </form>
    <div style="margin:15px 0; text-align:center; color:#455a64; font-size:11px;">— ou —</div>
    <!-- REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH -->
    <button type="button" class="btn btn-secondary" style="width:100%;" data-testid="btn-google-login"
            onclick="window.location.href='https://auth.emergentagent.com/?redirect='+encodeURIComponent(window.location.origin+'/auth/google/landing')">
        🔵 Entrar com Google
    </button>
</div>
"""

OPERADOR_TEMPLATE = """
{% if not session.get('matricula') %}
    {{ login|safe }}
{% else %}
    <div class="grid-3">
        <div class="metric-card">
            <div class="value">{{ ativas|length }}</div>
            <div class="label">🔄 Ativas</div>
        </div>
        <div class="metric-card" data-testid="meu-boas-hoje">
            <div class="value" style="color:#81c784;">{{ meu.boas }}</div>
            <div class="label">✅ Peças boas hoje</div>
        </div>
        <div class="metric-card" data-testid="meu-refugo-hoje">
            <div class="value" style="color:{{ '#ef9a9a' if meu.refugo else '#ffcc80' }};">{{ meu.refugo }}</div>
            <div class="label">♻️ Refugo hoje · {{ meu.qualidade }}% qualidade</div>
        </div>
    </div>
    
    <div class="card">
        <h3>▶️ Iniciar Produção</h3>
        <form method="POST" action="/operador/iniciar">
            <div class="form-group">
                <label>OP e Etapa</label>
                <select name="op_etapa" required>
                    {% for op in ops_disponiveis %}
                    <option value="{{ op.num_op }}|{{ op.numero_etapa }}">
                        {{ op.num_op }} - Etapa {{ op.numero_etapa }} ({{ op.descricao_etapa }})
                        Saldo: {{ op.saldo_residual }}
                    </option>
                    {% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label>Máquina</label>
                <select name="codigo_maquina" required>
                    {% for maq in maquinas_disponiveis %}
                    <option value="{{ maq.codigo_maquina }}">
                        {{ maq.codigo_maquina }} - {{ maq.descricao }}
                    </option>
                    {% endfor %}
                </select>
            </div>
            <button type="submit" class="btn btn-success">▶️ Iniciar</button>
        </form>
    </div>
    
    <div class="card">
        <h3>📎 Desenho da OP</h3>
        {% if anexos %}
        <div class="form-group">
            <label>Desenho técnico (PDF/imagem)</label>
            <select id="sel-anexo" data-testid="select-anexo-op" onchange="updAnexo()">
                {% for a in anexos %}
                <option value="/anexo/{{ a.num_op }}" data-img="{{ '1' if (a.content_type or '').startswith('image/') else '' }}">OP {{ a.num_op }} — {{ a.nome_arquivo }}</option>
                {% endfor %}
            </select>
        </div>
        <div id="anexo-preview" style="text-align:center;margin-bottom:10px;">
            <img id="anexo-img" src="/anexo/{{ anexos[0].num_op }}" alt="Prévia do desenho" data-testid="img-preview-desenho"
                 style="max-width:100%;max-height:280px;border-radius:8px;border:1px solid #1a2a3a;display:{{ 'block' if (anexos[0].content_type or '').startswith('image/') else 'none' }};">
            <div id="anexo-pdf" style="display:{{ 'none' if (anexos[0].content_type or '').startswith('image/') else 'block' }};padding:24px;background:#0d1b2a;border-radius:8px;border:1px solid #1a2a3a;">
                <span style="font-size:36px;">📄</span>
                <div style="font-size:11px;color:#78909c;margin-top:6px;">Documento PDF — use o botão abaixo para abrir</div>
            </div>
        </div>
        <a class="btn btn-primary touch-btn" id="btn-ver-anexo" data-testid="btn-ver-anexo" href="/anexo/{{ anexos[0].num_op }}" target="_blank">👁️ Abrir em nova aba / ampliar</a>
        <script>
        function updAnexo(){
            var sel = document.getElementById('sel-anexo');
            if(!sel || !sel.options[sel.selectedIndex]) return;
            var opt = sel.options[sel.selectedIndex];
            var isImg = opt.dataset.img === '1';
            var img = document.getElementById('anexo-img');
            var pdf = document.getElementById('anexo-pdf');
            img.style.display = isImg ? 'block' : 'none';
            pdf.style.display = isImg ? 'none' : 'block';
            if(isImg) img.src = sel.value;
            document.getElementById('btn-ver-anexo').href = sel.value;
        }
        updAnexo();
        </script>
        {% else %}
        <p style="font-size:12px;color:#78909c;">Nenhum desenho anexado ainda. O supervisor pode anexar em ⚙️ Gerenciar → 📎 Anexos.</p>
        {% endif %}
    </div>

    <div class="card">
        <h3>🔄 Atividades Ativas</h3>
        {% if ativas %}
            {% for op in ativas %}
            <div style="background:#0d1b2a;padding:10px;border-radius:6px;margin-bottom:8px;border:1px solid #1a2a3a;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <strong>{{ op.num_op }}</strong> - Etapa {{ op.numero_etapa }}
                        <div style="font-size:11px;color:#78909c;">Máquina: {{ op.codigo_maquina }}</div>
                        <div style="font-size:11px;color:#78909c;">Início: {{ op.data_hora_inicio[11:16] if op.data_hora_inicio else '--:--' }}</div>
                        <div style="margin-top:6px;font-family:'Courier New',monospace;font-size:22px;font-weight:bold;color:#10B981;text-shadow:0 0 8px rgba(16,185,129,.4);" class="crono" data-start="{{ op.data_hora_inicio }}">00:00:00</div>
                    </div>
                    <button data-testid="btn-abrir-fechar-{{ op.id }}" onclick="document.getElementById('fechar-form-{{ op.id }}').style.display='block'" 
                            class="btn btn-sm btn-primary">📦 Fechar</button>
                </div>
                
                <div id="fechar-form-{{ op.id }}" style="display:none;margin-top:10px;background:#0a0e17;padding:10px;border-radius:6px;">
                    <form method="POST" action="/operador/fechar">
                        <input type="hidden" name="id" value="{{ op.id }}">
                        <div class="grid-2">
                            <div class="form-group">
                                <label>✅ Peças Boas</label>
                                <input type="number" name="qtd_boas" required min="0" placeholder="Qtde" value="">
                            </div>
                            <div class="form-group">
                                <label>❌ Refugo</label>
                                <input type="number" name="refugo" value="0" min="0" placeholder="0">
                            </div>
                        </div>
                        <div class="form-group">
                            <label style="font-size:11px;color:#f59e0b;">📝 Justificativa (obrigatória se refugo > saldo)</label>
                            <textarea name="justificativa_sucata" rows="2" placeholder="Ex: Material fora de tolerância recebido do fornecedor" style="width:100%;background:#0d1b2a;color:#fff;border:1px solid #1a2a3a;padding:8px;border-radius:4px;font-size:12px;"></textarea>
                        </div>
                        <div style="display:flex;gap:6px;margin-top:6px;">
                            <button type="submit" class="btn btn-sm btn-success" style="flex:1;">✅ Confirmar</button>
                            <button type="button" onclick="document.getElementById('fechar-form-{{ op.id }}').style.display='none'" 
                                    class="btn btn-sm btn-danger" style="flex:1;">❌ Cancelar</button>
                        </div>
                    </form>
                </div>
            </div>
            {% endfor %}
        {% else %}
        <p class="text-muted">Nenhuma atividade ativa</p>
        {% endif %}
    </div>
    
    <div class="card">
        <h3>📋 Histórico Hoje</h3>
        {% if historico %}
        <table class="table">
            <thead><tr><th>OP</th><th>Peças</th><th>Refugo</th><th>Status</th></tr></thead>
            <tbody>
            {% for op in historico %}
            <tr>
                <td>{{ op.num_op }}</td>
                <td>{{ op.qtd_boas }}</td>
                <td>{{ op.refugo or 0 }}</td>
                <td><span class="badge badge-success">✅ Fechado</span></td>
            </tr>
            {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p class="text-muted">Nenhum registro hoje</p>
        {% endif %}
    </div>
    
    <div class="card" style="border-top: 4px solid #c62828;">
        <h3>🚨 Registrar Parada</h3>
        <form method="POST" action="/operador/parada">
            <div class="form-group">
                <label>Motivo</label>
                <select name="motivo" required>
                    <option>Falta de Matéria-Prima</option>
                    <option>Quebra de Componente</option>
                    <option>Setup</option>
                    <option>Manutenção Preventiva</option>
                    <option>Outro</option>
                </select>
            </div>
            <button type="submit" class="btn btn-danger">🔴 Disparar Parada</button>
        </form>
        {% if paradas_ativas %}
        <h4 style="margin-top:10px; color:#ff9800;">🔧 Paradas Ativas</h4>
        <table class="table">
            <thead><tr><th>Motivo</th><th>Início</th><th>Ação</th></tr></thead>
            <tbody>
            {% for p in paradas_ativas %}
            <tr>
                <td>{{ p.motivo }}</td>
                <td>{{ p.data_hora_inicio[11:16] if p.data_hora_inicio else '--:--' }}</td>
                <td>
                    <form method="POST" action="/operador/retomar_parada/{{ p.id }}" style="display:inline;">
                        <button type="submit" class="btn btn-sm btn-success">🔧 Retomar</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
            </tbody>
        </table>
        {% endif %}
    </div>
{% endif %}
"""

PCP_TEMPLATE = """
<div class="card">
    <h3>📋 Programar Nova OP</h3>
    <form method="POST" action="/pcp/nova">
        <div class="form-group">
            <label>Código da OP</label>
            <input type="text" name="num_op" placeholder="Ex: OP-010" required>
        </div>
        <div class="form-group">
            <label>Desenho</label>
            <select name="numero_desenho" required>
                {% for d in desenhos %}
                <option value="{{ d.numero_desenho }}">{{ d.numero_desenho }} - {{ d.nome_peca }}</option>
                {% endfor %}
            </select>
        </div>
        <div class="form-group">
            <label>Meta Total</label>
            <input type="number" name="meta_total" required min="1">
        </div>
        <button type="submit" class="btn btn-primary">⚡ CRIAR OP</button>
    </form>
</div>

<div class="card">
    <h3>📊 OPs em Andamento</h3>
    {% if ops %}
    <div style="overflow-x:auto;">
    <table class="table">
        <thead><tr><th>OP</th><th>Meta</th><th>Fases</th><th>Progresso</th><th>Previsão</th><th>Ações</th></tr></thead>
        <tbody>
        {% for op in ops %}
        <tr>
            <td><strong>{{ op.num_op }}</strong></td>
            <td>{{ op.meta_total }}</td>
            <td data-testid="pcp-fases-{{ op.num_op }}">
                {% set an = analise_por_op.get(op.num_op) %}
                {% for f in an.fases %}
                <span data-testid="pcp-fase-chip-{{ op.num_op }}-{{ f.numero_etapa }}" title="Etapa {{ f.numero_etapa }} - {{ f.descricao_etapa }}: {{ 'PARADA há ' ~ f.horas_sem ~ 'h' if f.parada else f.estado }}" style="display:inline-block;padding:2px 6px;margin:1px;border-radius:4px;font-size:10px;font-weight:700;background:{{ '#b91c1c' if f.parada else ('#065f46' if f.estado == 'Concluída' else ('#b45309' if f.estado == 'Em produção' else '#1e293b')) }};color:#fff;">{% if f.parada %}🛑 {% endif %}{{ f.numero_etapa }} {{ f.descricao_etapa[:10] }}</span>
                {% endfor %}
            </td>
            <td>
                {% set nf = an.fases|length %}
                {% set progresso = ((op.meta_total * nf - op.saldo_total) / (op.meta_total * nf) * 100)|round(1) if op.meta_total > 0 and nf > 0 else 0 %}
                <div class="progress-bar">
                    <div class="fill" style="width:{{ progresso if progresso >= 0 else 0 }}%; background:{% if progresso >= 100 %}#4CAF50{% elif progresso >= 50 %}#FF9800{% else %}#2196F3{% endif %};"></div>
                </div>
                <span style="font-size:10px;color:#78909c;" data-testid="pcp-progresso-{{ op.num_op }}">{{ progresso if progresso >= 0 else 0 }}%</span>
            </td>
            <td data-testid="pcp-previsao-{{ op.num_op }}" style="font-size:11px;">
                {% if an.previsao %}<b style="color:#4fc3f7;">{{ an.previsao.strftime('%d/%m %H:%M') }}</b><br><span style="color:#78909c;font-size:10px;">{{ an.horas_restantes }}h máq.</span>{% else %}<span style="color:#78909c;">—</span>{% endif %}
            </td>
            <td>
                <a href="/pcp/acompanhar/{{ op.num_op }}" data-testid="pcp-acompanhar-{{ op.num_op }}" class="btn btn-sm btn-primary" style="display:inline-block;width:auto;padding:3px 8px;text-decoration:none;">🎯 Fases</a>
                <a href="/pcp/imprimir/{{ op.num_op }}" target="_blank" class="btn btn-sm btn-info" style="display:inline-block;width:auto;padding:3px 8px;text-decoration:none;">🖨️</a>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    </div>
    {% else %}
    <p class="text-muted">Nenhuma OP em andamento</p>
    {% endif %}
</div>
"""

CONSULTA_TEMPLATE = """
<div class="card">
    <h3>🔍 Consultar Ordem de Produção</h3>
    <form method="POST" action="/supervisor/consulta/buscar">
        <div class="form-group">
            <label>Número da OP</label>
            <select name="num_op" required>
                <option value="">-- Selecione --</option>
                {% for op in ops %}
                <option value="{{ op.num_op }}">{{ op.num_op }}</option>
                {% endfor %}
            </select>
        </div>
        <button type="submit" class="btn btn-primary">🔍 Buscar</button>
    </form>
</div>

{% if op_detalhes %}
<div class="card" style="border-top: 4px solid #4fc3f7;">
    <h3>📋 Informações da OP</h3>
    <div class="grid-2">
        <div><strong>OP:</strong> {{ op_detalhes.num_op }}</div>
        <div><strong>Desenho:</strong> {{ op_detalhes.numero_desenho }} - {{ op_detalhes.nome_peca }}</div>
        <div><strong>Meta:</strong> {{ op_detalhes.meta_total }} peças</div>
        <div><strong>Status:</strong> <span class="badge {% if op_detalhes.status_geral == 'Concluída' %}badge-success{% else %}badge-warning{% endif %}">{{ op_detalhes.status_geral }}</span></div>
        <div><strong>Data Criação:</strong> {{ op_detalhes.data_criacao }}</div>
        <div><strong>Progresso:</strong> {{ progresso }}%</div>
    </div>
</div>

<div class="card">
    <h3>📊 Apontamentos da OP</h3>
    {% if apontamentos %}
    <table class="table">
        <thead><tr><th>Data</th><th>Operador</th><th>Etapa</th><th>Peças</th><th>Refugo</th><th>Máquina</th></tr></thead>
        <tbody>
        {% for a in apontamentos %}
        <tr>
            <td>{{ a.data_hora_inicio[8:10] }}/{{ a.data_hora_inicio[5:7] }}/{{ a.data_hora_inicio[0:4] }}</td>
            <td>{{ a.operador_nome or a.operador }}</td>
            <td>Etapa {{ a.numero_etapa }}</td>
            <td><strong>{{ a.qtd_boas }}</strong></td>
            <td>{{ a.refugo or 0 }}</td>
            <td>{{ a.codigo_maquina }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    <div class="grid-2" style="margin-top:10px;">
        <div class="metric-card">
            <div class="value" style="color:#81c784;">{{ total_apontamentos|sum(attribute='qtd_boas') }}</div>
            <div class="label">Total Produzido</div>
        </div>
        <div class="metric-card">
            <div class="value" style="color:#ef9a9a;">{{ total_apontamentos|sum(attribute='refugo') }}</div>
            <div class="label">Total Refugo</div>
        </div>
    </div>
    {% else %}
    <p class="text-muted">Nenhum apontamento registrado para esta OP</p>
    {% endif %}
    
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
        <a href="/pcp/imprimir/{{ op_detalhes.num_op }}" target="_blank" class="btn btn-sm btn-info" style="flex:1;text-align:center;text-decoration:none;">🖨️ Imprimir OP</a>
        <a href="/relatorio/csv/op/{{ op_detalhes.num_op }}" class="btn btn-sm btn-primary" style="flex:1;text-align:center;text-decoration:none;">📥 Exportar CSV</a>
    </div>
</div>
{% endif %}
"""

BACKUP_TEMPLATE = """
<div class="card" style="border-top: 4px solid #4CAF50;">
    <h3>💾 Gerenciar Backups</h3>
    
    <div style="margin-bottom:15px;">
        <form method="POST" action="/supervisor/backup/criar" style="display:inline-block;width:100%;">
            <button type="submit" class="btn btn-success">📦 Criar Backup Agora</button>
        </form>
    </div>
    
    <h4>📂 Backups Disponíveis</h4>
    {% if backups %}
    <table class="table">
        <thead><tr><th>Arquivo</th><th>Data</th><th>Ações</th></tr></thead>
        <tbody>
        {% for b in backups %}
        <tr>
            <td>{{ b }}</td>
            <td>{{ b[10:12] }}/{{ b[8:10] }}/{{ b[0:4] }} {{ b[13:15] }}:{{ b[15:17] }}</td>
            <td>
                <form method="POST" action="/supervisor/backup/restaurar/{{ b }}" style="display:inline;" onsubmit="return confirm('Restaurar backup {{ b }}? Os dados atuais serão substituídos.');">
                    <button type="submit" class="btn btn-sm btn-warning">🔄 Restaurar</button>
                </form>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <p class="text-muted">Nenhum backup disponível</p>
    {% endif %}
</div>
"""

IMPRESSAO_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Ordem de Produção - {{ op.num_op }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Courier New', monospace;
            background: #fff;
            color: #000;
            padding: 40px;
            max-width: 800px;
            margin: 0 auto;
        }
        .header { text-align: center; border-bottom: 3px double #000; padding-bottom: 20px; margin-bottom: 30px; }
        .header h1 { font-size: 24px; }
        .header .sub { font-size: 14px; color: #666; }
        
        .info { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 30px; }
        .info .label { font-weight: bold; }
        
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th, td { padding: 8px 12px; text-align: left; border: 1px solid #000; }
        th { background: #f0f0f0; font-weight: bold; }
        
        .footer { margin-top: 40px; border-top: 2px solid #000; padding-top: 20px; text-align: center; }
        .assinatura { margin-top: 30px; }
        .assinatura .line { border-top: 1px solid #000; width: 300px; margin: 5px auto; }
        
        .no-print { display: none; }
        @media print { .no-print { display: none; } }
    </style>
</head>
<body>
    <div class="header">
        <h1>🏭 MES / APS</h1>
        <div class="sub">ORDEM DE PRODUÇÃO</div>
    </div>
    
    <div class="info">
        <div><span class="label">OP:</span> {{ op.num_op }}</div>
        <div><span class="label">Desenho:</span> {{ op.numero_desenho }} - {{ op.nome_peca }}</div>
        <div><span class="label">Quantidade:</span> {{ op.meta_total }} peças</div>
        <div><span class="label">Status:</span> {{ op.status_geral }}</div>
        <div><span class="label">Data Criação:</span> {{ op.data_criacao }}</div>
        <div><span class="label">Progresso:</span> {{ progresso }}%</div>
    </div>
    
    <h3>📋 ROTEIRO DE FABRICAÇÃO</h3>
    <table>
        <thead>
            <tr>
                <th>Etapa</th>
                <th>Operação</th>
                <th>Máquina</th>
                <th>Tempo (min)</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            {% for e in etapas %}
            <tr>
                <td>{{ e.numero_etapa }}</td>
                <td>{{ e.descricao_etapa }}</td>
                <td>{{ e.codigo_maquina_padrao or '-' }}</td>
                <td>{{ e.tempo_ciclo_minutos or '-' }}</td>
                <td>
                    {% if e.status_execucao == 'Concluída' %}✅ Concluída
                    {% elif e.status_execucao == 'Em Execução' %}🔄 Em Execução
                    {% else %}⏳ Pendente{% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    
    <div style="margin-top:20px;">
        <strong>Total de Operações:</strong> {{ etapas|length }}<br>
        <strong>Tempo Total Estimado:</strong> {{ tempo_total }} minutos
    </div>
    
    <div class="footer">
        <div class="assinatura">
            <div class="line"></div>
            <div style="font-size:12px;color:#666;">Assinatura do Supervisor</div>
        </div>
        <div style="margin-top:20px;font-size:12px;color:#666;">
            Documento gerado em {{ data_atual }}
        </div>
    </div>
    
    <div class="no-print" style="margin-top:30px;text-align:center;">
        <button onclick="window.print()" style="padding:10px 30px;font-size:16px;cursor:pointer;">🖨️ Imprimir</button>
        <button onclick="window.close()" style="padding:10px 30px;font-size:16px;cursor:pointer;margin-left:10px;">✕ Fechar</button>
    </div>
</body>
</html>
"""

ROTEIRO_GESTAO_TEMPLATE = """
<div class="card" style="border-top: 4px solid #ff9800;">
    <h3>⚙️ Gerenciar Roteiros</h3>
    
    <form method="GET" action="/supervisor/roteiro/gerenciar">
        <div class="form-group">
            <label>Filtrar por Desenho</label>
            <select name="numero_desenho" onchange="this.form.submit()">
                <option value="">-- Todos --</option>
                {% for d in desenhos %}
                <option value="{{ d.numero_desenho }}" {% if filtro == d.numero_desenho %}selected{% endif %}>
                    {{ d.numero_desenho }} - {{ d.nome_peca }}
                </option>
                {% endfor %}
            </select>
        </div>
    </form>
    
    {% if roteiros_filtrados %}
    <table class="table">
        <thead><tr><th>Desenho</th><th>Etapa</th><th>Descrição</th><th>Máquina</th><th>Tempo</th><th>Ações</th></tr></thead>
        <tbody>
        {% for r in roteiros_filtrados %}
        <tr>
            <td>{{ r.numero_desenho }}</td>
            <td>{{ r.numero_etapa }}</td>
            <td>{{ r.descricao_etapa }}</td>
            <td>{{ r.codigo_maquina_padrao }}</td>
            <td>{{ r.tempo_ciclo_minutos }} min</td>
            <td>
                <button onclick="editarRoteiro({{ r.id }})" class="btn btn-sm btn-warning">✏️</button>
                <form method="POST" action="/supervisor/roteiro/excluir/{{ r.id }}" style="display:inline;" onsubmit="return confirm('Excluir esta etapa?')">
                    <button type="submit" class="btn btn-sm btn-danger">🗑️</button>
                </form>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <p class="text-muted">Nenhum roteiro encontrado</p>
    {% endif %}
</div>

<!-- Modal Edição -->
<div id="modalEditar" class="modal-overlay" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.8);z-index:1000;justify-content:center;align-items:center;">
    <div class="modal" style="background:#111927;padding:20px;border-radius:10px;max-width:400px;width:90%;">
        <h3 style="color:#4fc3f7;">✏️ Editar Etapa</h3>
        <form id="formEditarRoteiro" method="POST">
            <input type="hidden" name="id" id="edit_id">
            <div class="form-group">
                <label>Descrição</label>
                <input type="text" name="descricao_etapa" id="edit_descricao" required>
            </div>
            <div class="form-group">
                <label>Máquina</label>
                <input type="text" name="codigo_maquina_padrao" id="edit_maquina" required>
            </div>
            <div class="form-group">
                <label>Tempo (min)</label>
                <input type="number" step="0.1" name="tempo_ciclo_minutos" id="edit_tempo" required>
            </div>
            <div style="display:flex;gap:10px;margin-top:10px;">
                <button type="submit" class="btn btn-success" style="flex:1;">💾 Salvar</button>
                <button type="button" onclick="fecharModalEditar()" class="btn btn-danger" style="flex:1;">❌ Cancelar</button>
            </div>
        </form>
    </div>
</div>

<script>
function editarRoteiro(id) {
    // Buscar dados via fetch
    fetch('/supervisor/api/roteiro/' + id)
        .then(response => response.json())
        .then(data => {
            document.getElementById('edit_id').value = data.id;
            document.getElementById('edit_descricao').value = data.descricao_etapa;
            document.getElementById('edit_maquina').value = data.codigo_maquina_padrao;
            document.getElementById('edit_tempo').value = data.tempo_ciclo_minutos;
            document.getElementById('formEditarRoteiro').action = '/supervisor/roteiro/editar/' + data.id;
            document.getElementById('modalEditar').style.display = 'flex';
        });
}

function fecharModalEditar() {
    document.getElementById('modalEditar').style.display = 'none';
}

// Fechar modal clicando fora
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.style.display = 'none';
    }
});
</script>
"""

SUPERVISOR_TEMPLATE = """
<!-- PAINEL -->
<div class="card" style="border-top: 4px solid #2196F3;">
    <h3>👮 Painel do Supervisor</h3>
    <div class="grid-2">
        <div class="metric-card">
            <div class="value" style="color:#90caf9;">{{ total_operadores }}</div>
            <div class="label">Operadores</div>
        </div>
        <div class="metric-card">
            <div class="value" style="color:#81c784;">{{ total_maquinas }}</div>
            <div class="label">Máquinas</div>
        </div>
        <div class="metric-card">
            <div class="value" style="color:#ffcc80;">{{ total_ops }}</div>
            <div class="label">OPs Ativas</div>
        </div>
        <div class="metric-card">
            <div class="value" style="color:#ef9a9a;">{{ total_apontamentos }}</div>
            <div class="label">Apontamentos Hoje</div>
        </div>
    </div>
</div>

<!-- 1. CADASTRAR OPERADOR -->
<div class="card" id="operador">
    <h3>📦 Cadastrar Operador</h3>
    <form method="POST" action="/supervisor/operador">
        <div class="flex">
            <div class="flex-1">
                <div class="form-group"><label>Matrícula</label><input type="text" name="matricula" required></div>
            </div>
            <div class="flex-1">
                <div class="form-group"><label>Nome</label><input type="text" name="nome" required></div>
            </div>
        </div>
        <div class="form-group">
            <label>Turno</label>
            <select name="turno">
                <option>Turno A</option>
                <option>Turno B</option>
                <option>Turno C</option>
            </select>
        </div>
        <div class="form-group">
            <label>Senha</label>
            <input type="password" name="senha" placeholder="Senha: 123456" value="123456">
        </div>
        <button type="submit" class="btn btn-primary">➕ Cadastrar</button>
    </form>
</div>

<!-- 2. CADASTRAR MÁQUINA -->
<div class="card" id="maquina">
    <h3>🏭 Cadastrar Máquina</h3>
    <form method="POST" action="/supervisor/maquina">
        <div class="flex">
            <div class="flex-1">
                <div class="form-group"><label>Código</label><input type="text" name="codigo_maquina" required></div>
            </div>
            <div class="flex-1">
                <div class="form-group"><label>Descrição</label><input type="text" name="descricao" required></div>
            </div>
        </div>
        <button type="submit" class="btn btn-primary">➕ Cadastrar</button>
    </form>
</div>

<!-- 3. CADASTRAR DESENHO -->
<div class="card" id="desenho" style="border-top: 4px solid #4fc3f7;">
    <h3>📐 Cadastrar Desenho</h3>
    <form method="POST" action="/supervisor/desenho">
        <div class="flex">
            <div class="flex-1">
                <div class="form-group"><label>Número do Desenho</label><input type="text" name="numero_desenho" placeholder="Ex: 006" required></div>
            </div>
            <div class="flex-1">
                <div class="form-group"><label>Nome da Peça</label><input type="text" name="nome_peca" placeholder="Ex: Suporte" required></div>
            </div>
        </div>
        <button type="submit" class="btn btn-primary">➕ Cadastrar Desenho</button>
    </form>
</div>

<!-- 4. CADASTRAR ROTEIRO -->
<div class="card" id="roteiro" style="border-top: 4px solid #ff9800;">
    <h3>⚙️ Adicionar Etapa ao Roteiro</h3>
    <form method="POST" action="/supervisor/roteiro/adicionar">
        <div class="form-group">
            <label>Desenho</label>
            <select name="numero_desenho" required>
                {% for d in desenhos %}
                <option value="{{ d.numero_desenho }}">{{ d.numero_desenho }} - {{ d.nome_peca }}</option>
                {% endfor %}
            </select>
        </div>
        <div class="flex">
            <div class="flex-1">
                <div class="form-group"><label>Nº da Etapa</label><input type="number" name="numero_etapa" placeholder="10" required></div>
            </div>
            <div class="flex-1">
                <div class="form-group"><label>Descrição</label><input type="text" name="descricao_etapa" placeholder="Ex: Corte" required></div>
            </div>
        </div>
        <div class="flex">
            <div class="flex-1">
                <div class="form-group"><label>Máquina Padrão</label><input type="text" name="codigo_maquina_padrao" placeholder="Ex: SR-01" required></div>
            </div>
            <div class="flex-1">
                <div class="form-group"><label>Tempo (min/pç)</label><input type="number" step="0.1" name="tempo_ciclo_minutos" placeholder="1.5" required></div>
            </div>
        </div>
        <button type="submit" class="btn btn-primary">➕ Adicionar Etapa</button>
    </form>
    
    <div style="margin-top:12px;">
        <a href="/supervisor/roteiro/gerenciar" class="btn btn-sm btn-info" style="text-align:center;text-decoration:none;display:block;">⚙️ Gerenciar Roteiros</a>
    </div>
</div>

<!-- 5. LISTA DE OPERADORES -->
<div class="card" id="operadores">
    <h3>👥 Lista de Operadores</h3>
    <table class="table">
        <thead><tr><th>Matrícula</th><th>Nome</th><th>Turno</th></tr></thead>
        <tbody>
        {% for op in operadores %}
        <tr>
            <td>{{ op.matricula }}</td>
            <td>{{ op.nome }}</td>
            <td>{{ op.turno }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>

<!-- 6. LISTA DE MÁQUINAS -->
<div class="card" id="maquinas">
    <h3>🏭 Lista de Máquinas</h3>
    <table class="table">
        <thead><tr><th>Código</th><th>Descrição</th><th>Status</th></tr></thead>
        <tbody>
        {% for m in maquinas %}
        <tr>
            <td>{{ m.codigo_maquina }}</td>
            <td>{{ m.descricao }}</td>
            <td><span class="badge {% if m.status_operacional == 'Ativa' %}badge-success{% else %}badge-danger{% endif %}">{{ m.status_operacional }}</span></td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>

<!-- 7. LISTA DE DESENHOS -->
<div class="card" id="desenhos">
    <h3>📐 Lista de Desenhos</h3>
    <table class="table">
        <thead><tr><th>Número</th><th>Nome da Peça</th></tr></thead>
        <tbody>
        {% for d in desenhos %}
        <tr>
            <td>{{ d.numero_desenho }}</td>
            <td>{{ d.nome_peca }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>

<!-- 8. RELATÓRIOS -->
<div class="card" id="relatorios" style="border-top: 4px solid #4CAF50;">
    <h3>📊 Relatórios</h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <a href="/relatorio/csv/dia" class="btn btn-sm btn-info" style="flex:1;text-align:center;text-decoration:none;">📄 Hoje</a>
        <a href="/relatorio/csv/semana" class="btn btn-sm btn-info" style="flex:1;text-align:center;text-decoration:none;">📄 Semana</a>
        <a href="/relatorio/csv/mes" class="btn btn-sm btn-info" style="flex:1;text-align:center;text-decoration:none;">📄 Mês</a>
    </div>
    
    <h4 style="margin-top:12px;">📈 Produtividade por Operador</h4>
    <table class="table">
        <thead><tr><th>Operador</th><th>Peças</th><th>Refugo</th></tr></thead>
        <tbody>
        {% if relatorio_dados %}
            {% for r in relatorio_dados %}
            <tr>
                <td>{{ r.operador_nome or r.operador or 'Desconhecido' }}</td>
                <td><strong>{{ r.total_produzido or 0 }}</strong></td>
                <td><span class="badge badge-warning">{{ r.total_refugo or 0 }}</span></td>
            </tr>
            {% endfor %}
        {% else %}
            <tr><td colspan="3" class="text-muted" style="text-align:center;">Nenhum dado disponível</td></tr>
        {% endif %}
        </tbody>
    </table>
</div>
"""

# ============================================
# ROTAS - AUTENTICAÇÃO
# ============================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        matricula = request.form.get('matricula')
        senha = request.form.get('senha')
        
        operador = query("SELECT * FROM operadores WHERE matricula = ? AND ativo = 1", (matricula,), one=True)
        
        if operador:
            if verificar_senha(senha, operador['senha']):
                session['matricula'] = operador['matricula']
                session['nome'] = operador['nome']
                session['turno'] = operador['turno']
                session['papel'] = operador['papel'] or 'operador'
                # upgrade transparente para bcrypt em contas legadas SHA256
                upgrade_senha_se_legado(operador['matricula'], senha, operador['senha'])
                log_audit('login', 'sessao', operador['matricula'])
                flash(f'Bem-vindo, {operador["nome"]}!', 'success')
                return redirect(url_for('operador'))
            else:
                flash('Senha incorreta!', 'danger')
        else:
            flash('Matrícula não encontrada!', 'danger')
        return redirect(url_for('login'))
    
    return render_template_string(BASE_TEMPLATE, conteudo=LOGIN_TEMPLATE, aba='operador', session=session)

@app.route('/logout')
def logout():
    tok = request.cookies.get('session_token')
    if tok:
        query("DELETE FROM user_sessions WHERE session_token = ?", (tok,), commit=True)
    session.clear()
    resp = redirect(url_for('login'))
    resp.delete_cookie('session_token', path='/')
    flash('Logout realizado', 'info')
    return resp

@app.route('/')
def home():
    if session.get('matricula'):
        return redirect(url_for('operador'))
    return redirect(url_for('login'))

# ============================================
# ROTAS - OPERADOR
# ============================================

@app.route('/operador')
def operador():
    if not session.get('matricula'):
        return redirect(url_for('login'))
    if session.get('papel') == 'pcp':
        return redirect(url_for('pcp'))
    
    matricula = session['matricula']
    r = query("""SELECT COALESCE(SUM(qtd_boas),0) as boas, COALESCE(SUM(refugo),0) as refugo FROM apontamentos
        WHERE operador = ? AND DATE(data_hora_inicio) = DATE('now') AND qtd_boas IS NOT NULL""", (matricula,), one=True)
    total = (r['boas'] or 0) + (r['refugo'] or 0)
    meu = {'boas': r['boas'] or 0, 'refugo': r['refugo'] or 0,
           'qualidade': round((r['boas'] or 0) / total * 100, 1) if total else 100}
    
    ativas = query("""SELECT * FROM apontamentos 
        WHERE operador = ? AND qtd_boas IS NULL AND status = 'Ativo' 
        ORDER BY data_hora_inicio DESC""", (matricula,))
    
    historico = query("""SELECT * FROM apontamentos 
        WHERE operador = ? AND DATE(data_hora_inicio) = DATE('now') 
        AND qtd_boas IS NOT NULL AND status = 'Ativo' 
        ORDER BY data_hora_inicio DESC LIMIT 10""", (matricula,))
    
    ops_disponiveis = query("""SELECT e.num_op, e.numero_etapa, e.descricao_etapa, e.saldo_residual 
        FROM op_etapas_execucao e 
        WHERE e.saldo_residual > 0 AND e.status_execucao != 'Concluída'""")
    
    maquinas_disponiveis = query("""SELECT * FROM maquinas WHERE status_operacional = 'Ativa'""")
    
    paradas_ativas = query("""SELECT * FROM paradas_maquina 
        WHERE operador = ? AND status = 'Ativa' 
        ORDER BY data_hora_inicio DESC""", (matricula,))
    
    anexos = query("""SELECT DISTINCT a.num_op, a.nome_arquivo, a.content_type FROM op_anexos a
        JOIN ordens_producao o ON o.num_op = a.num_op
        WHERE o.status_geral NOT IN ('Concluída','Cancelada')""") or []

    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(OPERADOR_TEMPLATE,
            ativas=ativas, historico=historico, ops_disponiveis=ops_disponiveis,
            maquinas_disponiveis=maquinas_disponiveis, paradas_ativas=paradas_ativas,
            anexos=anexos, meu=meu,
            session=session, login=LOGIN_TEMPLATE),
        aba='operador', session=session)

@app.route('/operador/iniciar', methods=['POST'])
def operador_iniciar():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    op_etapa = request.form.get('op_etapa')
    codigo_maquina = request.form.get('codigo_maquina')
    
    if not op_etapa or '|' not in op_etapa:
        flash('Dados inválidos', 'danger')
        return redirect(url_for('operador'))
    
    num_op, numero_etapa = op_etapa.split('|')
    matricula = session['matricula']
    
    etapa = query("SELECT * FROM op_etapas_execucao WHERE num_op = ? AND numero_etapa = ?", 
                  (num_op, int(numero_etapa)), one=True)
    if not etapa or etapa['saldo_residual'] <= 0:
        flash('Etapa não disponível', 'warning')
        return redirect(url_for('operador'))
    
    query("INSERT INTO apontamentos (num_op, numero_etapa, operador, codigo_maquina) VALUES (?, ?, ?, ?)", 
          (num_op, int(numero_etapa), matricula, codigo_maquina), commit=True)
    query("UPDATE op_etapas_execucao SET status_execucao = 'Em Execução' WHERE num_op = ? AND numero_etapa = ?", 
          (num_op, int(numero_etapa)), commit=True)
    
    flash(f'✅ Operação iniciada em {codigo_maquina}', 'success')
    return redirect(url_for('operador'))

@app.route('/operador/fechar', methods=['POST'])
def operador_fechar():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    id_apontamento = request.form.get('id')
    qtd_boas = request.form.get('qtd_boas', type=int, default=0)
    refugo = request.form.get('refugo', type=int, default=0)
    
    if qtd_boas <= 0:
        flash('❌ Informe uma quantidade válida de peças!', 'danger')
        return redirect(url_for('operador'))
    
    apont = query("SELECT * FROM apontamentos WHERE id = ?", (id_apontamento,), one=True)
    if not apont:
        flash('Apontamento não encontrado', 'danger')
        return redirect(url_for('operador'))
    
    saldo = query("SELECT saldo_residual FROM op_etapas_execucao WHERE num_op = ? AND numero_etapa = ?", (apont['num_op'], apont['numero_etapa']), one=True)
    
    if not saldo:
        flash('Etapa não encontrada', 'danger')
        return redirect(url_for('operador'))
    
    if saldo['saldo_residual'] <= 0:
        flash('⚠️ Saldo já zerado! Não é possível produzir mais.', 'warning')
        return redirect(url_for('operador'))
    
    if qtd_boas > saldo['saldo_residual']:
        qtd_boas = saldo['saldo_residual']
        flash(f'⚠️ Quantidade ajustada para o saldo disponível: {qtd_boas} peças', 'warning')

    # Fase 2: sucata > saldo residual exige justificativa e vai para aprovação
    justificativa = request.form.get('justificativa_sucata', '').strip()
    aprovacao = 'ok'
    if refugo > saldo['saldo_residual']:
        if not justificativa:
            flash('⚠️ Refugo acima do saldo residual: justificativa obrigatória.', 'danger')
            return redirect(url_for('operador'))
        aprovacao = 'pendente'
        flash('⏳ Refugo enviado para aprovação do supervisor.', 'warning')
    
    query("""UPDATE apontamentos SET qtd_boas = ?, refugo = ?, data_hora_fim = CURRENT_TIMESTAMP,
             justificativa_sucata = ?, status_aprovacao = ? WHERE id = ?""",
          (qtd_boas, refugo, justificativa or None, aprovacao, id_apontamento), commit=True)
    log_audit('fechar', 'apontamento', id_apontamento, f'boas={qtd_boas} refugo={refugo} aprov={aprovacao}')
    # Só desconta saldo se não estiver pendente de aprovação
    if aprovacao == 'ok':
        query("UPDATE op_etapas_execucao SET saldo_residual = saldo_residual - ? WHERE num_op = ? AND numero_etapa = ?", (qtd_boas, apont['num_op'], apont['numero_etapa']), commit=True)
    
    saldo_novo = query("SELECT saldo_residual FROM op_etapas_execucao WHERE num_op = ? AND numero_etapa = ?", (apont['num_op'], apont['numero_etapa']), one=True)
    if saldo_novo and saldo_novo['saldo_residual'] <= 0:
        query("UPDATE op_etapas_execucao SET status_execucao = 'Concluída', saldo_residual = 0 WHERE num_op = ? AND numero_etapa = ?", (apont['num_op'], apont['numero_etapa']), commit=True)
        
        pendentes = query("SELECT COUNT(*) as total FROM op_etapas_execucao WHERE num_op = ? AND status_execucao != 'Concluída'", (apont['num_op'],), one=True)
        if pendentes and pendentes['total'] == 0:
            query("UPDATE ordens_producao SET status_geral = 'Concluída' WHERE num_op = ?", (apont['num_op'],), commit=True)
            flash(f'🎉 OP {apont["num_op"]} concluída!', 'success')
    
    flash(f'✅ Operação fechada! {qtd_boas} peças', 'success')
    return redirect(url_for('operador'))
@app.route('/operador/parada', methods=['POST'])
def operador_parada():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    motivo = request.form.get('motivo')
    query("INSERT INTO paradas_maquina (operador, motivo, status) VALUES (?, ?, 'Ativa')", 
          (session['matricula'], motivo), commit=True)
    flash('🔴 Parada registrada', 'warning')
    return redirect(url_for('operador'))

@app.route('/operador/retomar_parada/<int:id>', methods=['POST'])
def operador_retomar_parada(id):
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    query("UPDATE paradas_maquina SET status = 'Encerrada', data_hora_fim = CURRENT_TIMESTAMP WHERE id = ?", 
          (id,), commit=True)
    flash('🔄 Produção retomada', 'success')
    return redirect(url_for('operador'))

# ============================================
# ROTAS - PCP
# ============================================

@app.route('/pcp')
def pcp():
    if not session.get('matricula'):
        return redirect(url_for('login'))
    
    desenhos = query("SELECT * FROM desenhos ORDER BY numero_desenho")
    ops = query("""SELECT o.*, COALESCE(SUM(e.saldo_residual), 0) as saldo_total 
        FROM ordens_producao o 
        LEFT JOIN op_etapas_execucao e ON o.num_op = e.num_op 
        WHERE o.status_geral != 'Concluída' 
        GROUP BY o.num_op 
        ORDER BY o.data_criacao""")
    analise_por_op = {op['num_op']: _analise_op(op) for op in (ops or [])}
    
    return render_template_string(BASE_TEMPLATE, 
        conteudo=render_template_string(PCP_TEMPLATE, desenhos=desenhos, ops=ops, analise_por_op=analise_por_op, limite_parada=LIMITE_FASE_PARADA_H),
        aba='pcp', session=session)

@app.route('/pcp/nova', methods=['POST'])
def pcp_nova():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    num_op = request.form.get('num_op')
    numero_desenho = request.form.get('numero_desenho')
    meta_total = request.form.get('meta_total', type=int)
    
    if not all([num_op, numero_desenho, meta_total]):
        flash('Preencha todos os campos', 'danger')
        return redirect(url_for('pcp'))
    
    if query("SELECT num_op FROM ordens_producao WHERE num_op = ?", (num_op,), one=True):
        flash(f'OP {num_op} já existe', 'danger')
        return redirect(url_for('pcp'))
    
    query("""INSERT INTO ordens_producao (num_op, numero_desenho, meta_total) VALUES (?, ?, ?)""", 
          (num_op, numero_desenho, meta_total), commit=True)
    
    roteiro = query("""SELECT numero_etapa, descricao_etapa FROM roteiros_padrao 
                        WHERE numero_desenho = ? ORDER BY numero_etapa""", 
                    (numero_desenho,))
    
    if roteiro:
        for etapa in roteiro:
            query("""INSERT OR IGNORE INTO op_etapas_execucao 
                (num_op, numero_etapa, descricao_etapa, saldo_residual) 
                VALUES (?, ?, ?, ?)""", 
                (num_op, etapa['numero_etapa'], etapa['descricao_etapa'], meta_total), commit=True)
        flash(f'✅ OP {num_op} criada com {len(roteiro)} etapas', 'success')
    else:
        flash(f'⚠️ OP {num_op} criada sem roteiro', 'warning')
    
    return redirect(url_for('pcp'))

@app.route('/pcp/imprimir/<num_op>')
def pcp_imprimir(num_op):
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    # Buscar OP
    op = query("""SELECT o.*, d.nome_peca 
        FROM ordens_producao o 
        LEFT JOIN desenhos d ON o.numero_desenho = d.numero_desenho 
        WHERE o.num_op = ?""", (num_op,), one=True)
    
    if not op:
        flash('OP não encontrada', 'danger')
        return redirect(url_for('pcp'))
    
    # Buscar etapas
    etapas = query("""SELECT e.*, r.codigo_maquina_padrao, r.tempo_ciclo_minutos
        FROM op_etapas_execucao e
        LEFT JOIN roteiros_padrao r ON e.num_op = r.numero_desenho AND e.numero_etapa = r.numero_etapa
        WHERE e.num_op = ?
        ORDER BY e.numero_etapa""", (num_op,))
    
    # Calcular progresso
    meta = op['meta_total']
    saldo_total = query("SELECT SUM(saldo_residual) as total FROM op_etapas_execucao WHERE num_op = ?", (num_op,), one=True)
    saldo = saldo_total['total'] if saldo_total and saldo_total['total'] else 0
    progresso = round(((meta - saldo) / meta * 100), 1) if meta > 0 else 0
    progresso = max(0, progresso)
    
    # Calcular tempo total
    tempo_total = sum(e['tempo_ciclo_minutos'] or 0 for e in etapas)
    
    return render_template_string(IMPRESSAO_TEMPLATE, 
        op=op, etapas=etapas, progresso=progresso, 
        tempo_total=tempo_total, data_atual=datetime.now().strftime('%d/%m/%Y %H:%M'))

# ============================================
# ROTAS - SUPERVISOR
# ============================================

@app.route('/supervisor')
def supervisor():
    if not session.get('matricula'):
        return redirect(url_for('login'))
    
    total_operadores = query("SELECT COUNT(*) as total FROM operadores WHERE ativo = 1", one=True)['total']
    total_maquinas = query("SELECT COUNT(*) as total FROM maquinas", one=True)['total']
    total_ops = query("SELECT COUNT(*) as total FROM ordens_producao WHERE status_geral != 'Concluída'", one=True)['total']
    total_apontamentos = query("SELECT COUNT(*) as total FROM apontamentos WHERE DATE(data_hora_inicio) = DATE('now')", one=True)['total']
    
    operadores = query("SELECT * FROM operadores WHERE ativo = 1 ORDER BY nome")
    maquinas = query("SELECT * FROM maquinas ORDER BY codigo_maquina")
    desenhos = query("SELECT * FROM desenhos ORDER BY numero_desenho")
    
    relatorio_dados = query("""
        SELECT 
            a.operador,
            o.nome as operador_nome,
            SUM(a.qtd_boas) as total_produzido,
            SUM(a.refugo) as total_refugo
        FROM apontamentos a
        LEFT JOIN operadores o ON a.operador = o.matricula
        WHERE a.qtd_boas IS NOT NULL AND a.status = 'Ativo'
        GROUP BY a.operador, o.nome
        ORDER BY total_produzido DESC
        LIMIT 10
    """)
    
    return render_template_string(BASE_TEMPLATE, 
        conteudo=render_template_string(SUPERVISOR_TEMPLATE,
            total_operadores=total_operadores, total_maquinas=total_maquinas,
            total_ops=total_ops, total_apontamentos=total_apontamentos,
            operadores=operadores, maquinas=maquinas, desenhos=desenhos,
            relatorio_dados=relatorio_dados),
        aba='supervisor', session=session)

@app.route('/supervisor/operador', methods=['POST'])
def supervisor_operador():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    matricula = request.form.get('matricula')
    nome = request.form.get('nome')
    turno = request.form.get('turno')
    senha = request.form.get('senha', '123456')
    
    if not matricula or not nome:
        flash('Preencha todos os campos', 'danger')
        return redirect(url_for('supervisor'))
    
    senha_hash = gerar_hash_senha(senha)
    query("INSERT OR IGNORE INTO operadores (matricula, nome, turno, senha) VALUES (?, ?, ?, ?)", 
          (matricula, nome, turno, senha_hash), commit=True)
    flash(f'✅ Operador {nome} cadastrado', 'success')
    return redirect(url_for('supervisor'))

@app.route('/supervisor/maquina', methods=['POST'])
def supervisor_maquina():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    codigo_maquina = request.form.get('codigo_maquina')
    descricao = request.form.get('descricao')
    
    if not codigo_maquina or not descricao:
        flash('Preencha todos os campos', 'danger')
        return redirect(url_for('supervisor'))
    
    query("INSERT OR IGNORE INTO maquinas (codigo_maquina, descricao) VALUES (?, ?)", 
          (codigo_maquina, descricao), commit=True)
    flash(f'✅ Máquina {codigo_maquina} cadastrada', 'success')
    return redirect(url_for('supervisor'))

@app.route('/supervisor/desenho', methods=['POST'])
def supervisor_desenho():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    numero_desenho = request.form.get('numero_desenho')
    nome_peca = request.form.get('nome_peca')
    
    if not numero_desenho or not nome_peca:
        flash('Preencha todos os campos', 'danger')
        return redirect(url_for('supervisor'))
    
    existe = query("SELECT numero_desenho FROM desenhos WHERE numero_desenho = ?", (numero_desenho,), one=True)
    if existe:
        flash(f'❌ Desenho {numero_desenho} já existe!', 'danger')
        return redirect(url_for('supervisor'))
    
    query("INSERT INTO desenhos (numero_desenho, nome_peca) VALUES (?, ?)", 
          (numero_desenho, nome_peca), commit=True)
    flash(f'✅ Desenho {numero_desenho} cadastrado com sucesso!', 'success')
    return redirect(url_for('supervisor'))

@app.route('/supervisor/roteiro/adicionar', methods=['POST'])
def supervisor_roteiro_adicionar():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    numero_desenho = request.form.get('numero_desenho')
    numero_etapa = request.form.get('numero_etapa', type=int)
    descricao_etapa = request.form.get('descricao_etapa')
    codigo_maquina_padrao = request.form.get('codigo_maquina_padrao')
    tempo_ciclo_minutos = request.form.get('tempo_ciclo_minutos', type=float)
    
    if not all([numero_desenho, numero_etapa, descricao_etapa, codigo_maquina_padrao, tempo_ciclo_minutos]):
        flash('Preencha todos os campos', 'danger')
        return redirect(url_for('supervisor'))
    
    desenho = query("SELECT numero_desenho FROM desenhos WHERE numero_desenho = ?", (numero_desenho,), one=True)
    if not desenho:
        flash(f'❌ Desenho {numero_desenho} não encontrado!', 'danger')
        return redirect(url_for('supervisor'))
    
    existe = query("""SELECT id FROM roteiros_padrao 
        WHERE numero_desenho = ? AND numero_etapa = ?""", 
        (numero_desenho, numero_etapa), one=True)
    if existe:
        flash(f'⚠️ Etapa {numero_etapa} já existe para este desenho!', 'warning')
        return redirect(url_for('supervisor'))
    
    query("""INSERT INTO roteiros_padrao 
        (numero_desenho, numero_etapa, descricao_etapa, codigo_maquina_padrao, tempo_ciclo_minutos)
        VALUES (?, ?, ?, ?, ?)""", 
        (numero_desenho, numero_etapa, descricao_etapa, codigo_maquina_padrao, tempo_ciclo_minutos), commit=True)
    
    flash(f'✅ Etapa {numero_etapa} - {descricao_etapa} adicionada!', 'success')
    return redirect(url_for('supervisor'))

@app.route('/supervisor/roteiro/gerenciar')
def supervisor_roteiro_gerenciar():
    if not session.get('matricula'):
        return redirect(url_for('login'))
    
    filtro = request.args.get('numero_desenho', '')
    desenhos = query("SELECT * FROM desenhos ORDER BY numero_desenho")
    
    if filtro:
        roteiros_filtrados = query("""SELECT * FROM roteiros_padrao 
            WHERE numero_desenho = ? ORDER BY numero_etapa""", (filtro,))
    else:
        roteiros_filtrados = query("SELECT * FROM roteiros_padrao ORDER BY numero_desenho, numero_etapa")
    
    return render_template_string(BASE_TEMPLATE, 
        conteudo=render_template_string(ROTEIRO_GESTAO_TEMPLATE,
            desenhos=desenhos, roteiros_filtrados=roteiros_filtrados, filtro=filtro),
        aba='supervisor', session=session)

@app.route('/supervisor/roteiro/editar/<int:id>', methods=['POST'])
def supervisor_roteiro_editar(id):
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    descricao_etapa = request.form.get('descricao_etapa')
    codigo_maquina_padrao = request.form.get('codigo_maquina_padrao')
    tempo_ciclo_minutos = request.form.get('tempo_ciclo_minutos', type=float)
    
    if not all([descricao_etapa, codigo_maquina_padrao, tempo_ciclo_minutos]):
        flash('Preencha todos os campos', 'danger')
        return redirect(url_for('supervisor'))
    
    query("""UPDATE roteiros_padrao 
        SET descricao_etapa = ?, codigo_maquina_padrao = ?, tempo_ciclo_minutos = ?
        WHERE id = ?""", 
        (descricao_etapa, codigo_maquina_padrao, tempo_ciclo_minutos, id), commit=True)
    
    flash('✅ Etapa atualizada com sucesso!', 'success')
    return redirect(url_for('supervisor_roteiro_gerenciar'))

@app.route('/supervisor/roteiro/excluir/<int:id>', methods=['POST'])
def supervisor_roteiro_excluir(id):
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    query("DELETE FROM roteiros_padrao WHERE id = ?", (id,), commit=True)
    flash('🗑️ Etapa excluída', 'info')
    return redirect(url_for('supervisor_roteiro_gerenciar'))

@app.route('/supervisor/api/roteiro/<int:id>')
def api_roteiro(id):
    if not session.get('matricula'):
        return jsonify({'erro': 'Não logado'}), 401
    
    roteiro = query("SELECT * FROM roteiros_padrao WHERE id = ?", (id,), one=True)
    if not roteiro:
        return jsonify({'erro': 'Não encontrado'}), 404
    
    return jsonify(dict(roteiro))

# ============================================
# ROTAS - CONSULTA DE OP
# ============================================

@app.route('/supervisor/consulta')
def supervisor_consulta():
    if not session.get('matricula'):
        return redirect(url_for('login'))
    
    ops = query("SELECT num_op FROM ordens_producao ORDER BY data_criacao DESC")
    
    return render_template_string(BASE_TEMPLATE, 
        conteudo=render_template_string(CONSULTA_TEMPLATE, ops=ops),
        aba='supervisor', session=session)

@app.route('/supervisor/consulta/buscar', methods=['POST'])
def supervisor_consulta_buscar():
    if not session.get('matricula'):
        return redirect(url_for('login'))
    
    num_op = request.form.get('num_op')
    
    if not num_op:
        flash('Selecione uma OP', 'warning')
        return redirect(url_for('supervisor_consulta'))
    
    # Buscar OP
    op_detalhes = query("""SELECT o.*, d.nome_peca 
        FROM ordens_producao o 
        LEFT JOIN desenhos d ON o.numero_desenho = d.numero_desenho 
        WHERE o.num_op = ?""", (num_op,), one=True)
    
    if not op_detalhes:
        flash('OP não encontrada', 'danger')
        return redirect(url_for('supervisor_consulta'))
    
    # Buscar apontamentos
    apontamentos = query("""SELECT a.*, op.nome as operador_nome 
        FROM apontamentos a
        LEFT JOIN operadores op ON a.operador = op.matricula
        WHERE a.num_op = ? AND a.qtd_boas IS NOT NULL AND a.status = 'Ativo'
        ORDER BY a.data_hora_inicio DESC""", (num_op,))
    
    # Calcular progresso
    meta = op_detalhes['meta_total']
    saldo_total = query("SELECT SUM(saldo_residual) as total FROM op_etapas_execucao WHERE num_op = ?", (num_op,), one=True)
    saldo = saldo_total['total'] if saldo_total and saldo_total['total'] else 0
    progresso = round(((meta - saldo) / meta * 100), 1) if meta > 0 else 0
    progresso = max(0, progresso)
    
    ops = query("SELECT num_op FROM ordens_producao ORDER BY data_criacao DESC")
    
    return render_template_string(BASE_TEMPLATE, 
        conteudo=render_template_string(CONSULTA_TEMPLATE, 
            ops=ops, op_detalhes=op_detalhes, apontamentos=apontamentos, 
            progresso=progresso, total_apontamentos=apontamentos),
        aba='supervisor', session=session)

# ============================================
# ROTAS - BACKUP
# ============================================

@app.route('/supervisor/backup')
def supervisor_backup():
    if not session.get('matricula'):
        return redirect(url_for('login'))
    
    backups = listar_backups()
    
    return render_template_string(BASE_TEMPLATE, 
        conteudo=render_template_string(BACKUP_TEMPLATE, backups=backups),
        aba='supervisor', session=session)

@app.route('/supervisor/backup/criar', methods=['POST'])
def supervisor_backup_criar():
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    criar_backup()
    flash('✅ Backup criado com sucesso!', 'success')
    return redirect(url_for('supervisor_backup'))

@app.route('/supervisor/backup/restaurar/<nome_arquivo>', methods=['POST'])
def supervisor_backup_restaurar(nome_arquivo):
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    if restaurar_backup(nome_arquivo):
        flash(f'✅ Backup {nome_arquivo} restaurado com sucesso!', 'success')
    else:
        flash('❌ Erro ao restaurar backup', 'danger')
    
    return redirect(url_for('supervisor_backup'))

# ============================================
# ROTAS - RELATÓRIOS
# ============================================

@app.route('/relatorio/csv/<periodo>')
def relatorio_csv(periodo):
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    if periodo == 'dia':
        filtro = "DATE(data_hora_inicio) = DATE('now')"
    elif periodo == 'semana':
        filtro = "DATE(data_hora_inicio) >= DATE('now', '-7 days')"
    else:
        filtro = "DATE(data_hora_inicio) >= DATE('now', '-30 days')"
    
    dados = query(f"""
        SELECT 
            a.num_op, a.numero_etapa, a.operador, a.codigo_maquina,
            a.qtd_boas, a.refugo, a.data_hora_inicio, a.data_hora_fim,
            o.nome as operador_nome
        FROM apontamentos a
        LEFT JOIN operadores o ON a.operador = o.matricula
        WHERE {filtro} AND a.qtd_boas IS NOT NULL AND a.status = 'Ativo'
        ORDER BY a.data_hora_inicio DESC
    """)
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['OP', 'Etapa', 'Operador', 'Máquina', 'Peças Boas', 'Refugo', 'Início', 'Fim'])
    
    for row in dados:
        writer.writerow([row['num_op'], row['numero_etapa'], 
                         row['operador_nome'] or row['operador'],
                         row['codigo_maquina'], row['qtd_boas'], row['refugo'],
                         row['data_hora_inicio'], row['data_hora_fim']])
    
    output.seek(0)
    nome_arquivo = f"relatorio_{periodo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=nome_arquivo
    )

@app.route('/relatorio/csv/op/<num_op>')
def relatorio_csv_op(num_op):
    if not session.get('matricula'):
        flash('Faça login primeiro', 'warning')
        return redirect(url_for('login'))
    
    dados = query("""
        SELECT 
            a.num_op, a.numero_etapa, a.operador, a.codigo_maquina,
            a.qtd_boas, a.refugo, a.data_hora_inicio, a.data_hora_fim,
            o.nome as operador_nome
        FROM apontamentos a
        LEFT JOIN operadores o ON a.operador = o.matricula
        WHERE a.num_op = ? AND a.qtd_boas IS NOT NULL AND a.status = 'Ativo'
        ORDER BY a.data_hora_inicio DESC
    """, (num_op,))
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['OP', 'Etapa', 'Operador', 'Máquina', 'Peças Boas', 'Refugo', 'Início', 'Fim'])
    
    for row in dados:
        writer.writerow([row['num_op'], row['numero_etapa'], 
                         row['operador_nome'] or row['operador'],
                         row['codigo_maquina'], row['qtd_boas'], row['refugo'],
                         row['data_hora_inicio'], row['data_hora_fim']])
    
    output.seek(0)
    nome_arquivo = f"op_{num_op}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=nome_arquivo
    )

# ============================================
# MAIN
# ============================================

# ============================================
# FASE 2/3/4 — NOVAS ROTAS (fila validação, painel saúde, relatórios, modais)
# ============================================

def _supervisor_only():
    return session.get('matricula') and query(
        "SELECT papel FROM operadores WHERE matricula = ?", (session['matricula'],), one=True
    )

FASE_TEMPLATE_BASE = """
<style>
.metric{background:#0d1b2a;border:1px solid #1a2a3a;padding:14px;border-radius:8px;text-align:center;flex:1;min-width:120px}
.metric .v{font-size:26px;font-weight:900;color:#4fc3f7;font-family:'Courier New',monospace}
.metric .l{font-size:10px;color:#78909c;text-transform:uppercase;letter-spacing:1px;margin-top:4px}
.metric.warn .v{color:#f59e0b}
.metric.danger .v{color:#ef4444}
.metric.ok .v{color:#10b981}
.row-flex{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
table.rep{width:100%;border-collapse:collapse;font-size:12px}
table.rep th,table.rep td{padding:8px;border-bottom:1px solid #1a2a3a;text-align:left}
table.rep th{background:#0d1b2a;color:#4fc3f7;text-transform:uppercase;font-size:10px;letter-spacing:1px}
.bar{background:#0d1b2a;height:20px;border-radius:4px;overflow:hidden;position:relative}
.bar>span{display:block;height:100%;background:linear-gradient(90deg,#10b981,#4fc3f7);transition:width .3s}
.badge{padding:3px 8px;border-radius:4px;font-size:10px;font-weight:bold;text-transform:uppercase}
.b-ok{background:#065f46;color:#a7f3d0}.b-pend{background:#4338ca;color:#c7d2fe}.b-late{background:#7f1d1d;color:#fecaca}
.filter-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.filter-row input,.filter-row select{background:#0d1b2a;color:#fff;border:1px solid #1a2a3a;padding:8px;border-radius:4px;font-size:12px;flex:1;min-width:120px}
</style>
"""

FILA_TEMPLATE = FASE_TEMPLATE_BASE + """
<div class="card"><h3>✅ Fila de Validação (Fase 2)</h3>
<p style="font-size:11px;color:#78909c;margin-bottom:10px;">Apontamentos com refugo acima do saldo ou fechados automaticamente no fim de turno.</p>
<form method="POST" action="/supervisor/auto-close" style="margin-bottom:14px;">
  <button type="submit" data-testid="btn-auto-close" class="btn btn-sm btn-warning">🌙 Executar Fechamento Automático (fim de turno)</button>
</form>
<table class="rep">
<tr><th>OP</th><th>Etapa</th><th>Operador</th><th>Boas</th><th>Refugo</th><th>Justificativa</th><th>Auto?</th><th>Ações</th></tr>
{% for a in pendentes %}
<tr>
  <td>{{ a.num_op }}</td><td>{{ a.numero_etapa }}</td><td>{{ a.operador }}</td>
  <td>{{ a.qtd_boas or 0 }}</td><td>{{ a.refugo or 0 }}</td>
  <td style="max-width:200px;font-size:11px;">{{ a.justificativa_sucata or '-' }}</td>
  <td>{% if a.auto_fechado %}<span class="badge b-pend">Auto</span>{% else %}-{% endif %}</td>
  <td>
    <form method="POST" action="/supervisor/aprovar/{{ a.id }}" style="display:inline;">
      <input type="hidden" name="decisao" value="aprovar">
      <button class="btn btn-sm btn-success" data-testid="btn-aprovar-{{ a.id }}">✔️</button>
    </form>
    <form method="POST" action="/supervisor/aprovar/{{ a.id }}" style="display:inline;">
      <input type="hidden" name="decisao" value="rejeitar">
      <button class="btn btn-sm btn-danger" data-testid="btn-rejeitar-{{ a.id }}">✖️</button>
    </form>
  </td>
</tr>
{% else %}<tr><td colspan="8" style="text-align:center;color:#78909c;padding:20px;">Nenhum item pendente 🎉</td></tr>{% endfor %}
</table></div>
"""

SAUDE_TEMPLATE = FASE_TEMPLATE_BASE + """
<div class="card"><h3>❤️ Painel de Saúde do Sistema</h3>
{% if backup_atrasado %}
<div style="background:#7f1d1d;border:1px solid #ef4444;color:#fecaca;padding:10px;border-radius:6px;margin-bottom:14px;font-size:12px;" data-testid="alert-backup">
  ⚠️ <b>ATENÇÃO:</b> Último backup tem mais de 24h! Verifique se o agendamento está ativo.
</div>
{% endif %}
<div class="row-flex">
  <div class="metric ok"><div class="v">{{ tam_db_mb }}</div><div class="l">Tamanho DB (MB)</div></div>
  <div class="metric"><div class="v">{{ apontamentos_abertos }}</div><div class="l">Apontamentos Abertos</div></div>
  <div class="metric warn"><div class="v">{{ pendentes_aprov }}</div><div class="l">Pendentes Aprovação</div></div>
  <div class="metric"><div class="v" style="font-size:14px;">{{ ultimo_backup or '—' }}</div><div class="l">Último Backup</div></div>
  <div class="metric"><div class="v">{{ qtd_backups }}</div><div class="l">Total Backups</div></div>
</div>
<h4>⚙️ Configurações</h4>
<form method="POST" action="/supervisor/saude/config" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
  <label style="font-size:11px;">Fim de turno<input name="shift_end_time" value="{{ shift_end_time }}" data-testid="cfg-shift"></label>
  <label style="font-size:11px;">Comentário parada ≥ (min)<input name="min_stoppage_comment_min" type="number" value="{{ min_stop_min }}" data-testid="cfg-stop-min"></label>
  <label style="font-size:11px;">OEE Meta padrão<input name="oee_target_default" type="number" step="0.01" value="{{ oee_target }}" data-testid="cfg-oee"></label>
  <label style="font-size:11px;">Retenção backup (dias)<input name="backup_retention_days" type="number" value="{{ retencao }}" data-testid="cfg-retencao"></label>
  <button type="submit" class="btn btn-sm btn-primary" style="grid-column:span 2;" data-testid="btn-salvar-config">💾 Salvar Configurações</button>
</form></div>
"""

RELATORIOS_TEMPLATE = FASE_TEMPLATE_BASE + """
<div class="card"><h3>📊 Relatórios (Fase 3)</h3>
<form method="GET" action="/supervisor/relatorios" class="filter-row">
  <input type="date" name="date_from" value="{{ date_from or '' }}" data-testid="filter-date-from">
  <input type="date" name="date_to" value="{{ date_to or '' }}" data-testid="filter-date-to">
  <select name="machine" data-testid="filter-machine"><option value="">Todas máquinas</option>
    {% for m in maquinas %}<option value="{{ m.codigo_maquina }}" {% if m.codigo_maquina==machine %}selected{% endif %}>{{ m.codigo_maquina }} - {{ m.descricao }}</option>{% endfor %}
  </select>
  <button class="btn btn-sm btn-primary" data-testid="btn-filtrar">Filtrar</button>
  <a class="btn btn-sm btn-success" href="/relatorio/csv/mes" data-testid="btn-export-csv">⬇ CSV</a>
  <a class="btn btn-sm btn-secondary" href="/supervisor/export/oee/xlsx" data-testid="btn-export-oee-xlsx">⬇ OEE XLSX</a>
  <a class="btn btn-sm btn-secondary" href="/supervisor/export/oee/pdf" data-testid="btn-export-oee-pdf">⬇ OEE PDF</a>
  <a class="btn btn-sm btn-secondary" href="/supervisor/export/pareto/xlsx" data-testid="btn-export-pareto-xlsx">⬇ Pareto XLSX</a>
  <a class="btn btn-sm btn-secondary" href="/supervisor/export/pareto/pdf" data-testid="btn-export-pareto-pdf">⬇ Pareto PDF</a>
  <a class="btn btn-sm btn-secondary" href="/supervisor/export/aging/xlsx" data-testid="btn-export-aging-xlsx">⬇ Aging XLSX</a>
  <a class="btn btn-sm btn-secondary" href="/supervisor/export/aging/pdf" data-testid="btn-export-aging-pdf">⬇ Aging PDF</a>
  <a class="btn btn-sm btn-secondary" href="/supervisor/export/produtividade/xlsx" data-testid="btn-export-prod-xlsx">⬇ Prod. XLSX</a>
  <a class="btn btn-sm btn-secondary" href="/supervisor/export/produtividade/pdf" data-testid="btn-export-prod-pdf">⬇ Prod. PDF</a>
</form>
<h4>🎯 OEE por Máquina</h4>
<canvas id="chartOee" height="120" data-testid="chart-oee"></canvas>
<table class="rep">
<tr><th>Máquina</th><th>Disponib.</th><th>Perform.</th><th>Qualidade</th><th>OEE</th></tr>
{% for r in oee %}<tr>
  <td>{{ r.maquina }}</td><td>{{ r.availability }}%</td><td>{{ r.performance }}%</td>
  <td>{{ r.quality }}%</td>
  <td><div class="bar"><span style="width:{{ r.oee }}%"></span></div><small style="font-family:monospace">{{ r.oee }}%</small></td>
</tr>{% else %}<tr><td colspan="5" style="text-align:center;color:#78909c;">Sem dados</td></tr>{% endfor %}
</table>
<h4 style="margin-top:20px;">⏱️ Pareto de Paradas</h4>
<canvas id="chartPareto" height="140" data-testid="chart-pareto"></canvas>
<table class="rep">
<tr><th>Motivo</th><th>Min. Perdidos</th><th>% Isolado</th><th>% Acumulado</th></tr>
{% for p in pareto %}<tr>
  <td>{{ p.motivo }}</td><td>{{ p.minutos }}</td>
  <td><div class="bar"><span style="width:{{ p.pct }}%"></span></div><small>{{ p.pct }}%</small></td>
  <td>{{ p.cumulativa }}%</td>
</tr>{% else %}<tr><td colspan="4" style="text-align:center;color:#78909c;">Sem paradas registradas</td></tr>{% endfor %}
</table>
<h4 style="margin-top:20px;">📅 Aging de OPs</h4>
<canvas id="chartAging" height="140" data-testid="chart-aging"></canvas>
<table class="rep">
<tr><th>OP</th><th>Desenho</th><th>Status</th><th>Dias Aberta</th><th>Meta</th><th>Saldo</th><th>%</th></tr>
{% for a in aging %}<tr>
  <td>{{ a.num_op }}</td><td>{{ a.numero_desenho }}</td>
  <td><span class="badge {% if a.overdue %}b-late{% else %}b-ok{% endif %}">{{ a.status_geral }}</span></td>
  <td style="{% if a.overdue %}color:#ef4444;font-weight:bold;{% endif %}">{{ a.age }}</td>
  <td>{{ a.meta_total }}</td><td>{{ a.saldo }}</td>
  <td><div class="bar"><span style="width:{{ a.pct }}%"></span></div></td>
</tr>{% else %}<tr><td colspan="7" style="text-align:center;color:#78909c;">Sem OPs abertas</td></tr>{% endfor %}
</table>
<h4 style="margin-top:20px;">👷 Produtividade por Operador</h4>
<canvas id="chartProd" height="120" data-testid="chart-prod"></canvas>
<table class="rep">
<tr><th>Operador</th><th>Boas</th><th>Refugo</th><th>% Refugo</th><th>Peças/h</th></tr>
{% for p in produtividade %}<tr>
  <td>{{ p.nome }}</td><td>{{ p.boas }}</td><td>{{ p.refugo }}</td>
  <td>{{ p.pct_refugo }}%</td><td>{{ p.pph }}</td>
</tr>{% else %}<tr><td colspan="5" style="text-align:center;color:#78909c;">Sem dados</td></tr>{% endfor %}
</table></div>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
(function(){
  var GRID = {color:'rgba(148,163,184,.15)'};
  var TICKS = {color:'#94a3b8', font:{size:11}};
  var LEGEND = {labels:{color:'#e2e8f0', font:{size:11}}};

  var oee = {{ oee|tojson }};
  if(oee.length){
    new Chart(document.getElementById('chartOee'), {type:'bar',
      data:{labels:oee.map(function(r){return r.maquina}),
        datasets:[
          {label:'Disponibilidade %', data:oee.map(function(r){return r.availability}), backgroundColor:'#4fc3f7'},
          {label:'Performance %', data:oee.map(function(r){return r.performance}), backgroundColor:'#f59e0b'},
          {label:'Qualidade %', data:oee.map(function(r){return r.quality}), backgroundColor:'#10b981'},
          {label:'OEE %', data:oee.map(function(r){return r.oee}), backgroundColor:'#6366f1'}]},
      options:{plugins:{legend:LEGEND, tooltip:{callbacks:{label:function(c){return c.dataset.label+': '+c.parsed.y+'%'}}}},
        scales:{x:{ticks:TICKS, grid:GRID}, y:{ticks:TICKS, grid:GRID, beginAtZero:true, max:100}}}});
  }

  var par = {{ pareto|tojson }};
  if(par.length){
    new Chart(document.getElementById('chartPareto'), {
      data:{labels:par.map(function(p){return p.motivo}),
        datasets:[
          {type:'bar', label:'Minutos parados', data:par.map(function(p){return p.minutos}), backgroundColor:'#ef4444'},
          {type:'line', label:'% acumulado', data:par.map(function(p){return p.cumulativa}),
           borderColor:'#4fc3f7', backgroundColor:'#4fc3f7', tension:0.3, yAxisID:'y1'}]},
      options:{plugins:{legend:LEGEND}, scales:{
        x:{ticks:TICKS, grid:GRID},
        y:{ticks:TICKS, grid:GRID, beginAtZero:true, title:{display:true,text:'min',color:'#94a3b8'}},
        y1:{position:'right', min:0, max:100, ticks:{color:'#4fc3f7'}, grid:{drawOnChartArea:false}, title:{display:true,text:'% acum.',color:'#4fc3f7'}}}}});
  }

  var ag = {{ aging|tojson }};
  if(ag.length){
    new Chart(document.getElementById('chartAging'), {type:'bar',
      data:{labels:ag.map(function(a){return a.num_op}),
        datasets:[{label:'Dias em aberto (vermelho = atrasada)', data:ag.map(function(a){return a.age}),
          backgroundColor:ag.map(function(a){return a.overdue ? '#ef4444' : '#10b981'})}]},
      options:{indexAxis:'y', plugins:{legend:{display:false}},
        scales:{x:{ticks:TICKS, grid:GRID, beginAtZero:true}, y:{ticks:{color:'#e2e8f0', font:{size:10}}, grid:GRID}}}});
  }

  var pr = {{ produtividade|tojson }};
  if(pr.length){
    new Chart(document.getElementById('chartProd'), {type:'bar',
      data:{labels:pr.map(function(p){return p.nome}),
        datasets:[
          {label:'Peças/h', data:pr.map(function(p){return p.pph}), backgroundColor:'#4fc3f7'},
          {label:'% Refugo', data:pr.map(function(p){return p.pct_refugo}), backgroundColor:'#f59e0b'}]},
      options:{plugins:{legend:LEGEND}, scales:{x:{ticks:TICKS, grid:GRID}, y:{ticks:TICKS, grid:GRID, beginAtZero:true}}}});
  }
})();
</script>
"""

@app.route('/supervisor/fila')
@login_required
def supervisor_fila():
    pendentes = query("""SELECT * FROM apontamentos
        WHERE status_aprovacao = 'pendente' OR auto_fechado = 1
        ORDER BY data_hora_fim DESC LIMIT 200""") or []
    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(FILA_TEMPLATE, pendentes=pendentes),
        aba='fila', session=session)

@app.route('/supervisor/aprovar/<int:aid>', methods=['POST'])
@login_required
def supervisor_aprovar(aid):
    decisao = request.form.get('decisao', 'aprovar')
    apont = query("SELECT * FROM apontamentos WHERE id = ?", (aid,), one=True)
    if not apont:
        flash('Apontamento não encontrado', 'danger'); return redirect(url_for('supervisor_fila'))
    novo_status = 'aprovado' if decisao == 'aprovar' else 'rejeitado'
    query("""UPDATE apontamentos SET status_aprovacao = ?, aprovador = ?, data_hora_aprovacao = CURRENT_TIMESTAMP
             WHERE id = ?""", (novo_status, session.get('matricula'), aid), commit=True)
    # Se aprovou, aplica o desconto no saldo residual
    if decisao == 'aprovar' and apont['qtd_boas']:
        query("UPDATE op_etapas_execucao SET saldo_residual = MAX(0, saldo_residual - ?) WHERE num_op = ? AND numero_etapa = ?",
              (apont['qtd_boas'], apont['num_op'], apont['numero_etapa']), commit=True)
    log_audit(f'aprovacao_{decisao}', 'apontamento', aid)
    flash(f'✅ Apontamento {decisao}do.', 'success')
    return redirect(url_for('supervisor_fila'))

@app.route('/supervisor/auto-close', methods=['POST'])
@login_required
def supervisor_auto_close():
    abertos = query("SELECT id FROM apontamentos WHERE qtd_boas IS NULL AND status = 'Ativo'") or []
    for a in abertos:
        query("""UPDATE apontamentos SET data_hora_fim = CURRENT_TIMESTAMP, status_aprovacao = 'pendente',
                 auto_fechado = 1, qtd_boas = 0, refugo = 0 WHERE id = ?""", (a['id'],), commit=True)
        log_audit('auto_close', 'apontamento', a['id'])
    flash(f'🌙 {len(abertos)} apontamento(s) fechados automaticamente.', 'success')
    return redirect(url_for('supervisor_fila'))

@app.route('/supervisor/saude')
@login_required
def supervisor_saude():
    tam = os.path.getsize(DB_NAME) if os.path.exists(DB_NAME) else 0
    backups = listar_backups()
    ultimo = backups[0] if backups else None
    # Calcula idade do último backup em horas
    backup_atrasado = False
    if ultimo:
        bpath = os.path.join(BACKUP_DIR, ultimo if isinstance(ultimo, str) else ultimo.get('arquivo',''))
        if os.path.exists(bpath):
            backup_atrasado = ((time.time() - os.path.getmtime(bpath)) / 3600) > 24
    abertos = query("SELECT COUNT(*) as c FROM apontamentos WHERE qtd_boas IS NULL AND status='Ativo'", one=True)
    pend = query("SELECT COUNT(*) as c FROM apontamentos WHERE status_aprovacao='pendente'", one=True)
    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(SAUDE_TEMPLATE,
            tam_db_mb=round(tam/1024/1024, 2),
            ultimo_backup=ultimo, qtd_backups=len(backups),
            apontamentos_abertos=abertos['c'] if abertos else 0,
            pendentes_aprov=pend['c'] if pend else 0,
            shift_end_time=get_config('shift_end_time','22:00'),
            min_stop_min=get_config('min_stoppage_comment_min','15'),
            oee_target=get_config('oee_target_default','0.75'),
            retencao=get_config('backup_retention_days','30'),
            backup_atrasado=backup_atrasado),
        aba='saude', session=session)

@app.route('/supervisor/saude/config', methods=['POST'])
@login_required
def supervisor_saude_config():
    for k in ['shift_end_time','min_stoppage_comment_min','oee_target_default','backup_retention_days']:
        v = request.form.get(k)
        if v is not None: set_config(k, v)
    log_audit('update_config', 'configuracoes', 'global')
    flash('💾 Configurações salvas.', 'success')
    return redirect(url_for('supervisor_saude'))

@app.route('/supervisor/relatorios')
@login_required
def supervisor_relatorios():
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    machine = request.args.get('machine', '')
    where = ["a.qtd_boas IS NOT NULL", "a.status='Ativo'"]
    params = []
    if date_from: where.append("DATE(a.data_hora_inicio) >= ?"); params.append(date_from)
    if date_to: where.append("DATE(a.data_hora_inicio) <= ?"); params.append(date_to)
    if machine: where.append("a.codigo_maquina = ?"); params.append(machine)
    where_sql = " AND ".join(where)

    # OEE por máquina — perf via ciclo padrão dos roteiros
    oee_rows = query(f"""
        SELECT a.codigo_maquina as maquina,
               SUM(a.qtd_boas) as boas, SUM(a.refugo) as refugo,
               SUM((julianday(a.data_hora_fim)-julianday(a.data_hora_inicio))*86400) as run_s,
               AVG(COALESCE(r.tempo_ciclo_minutos,1)*60) as ciclo_s
        FROM apontamentos a
        LEFT JOIN op_etapas_execucao e ON e.num_op=a.num_op AND e.numero_etapa=a.numero_etapa
        LEFT JOIN roteiros_padrao r ON r.numero_desenho=(SELECT numero_desenho FROM ordens_producao WHERE num_op=a.num_op)
             AND r.numero_etapa=a.numero_etapa
        WHERE {where_sql}
        GROUP BY a.codigo_maquina""", tuple(params)) or []
    oee_list = []
    for r in oee_rows:
        run_s = r['run_s'] or 0; boas = r['boas'] or 0; refugo = r['refugo'] or 0
        ciclo = r['ciclo_s'] or 60
        ideal = (boas + refugo) * ciclo
        # Paradas do periodo (todos operadores)
        par = query("SELECT SUM((julianday(COALESCE(data_hora_fim,datetime('now')))-julianday(data_hora_inicio))*86400) as s FROM paradas_maquina WHERE 1=1"
                    + (" AND DATE(data_hora_inicio) >= ?" if date_from else "")
                    + (" AND DATE(data_hora_inicio) <= ?" if date_to else ""), tuple([p for p in [date_from, date_to] if p]), one=True)
        stop_s = par['s'] if par and par['s'] else 0
        total = run_s + stop_s or 1
        availability = run_s/total
        performance = min(1.0, ideal/run_s) if run_s>0 else 0
        quality = boas/((boas+refugo) or 1)
        oee_list.append({'maquina': r['maquina'],
            'availability': round(availability*100,1),
            'performance': round(performance*100,1),
            'quality': round(quality*100,1),
            'oee': round(availability*performance*quality*100,1)})

    # Pareto paradas
    par_rows = query("""SELECT motivo,
        SUM((julianday(COALESCE(data_hora_fim,datetime('now')))-julianday(data_hora_inicio))*1440) as min
        FROM paradas_maquina GROUP BY motivo ORDER BY min DESC""") or []
    total_p = sum(r['min'] or 0 for r in par_rows) or 1
    pareto = []; acc = 0
    for r in par_rows:
        m = round(r['min'] or 0, 1); acc += m
        pareto.append({'motivo': r['motivo'] or 'Sem motivo', 'minutos': m,
                       'pct': round(m/total_p*100,1), 'cumulativa': round(acc/total_p*100,1)})

    # Aging OPs
    aging_rows = query("""SELECT o.*, COALESCE(SUM(e.saldo_residual),0) as saldo
        FROM ordens_producao o LEFT JOIN op_etapas_execucao e ON o.num_op=e.num_op
        WHERE o.status_geral != 'Concluída' GROUP BY o.num_op ORDER BY o.data_criacao""") or []
    aging = []
    for o in aging_rows:
        try: age = (datetime.now().date() - datetime.strptime(o['data_criacao'],'%Y-%m-%d').date()).days
        except Exception: age = 0
        pct = 100 - (o['saldo']*100/o['meta_total']) if o['meta_total'] else 0
        aging.append({'num_op': o['num_op'], 'numero_desenho': o['numero_desenho'],
                      'status_geral': o['status_geral'], 'age': age, 'meta_total': o['meta_total'],
                      'saldo': o['saldo'], 'pct': round(pct,1), 'overdue': age > 7})

    # Produtividade por operador
    prod_rows = query(f"""SELECT a.operador, o.nome,
        SUM(a.qtd_boas) as boas, SUM(a.refugo) as refugo,
        SUM((julianday(a.data_hora_fim)-julianday(a.data_hora_inicio))*24) as horas
        FROM apontamentos a LEFT JOIN operadores o ON o.matricula=a.operador
        WHERE {where_sql} GROUP BY a.operador""", tuple(params)) or []
    produtividade = []
    for r in prod_rows:
        boas = r['boas'] or 0; refugo = r['refugo'] or 0; horas = r['horas'] or 0.01
        produtividade.append({'nome': r['nome'] or r['operador'], 'boas': boas, 'refugo': refugo,
            'pct_refugo': round(refugo/((boas+refugo) or 1)*100,1),
            'pph': round((boas+refugo)/horas, 1)})

    maquinas = query("SELECT * FROM maquinas ORDER BY codigo_maquina") or []
    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(RELATORIOS_TEMPLATE,
            oee=oee_list, pareto=pareto, aging=aging, produtividade=produtividade,
            maquinas=maquinas, date_from=date_from, date_to=date_to, machine=machine),
        aba='rel', session=session)

@app.route('/supervisor/operador/reset/<matricula>', methods=['POST'])
@login_required
def supervisor_reset_senha(matricula):
    nova = request.form.get('nova_senha', '').strip()
    if len(nova) < 4:
        flash('❌ Senha deve ter no mínimo 4 caracteres', 'danger')
        return redirect(url_for('supervisor'))
    query("UPDATE operadores SET senha = ? WHERE matricula = ?", (gerar_hash_senha(nova), matricula), commit=True)
    log_audit('reset_senha', 'operador', matricula)
    flash(f'🔑 Senha do operador {matricula} redefinida.', 'success')
    return redirect(url_for('supervisor'))

@app.route('/supervisor/operador/excluir/<matricula>', methods=['POST'])
@login_required
def supervisor_operador_excluir(matricula):
    query("UPDATE operadores SET ativo = 0 WHERE matricula = ?", (matricula,), commit=True)
    log_audit('soft_delete', 'operador', matricula)
    flash('🗑️ Operador desativado.', 'info')
    return redirect(url_for('supervisor'))

@app.route('/supervisor/operador/editar/<matricula>', methods=['POST'])
@login_required
def supervisor_operador_editar(matricula):
    nome = request.form.get('nome', '').strip()
    turno = request.form.get('turno', '').strip()
    papel = request.form.get('papel', 'operador').strip()
    if not nome:
        flash('Nome obrigatório', 'danger'); return redirect(url_for('supervisor_gerenciar'))
    antigo = query("SELECT * FROM operadores WHERE matricula = ?", (matricula,), one=True)
    query("UPDATE operadores SET nome = ?, turno = ?, papel = ? WHERE matricula = ?",
          (nome, turno, papel, matricula), commit=True)
    mudancas = []
    if antigo:
        if antigo['nome'] != nome: mudancas.append(f"nome:{antigo['nome']}→{nome}")
        if antigo['turno'] != turno: mudancas.append(f"turno:{antigo['turno']}→{turno}")
        if (antigo['papel'] or '') != papel: mudancas.append(f"papel:{antigo['papel']}→{papel}")
    log_audit('editar', 'operador', matricula, '; '.join(mudancas) or 'sem mudanças')
    flash('✏️ Operador atualizado.', 'success')
    return redirect(url_for('supervisor_gerenciar'))

@app.route('/supervisor/maquina/editar/<codigo>', methods=['POST'])
@login_required
def supervisor_maquina_editar(codigo):
    descricao = request.form.get('descricao', '').strip()
    if not descricao:
        flash('Descrição obrigatória', 'danger'); return redirect(url_for('supervisor_gerenciar'))
    antigo = query("SELECT * FROM maquinas WHERE codigo_maquina = ?", (codigo,), one=True)
    query("UPDATE maquinas SET descricao = ? WHERE codigo_maquina = ?", (descricao, codigo), commit=True)
    mud = f"desc:{antigo['descricao']}→{descricao}" if antigo and antigo['descricao'] != descricao else ''
    log_audit('editar', 'maquina', codigo, mud)
    flash('✏️ Máquina atualizada.', 'success')
    return redirect(url_for('supervisor_gerenciar'))

@app.route('/supervisor/desenho/editar/<numero>', methods=['POST'])
@login_required
def supervisor_desenho_editar(numero):
    nome_peca = request.form.get('nome_peca', '').strip()
    if not nome_peca:
        flash('Nome obrigatório', 'danger'); return redirect(url_for('supervisor_gerenciar'))
    antigo = query("SELECT * FROM desenhos WHERE numero_desenho = ?", (numero,), one=True)
    query("UPDATE desenhos SET nome_peca = ? WHERE numero_desenho = ?", (nome_peca, numero), commit=True)
    mud = f"nome:{antigo['nome_peca']}→{nome_peca}" if antigo and antigo['nome_peca'] != nome_peca else ''
    log_audit('editar', 'desenho', numero, mud)
    flash('✏️ Desenho atualizado.', 'success')
    return redirect(url_for('supervisor_gerenciar'))

@app.route('/supervisor/auditoria')
@login_required
def supervisor_auditoria():
    page = max(1, int(request.args.get('page', 1)))
    per = 50
    total_row = query("SELECT COUNT(*) as c FROM audit_log", one=True)
    total = total_row['c'] if total_row else 0
    total_pages = max(1, (total + per - 1) // per)
    rows = query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (per, (page-1)*per)) or []
    html = FASE_TEMPLATE_BASE + """
    <div class="card"><h3>📜 Auditoria de Mudanças</h3>
    <p style="font-size:11px;color:#78909c;">Total: {{ total }} · Página {{ page }}/{{ total_pages }}</p>
    <table class="rep"><tr><th>Quando</th><th>Usuário</th><th>Ação</th><th>Entidade</th><th>ID</th><th>Detalhes</th></tr>
    {% for r in rows %}<tr>
      <td style="font-size:11px;">{{ r.data_hora }}</td><td>{{ r.usuario }}</td>
      <td><span class="badge b-pend">{{ r.acao }}</span></td><td>{{ r.entidade }}</td>
      <td>{{ r.entidade_id }}</td><td style="font-size:11px;">{{ r.detalhes or '-' }}</td>
    </tr>{% endfor %}</table>
    <div class="pagination">
      {% if page > 1 %}<a href="?page={{ page-1 }}">‹</a>{% endif %}
      <span class="cur">{{ page }}</span>
      {% if page < total_pages %}<a href="?page={{ page+1 }}">›</a>{% endif %}
    </div></div>"""
    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(html, rows=rows, total=total, page=page, total_pages=total_pages),
        aba='ger', session=session)

@app.route('/supervisor/op/cancelar/<num_op>', methods=['POST'])
@login_required
def supervisor_op_cancelar(num_op):
    motivo = request.form.get('motivo','').strip()
    if not motivo:
        flash('Justificativa obrigatória.', 'danger'); return redirect(url_for('pcp'))
    query("UPDATE ordens_producao SET status_geral='Cancelada' WHERE num_op = ?", (num_op,), commit=True)
    log_audit('cancelar_op', 'ordem', num_op, motivo)
    flash(f'❌ OP {num_op} cancelada.', 'warning')
    return redirect(url_for('pcp'))

@app.route('/supervisor/op/reabrir/<num_op>', methods=['POST'])
@login_required
def supervisor_op_reabrir(num_op):
    motivo = request.form.get('motivo','').strip()
    if not motivo:
        flash('Justificativa obrigatória.', 'danger'); return redirect(url_for('pcp'))
    query("UPDATE ordens_producao SET status_geral='Em Andamento' WHERE num_op = ? AND status_geral IN ('Concluída','Cancelada')",
          (num_op,), commit=True)
    log_audit('reabrir_op', 'ordem', num_op, motivo)
    flash(f'🔄 OP {num_op} reaberta.', 'success')
    return redirect(url_for('pcp'))


# ============================================
# FASE 4.5 — GERENCIAR (modais) + HISTÓRICO (busca + paginação)
# ============================================

MODAL_CSS = """
<style>
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:1000;align-items:center;justify-content:center;padding:20px}
.modal-bg.on{display:flex}
.modal-box{background:#111927;border:1px solid #1a2a3a;border-radius:12px;padding:22px;max-width:520px;width:100%;max-height:90vh;overflow-y:auto}
.modal-box h3{color:#4fc3f7;margin-bottom:14px;font-size:16px}
.modal-close{float:right;background:transparent;border:none;color:#ef4444;font-size:20px;cursor:pointer}
.list-row{display:flex;align-items:center;justify-content:space-between;padding:10px;background:#0d1b2a;border:1px solid #1a2a3a;border-radius:6px;margin-bottom:6px;gap:8px;flex-wrap:wrap}
.list-row .info{flex:1;min-width:150px}
.list-row .info b{color:#fff}
.list-row .info small{color:#78909c;display:block;font-size:11px}
.act-btns{display:flex;gap:5px}
.pagination{display:flex;gap:5px;justify-content:center;margin-top:14px;flex-wrap:wrap}
.pagination a,.pagination span{padding:6px 10px;background:#1a3a4a;color:#fff;border-radius:4px;text-decoration:none;font-size:12px;font-family:monospace}
.pagination .cur{background:#2196F3;font-weight:bold}
</style>
<script>
function openM(id){document.getElementById(id).classList.add('on')}
function closeM(id){document.getElementById(id).classList.remove('on')}
function fillEdit(id, data){for(var k in data){var el=document.querySelector('#'+id+' [name="'+k+'"]');if(el)el.value=data[k]||''}openM(id)}
</script>
"""

GERENCIAR_TEMPLATE = FASE_TEMPLATE_BASE + MODAL_CSS + """
<div class="card"><h3>⚙️ Gerenciar Cadastros</h3>
<form method="GET" class="filter-row">
  <input name="q" value="{{ q }}" placeholder="🔍 Buscar (nome, código, matrícula)" data-testid="ger-search">
  <button class="btn btn-sm btn-primary" data-testid="btn-search-ger">Buscar</button>
  <a class="btn btn-sm btn-warning" href="/supervisor/gerenciar" data-testid="btn-clear-ger">✖ Limpar</a>
</form>

<h4>👥 Operadores <button class="btn btn-sm btn-success" onclick="openM('modalOp')" data-testid="btn-add-op">+ Novo</button></h4>
{% for o in operadores %}
<div class="list-row">
  <div class="info"><b>{{ o.matricula }}</b> — {{ o.nome }} <small>Turno {{ o.turno }} {% if o.papel %}· {{ o.papel }}{% endif %}</small></div>
  <div class="act-btns">
    <button class="btn btn-sm btn-primary" data-testid="btn-edit-op-{{ o.matricula }}"
      onclick="fillEdit('modalEditOp',{matricula:'{{ o.matricula }}',nome:'{{ o.nome }}',turno:'{{ o.turno }}',papel:'{{ o.papel or "operador" }}'})">✏️</button>
    <button class="btn btn-sm btn-warning" data-testid="btn-reset-{{ o.matricula }}"
      onclick="fillEdit('modalReset',{matricula:'{{ o.matricula }}',nome:'{{ o.nome }}'})">🔑 Reset</button>
    <form method="POST" action="/supervisor/operador/excluir/{{ o.matricula }}" style="display:inline"
      onsubmit="return confirm('Desativar {{ o.matricula }}?')">
      <button class="btn btn-sm btn-danger" data-testid="btn-del-op-{{ o.matricula }}">🗑️</button>
    </form>
  </div>
</div>
{% else %}<p style="color:#78909c;font-size:12px;">Nenhum operador.</p>{% endfor %}

<h4 style="margin-top:20px;">🏭 Máquinas <button class="btn btn-sm btn-success" onclick="openM('modalMaq')" data-testid="btn-add-maq">+ Nova</button></h4>
{% for m in maquinas %}
<div class="list-row">
  <div class="info"><b>{{ m.codigo_maquina }}</b> — {{ m.descricao }} <small>{{ m.status_operacional }}</small></div>
  <div class="act-btns">
    <button class="btn btn-sm btn-primary" data-testid="btn-edit-maq-{{ m.codigo_maquina }}"
      onclick="fillEdit('modalEditMaq',{codigo:'{{ m.codigo_maquina }}',descricao:'{{ m.descricao }}'})">✏️</button>
  </div>
</div>
{% else %}<p style="color:#78909c;font-size:12px;">Nenhuma máquina.</p>{% endfor %}

<h4 style="margin-top:20px;">📐 Desenhos <button class="btn btn-sm btn-success" onclick="openM('modalDes')" data-testid="btn-add-des">+ Novo</button></h4>
{% for d in desenhos %}
<div class="list-row">
  <div class="info"><b>{{ d.numero_desenho }}</b> — {{ d.nome_peca }}</div>
  <div class="act-btns">
    <button class="btn btn-sm btn-primary" data-testid="btn-edit-des-{{ d.numero_desenho }}"
      onclick="fillEdit('modalEditDes',{numero:'{{ d.numero_desenho }}',nome_peca:'{{ d.nome_peca }}'})">✏️</button>
  </div>
</div>
{% else %}<p style="color:#78909c;font-size:12px;">Nenhum desenho.</p>{% endfor %}
<p style="margin-top:16px;"><a class="btn btn-sm btn-secondary" href="/supervisor/auditoria" data-testid="btn-audit">📜 Ver Auditoria de Mudanças</a></p>

<h4 style="margin-top:20px;">📎 Anexos de Desenho (por OP)</h4>
<p style="font-size:11px;color:#78909c;margin-bottom:8px;">PDF ou imagem do desenho técnico. Aparece na tela do operador.</p>
{% for op in ops_anexo %}
<div class="list-row">
  <div class="info"><b>OP {{ op.num_op }}</b>
    <small>{% if op.nome_arquivo %}📎 {{ op.nome_arquivo }} · <a href="/anexo/{{ op.num_op }}" target="_blank" style="color:#4fc3f7;">visualizar</a>{% else %}sem anexo{% endif %}</small></div>
  <form method="POST" action="/supervisor/op/{{ op.num_op }}/anexo" enctype="multipart/form-data" style="display:flex;gap:5px;align-items:center;" data-testid="form-anexo-{{ op.num_op }}">
    <input type="file" name="arquivo" accept=".pdf,.png,.jpg,.jpeg,.webp,.gif" required style="font-size:11px;color:#78909c;max-width:180px;" data-testid="input-anexo-{{ op.num_op }}">
    <button class="btn btn-sm btn-primary" data-testid="btn-upload-{{ op.num_op }}">⬆️</button>
  </form>
</div>
{% else %}<p style="color:#78909c;font-size:12px;">Nenhuma OP aberta.</p>{% endfor %}
</div>

<!-- Modal Editar Operador -->
<div class="modal-bg" id="modalEditOp"><div class="modal-box">
  <button class="modal-close" onclick="closeM('modalEditOp')" type="button">×</button>
  <h3>✏️ Editar Operador</h3>
  <form method="POST" id="formEditOp">
    <div class="form-group"><label>Matrícula</label><input name="matricula" readonly style="opacity:.6"></div>
    <div class="form-group"><label>Nome</label><input name="nome" required data-testid="edit-op-nome"></div>
    <div class="form-group"><label>Turno</label>
      <select name="turno"><option>Turno A</option><option>Turno B</option><option>Turno C</option></select></div>
    <div class="form-group"><label>Papel</label>
      <select name="papel"><option value="operador">operador</option><option value="pcp">pcp</option><option value="supervisor">supervisor</option></select></div>
    <button class="btn btn-sm btn-success" data-testid="btn-save-edit-op">Salvar</button>
  </form>
</div></div>
<script>
document.getElementById('formEditOp').addEventListener('submit',function(e){
  e.preventDefault();
  var m=this.matricula.value;
  this.action='/supervisor/operador/editar/'+m;
  this.submit();
});
</script>

<!-- Modal Editar Máquina -->
<div class="modal-bg" id="modalEditMaq"><div class="modal-box">
  <button class="modal-close" onclick="closeM('modalEditMaq')" type="button">×</button>
  <h3>✏️ Editar Máquina</h3>
  <form method="POST" id="formEditMaq">
    <div class="form-group"><label>Código</label><input name="codigo" readonly style="opacity:.6"></div>
    <div class="form-group"><label>Descrição</label><input name="descricao" required data-testid="edit-maq-desc"></div>
    <button class="btn btn-sm btn-success" data-testid="btn-save-edit-maq">Salvar</button>
  </form>
</div></div>
<script>
document.getElementById('formEditMaq').addEventListener('submit',function(e){
  e.preventDefault();
  var c=this.codigo.value;
  this.action='/supervisor/maquina/editar/'+encodeURIComponent(c);
  this.submit();
});
</script>

<!-- Modal Editar Desenho -->
<div class="modal-bg" id="modalEditDes"><div class="modal-box">
  <button class="modal-close" onclick="closeM('modalEditDes')" type="button">×</button>
  <h3>✏️ Editar Desenho</h3>
  <form method="POST" id="formEditDes">
    <div class="form-group"><label>Número</label><input name="numero" readonly style="opacity:.6"></div>
    <div class="form-group"><label>Nome da Peça</label><input name="nome_peca" required data-testid="edit-des-nome"></div>
    <button class="btn btn-sm btn-success" data-testid="btn-save-edit-des">Salvar</button>
  </form>
</div></div>
<script>
document.getElementById('formEditDes').addEventListener('submit',function(e){
  e.preventDefault();
  var n=this.numero.value;
  this.action='/supervisor/desenho/editar/'+encodeURIComponent(n);
  this.submit();
});
</script>

<!-- Modal Operador -->
<div class="modal-bg" id="modalOp"><div class="modal-box">
  <button class="modal-close" onclick="closeM('modalOp')" type="button">×</button>
  <h3>➕ Novo Operador</h3>
  <form method="POST" action="/supervisor/operador">
    <div class="form-group"><label>Matrícula</label><input name="matricula" required data-testid="new-op-mat"></div>
    <div class="form-group"><label>Nome</label><input name="nome" required data-testid="new-op-nome"></div>
    <div class="form-group"><label>Turno</label>
      <select name="turno"><option>Turno A</option><option>Turno B</option><option>Turno C</option></select></div>
    <div class="form-group"><label>Senha inicial</label><input name="senha" required minlength="4" data-testid="new-op-senha"></div>
    <button class="btn btn-sm btn-success" data-testid="btn-save-op">Salvar</button>
  </form>
</div></div>

<!-- Modal Reset Senha -->
<div class="modal-bg" id="modalReset"><div class="modal-box">
  <button class="modal-close" onclick="closeM('modalReset')" type="button">×</button>
  <h3>🔑 Redefinir Senha</h3>
  <form method="POST" id="formReset">
    <div class="form-group"><label>Operador</label><input name="nome" readonly style="opacity:.7"></div>
    <input type="hidden" name="matricula">
    <div class="form-group"><label>Nova senha (min. 4)</label><input name="nova_senha" required minlength="4" data-testid="reset-senha-input"></div>
    <button class="btn btn-sm btn-success" data-testid="btn-save-reset">Redefinir</button>
  </form>
</div></div>
<script>
document.getElementById('formReset').addEventListener('submit',function(e){
  e.preventDefault();
  var mat=this.matricula.value;
  this.action='/supervisor/operador/reset/'+mat;
  this.submit();
});
</script>

<!-- Modal Máquina -->
<div class="modal-bg" id="modalMaq"><div class="modal-box">
  <button class="modal-close" onclick="closeM('modalMaq')" type="button">×</button>
  <h3>➕ Nova Máquina</h3>
  <form method="POST" action="/supervisor/maquina">
    <div class="form-group"><label>Código</label><input name="codigo_maquina" required data-testid="new-maq-cod"></div>
    <div class="form-group"><label>Descrição</label><input name="descricao" required data-testid="new-maq-desc"></div>
    <button class="btn btn-sm btn-success" data-testid="btn-save-maq">Salvar</button>
  </form>
</div></div>

<!-- Modal Desenho -->
<div class="modal-bg" id="modalDes"><div class="modal-box">
  <button class="modal-close" onclick="closeM('modalDes')" type="button">×</button>
  <h3>➕ Novo Desenho</h3>
  <form method="POST" action="/supervisor/desenho">
    <div class="form-group"><label>Número</label><input name="numero_desenho" required data-testid="new-des-num"></div>
    <div class="form-group"><label>Nome da Peça</label><input name="nome_peca" required data-testid="new-des-nome"></div>
    <button class="btn btn-sm btn-success" data-testid="btn-save-des">Salvar</button>
  </form>
</div></div>
"""

HISTORICO_TEMPLATE = FASE_TEMPLATE_BASE + """
<div class="card"><h3>📚 Histórico de Apontamentos</h3>
<form method="GET" class="filter-row">
  <input name="q" value="{{ q }}" placeholder="🔍 OP, operador, máquina" data-testid="hist-search">
  <input type="date" name="date_from" value="{{ date_from }}" data-testid="hist-date-from">
  <input type="date" name="date_to" value="{{ date_to }}" data-testid="hist-date-to">
  <select name="status" data-testid="hist-status">
    <option value="">Todos status</option>
    <option value="ok" {% if status=='ok' %}selected{% endif %}>OK</option>
    <option value="pendente" {% if status=='pendente' %}selected{% endif %}>Pendente</option>
    <option value="aprovado" {% if status=='aprovado' %}selected{% endif %}>Aprovado</option>
    <option value="rejeitado" {% if status=='rejeitado' %}selected{% endif %}>Rejeitado</option>
  </select>
  <button class="btn btn-sm btn-primary" data-testid="btn-hist-filter">Filtrar</button>
</form>
<p style="font-size:11px;color:#78909c;">Total: {{ total }} · Página {{ page }}/{{ total_pages }}</p>
<table class="rep">
<tr><th>ID</th><th>OP</th><th>Etapa</th><th>Operador</th><th>Máquina</th><th>Boas</th><th>Refugo</th><th>Início</th><th>Status</th></tr>
{% for r in rows %}
<tr>
  <td>{{ r.id }}</td><td>{{ r.num_op }}</td><td>{{ r.numero_etapa }}</td>
  <td>{{ r.operador }}</td><td>{{ r.codigo_maquina }}</td>
  <td>{{ r.qtd_boas or '-' }}</td><td>{{ r.refugo or 0 }}</td>
  <td style="font-size:11px;">{{ (r.data_hora_inicio or '')[:16] }}</td>
  <td><span class="badge {% if r.status_aprovacao=='pendente' %}b-pend{% elif r.status_aprovacao=='rejeitado' %}b-late{% else %}b-ok{% endif %}">{{ r.status_aprovacao or 'ok' }}</span></td>
</tr>
{% else %}<tr><td colspan="9" style="text-align:center;color:#78909c;padding:20px;">Nenhum registro</td></tr>{% endfor %}
</table>
<div class="pagination">
  {% if page > 1 %}<a href="?{{ qs }}&page={{ page-1 }}" data-testid="pag-prev">‹ Anterior</a>{% endif %}
  <span class="cur">{{ page }}</span>
  {% if page < total_pages %}<a href="?{{ qs }}&page={{ page+1 }}" data-testid="pag-next">Próxima ›</a>{% endif %}
</div>
</div>
"""

@app.route('/supervisor/gerenciar')
@login_required
def supervisor_gerenciar():
    q = request.args.get('q', '').strip()
    like = f"%{q}%"
    if q:
        operadores = query("SELECT * FROM operadores WHERE ativo=1 AND (matricula LIKE ? OR nome LIKE ?) ORDER BY nome", (like, like)) or []
        maquinas = query("SELECT * FROM maquinas WHERE codigo_maquina LIKE ? OR descricao LIKE ? ORDER BY codigo_maquina", (like, like)) or []
        desenhos = query("SELECT * FROM desenhos WHERE numero_desenho LIKE ? OR nome_peca LIKE ? ORDER BY numero_desenho", (like, like)) or []
    else:
        operadores = query("SELECT * FROM operadores WHERE ativo=1 ORDER BY nome") or []
        maquinas = query("SELECT * FROM maquinas ORDER BY codigo_maquina") or []
        desenhos = query("SELECT * FROM desenhos ORDER BY numero_desenho") or []
    ops_anexo = query("""SELECT o.num_op, a.nome_arquivo FROM ordens_producao o
        LEFT JOIN op_anexos a ON a.num_op = o.num_op
        WHERE o.status_geral NOT IN ('Concluída','Cancelada') ORDER BY o.num_op""") or []
    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(GERENCIAR_TEMPLATE,
            operadores=operadores, maquinas=maquinas, desenhos=desenhos, q=q,
            ops_anexo=ops_anexo),
        aba='ger', session=session)

@app.route('/supervisor/historico')
@login_required
def supervisor_historico():
    q = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    status = request.args.get('status', '')
    try: page = max(1, int(request.args.get('page', 1)))
    except Exception: page = 1
    per = 25
    where = ["1=1"]; params = []
    if q:
        where.append("(a.num_op LIKE ? OR a.operador LIKE ? OR a.codigo_maquina LIKE ?)")
        params += [f"%{q}%"]*3
    if date_from: where.append("DATE(a.data_hora_inicio) >= ?"); params.append(date_from)
    if date_to: where.append("DATE(a.data_hora_inicio) <= ?"); params.append(date_to)
    if status: where.append("COALESCE(a.status_aprovacao,'ok') = ?"); params.append(status)
    wsql = " AND ".join(where)
    total_row = query(f"SELECT COUNT(*) as c FROM apontamentos a WHERE {wsql}", tuple(params), one=True)
    total = total_row['c'] if total_row else 0
    total_pages = max(1, (total + per - 1) // per)
    rows = query(f"""SELECT * FROM apontamentos a WHERE {wsql}
        ORDER BY a.data_hora_inicio DESC LIMIT ? OFFSET ?""",
        tuple(params + [per, (page-1)*per])) or []
    from urllib.parse import urlencode
    qs = urlencode({'q':q,'date_from':date_from,'date_to':date_to,'status':status})
    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(HISTORICO_TEMPLATE,
            rows=rows, total=total, page=page, total_pages=total_pages,
            q=q, date_from=date_from, date_to=date_to, status=status, qs=qs),
        aba='hist', session=session)


# ============================================
# OBJECT STORAGE (desenhos técnicos anexados às OPs)
# ============================================
STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
_storage_key_cache = {"key": None}

def _env_key(name):
    """Lê do ambiente ou do .env do backend (sem editar o arquivo)."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        with open(os.path.join(BASE_DIR, ".env")) as fh:
            for line in fh:
                if line.strip().startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return None

def init_storage(force=False):
    if _storage_key_cache["key"] and not force:
        return _storage_key_cache["key"]
    key = _env_key("EMERGENT_LLM_KEY")
    if not key:
        print("[storage] EMERGENT_LLM_KEY ausente")
        return None
    try:
        r = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": key}, timeout=30)
        r.raise_for_status()
        _storage_key_cache["key"] = r.json()["storage_key"]
        return _storage_key_cache["key"]
    except Exception as e:
        print(f"[storage] init falhou: {e}")
        return None

def put_object(path, data, content_type):
    k = init_storage()
    if not k:
        raise Exception("Armazenamento indisponível")
    r = requests.put(f"{STORAGE_URL}/objects/{path}",
                     headers={"X-Storage-Key": k, "Content-Type": content_type},
                     data=data, timeout=120)
    if r.status_code in (403, 404):
        k = init_storage(force=True)
        r = requests.put(f"{STORAGE_URL}/objects/{path}",
                         headers={"X-Storage-Key": k, "Content-Type": content_type},
                         data=data, timeout=120)
    r.raise_for_status()
    return r.json() if r.content else {"path": path, "size": len(data)}

def get_object(path):
    k = init_storage()
    if not k:
        raise Exception("Armazenamento indisponível")
    r = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": k}, timeout=60)
    if r.status_code in (403, 404):
        k = init_storage(force=True)
        r = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": k}, timeout=60)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "application/octet-stream")


# ============================================
# BACKUP AGENDADO + EXPORT EXCEL/PDF
# ============================================
_last_backup_check = [0.0]

@app.before_request
def check_scheduled_backup():
    """Roda backup no início de cada turno se o último tiver > 24h (limite de 1 check / 5 min)."""
    import time as _t
    if request.path.startswith('/static'):
        return
    now = _t.time()
    if now - _last_backup_check[0] < 300:
        return
    _last_backup_check[0] = now
    try:
        backups = listar_backups()
        if backups:
            fname = backups[0] if isinstance(backups[0], str) else backups[0].get('arquivo', '')
            path = os.path.join(BACKUP_DIR, fname)
            if os.path.exists(path):
                age_h = (time.time() - os.path.getmtime(path)) / 3600
                if age_h < 24:
                    return
        criar_backup()
        log_audit('backup_agendado', 'sistema', 'backup', 'auto >24h')
    except Exception as e:
        print(f"[backup agendado] {e}")

def _report_data(kind):
    """Reaproveita a lógica dos relatórios para export."""
    if kind == 'oee':
        return query("""SELECT a.codigo_maquina as maquina,
            SUM(a.qtd_boas) as boas, SUM(a.refugo) as refugo,
            ROUND(AVG(COALESCE(r.tempo_ciclo_minutos,1)*60),1) as ciclo_s
            FROM apontamentos a
            LEFT JOIN roteiros_padrao r ON r.numero_etapa=a.numero_etapa
            WHERE a.qtd_boas IS NOT NULL GROUP BY a.codigo_maquina""") or []
    if kind == 'pareto':
        return query("""SELECT motivo,
            ROUND(SUM((julianday(COALESCE(data_hora_fim,datetime('now')))-julianday(data_hora_inicio))*1440),1) as minutos
            FROM paradas_maquina GROUP BY motivo ORDER BY minutos DESC""") or []
    if kind == 'aging':
        return query("""SELECT o.num_op, o.numero_desenho, o.status_geral, o.meta_total,
            COALESCE(SUM(e.saldo_residual),0) as saldo, o.data_criacao
            FROM ordens_producao o LEFT JOIN op_etapas_execucao e ON o.num_op=e.num_op
            WHERE o.status_geral != 'Concluída' GROUP BY o.num_op""") or []
    if kind == 'produtividade':
        return query("""SELECT a.operador, o.nome, SUM(a.qtd_boas) as boas, SUM(a.refugo) as refugo,
            ROUND(SUM((julianday(a.data_hora_fim)-julianday(a.data_hora_inicio))*24),2) as horas
            FROM apontamentos a LEFT JOIN operadores o ON o.matricula=a.operador
            WHERE a.qtd_boas IS NOT NULL GROUP BY a.operador""") or []
    return []

@app.route('/supervisor/export/<kind>/<fmt>')
@login_required
def supervisor_export(kind, fmt):
    data = _report_data(kind)
    if not data:
        flash('Sem dados para exportar.', 'info'); return redirect(url_for('supervisor_relatorios'))
    headers = list(data[0].keys())
    if fmt == 'xlsx':
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.title = kind[:30]
        ws.append(headers)
        for row in data: ws.append([row[h] for h in headers])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        return send_file(buf, as_attachment=True, download_name=f"{kind}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == 'pdf':
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
        from reportlab.lib.styles import getSampleStyleSheet
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        styles = getSampleStyleSheet()
        elems = [Paragraph(f"Relatório {kind.upper()} — ProFab MES/APS", styles['Title'])]
        rows = [headers] + [[str(r[h]) for h in headers] for r in data]
        t = Table(rows)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0d1b2a')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
            ('FONTSIZE', (0,0), (-1,-1), 7),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f1f5f9')]),
        ]))
        elems.append(t)
        doc.build(elems)
        buf.seek(0)
        return send_file(buf, as_attachment=True, download_name=f"{kind}.pdf", mimetype="application/pdf")
    flash('Formato inválido', 'danger')
    return redirect(url_for('supervisor_relatorios'))


# ============================================
# GOOGLE SIGN-IN (Emergent managed OAuth)
# ============================================
GOOGLE_LANDING_TEMPLATE = """
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><title>Entrando...</title>
<style>body{background:#0d1b2a;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.m{text-align:center}.m .l{font-size:20px;margin-bottom:10px}.m .s{font-size:12px;color:#78909c}
.err{background:#7f1d1d;color:#fecaca;padding:12px;border-radius:8px;max-width:400px;margin:10px auto;font-size:13px}</style></head>
<body><div class="m"><div class="l">🔵 Autenticando com Google...</div>
<div class="s" id="st">Validando sessão segura</div>
<div id="err"></div></div>
<script>
async function process(){
  try{
    var h = location.hash || '';
    var m = h.match(/session_id=([^&]+)/);
    if(!m){document.getElementById('st').textContent='Redirecionando para o login...';location.replace('/login');return;}
    var r = await fetch('/auth/google/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:m[1]})});
    var j = await r.json();
    if(r.ok && j.ok){history.replaceState(null,'',location.pathname);location.replace(j.redirect||'/operador');}
    else{document.getElementById('err').innerHTML='<div class="err">'+(j.detail||'Falha na autenticação')+'</div>';document.getElementById('st').textContent='';}
  }catch(e){document.getElementById('err').innerHTML='<div class="err">Erro inesperado: '+e+'</div>';}
}
process();
</script></body></html>
"""

@app.route('/auth/google/landing')
def auth_google_landing():
    """Página de callback que processa o fragment #session_id via JS (fragment não chega ao servidor)."""
    return render_template_string(GOOGLE_LANDING_TEMPLATE)

@app.route('/auth/google/session', methods=['POST'])
def auth_google_session():
    """Troca session_id por dados do usuário (chamada server-side) e cria sessão local."""
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id', '').strip()
    if not sid:
        return jsonify({'ok': False, 'detail': 'session_id ausente'}), 400
    try:
        r = requests.get(EMERGENT_AUTH_URL, headers={'X-Session-ID': sid}, timeout=30)
        r.raise_for_status()
        prof = r.json()
    except Exception as e:
        return jsonify({'ok': False, 'detail': f'Falha ao validar sessão com Emergent: {e}'}), 401

    email = (prof.get('email') or '').lower()
    nome = prof.get('name') or email.split('@')[0]
    session_token = prof.get('session_token')
    if not email or not session_token:
        return jsonify({'ok': False, 'detail': 'Resposta inválida do provedor'}), 401

    # Resolve matricula: vínculo pré-existente ou dono do app
    vinc = query("SELECT matricula FROM google_vinculos WHERE email = ?", (email,), one=True)
    if vinc:
        matricula = vinc['matricula']
    elif email == OWNER_EMAIL:
        # Auto-provisiona o dono como supervisor
        existente = query("SELECT matricula FROM operadores WHERE papel = 'supervisor' LIMIT 1", one=True)
        matricula = existente['matricula'] if existente else None
        if not matricula:
            matricula = '1000'
            query("""INSERT INTO operadores (matricula, nome, senha, turno, papel, ativo)
                     VALUES (?, ?, ?, 'Turno A', 'supervisor', 1)""",
                  (matricula, nome, gerar_hash_senha(secrets.token_hex(16))), commit=True)
        query("INSERT OR REPLACE INTO google_vinculos (email, matricula) VALUES (?, ?)", (email, matricula), commit=True)
    else:
        log_audit('google_login_negado', 'auth', email)
        return jsonify({'ok': False, 'detail': f'Conta {email} sem vínculo. Peça ao supervisor para vincular seu e-mail em Gerenciar.'}), 403

    op = query("SELECT * FROM operadores WHERE matricula = ? AND ativo = 1", (matricula,), one=True)
    if not op:
        return jsonify({'ok': False, 'detail': f'Operador {matricula} vinculado mas inativo'}), 403

    # Sessão local (7 dias, timezone-aware)
    expires = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=7)).isoformat()
    query("INSERT INTO user_sessions (email, matricula, session_token, expires_at) VALUES (?, ?, ?, ?)",
          (email, matricula, session_token, expires), commit=True)
    log_audit('google_login', 'auth', email, f'matricula={matricula}')

    resp = jsonify({'ok': True, 'redirect': {'supervisor': '/supervisor', 'pcp': '/pcp'}.get(op['papel'] or 'operador', '/operador'),
                    'user': {'matricula': matricula, 'nome': op['nome'], 'papel': op['papel'] or 'operador'}})
    resp.set_cookie('session_token', session_token, max_age=7*24*3600,
                    httponly=True, secure=True, samesite='None', path='/')
    return resp

@app.route('/supervisor/vincular-google', methods=['POST'])
@login_required
def supervisor_vincular_google():
    email = request.form.get('email', '').strip().lower()
    matricula = request.form.get('matricula', '').strip()
    if not email or '@' not in email or not matricula:
        flash('E-mail e matrícula obrigatórios.', 'danger')
        return redirect(url_for('supervisor_gerenciar'))
    if not query("SELECT 1 FROM operadores WHERE matricula = ?", (matricula,), one=True):
        flash('Matrícula não encontrada.', 'danger')
        return redirect(url_for('supervisor_gerenciar'))
    query("INSERT OR REPLACE INTO google_vinculos (email, matricula) VALUES (?, ?)", (email, matricula), commit=True)
    log_audit('vincular_google', 'auth', email, f'matricula={matricula}')
    flash(f'🔗 {email} vinculado ao operador {matricula}.', 'success')
    return redirect(url_for('supervisor_gerenciar'))


# ============================================
# ANEXOS DE OP + PENDÊNCIAS + MANUAL
# ============================================
EXTENSOES_ANEXO = ('pdf', 'png', 'jpg', 'jpeg', 'webp', 'gif')

@app.route('/supervisor/op/<num_op>/anexo', methods=['POST'])
@login_required
def supervisor_op_anexo(num_op):
    f = request.files.get('arquivo')
    if not f or not f.filename:
        flash('Selecione um arquivo (PDF ou imagem).', 'danger')
        return redirect(url_for('supervisor_gerenciar'))
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in EXTENSOES_ANEXO:
        flash('❌ Formato não suportado. Use PDF, PNG, JPG, WEBP ou GIF.', 'danger')
        return redirect(url_for('supervisor_gerenciar'))
    data = f.read()
    if len(data) > 20 * 1024 * 1024:
        flash('❌ Arquivo muito grande (máx. 20MB).', 'danger')
        return redirect(url_for('supervisor_gerenciar'))
    storage_path = f"profab-mes/desenhos/{num_op}/{secrets.token_hex(8)}.{ext}"
    try:
        res = put_object(storage_path, data, f.content_type or 'application/octet-stream')
    except Exception as e:
        flash(f'❌ Falha no upload: {e}', 'danger')
        return redirect(url_for('supervisor_gerenciar'))
    query("DELETE FROM op_anexos WHERE num_op = ?", (num_op,), commit=True)
    query("""INSERT INTO op_anexos (num_op, nome_arquivo, storage_path, content_type, enviado_por)
             VALUES (?, ?, ?, ?, ?)""",
          (num_op, f.filename, res.get('path', storage_path), f.content_type or 'application/octet-stream',
           session.get('matricula')), commit=True)
    log_audit('upload_anexo', 'op', num_op, f.filename)
    flash(f'📎 Desenho anexado à OP {num_op}.', 'success')
    return redirect(url_for('supervisor_gerenciar'))

@app.route('/anexo/<num_op>')
@login_required
def ver_anexo(num_op):
    a = query("SELECT * FROM op_anexos WHERE num_op = ? ORDER BY id DESC LIMIT 1", (num_op,), one=True)
    if not a:
        flash('Nenhum desenho anexado a esta OP.', 'info')
        return redirect(url_for('operador'))
    data, ct = get_object(a['storage_path'])
    import mimetypes
    mt = a['content_type'] or mimetypes.guess_type(a['nome_arquivo'])[0] or 'application/octet-stream'
    return send_file(io.BytesIO(data), mimetype=mt, download_name=a['nome_arquivo'])

@app.route('/pendencias/count')
def pendencias_count():
    if not session.get('matricula'):
        return jsonify({'count': 0}), 401
    r = query("SELECT COUNT(*) as c FROM apontamentos WHERE status_aprovacao = 'pendente' OR auto_fechado = 1", one=True)
    return jsonify({'count': r['c'] if r else 0})

MANUAL_TEMPLATE = FASE_TEMPLATE_BASE + """
<div class="card"><h3>📖 Manual do Sistema</h3>

<h4>🔐 1. Acesso ao Sistema</h4>
<table class="rep">
<tr><td><b>Google</b></td><td>Clique em <b>🔵 Entrar com Google</b> na tela de login. O dono do app ({{ owner_email }}) entra automaticamente como <b>supervisor</b>. Outros e-mails precisam ser vinculados a uma matrícula pelo supervisor (tela ⚙️ Gerenciar).</td></tr>
<tr><td><b>Matrícula</b></td><td>Caminho tradicional: matrícula (ex: 1024) + senha. Senhas são guardadas com bcrypt.</td></tr>
</table>

<h4>🧑‍🔧 2. Rotina do Operador</h4>
<table class="rep">
<tr><td>① Iniciar</td><td>Na aba 🧑‍🔧 OP, escolha a <b>OP e etapa</b>, a <b>máquina</b> e clique <b>▶ Iniciar</b>. O cronômetro verde começa a rodar e fica visível o tempo todo.</td></tr>
<tr><td>② Durante</td><td>Se a máquina parar, use <b>🔴 Disparar Parada</b> escolhendo o motivo. Paradas com ≥ {{ min_stop }} min exigem comentário ao retomar.</td></tr>
<tr><td>③ Fechar</td><td>Clique <b>📦 Fechar</b>, informe <b>peças boas</b> e <b>refugo</b>. Se o refugo passar do saldo da OP, escreva a <b>justificativa</b> — o apontamento vai para aprovação do supervisor.</td></tr>
<tr><td>④ Desenho</td><td>No cartão <b>📎 Desenho da OP</b>, abra o PDF/imagem do desenho técnico anexado pelo supervisor.</td></tr>
</table>

<h4>👮 3. Rotina do Supervisor / PCP</h4>
<table class="rep">
<tr><td>✅ Fila</td><td>Apontamentos com refugo acima do saldo ou fechados automaticamente aparecem aqui. O <b>badge vermelho piscando</b> no menu mostra a quantidade pendente. Aprove ✔️ para descontar o saldo ou rejeite ✖️.</td></tr>
<tr><td>🌙 Fim de turno</td><td>Use <b>Executar Fechamento Automático</b> na Fila para fechar tudo que ficou aberto (vai para validação).</td></tr>
<tr><td>📋 PCP</td><td>Crie OPs com roteiro de etapas, máquina e quantidade.</td></tr>
<tr><td>📊 Relat.</td><td>OEE por máquina, Pareto de paradas, Aging de OPs e Produtividade — com filtros de período/máquina e exportação em <b>CSV, Excel e PDF</b>.</td></tr>
<tr><td>⚙️ Gerenciar</td><td>Cadastre/editar operadores (✏️), reset de senha (🔑), máquinas, desenhos, <b>anexo de desenho na OP</b> (📎) e vínculos Google.</td></tr>
<tr><td>❤️ Saúde</td><td>Tamanho do DB, pendências, último backup. <b>Alerta vermelho</b> se o backup tiver mais de 24h — o backup também roda sozinho quando atrasa.</td></tr>
<tr><td>📚 Histórico</td><td>Todos os apontamentos com busca, filtro de período/status e paginação.</td></tr>
<tr><td>📜 Auditoria</td><td>Em Gerenciar → ver quem editou o quê, com o valor antes → depois.</td></tr>
</table>

<h4>💡 Dicas</h4>
<table class="rep">
<tr><td>🔒</td><td>Sessão do Google dura 7 dias; depois basta entrar com Google de novo.</td></tr>
<tr><td>💾</td><td>Backup automático: no login do sistema e sempre que o último tiver mais de 24h.</td></tr>
<tr><td>📱</td><td>O sistema funciona em tablet/celular do chão de fábrica — os cronômetros e botões são grandes de propósito.</td></tr>
</table>
</div>
"""

@app.route('/manual')
@login_required
def manual():
    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(MANUAL_TEMPLATE,
            owner_email=OWNER_EMAIL,
            min_stop=get_config('min_stoppage_comment_min', '15')),
        aba='manual', session=session)


# ============================================
# ACOMPANHAMENTO DE OP POR FASE (PCP)
# ============================================

LIMITE_FASE_PARADA_H = 8
HORAS_PRODUTIVAS_DIA = 16


def _parse_dt(valor):
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(valor)[:19], fmt)
        except (ValueError, TypeError):
            continue
    return None


def _analise_op(op):
    """Detecta fases paradas e estima a entrega da OP pelo tempo de ciclo do roteiro."""
    num_op = op['num_op']
    etapas = query("""SELECT e.*, r.tempo_ciclo_minutos FROM op_etapas_execucao e
        LEFT JOIN roteiros_padrao r ON r.numero_desenho = ? AND r.numero_etapa = e.numero_etapa
        WHERE e.num_op = ? ORDER BY e.numero_etapa""", (op['numero_desenho'], num_op)) or []
    ult = {r['numero_etapa']: r for r in (query("""SELECT numero_etapa,
        MAX(COALESCE(data_hora_fim, data_hora_inicio)) as ultima,
        SUM(CASE WHEN qtd_boas IS NULL AND status='Ativo' THEN 1 ELSE 0 END) as ativos
        FROM apontamentos WHERE num_op = ? GROUP BY numero_etapa""", (num_op,)) or [])}
    agora = datetime.now()
    referencia = op['data_criacao']
    primeira_aberta = True
    fases, minutos = [], 0
    for e in etapas:
        u = ult.get(e['numero_etapa'])
        concluida = e['status_execucao'] == 'Concluída'
        ativa = bool(u and u['ativos'])
        dt = _parse_dt((u['ultima'] if u and u['ultima'] else None) or referencia)
        horas = round((agora - dt).total_seconds() / 3600, 1) if dt else None
        candidata = not concluida and not ativa and (bool(u) or primeira_aberta)
        parada = candidata and horas is not None and horas >= LIMITE_FASE_PARADA_H
        if not concluida:
            primeira_aberta = False
            minutos += (e['saldo_residual'] or 0) * (e['tempo_ciclo_minutos'] or 0)
        if u and u['ultima']:
            referencia = u['ultima']
        fases.append({'numero_etapa': e['numero_etapa'], 'descricao_etapa': e['descricao_etapa'] or '',
                      'estado': 'Concluída' if concluida else ('Em produção' if ativa else 'Pendente'),
                      'parada': parada, 'horas_sem': horas})
    dias = minutos / 60 / HORAS_PRODUTIVAS_DIA
    previsao = agora + timedelta(days=dias) if minutos > 0 else None
    return {'fases': fases, 'horas_restantes': round(minutos / 60, 1), 'previsao': previsao,
            'paradas': sum(1 for f in fases if f['parada'])}


ACOMPANHAR_TEMPLATE = FASE_TEMPLATE_BASE + MODAL_CSS + """
<div class="card">
  <h3>🎯 Acompanhamento da OP {{ op.num_op }}</h3>
  <div class="row-flex">
    <div class="metric"><div class="v">{{ op.numero_desenho }}</div><div class="l">Desenho</div></div>
    <div class="metric ok"><div class="v">{{ progresso_total }}%</div><div class="l">Progresso Total</div></div>
    <div class="metric"><div class="v">{{ op.meta_total }}</div><div class="l">Meta (peças)</div></div>
    <div class="metric warn"><div class="v">{{ saldo_total }}</div><div class="l">Saldo nas Fases</div></div>
    <div class="metric"><div class="v" style="font-size:14px;">{{ op.data_criacao }}</div><div class="l">Criada em</div></div>
    <div class="metric" data-testid="acompanhar-previsao"><div class="v" style="font-size:14px;">{{ analise.previsao.strftime('%d/%m/%Y %H:%M') if analise.previsao else '—' }}</div><div class="l">Previsão de entrega · {{ analise.horas_restantes }}h máq.</div></div>
  </div>
  <small style="color:#78909c;font-size:10px;">Previsão = saldo de cada fase × tempo de ciclo do roteiro, considerando {{ horas_dia }}h produtivas/dia.</small>
  {% if analise.paradas %}<div data-testid="acompanhar-alerta-parada" style="margin-top:10px;padding:10px;border-radius:6px;background:#7f1d1d;color:#fecaca;font-size:12px;font-weight:700;">🛑 {{ analise.paradas }} fase(s) sem apontamento há mais de {{ limite_parada }}h</div>{% endif %}
  <a class="btn btn-sm btn-info" href="/pcp" data-testid="acompanhar-voltar-btn" style="display:inline-block;width:auto;text-decoration:none;margin-top:10px;padding:6px 12px;">← Voltar ao PCP</a>
</div>

<div class="card">
  <h3>🛤️ Fases da OP (Roteiro)</h3>
  {% for e in fases %}
  <div class="list-row" data-testid="fase-{{ e.numero_etapa }}" style="border-left:4px solid {{ '#ef4444' if e.parada else ('#10b981' if e.status == 'Concluída' else ('#f59e0b' if e.em_producao else '#334155')) }};{{ 'background:#2a0f12;' if e.parada else '' }}">
    <div class="info">
      <b>Etapa {{ e.numero_etapa }} — {{ e.descricao }}</b>
      <small>
        {% if e.status == 'Concluída' %}<span class="badge b-ok">✅ Concluída</span>
        {% elif e.em_producao %}<span class="badge b-pend">🔄 Em produção</span>
        {% else %}<span class="badge b-late">⏳ Pendente</span>{% endif %}
        {% if e.parada %}<span class="badge b-late" data-testid="fase-parada-{{ e.numero_etapa }}" style="background:#b91c1c;color:#fff;">🛑 PARADA há {{ e.horas_sem }}h</span>{% endif %}
        {% if e.em_producao %}
          &nbsp;· <span class="pulse-dot" style="color:#10b981;">●</span>
          {% for a in e.ativos %}{{ a.nome or a.operador }} ({{ a.codigo_maquina }}){% if not loop.last %}, {% endif %}{% endfor %}
        {% endif %}
      </small>
      {% if e.pendentes > 0 %}<small style="color:#c7d2fe;">⚠️ {{ e.pendentes }} apontamento(s) aguardando validação</small>{% endif %}
    </div>
    <div style="text-align:right;min-width:140px;">
      <div style="font-family:monospace;font-size:13px;color:#e2e8f0;">{{ e.produzidas }} feitas · {{ e.refugo }} refugo</div>
      <div class="bar" style="margin-top:4px;width:140px;"><span style="width:{{ e.pct }}%"></span></div>
      <small style="color:#78909c;font-size:10px;">{{ e.pct }}% da fase</small>
    </div>
  </div>
  {% else %}<p style="color:#78909c;font-size:12px;">OP sem roteiro de fases.</p>{% endfor %}
</div>

<div class="card">
  <h3>📜 Histórico de Apontamentos desta OP</h3>
  <div style="overflow-x:auto;"><table class="rep" data-testid="acompanhar-historico">
  <tr><th>Etapa</th><th>Operador</th><th>Máquina</th><th>Boas</th><th>Refugo</th><th>Início</th><th>Fim</th><th>Status</th></tr>
  {% for h in historico %}
  <tr>
    <td>{{ h.numero_etapa }}</td><td>{{ h.nome_op or h.operador }}</td><td>{{ h.codigo_maquina }}</td>
    <td>{{ h.qtd_boas or '-' }}</td><td>{{ h.refugo or 0 }}</td>
    <td style="font-size:11px;">{{ (h.data_hora_inicio or '')[:16] }}</td>
    <td style="font-size:11px;">{{ (h.data_hora_fim or '')[:16] if h.data_hora_fim else '<b style=color:#10b981>EM CURSO</b>'|safe }}</td>
    <td><span class="badge {% if (h.status_aprovacao or 'ok')=='pendente' %}b-pend{% elif h.status_aprovacao=='rejeitado' %}b-late{% else %}b-ok{% endif %}">{{ h.status_aprovacao or 'ok' }}</span></td>
  </tr>
  {% else %}<tr><td colspan="8" style="text-align:center;color:#78909c;padding:20px;">Nenhum apontamento ainda</td></tr>{% endfor %}
  </table></div>
</div>
"""

@app.route('/pcp/acompanhar/<num_op>')
@login_required
def pcp_acompanhar(num_op):
    op = query("SELECT * FROM ordens_producao WHERE num_op = ?", (num_op,), one=True)
    if not op:
        flash('OP não encontrada', 'danger')
        return redirect(url_for('pcp'))

    etapas_rows = query("SELECT * FROM op_etapas_execucao WHERE num_op = ? ORDER BY numero_etapa", (num_op,)) or []
    resumo = {r['numero_etapa']: r for r in (query("""
        SELECT numero_etapa, COALESCE(SUM(qtd_boas),0) as produzidas, COALESCE(SUM(refugo),0) as refugo,
               COALESCE(SUM(CASE WHEN status_aprovacao='pendente' THEN 1 ELSE 0 END),0) as pendentes
        FROM apontamentos WHERE num_op = ? GROUP BY numero_etapa""", (num_op,)) or [])}
    ativos = query("""SELECT a.numero_etapa, a.operador, o.nome, a.codigo_maquina
        FROM apontamentos a LEFT JOIN operadores o ON o.matricula = a.operador
        WHERE a.num_op = ? AND a.qtd_boas IS NULL AND a.status = 'Ativo'""", (num_op,)) or []

    fases = []
    for e in etapas_rows:
        r = resumo.get(e['numero_etapa'], {'produzidas': 0, 'refugo': 0, 'pendentes': 0})
        total_fase = (r['produzidas'] or 0) + (r['refugo'] or 0) + (e['saldo_residual'] or 0)
        pct = round((r['produzidas'] or 0) / total_fase * 100, 1) if total_fase else 0
        fases.append({
            'numero_etapa': e['numero_etapa'], 'descricao': e['descricao_etapa'],
            'status': e['status_execucao'], 'saldo': e['saldo_residual'],
            'produzidas': r['produzidas'], 'refugo': r['refugo'], 'pendentes': r['pendentes'],
            'pct': pct, 'total_fase': total_fase,
            'em_producao': any(a['numero_etapa'] == e['numero_etapa'] for a in ativos),
            'ativos': [a for a in ativos if a['numero_etapa'] == e['numero_etapa']],
        })

    analise = _analise_op(op)
    info = {f['numero_etapa']: f for f in analise['fases']}
    for f in fases:
        f['parada'] = info[f['numero_etapa']]['parada']
        f['horas_sem'] = info[f['numero_etapa']]['horas_sem']

    saldo_total = sum(f['saldo'] for f in fases)
    trabalho = (op['meta_total'] or 0) * len(fases)
    progresso_total = round(max(0, min(100, (trabalho - saldo_total) / trabalho * 100)), 1) if trabalho else 0

    historico = query("""SELECT a.*, o.nome as nome_op FROM apontamentos a
        LEFT JOIN operadores o ON o.matricula = a.operador
        WHERE a.num_op = ? ORDER BY a.data_hora_inicio DESC LIMIT 40""", (num_op,)) or []

    return render_template_string(BASE_TEMPLATE,
        conteudo=render_template_string(ACOMPANHAR_TEMPLATE,
            op=op, fases=fases, historico=historico,
            saldo_total=saldo_total, progresso_total=progresso_total, analise=analise,
            limite_parada=LIMITE_FASE_PARADA_H, horas_dia=HORAS_PRODUTIVAS_DIA),
        aba='pcp', session=session)


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 MES/APS - VERSÃO COMPLETA")
    print("=" * 60)
    print("✅ 1. Progresso Negativo Corrigido")
    print("✅ 2. Persistência de Dados")
    print("✅ 3. Backup Automático")
    print("✅ 4. Menu Burger no Supervisor")
    print("✅ 5. Gerenciamento de Roteiros")
    print("✅ 6. Impressão da Ordem de Produção")
    print("✅ 7. Consulta de OP com Apontamentos")
    print("=" * 60)
    
    init_db()
    
    # Criar backup ao iniciar
    criar_backup()
    
    print("=" * 60)
    print("🌐 Servidor: http://localhost:8550")
    print("👤 Usuários: 1024, 1025, 1026")
    print("🔑 Senha: 123456")
    print("=" * 60)
    
    port = int(os.environ.get('PORT', '3000'))
    app.run(host='0.0.0.0', port=port, debug=DEBUG)
