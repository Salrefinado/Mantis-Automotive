import os
import locale
import math
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from sqlalchemy import text
from urllib.parse import unquote
from database import db, Cliente, Moto, Agendamento, Produto, MidiaAgendamento, Servico, ConfiguracaoFinanceira, FechamentoMensal

app = Flask(__name__)

# --- Configuração de Banco de Dados ---
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave-secreta-trocar-em-producao')
app.config['SQLALCHEMY_DATABASE_URI'] = database_url if database_url else 'sqlite:///lavagem.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
# Aumenta o limite de upload do Flask para 64MB
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024 

db.init_app(app)

# --- FUNÇÃO DE MIGRAÇÃO AUTOMÁTICA (CORREÇÃO DE BANCO) ---
def verificar_migracoes_banco():
    with app.app_context():
        try:
            with db.engine.connect() as conn:
                # 1. Migrações de CLIENTES
                colunas_clientes = [
                    ("qtd_descontos", "INTEGER DEFAULT 0"),
                    ("preferencias", "TEXT"),
                    ("feedback_texto", "TEXT"),
                    ("feedback_estrelas", "INTEGER DEFAULT 0"),
                    ("indicado_por_id", "INTEGER")
                ]
                for col, tipo in colunas_clientes:
                    try:
                        conn.execute(text(f"ALTER TABLE clientes ADD COLUMN {col} {tipo}"))
                        conn.commit()
                    except Exception:
                        conn.rollback()

                # 2. Migrações de PRODUTOS
                try:
                    conn.execute(text("ALTER TABLE produtos ADD COLUMN link_compra TEXT"))
                    conn.commit()
                except Exception:
                    conn.rollback()

                # 3. Migrações de AGENDAMENTOS
                colunas_agendamentos = [
                    ("forma_pagamento_prevista", "VARCHAR(50)"),
                    ("forma_pagamento_real", "VARCHAR(50)"),
                    ("parcelas", "INTEGER DEFAULT 1"),
                    ("taxa_aplicada", "FLOAT DEFAULT 0.0"),
                    ("valor_liquido", "FLOAT")
                ]
                for col, tipo in colunas_agendamentos:
                    try:
                        conn.execute(text(f"ALTER TABLE agendamentos ADD COLUMN {col} {tipo}"))
                        conn.commit()
                    except Exception:
                        conn.rollback()

                # 4. Migrações CONFIGURACAO FINANCEIRA (Gestão Patrimonial)
                colunas_config = [
                    ("aporte_erick", "FLOAT DEFAULT 0.0"),
                    ("aporte_andrei", "FLOAT DEFAULT 0.0"),
                    ("capex_produtos", "FLOAT DEFAULT 0.0"),
                    ("capex_ferramentas", "FLOAT DEFAULT 0.0"),
                    ("capex_estrutura", "FLOAT DEFAULT 0.0"),
                    ("capex_marketing", "FLOAT DEFAULT 0.0"),
                    ("capex_outros", "FLOAT DEFAULT 0.0")
                ]
                for col, tipo in colunas_config:
                    try:
                        conn.execute(text(f"ALTER TABLE configuracao_financeira ADD COLUMN {col} {tipo}"))
                        conn.commit()
                    except Exception:
                        conn.rollback()

                # 5. Migrações FECHAMENTO MENSAL (Retiradas)
                try:
                    conn.execute(text("ALTER TABLE fechamento_mensal ADD COLUMN retiradas_extras FLOAT DEFAULT 0.0"))
                    conn.commit()
                except Exception:
                    conn.rollback()

                # 6. Migrações SERVIÇOS (Descrição)
                try:
                    conn.execute(text("ALTER TABLE servicos ADD COLUMN descricao TEXT"))
                    conn.commit()
                except Exception:
                    conn.rollback()
                        
        except Exception as e:
            print(f"Erro ao verificar migrações: {e}")

# --- FUNÇÕES DE CICLO FINANCEIRO ---
def get_quarto_dia_util(ano, mes):
    dias_uteis = 0
    dia = 1
    while dias_uteis < 4:
        dt = date(ano, mes, dia)
        if dt.weekday() < 5: # 0 a 4 são Segunda a Sexta
            dias_uteis += 1
        if dias_uteis < 4:
            dia += 1
    return date(ano, mes, dia)

def get_mes_anterior(ano, mes):
    if mes == 1: return ano - 1, 12
    return ano, mes - 1

def get_proximo_mes(ano, mes):
    if mes == 12: return ano + 1, 1
    return ano, mes + 1
    
def obter_ciclo_atual(data_ref=None):
    if not data_ref:
        data_ref = datetime.now().date()
        
    quarto_dia_atual = get_quarto_dia_util(data_ref.year, data_ref.month)
    
    if data_ref <= quarto_dia_atual:
        ano_ant, mes_ant = get_mes_anterior(data_ref.year, data_ref.month)
        quarto_dia_ant = get_quarto_dia_util(ano_ant, mes_ant)
        
        data_inicio = quarto_dia_ant + timedelta(days=1)
        data_fim = quarto_dia_atual
        mes_referencia = f"{ano_ant}-{mes_ant:02d}"
        mes_anterior_str = f"{get_mes_anterior(ano_ant, mes_ant)[0]}-{get_mes_anterior(ano_ant, mes_ant)[1]:02d}"
    else:
        ano_prox, mes_prox = get_proximo_mes(data_ref.year, data_ref.month)
        quarto_dia_prox = get_quarto_dia_util(ano_prox, mes_prox)
        
        data_inicio = quarto_dia_atual + timedelta(days=1)
        data_fim = quarto_dia_prox
        mes_referencia = f"{data_ref.year}-{data_ref.month:02d}"
        mes_anterior_str = f"{get_mes_anterior(data_ref.year, data_ref.month)[0]}-{get_mes_anterior(data_ref.year, data_ref.month)[1]:02d}"
        
    return data_inicio, data_fim, mes_referencia, mes_anterior_str
    
