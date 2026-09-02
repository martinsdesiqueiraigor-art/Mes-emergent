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
from datetime import datetime
from functools import wraps
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify, send_file

# --- CONFIGURAÇÕES ---
DB_NAME = "producao.db"
BACKUP_DIR = "backups"
SECRET_KEY = secrets.token_hex(32)
DEBUG = True
MAX_BACKUPS = 10

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ============================================
# FUNÇÕES DE SEGURANÇA
# ============================================

def gerar_hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

def verificar_senha(senha_digitada, senha_hash):
    return hashlib.sha256(senha_digitada.encode()).hexdigest() == senha_hash

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('matricula'):
            flash('Por favor, faça login primeiro.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
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

# ============================================
# INICIALIZAÇÃO DO BANCO (COM PERSISTÊNCIA)
# ============================================

def init_db():
    """Inicializa o banco de dados apenas se não existir"""
    
    # Verificar se o banco já existe
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
        
        <div class="nav-tabs">
            <a href="/operador" class="{% if aba == 'operador' %}active{% endif %}">🧑‍🔧 OP</a>
            <a href="/pcp" class="{% if aba == 'pcp' %}active{% endif %}">📋 PCP</a>
            <a href="/supervisor" class="{% if aba == 'supervisor' %}active{% endif %}">👮 SUP</a>
        </div>
        
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
            if (menu.classList.contains('active') && !e.target.closest('.burger-content') && !e.target.closest('.burger-toggle')) {
                menu.classList.remove('active');
            }
        });
    </script>
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
            <input type="number" name="matricula" placeholder="Ex: 1024" required>
        </div>
        <div class="form-group">
            <label>Senha</label>
            <input type="password" name="senha" placeholder="Digite sua senha" required>
        </div>
        <button type="submit" class="btn btn-primary">🚀 Entrar</button>
    </form>
    <div style="margin-top:15px; font-size:11px; color:#455a64; text-align:center;">
        Teste: 1024, 1025, 1026<br>Senha: 123456
    </div>
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
        <div class="metric-card">
            <div class="value" style="color:#81c784;">{{ historico|length }}</div>
            <div class="label">✅ Hoje</div>
        </div>
        <div class="metric-card">
            <div class="value" style="color:#ffcc80;">85%</div>
            <div class="label">📈 OEE</div>
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
        <h3>🔄 Atividades Ativas</h3>
        {% if ativas %}
            {% for op in ativas %}
            <div style="background:#0d1b2a;padding:10px;border-radius:6px;margin-bottom:8px;border:1px solid #1a2a3a;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <strong>{{ op.num_op }}</strong> - Etapa {{ op.numero_etapa }}
                        <div style="font-size:11px;color:#78909c;">Máquina: {{ op.codigo_maquina }}</div>
                        <div style="font-size:11px;color:#78909c;">Início: {{ op.data_hora_inicio[11:16] if op.data_hora_inicio else '--:--' }}</div>
                    </div>
                    <button onclick="document.getElementById('fechar-form-{{ op.id }}').style.display='block'" 
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
    <table class="table">
        <thead><tr><th>OP</th><th>Meta</th><th>Progresso</th><th>Ações</th></tr></thead>
        <tbody>
        {% for op in ops %}
        <tr>
            <td><strong>{{ op.num_op }}</strong></td>
            <td>{{ op.meta_total }}</td>
            <td>
                {% set progresso = ((op.meta_total - op.saldo_total) / op.meta_total * 100)|round(0) if op.meta_total > 0 else 0 %}
                <div class="progress-bar">
                    <div class="fill" style="width:{{ progresso if progresso >= 0 else 0 }}%; background:{% if progresso >= 100 %}#4CAF50{% elif progresso >= 50 %}#FF9800{% else %}#2196F3{% endif %};"></div>
                </div>
                <span style="font-size:10px;color:#78909c;">{{ progresso if progresso >= 0 else 0 }}%</span>
            </td>
            <td>
                <a href="/pcp/imprimir/{{ op.num_op }}" target="_blank" class="btn btn-sm btn-info" style="display:inline-block;width:auto;padding:3px 8px;text-decoration:none;">🖨️</a>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
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
    fetch('/api/roteiro/' + id)
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
    session.clear()
    flash('Logout realizado', 'info')
    return redirect(url_for('login'))

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
    
    matricula = session['matricula']
    
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
    
    return render_template_string(BASE_TEMPLATE, 
        conteudo=render_template_string(OPERADOR_TEMPLATE,
            ativas=ativas, historico=historico, ops_disponiveis=ops_disponiveis,
            maquinas_disponiveis=maquinas_disponiveis, paradas_ativas=paradas_ativas,
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
    
    query("UPDATE apontamentos SET qtd_boas = ?, refugo = ?, data_hora_fim = CURRENT_TIMESTAMP WHERE id = ?", (qtd_boas, refugo, id_apontamento), commit=True)
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
    
    return render_template_string(BASE_TEMPLATE, 
        conteudo=render_template_string(PCP_TEMPLATE, desenhos=desenhos, ops=ops),
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

@app.route('/api/roteiro/<int:id>')
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
    
    app.run(host='0.0.0.0', port=8551, debug=DEBUG)