def processar_fechamentos_pendentes():
    try:
        _, _, _, mes_anterior_str = obter_ciclo_atual()
        fechamento_ant = FechamentoMensal.query.filter_by(mes_ano=mes_anterior_str).first()
        
        if not fechamento_ant:
            ano_str, mes_str = map(int, mes_anterior_str.split('-'))
            ano_prox, mes_prox = get_proximo_mes(ano_str, mes_str)
            
            q_dia_inicio = get_quarto_dia_util(ano_str, mes_str)
            q_dia_fim = get_quarto_dia_util(ano_prox, mes_prox)
            
            inicio_ciclo = q_dia_inicio + timedelta(days=1)
            fim_ciclo = q_dia_fim
            
            concluidos = Agendamento.query.filter(
                Agendamento.data_agendada >= datetime.combine(inicio_ciclo, datetime.min.time()),
                Agendamento.data_agendada <= datetime.combine(fim_ciclo, datetime.max.time()),
                Agendamento.status.in_(['Lavagem Concluída', 'Retirado'])
            ).all()
            
            fat_liq = sum(a.valor_liquido if a.valor_liquido else a.valor_cobrado for a in concluidos)
            custo_prod = sum(a.custo_total_produtos for a in concluidos)
            custo_var_extras = sum(a.gastos_extras for a in concluidos)
            
            config = ConfiguracaoFinanceira.query.first()
            custos_fixos_base = config.aluguel_iptu + config.pro_labore + config.agua_energia_base + config.internet_telefone + config.mei_impostos + config.marketing + config.seguro if config else 0
            
            ano_ant_ant, mes_ant_ant = get_mes_anterior(ano_str, mes_str)
            mes_ant_ant_str = f"{ano_ant_ant}-{mes_ant_ant:02d}"
            fechamento_ant_ant = FechamentoMensal.query.filter_by(mes_ano=mes_ant_ant_str).first()
            deficit_ant = abs(fechamento_ant_ant.deficit_acumulado) if fechamento_ant_ant and fechamento_ant_ant.deficit_acumulado < 0 else 0
            
            custos_totais = custos_fixos_base + custo_prod + custo_var_extras + deficit_ant
            lucro_real = fat_liq - custos_totais
            novo_deficit = lucro_real if lucro_real < 0 else 0
            
            novo_fechamento = FechamentoMensal(
                mes_ano=mes_anterior_str,
                total_faturado=fat_liq,
                custos_totais=custos_totais,
                lucro_real=lucro_real,
                deficit_acumulado=novo_deficit,
                retiradas_extras=0.0 # Inicializa com zero
            )
            db.session.add(novo_fechamento)
            db.session.commit()
    except Exception as e:
        print(f"Erro ao processar fechamentos pendentes: {e}")

def inicializar_configuracoes_financeiras():
    try:
        if ConfiguracaoFinanceira.query.first() is None:
            nova_config = ConfiguracaoFinanceira()
            db.session.add(nova_config)
            db.session.commit()
            print("--- Configurações Financeiras Iniciais Criadas ---")
    except Exception as e:
        print(f"Erro ao inicializar configurações financeiras: {e}")

def inicializar_produtos_padrao():
    try:
        produtos_iniciais = [
            ("Moto-V", "ml", 10.0, 64.50, 500.0),
            ("Rexer", "ml", 30.0, 54.90, 500.0),
            ("V-Mol", "ml", 10.0, 94.03, 500.0),
            ("V-Floc", "ml", 5.0, 114.90, 500.0),
            ("Vexus", "ml", 25.0, 81.90, 500.0),
            ("Sintra Fast", "ml", 15.0, 80.70, 500.0),
            ("Izer", "ml", 30.0, 122.90, 500.0),
            ("Strike", "ml", 5.0, 130.00, 500.0),
            ("Delet", "ml", 20.0, 92.30, 500.0),
            ("V-Bar", "g", 2.0, 20.00, 50.0),
            ("V-Lub", "ml", 40.0, 20.00, 500.0),
            ("Revelax", "ml", 20.0, 95.90, 500.0),
            ("V-Polish", "ml", 10.0, 115.20, 500.0),
            ("Blend (Spray)", "ml", 10.0, 47.93, 500.0),
            ("Native (Paste)", "g", 3.0, 54.00, 100.0),
            ("Tok Final", "ml", 15.0, 25.30, 500.0),
            ("V-80", "ml", 10.0, 55.80, 500.0),
            ("SIO2-PRO", "ml", 10.0, 43.90, 500.0),
            ("Verniz Motor", "ml", 40.0, 89.50, 500.0),
            ("Verom", "ml", 30.0, 75.79, 500.0),
            ("Restaurax", "ml", 10.0, 115.90, 500.0),
            ("Revox", "ml", 5.0, 42.50, 500.0),
            ("Shiny", "ml", 5.0, 151.00, 500.0),
            ("Glazy", "ml", 10.0, 27.76, 500.0),
            ("Prizm", "ml", 5.0, 33.00, 500.0),
            ("Aquaglass", "ml", 3.0, 30.00, 50.0),
            ("V-Paint", "ml", 10.0, 74.30, 50.0),
            ("V-Plastic", "ml", 10.0, 63.90, 50.0),
            ("V-Energy", "ml", 5.0, 125.50, 50.0),
            ("V-Light", "ml", 2.0, 61.50, 50.0),
            ("V-Leather", "ml", 5.0, 138.90, 50.0),
            ("V-Wheels", "ml", 10.0, 50.00, 50.0),
            ("Ziva", "ml", 10.0, 50.00, 50.0)
        ]
        
        count_novos = 0
        for core_nome, un, gasto, custo, qtd in produtos_iniciais:
            produto_existente = Produto.query.filter(Produto.nome.ilike(f"{core_nome}%")).first()
            if not produto_existente:
                novo = Produto(
                    nome=core_nome, unidade_medida=un, estoque_atual=0.0,
                    custo_compra=custo, quantidade_compra=qtd, 
                    gasto_medio_lavagem=gasto, ponto_pedido=5.0, link_compra=""
                )
                db.session.add(novo)
                count_novos += 1
            else:
                produto_existente.nome = core_nome
                produto_existente.custo_compra = custo
                produto_existente.quantidade_compra = qtd
                produto_existente.gasto_medio_lavagem = gasto
        
        db.session.commit()
        if count_novos > 0:
            print(f"--- {count_novos} Produtos Iniciais Cadastrados/Atualizados ---")
            
    except Exception as e:
        print(f"Erro ao inicializar produtos: {e}")

def inicializar_servicos_padrao():
    try:
        # Pega a base de produtos atualizada para vincular aos serviços
        todos_produtos = Produto.query.all()
        vitrificadores_nomes = ['V-Paint', 'V-Plastic', 'V-Energy', 'V-Light', 'V-Leather']
        prods_standard = [p for p in todos_produtos if p.nome not in vitrificadores_nomes]
        prods_premium = todos_produtos

        if Servico.query.first() is None:
            padroes = [
                ('Naked', 'Standard Naked', 50.00, 'Lavagem detalhada básica'), 
                ('Naked', 'Premium Naked', 90.00, 'Lavagem com enceramento e proteção vitrificada'),
                ('Sport', 'Standard Sport', 70.00, 'Lavagem detalhada básica'), 
                ('Sport', 'Premium Sport', 120.00, 'Lavagem com enceramento e proteção vitrificada'),
                ('Custom', 'Standard Custom', 80.00, 'Lavagem detalhada básica com polimento de cromados leves'), 
                ('Custom', 'Premium Custom', 150.00, 'Lavagem completa com proteção avançada de metais e vitrificadores'),
                ('BigTrail', 'Standard Trail', 60.00, 'Lavagem para remoção de terra e barro leve'), 
                ('BigTrail', 'Premium Trail', 110.00, 'Lavagem profunda desincrustante e proteção plástica premium')
            ]
            for cat, nome, valor, desc in padroes:
                novo_servico = Servico(categoria=cat, nome=nome, valor=valor, descricao=desc)
                if 'Standard' in nome:
                    novo_servico.produtos_vinculados = prods_standard
                else:
                    novo_servico.produtos_vinculados = prods_premium
                db.session.add(novo_servico)
            db.session.commit()
        else:
            # Caso a tabela já exista, forçamos o vínculo para os serviços que estiverem vazios
            servicos = Servico.query.all()
            for s in servicos:
                if not s.produtos_vinculados:
                    if 'Standard' in s.nome:
                        s.produtos_vinculados = prods_standard
                    elif 'Premium' in s.nome:
                        s.produtos_vinculados = prods_premium
            db.session.commit()
            
    except Exception as e:
        print(f"Erro ao inicializar serviços: {e}")

with app.app_context():
    db.create_all()
    verificar_migracoes_banco()
    inicializar_configuracoes_financeiras()
    inicializar_produtos_padrao() 
    inicializar_servicos_padrao()

try:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
except OSError:
    pass

@app.template_filter('data_pt')
def format_data_pt(value):
    if not value: return ""
    dias = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']
    dia_semana = dias[value.weekday()]
    return f"{dia_semana}, {value.day:02d}/{value.month:02d}"

# --- DASHBOARD ---
@app.route('/')
def dashboard():
    hoje_data = datetime.now().date()
    hoje_completo = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    agendamentos = Agendamento.query.filter(Agendamento.data_agendada >= hoje_completo).order_by(Agendamento.data_agendada).all()
    produtos_alerta = Produto.query.filter(Produto.estoque_atual <= Produto.ponto_pedido).all()
    clientes_todos = Cliente.query.all()
    config = ConfiguracaoFinanceira.query.first()
    
    servicos_db = Servico.query.all()
    tabela_precos = {}
    for s in servicos_db:
        if s.categoria not in tabela_precos:
            tabela_precos[s.categoria] = []
        tabela_precos[s.categoria].append({'nome': s.nome, 'valor': s.valor})
    
    return render_template('dashboard.html', 
                           agendamentos=agendamentos, 
                           alertas=produtos_alerta, 
                           clientes_todos=clientes_todos, 
                           hoje=hoje_data,
                           tabela_precos=tabela_precos,
                           config=config)

# --- FINANCEIRO ---
@app.route('/financeiro')
def financeiro():
    config = ConfiguracaoFinanceira.query.first()
    if not config:
        config = ConfiguracaoFinanceira()
        db.session.add(config)
        db.session.commit()
        
    processar_fechamentos_pendentes()
    
    mes_query = request.args.get('mes')
    hoje = datetime.now().date()
    
    if mes_query:
        try:
            ano_q, mes_q = map(int, mes_query.split('-'))
            ano_prox, mes_prox = get_proximo_mes(ano_q, mes_q)
            
            q_dia_inicio = get_quarto_dia_util(ano_q, mes_q)
            q_dia_fim = get_quarto_dia_util(ano_prox, mes_prox)
            
            data_inicio = q_dia_inicio + timedelta(days=1)
            data_fim = q_dia_fim
            mes_referencia = mes_query
            
            ano_ant_q, mes_ant_q = get_mes_anterior(ano_q, mes_q)
            mes_anterior_str = f"{ano_ant_q}-{mes_ant_q:02d}"
        except:
            data_inicio, data_fim, mes_referencia, mes_anterior_str = obter_ciclo_atual(hoje)
    else:
        data_inicio, data_fim, mes_referencia, mes_anterior_str = obter_ciclo_atual(hoje)
        
    concluidos = Agendamento.query.filter(
        Agendamento.data_agendada >= datetime.combine(data_inicio, datetime.min.time()),
        Agendamento.data_agendada <= datetime.combine(data_fim, datetime.max.time()),
        Agendamento.status.in_(['Lavagem Concluída', 'Retirado'])
    ).all()
    
    # 1. Receita e Margem
    faturamento_bruto = sum(a.valor_cobrado for a in concluidos)
    faturamento_liquido = sum(a.valor_liquido if a.valor_liquido else a.valor_cobrado for a in concluidos)
    total_taxas_pagamento = faturamento_bruto - faturamento_liquido
    
    custo_produtos_total = sum(a.custo_total_produtos for a in concluidos)
    total_outras_variaveis = sum(a.gastos_extras for a in concluidos)
    total_custos_variaveis = custo_produtos_total + total_outras_variaveis
    
    custos_fixos_base = config.aluguel_iptu + config.pro_labore + config.agua_energia_base + config.internet_telefone + config.mei_impostos + config.marketing + config.seguro
    
    fechamento_anterior = FechamentoMensal.query.filter_by(mes_ano=mes_anterior_str).first()
    deficit_anterior = abs(fechamento_anterior.deficit_acumulado) if fechamento_anterior and fechamento_anterior.deficit_acumulado < 0 else 0.0
    
    custos_fixos_total = custos_fixos_base + deficit_anterior
    
    margem_contribuicao_total = faturamento_liquido - total_custos_variaveis
    margem_media = margem_contribuicao_total / len(concluidos) if concluidos else 0
    lucro_estimado = margem_contribuicao_total - custos_fixos_total
    ticket_medio = faturamento_bruto / len(concluidos) if concluidos else 0
    total_motos_ciclo = len(concluidos)

    # --- LÓGICA INTELIGENTE DE META DE MOTOS ---
    menor_servico = Servico.query.order_by(Servico.valor.asc()).first()
    pior_margem = 50.0 # Fallback de segurança
    
    if menor_servico and menor_servico.valor > 0:
        # Pior cenário exigido: Pagamento em Crédito Parcelado
        taxa_pior = config.taxa_credito_parcelado
        receita_liquida_pior = menor_servico.valor - (menor_servico.valor * (taxa_pior / 100.0))
        custo_prod_pior = sum(p.custo_por_dose for p in menor_servico.produtos_vinculados) if menor_servico.produtos_vinculados else 0.0
        pior_margem_calc = receita_liquida_pior - custo_prod_pior
        
        if pior_margem_calc > 0:
            pior_margem = pior_margem_calc

    custos_fixos_restantes = custos_fixos_total - margem_contribuicao_total
    motos_restantes_meta = 0

    if custos_fixos_restantes > 0:
        motos_restantes_meta = math.ceil(custos_fixos_restantes / pior_margem)
        meta_motos = total_motos_ciclo + motos_restantes_meta
    else:
        meta_motos = total_motos_ciclo # Meta já foi atingida ou ultrapassada

    
    # 2. DRE Lista
    dre_lista = []
    for a in concluidos:
        recebido = a.valor_liquido if a.valor_liquido else a.valor_cobrado
        produtos = a.custo_total_produtos
        desp_variaveis = a.gastos_extras
        margem_contribuicao_moto = recebido - produtos - desp_variaveis
        
        dre_lista.append({
            'cliente': a.cliente.nome,
            'moto': f"{a.moto.modelo} ({a.moto.placa})",
            'data': a.data_agendada,
            'valor_cobrado': a.valor_cobrado,
            'forma_pagamento': a.forma_pagamento_real if a.forma_pagamento_real else (a.forma_pagamento_prevista if a.forma_pagamento_prevista else 'PIX'),
            'valor_recebido': recebido,
            'gasto_produtos': produtos,
            'despesas_variaveis': desp_variaveis,
            'margem_contribuicao': margem_contribuicao_moto
        })
        
    dre_lista.sort(key=lambda x: x['data'])
    
    # 3. GESTÃO PATRIMONIAL E SUSTENTAÇÃO
    total_aporte = config.aporte_erick + config.aporte_andrei
    total_capex = config.capex_produtos + config.capex_ferramentas + config.capex_estrutura + config.capex_marketing + config.capex_outros
    
    # Soma todo o histórico
    todos_fechamentos = FechamentoMensal.query.all()
    lucro_historico_fechados = sum(f.lucro_real for f in todos_fechamentos)
    retiradas_historico = sum(f.retiradas_extras for f in todos_fechamentos)
    
    # Acumulado = Histórico + Mês Atual (se for ciclo fechado ou aberto)
    lucro_acumulado = lucro_historico_fechados + lucro_estimado
    
    caixa_atual = total_aporte - total_capex + lucro_acumulado - retiradas_historico
    
    payback_percentual = (lucro_acumulado / total_aporte * 100) if total_aporte > 0 else 0
    
    is_sustentavel = lucro_estimado >= 0
    
    servicos = Servico.query.order_by(Servico.categoria, Servico.valor).all()
    produtos_todos = Produto.query.order_by(Produto.nome).all()
    
    meses_disponiveis = []
    data_temp = hoje
    for _ in range(6): 
        m_ref = obter_ciclo_atual(data_temp)[2]
        if m_ref not in meses_disponiveis:
            meses_disponiveis.append(m_ref)
        ano_t, mes_t = get_mes_anterior(data_temp.year, data_temp.month)
        data_temp = date(ano_t, mes_t, 15)
        
    return render_template('financeiro.html', 
                           faturamento_bruto=faturamento_bruto,
                           faturamento_liquido=faturamento_liquido,
                           total_taxas_pagamento=total_taxas_pagamento,
                           custos_produtos=custo_produtos_total, 
                           total_outras_variaveis=total_outras_variaveis,
                           total_custos_variaveis=total_custos_variaveis,
                           custos_fixos=custos_fixos_total,
                           lucro=lucro_estimado,
                           margem_contribuicao_total=margem_contribuicao_total,
                           margem_media=margem_media,
                           ticket_medio=ticket_medio,
                           qtd_servicos=total_motos_ciclo,
                           servicos=servicos,
                           produtos_todos=produtos_todos,
                           config=config,
                           meta_motos=meta_motos,
                           motos_restantes_meta=motos_restantes_meta,
                           dre_lista=dre_lista,
                           mes_referencia=mes_referencia,
                           data_inicio=data_inicio,
                           data_fim=data_fim,
                           deficit_anterior=deficit_anterior,
                           meses_disponiveis=meses_disponiveis,
                           # Variaveis Patrimoniais
                           total_aporte=total_aporte,
                           total_capex=total_capex,
                           lucro_acumulado=lucro_acumulado,
                           caixa_atual=caixa_atual,
                           payback_percentual=payback_percentual,
                           is_sustentavel=is_sustentavel)

# --- ROTAS DE SERVIÇOS (TABELA DE PREÇOS) ---
@app.route('/adicionar_servico', methods=['POST'])
def adicionar_servico():
    try:
        novo = Servico(
            categoria=request.form.get('categoria'),
            nome=request.form.get('nome'),
            valor=float(request.form.get('valor')),
            descricao=request.form.get('descricao')
        )
        db.session.add(novo)
        db.session.commit()
        flash('Novo serviço cadastrado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao cadastrar serviço: {e}', 'error')
    return redirect(url_for('financeiro'))

@app.route('/editar_servico', methods=['POST'])
def editar_servico():
    try:
        s_id = request.form.get('servico_id')
        s = Servico.query.get(s_id)
        if s:
            s.categoria = request.form.get('categoria')
            s.nome = request.form.get('nome')
            s.valor = float(request.form.get('valor'))
            s.descricao = request.form.get('descricao')
            db.session.commit()
            flash('Serviço atualizado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao atualizar serviço: {e}', 'error')
    return redirect(url_for('financeiro'))

@app.route('/excluir_servico/<int:id>')
def excluir_servico(id):
    try:
        s = Servico.query.get(id)
        if s:
            s.produtos_vinculados = []
            db.session.delete(s)
            db.session.commit()
            flash('Serviço excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir serviço: {e}', 'error')
    return redirect(url_for('financeiro'))

@app.route('/atualizar_preco', methods=['POST'])
def atualizar_preco():
    # Rota mantida por segurança/compatibilidade, mas a edição completa é recomendada
    try:
        servico_id = request.form.get('id')
        novo_valor = request.form.get('valor')
        servico = Servico.query.get(servico_id)
        if servico:
            servico.valor = float(novo_valor)
            db.session.commit()
            flash('Preço rápido atualizado!', 'success')
    except Exception as e:
        flash(f'Erro: {e}', 'error')
    return redirect(url_for('financeiro'))

@app.route('/vincular_produtos_servico', methods=['POST'])
def vincular_produtos_servico():
    try:
        servico_id = request.form.get('servico_id')
        produto_ids = request.form.getlist('produtos')
        
        servico = Servico.query.get(servico_id)
        if servico:
            servico.produtos_vinculados = [] 
            if produto_ids:
                produtos = Produto.query.filter(Produto.id.in_(produto_ids)).all()
                servico.produtos_vinculados.extend(produtos)
            db.session.commit()
            flash(f'Insumos atualizados para o serviço {servico.nome}!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao vincular produtos: {e}', 'error')
    return redirect(url_for('financeiro'))

@app.route('/salvar_configuracao_financeira', methods=['POST'])
def salvar_configuracao_financeira():
    try:
        config = ConfiguracaoFinanceira.query.first()
        if not config:
            config = ConfiguracaoFinanceira()
            db.session.add(config)
        
        # Custos Fixos
        config.aluguel_iptu = float(request.form.get('aluguel_iptu', config.aluguel_iptu))
        config.pro_labore = float(request.form.get('pro_labore', config.pro_labore))
        config.agua_energia_base = float(request.form.get('agua_energia_base', config.agua_energia_base))
        config.internet_telefone = float(request.form.get('internet_telefone', config.internet_telefone))
        config.mei_impostos = float(request.form.get('mei_impostos', config.mei_impostos))
        config.marketing = float(request.form.get('marketing', config.marketing))
        config.seguro = float(request.form.get('seguro', config.seguro))
        
        # Taxas
        config.taxa_debito = float(request.form.get('taxa_debito', config.taxa_debito))
        config.taxa_credito_vista = float(request.form.get('taxa_credito_vista', config.taxa_credito_vista))
        config.taxa_credito_parcelado = float(request.form.get('taxa_credito_parcelado', config.taxa_credito_parcelado))
        config.minimo_parcelamento = float(request.form.get('minimo_parcelamento', config.minimo_parcelamento))
        config.capacidade_mensal = int(request.form.get('capacidade_mensal', config.capacidade_mensal))
        
        # Patrimonial (Aportes e CAPEX)
        config.aporte_erick = float(request.form.get('aporte_erick', config.aporte_erick))
        config.aporte_andrei = float(request.form.get('aporte_andrei', config.aporte_andrei))
        config.capex_produtos = float(request.form.get('capex_produtos', config.capex_produtos))
        config.capex_ferramentas = float(request.form.get('capex_ferramentas', config.capex_ferramentas))
        config.capex_estrutura = float(request.form.get('capex_estrutura', config.capex_estrutura))
        config.capex_marketing = float(request.form.get('capex_marketing', config.capex_marketing))
        config.capex_outros = float(request.form.get('capex_outros', config.capex_outros))
        
        db.session.commit()
        flash('Configurações atualizadas com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao salvar configurações: {e}', 'error')
    return redirect(url_for('financeiro'))

@app.route('/restart_financeiro', methods=['POST'])
def restart_financeiro():
    try:
        # Apaga todo o histórico de fechamentos
        db.session.query(FechamentoMensal).delete()
        db.session.commit()
        
        # O sistema é programado para gerar fechamentos automáticos se o mês anterior não existir.
        # Como deletamos o mês anterior, ele tentou recalcular os custos fixos sem nenhuma moto e gerou déficit de novo.
        # A solução é "blindar" o mês anterior criando um fechamento zerado (O marco zero da empresa).
        _, _, _, mes_anterior_str = obter_ciclo_atual()
        
        marco_zero = FechamentoMensal(
            mes_ano=mes_anterior_str,
            total_faturado=0.0,
            custos_totais=0.0,
            lucro_real=0.0,
            deficit_acumulado=0.0,
            retiradas_extras=0.0
        )
        db.session.add(marco_zero)
        db.session.commit()
        
        flash('Histórico financeiro resetado com sucesso! O sistema assumiu hoje como o Marco Zero das operações.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao resetar histórico: {e}', 'error')
    return redirect(url_for('financeiro'))


# --- AGENDAMENTO ---
@app.route('/novo_agendamento', methods=['POST'])
def novo_agendamento():
    try:
        cliente_id = request.form.get('cliente_id')
        moto_id = request.form.get('moto_id')
        data_str = request.form.get('data_dia')
        hora_str = request.form.get('data_hora')
        tipo_servico = request.form.get('tipo_servico')
        valor = float(request.form.get('valor'))
        
        forma_pagamento_prevista = request.form.get('forma_pagamento_prevista')
        parcelas = int(request.form.get('parcelas', 1))
        
        data_agendada = datetime.strptime(f"{data_str} {hora_str}", '%Y-%m-%d %H:%M')
        
        cliente = Cliente.query.get(cliente_id)
        aplicar_desconto = False
        
        if cliente.qtd_descontos > 0:
            valor = valor * 0.90 
            aplicar_desconto = True
            cliente.qtd_descontos -= 1 
        
        novo_agendamento = Agendamento(
            cliente_id=cliente_id, 
            moto_id=moto_id, 
            data_agendada=data_agendada,
            tipo_servico=tipo_servico, 
            valor_cobrado=valor, 
            desconto_aplicado=aplicar_desconto,
            forma_pagamento_prevista=forma_pagamento_prevista,
            parcelas=parcelas
        )
        db.session.add(novo_agendamento)
        db.session.commit()
        
        msg_desconto = " (Com 10% de desconto!)" if aplicar_desconto else ""
        flash(f'Agendamento realizado!{msg_desconto}', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/editar_agendamento', methods=['POST'])
def editar_agendamento():
    try:
        id_ = request.form.get('agendamento_id')
        data = request.form.get('data_dia')
        hora = request.form.get('data_hora')
        agenda = Agendamento.query.get(id_)
        if agenda:
            agenda.data_agendada = datetime.strptime(f"{data} {hora}", '%Y-%m-%d %H:%M')
            db.session.commit()
            flash('Atualizado!', 'success')
    except: flash('Erro ao editar', 'error')
    return redirect(url_for('dashboard'))

@app.route('/cancelar_agendamento/<int:id>')
def cancelar_agendamento(id):
    a = Agendamento.query.get(id)
    if a:
        if a.status != 'Cancelado' and a.desconto_aplicado:
            a.cliente.qtd_descontos += 1
            
        a.status = 'Cancelado'
        db.session.commit()
        flash('Cancelado. (Se havia desconto, foi devolvido)', 'info')
    return redirect(url_for('dashboard'))

@app.route('/excluir_agendamento/<int:id>')
def excluir_agendamento(id):
    try:
        a = Agendamento.query.get(id)
        if a:
            if a.status != 'Cancelado' and a.status != 'Lavagem Concluída' and a.status != 'Retirado' and a.desconto_aplicado:
                a.cliente.qtd_descontos += 1

            for midia in a.midias:
                db.session.delete(midia)
            db.session.delete(a)
            db.session.commit()
            flash('Agendamento excluído permanentemente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir: {e}', 'error')
    return redirect(url_for('dashboard'))

# --- GESTÃO DE CLIENTES ---
@app.route('/cadastrar_cliente', methods=['POST'])
def cadastrar_cliente():
    try:
        nome = request.form.get('nome')
        telefone = request.form.get('telefone')
        endereco = request.form.get('endereco')
        quem_indicou_id = request.form.get('quem_indicou_id')

        novo = Cliente(nome=nome, telefone=telefone, endereco=endereco)
        
        if quem_indicou_id:
            padrinho = Cliente.query.get(quem_indicou_id)
            if padrinho:
                novo.indicado_por_id = padrinho.id
                novo.qtd_descontos = 1 
        
        db.session.add(novo)
        db.session.flush() 
        
        moto = Moto(
            cliente_id=novo.id, 
            modelo=request.form.get('modelo_moto'), 
            placa=request.form.get('placa_moto'), 
            categoria=request.form.get('categoria_moto')
        )
        db.session.add(moto)
        db.session.commit()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'cliente': {'id': novo.id, 'nome': novo.nome, 'telefone': novo.telefone}, 'moto': moto.to_dict()})

        flash('Cliente cadastrado com sucesso!', 'success')
        if request.referrer and "clientes" in request.referrer: 
            return redirect(url_for('listar_clientes'))
            
    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': False, 'error': str(e)}), 400
        flash(f'Erro: {e}', 'error')
    
    return redirect(url_for('dashboard'))

@app.route('/api/buscar_cliente')
def buscar_cliente():
    termo = request.args.get('q', '').strip()
    if len(termo) < 1:
        return jsonify([]) 
        
    clientes = Cliente.query.filter(
        (Cliente.nome.ilike(f'%{termo}%')) | (Cliente.telefone.ilike(f'%{termo}%'))
    ).limit(10).all()
    
    return jsonify([{
        'id': c.id, 
        'text': f"{c.nome} - {c.telefone}", 
        'motos': [m.to_dict() for m in c.motos], 
        'qtd_descontos': c.qtd_descontos,
        'preferencias': c.preferencias
    } for c in clientes])

@app.route('/api/adicionar_moto', methods=['POST'])
def adicionar_moto():
    try:
        cliente_id = request.form.get('cliente_id')
        modelo = request.form.get('modelo')
        categoria = request.form.get('categoria')
        placa = request.form.get('placa')
        
        nova_moto = Moto(cliente_id=cliente_id, modelo=modelo, categoria=categoria, placa=placa)
        db.session.add(nova_moto)
        db.session.commit()
        return jsonify({'success': True, 'moto': nova_moto.to_dict()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/salvar_moto_cliente', methods=['POST'])
def salvar_moto_cliente():
    try:
        moto_id = request.form.get('moto_id')
        cliente_id = request.form.get('cliente_id')
        
        if moto_id:
            moto = Moto.query.get(moto_id)
            if moto:
                moto.modelo = request.form.get('modelo')
                moto.placa = request.form.get('placa')
                moto.categoria = request.form.get('categoria')
                db.session.commit()
                flash('Veículo atualizado com sucesso!', 'success')
        elif cliente_id:
            nova_moto = Moto(
                cliente_id=cliente_id,
                modelo=request.form.get('modelo'),
                placa=request.form.get('placa'),
                categoria=request.form.get('categoria')
            )
            db.session.add(nova_moto)
            db.session.commit()
            flash('Novo veículo adicionado!', 'success')
            
    except Exception as e:
        flash(f'Erro ao salvar veículo: {e}', 'error')
    
    return redirect(url_for('listar_clientes'))

@app.route('/editar_cliente_dados', methods=['POST'])
def editar_cliente_dados():
    try:
        cid = request.form.get('cliente_id')
        cliente = Cliente.query.get(cid)
        if cliente:
            cliente.nome = request.form.get('nome')
            cliente.telefone = request.form.get('telefone')
            cliente.endereco = request.form.get('endereco')
            cliente.preferencias = request.form.get('preferencias')
            db.session.commit()
            flash('Dados do cliente atualizados.', 'success')
    except Exception as e:
        flash(f'Erro: {e}', 'error')
    return redirect(url_for('listar_clientes'))

@app.route('/salvar_feedback', methods=['POST'])
def salvar_feedback():
    try:
        cid = request.form.get('cliente_id')
        cliente = Cliente.query.get(cid)
        if cliente:
            cliente.feedback_texto = request.form.get('feedback_texto')
            try:
                cliente.feedback_estrelas = int(request.form.get('feedback_estrelas'))
            except:
                cliente.feedback_estrelas = 0
            db.session.commit()
            flash('Feedback salvo!', 'success')
    except:
        flash('Erro ao salvar feedback', 'error')
    return redirect(url_for('listar_clientes'))

@app.route('/atualizar_status/<int:id>/<status>', methods=['POST'])
def atualizar_status(id, status):
    status = unquote(status)
    
    a = Agendamento.query.get(id)
    horario_str = request.form.get('horario')
    
    if horario_str:
        agora = datetime.now()
        try:
            horario_dt = datetime.strptime(horario_str, '%H:%M').replace(year=agora.year, month=agora.month, day=agora.day)
        except:
            horario_dt = datetime.now()
    else:
        horario_dt = datetime.now()

    if status == 'Em Lavagem':
        a.status = 'Em Lavagem'
        a.tempo_inicio = horario_dt
        
    elif status == 'Lavagem Concluída':
        a.status = 'Lavagem Concluída'
        a.tempo_fim = horario_dt
        
        if a.custo_total_produtos == 0:
            custo = 0
            servico_realizado = Servico.query.filter_by(nome=a.tipo_servico).first()
            
            # Dá baixa apenas nos produtos que fazem parte da receita deste serviço específico
            if servico_realizado and servico_realizado.produtos_vinculados:
                for p in servico_realizado.produtos_vinculados:
                    # Removemos a condicional que barrava a baixa e o custo se não houvesse estoque
                    p.estoque_atual -= p.gasto_medio_lavagem
                    custo += p.custo_por_dose
            a.custo_total_produtos = custo
        
        if a.cliente.indicado_por_id:
            lavagens_concluidas = Agendamento.query.filter(Agendamento.cliente_id == a.cliente.id, Agendamento.status.in_(['Lavagem Concluída', 'Retirado'])).count()
            if lavagens_concluidas == 1:
                padrinho = Cliente.query.get(a.cliente.indicado_por_id)
                if padrinho:
                    padrinho.qtd_descontos += 1
                    
    elif status == 'Retirado':
        a.status = 'Retirado'
        
        forma_pgto = request.form.get('forma_pagamento_real')
        parcelas = request.form.get('parcelas_reais')
        
        if forma_pgto:
            a.forma_pagamento_real = forma_pgto
            a.parcelas = int(parcelas) if parcelas else 1
            
            config = ConfiguracaoFinanceira.query.first()
            taxa = 0.0
            
            if forma_pgto == 'Debito':
                taxa = config.taxa_debito
            elif forma_pgto == 'Credito A Vista':
                taxa = config.taxa_credito_vista
            elif forma_pgto == 'Credito Parcelado':
                taxa = config.taxa_credito_parcelado
                
            a.taxa_aplicada = taxa
            a.valor_liquido = a.valor_cobrado - (a.valor_cobrado * (taxa / 100.0))
        else:
            a.forma_pagamento_real = a.forma_pagamento_prevista
            a.valor_liquido = a.valor_cobrado

    db.session.commit()
    flash(f'Status atualizado para {status} às {horario_dt.strftime("%H:%M")}', 'info')
    return redirect(url_for('dashboard'))

@app.route('/upload_midia/<int:agendamento_id>', methods=['POST'])
def upload_midia(agendamento_id):
    if 'arquivo' not in request.files: return 'Erro', 400
    arquivo = request.files['arquivo']
    if arquivo:
        filename = secure_filename(f"{agendamento_id}_{datetime.now().timestamp()}_{arquivo.filename}")
        arquivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        db.session.add(MidiaAgendamento(agendamento_id=agendamento_id, caminho_arquivo=filename, tipo=request.form.get('tipo')))
        db.session.commit()
    return redirect(request.referrer or url_for('dashboard'))

# --- GESTÃO DE PRODUTOS ---
@app.route('/produtos', methods=['GET', 'POST'])
def gerenciar_produtos():
    if request.method == 'POST':
        db.session.add(Produto(
            nome=request.form.get('nome'), 
            unidade_medida=request.form.get('unidade'),
            custo_compra=float(request.form.get('custo')), 
            quantidade_compra=float(request.form.get('qtd_compra')),
            gasto_medio_lavagem=float(request.form.get('gasto_medio')), 
            estoque_atual=float(request.form.get('estoque_inicial')),
            link_compra=request.form.get('link_compra') 
        ))
        db.session.commit()
        flash('Produto cadastrado com sucesso!', 'success')
        
    return render_template('produtos.html', produtos=Produto.query.all())

@app.route('/editar_produto', methods=['POST'])
def editar_produto():
    try:
        id_ = request.form.get('produto_id')
        prod = Produto.query.get(id_)
        if prod:
            prod.nome = request.form.get('nome')
            prod.unidade_medida = request.form.get('unidade_medida')
            prod.estoque_atual = float(request.form.get('estoque_atual'))
            prod.custo_compra = float(request.form.get('custo_compra'))
            prod.quantidade_compra = float(request.form.get('quantidade_compra'))
            prod.gasto_medio_lavagem = float(request.form.get('gasto_medio_lavagem'))
            prod.link_compra = request.form.get('link_compra')
            
            db.session.commit()
            flash('Produto atualizado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao editar produto: {e}', 'error')
    return redirect(url_for('gerenciar_produtos'))

@app.route('/excluir_produto/<int:id>')
def excluir_produto(id):
    try:
        prod = Produto.query.get(id)
        if prod:
            if prod.estoque_atual > 0:
                prod.estoque_atual = 0.0
                db.session.commit()
                flash('Produto movido para "Fora de Estoque" (Quantidade zerada).', 'info')
            else:
                db.session.delete(prod)
                db.session.commit()
                flash('Produto excluído permanentemente.', 'success')
    except Exception as e:
        flash(f'Erro ao excluir: {e}', 'error')
    return redirect(url_for('gerenciar_produtos'))

@app.route('/clientes')
def listar_clientes():
    clientes_brutos = Cliente.query.all()
    clientes_processados = []
    
    for c in clientes_brutos:
        agendamentos_recentes = sorted(c.agendamentos, key=lambda x: x.data_agendada, reverse=True)
        lavagens_concluidas = [a for a in c.agendamentos if a.status in ('Lavagem Concluída', 'Retirado')]
        lavagens_canceladas = [a for a in c.agendamentos if a.status == 'Cancelado']
        
        clientes_processados.append({
            'dados': c, 
            'motos': c.motos,
            'agendamentos': agendamentos_recentes, 
            'qtd_lavagens': len(lavagens_concluidas),
            'qtd_canceladas': len(lavagens_canceladas),
            'total_gasto': sum(a.valor_cobrado for a in lavagens_concluidas),
            'midias': [m for a in lavagens_concluidas for m in a.midias]
        })
    
    clientes_processados.sort(key=lambda x: x['total_gasto'], reverse=True)
    return render_template('clientes.html', clientes=clientes_processados, clientes_todos=clientes_brutos)

if __name__ == '__main__': 
    app.run(debug=True, host='0.0.0.0')
