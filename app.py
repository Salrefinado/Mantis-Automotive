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

                # 2. Migrações de PRODUTOS (Novo Campo Link)
                try:
                    conn.execute(text("ALTER TABLE produtos ADD COLUMN link_compra TEXT"))
                    conn.commit()
                    print("--- Migração: Coluna 'link_compra' adicionada em Produtos. ---")
                except Exception:
                    conn.rollback()

                # 3. Migrações de AGENDAMENTOS (Campos Financeiros)
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
            
            config = ConfiguracaoFinanceira.query.first()
            custos_fixos_base = config.aluguel_iptu + config.pro_labore + config.agua_energia_base + config.internet_telefone + config.mei_impostos + config.marketing + config.seguro if config else 0
            
            ano_ant_ant, mes_ant_ant = get_mes_anterior(ano_str, mes_str)
            mes_ant_ant_str = f"{ano_ant_ant}-{mes_ant_ant:02d}"
            fechamento_ant_ant = FechamentoMensal.query.filter_by(mes_ano=mes_ant_ant_str).first()
            deficit_ant = abs(fechamento_ant_ant.deficit_acumulado) if fechamento_ant_ant and fechamento_ant_ant.deficit_acumulado < 0 else 0
            
            custos_totais = custos_fixos_base + custo_prod + deficit_ant
            lucro_real = fat_liq - custos_totais
            novo_deficit = lucro_real if lucro_real < 0 else 0
            
            novo_fechamento = FechamentoMensal(
                mes_ano=mes_anterior_str,
                total_faturado=fat_liq,
                custos_totais=custos_totais,
                lucro_real=lucro_real,
                deficit_acumulado=novo_deficit
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

def inicializar_servicos_padrao():
    try:
        if Servico.query.first() is None:
            padroes = [
                ('Naked', 'Standard Naked', 50.00), ('Naked', 'Premium Naked', 90.00),
                ('Sport', 'Standard Sport', 70.00), ('Sport', 'Premium Sport', 120.00),
                ('Custom', 'Standard Custom', 80.00), ('Custom', 'Premium Custom', 150.00),
                ('BigTrail', 'Standard Trail', 60.00), ('BigTrail', 'Premium Trail', 110.00)
            ]
            for cat, nome, valor in padroes:
                db.session.add(Servico(categoria=cat, nome=nome, valor=valor))
            db.session.commit()
    except Exception as e:
        print(f"Erro ao inicializar serviços: {e}")

def inicializar_produtos_padrao():
    try:
        produtos_iniciais = [
            ("Moto-V (Shampoo p/ graxa e barro)", "ml", 10.0),
            ("Rexer (Desengraxante chassis/suspen.)", "ml", 30.0),
            ("V-Mol (Shampoo desincrustante (terra))", "ml", 10.0),
            ("V-Floc (Shampoo neutro (manutenção))", "ml", 5.0),
            ("Vexus (Limpador de rodas e motores)", "ml", 25.0),
            ("Sintra Fast (APC (manoplas e detalhes))", "ml", 15.0),
            ("Izer (Descontaminante ferroso)", "ml", 30.0),
            ("Strike (Removedor de piche e cola)", "ml", 5.0),
            ("Delet (Limpador de pneus/borrachas)", "ml", 20.0),
            ("V-Bar (Clay Bar (remoção aspereza))", "g", 2.0),
            ("V-Lub (Lubrificante p/ V-Bar)", "ml", 40.0),
            ("Revelax (Revelador de hologramas)", "ml", 20.0),
            ("V-Polish (Composto de refino e lustro)", "ml", 10.0),
            ("Blend Spray (Proteção híbrida)", "ml", 10.0),
            ("Native Paste (Cera de Carnaúba Pura)", "g", 3.0),
            ("Tok Final (Cera rápida pós-lavagem)", "ml", 15.0),
            ("V-80 (Selante sintético)", "ml", 10.0),
            ("SIO2-PRO (Selante p/ pinturas foscas)", "ml", 10.0),
            ("Verniz Motor (Proteção e brilho (motor))", "ml", 40.0),
            ("Verom (Condicionador de motor (água))", "ml", 30.0),
            ("Restaurax (Renovador de plásticos)", "ml", 10.0),
            ("Revox (Selante de pneus (fosco))", "ml", 5.0),
            ("Shiny (Brilho intenso para pneus)", "ml", 5.0),
            ("Glazy (Limpa vidros e retrovisores)", "ml", 10.0),
            ("Prizm (Removedor de chuva ácida)", "ml", 5.0),
            ("Aquaglass (Repelente de água (viseiras))", "ml", 3.0),
            ("V-Paint (Vitrificador cerâmico)", "ml", 10.0),
            ("V-Plastic (Vitrificador p/ plásticos)", "ml", 10.0),
            ("V-Energy (Vitrificador de Motor)", "ml", 5.0),
            ("V-Light (Vitrificador de faróis)", "ml", 2.0),
            ("V-Leather (Coating p/ bancos em couro)", "ml", 5.0)
        ]
        
        count_novos = 0
        for nome, un, gasto in produtos_iniciais:
            produto_existente = Produto.query.filter_by(nome=nome).first()
            if not produto_existente:
                novo = Produto(
                    nome=nome, unidade_medida=un, estoque_atual=0.0,
                    custo_compra=0.0, quantidade_compra=0.0, 
                    gasto_medio_lavagem=gasto, ponto_pedido=5.0, link_compra=""
                )
                db.session.add(novo)
                count_novos += 1
        
        if count_novos > 0:
            db.session.commit()
            print(f"--- {count_novos} Produtos Iniciais Cadastrados (Zerados) ---")
            
    except Exception as e:
        print(f"Erro ao inicializar produtos: {e}")

with app.app_context():
    db.create_all()
    verificar_migracoes_banco()
    inicializar_configuracoes_financeiras()
    inicializar_servicos_padrao()
    inicializar_produtos_padrao() 

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
    
    faturamento_bruto = sum(a.valor_cobrado for a in concluidos)
    faturamento_liquido = sum(a.valor_liquido if a.valor_liquido else a.valor_cobrado for a in concluidos)
    
    custo_produtos_total = sum(a.custo_total_produtos for a in concluidos)
    
    custos_fixos_base = config.aluguel_iptu + config.pro_labore + config.agua_energia_base + config.internet_telefone + config.mei_impostos + config.marketing + config.seguro
    
    fechamento_anterior = FechamentoMensal.query.filter_by(mes_ano=mes_anterior_str).first()
    deficit_anterior = abs(fechamento_anterior.deficit_acumulado) if fechamento_anterior and fechamento_anterior.deficit_acumulado < 0 else 0.0
    
    custos_fixos_total = custos_fixos_base + deficit_anterior
    
    lucro_estimado = faturamento_liquido - custo_produtos_total - custos_fixos_total
    ticket_medio = faturamento_bruto / len(concluidos) if concluidos else 0
    
    margem_contribuicao_total = faturamento_liquido - custo_produtos_total
    margem_media = margem_contribuicao_total / len(concluidos) if concluidos else 0
    
    if margem_media > 0:
        meta_motos = math.ceil(custos_fixos_total / margem_media)
    else:
        meta_motos = math.ceil(custos_fixos_total / 70.0) if custos_fixos_total > 0 else 0
        
    total_motos_ciclo = len(concluidos)
    custo_fixo_por_moto = custos_fixos_total / total_motos_ciclo if total_motos_ciclo > 0 else 0
    
    dre_lista = []
    for a in concluidos:
        recebido = a.valor_liquido if a.valor_liquido else a.valor_cobrado
        produtos = a.custo_total_produtos
        desp_variaveis = a.gastos_extras
        lucro_real_moto = recebido - produtos - desp_variaveis - custo_fixo_por_moto
        
        dre_lista.append({
            'cliente': a.cliente.nome,
            'moto': f"{a.moto.modelo} ({a.moto.placa})",
            'data': a.data_agendada,
            'valor_cobrado': a.valor_cobrado,
            'forma_pagamento': a.forma_pagamento_real if a.forma_pagamento_real else (a.forma_pagamento_prevista if a.forma_pagamento_prevista else 'PIX'),
            'valor_recebido': recebido,
            'gasto_produtos': produtos,
            'despesas_fixas_rateadas': custo_fixo_por_moto,
            'despesas_variaveis': desp_variaveis,
            'lucro_estimado': lucro_real_moto
        })
        
    dre_lista.sort(key=lambda x: x['data'])
    
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
                           custos_produtos=custo_produtos_total, 
                           custos_fixos=custos_fixos_total,
                           lucro=lucro_estimado,
                           ticket_medio=ticket_medio,
                           qtd_servicos=total_motos_ciclo,
                           servicos=servicos,
                           produtos_todos=produtos_todos,
                           config=config,
                           meta_motos=meta_motos,
                           dre_lista=dre_lista,
                           mes_referencia=mes_referencia,
                           data_inicio=data_inicio,
                           data_fim=data_fim,
                           deficit_anterior=deficit_anterior,
                           meses_disponiveis=meses_disponiveis)

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
        
        config.aluguel_iptu = float(request.form.get('aluguel_iptu', 0))
        config.pro_labore = float(request.form.get('pro_labore', 0))
        config.agua_energia_base = float(request.form.get('agua_energia_base', 0))
        config.internet_telefone = float(request.form.get('internet_telefone', 0))
        config.mei_impostos = float(request.form.get('mei_impostos', 0))
        config.marketing = float(request.form.get('marketing', 0))
        config.seguro = float(request.form.get('seguro', 0))
        
        config.taxa_debito = float(request.form.get('taxa_debito', 0))
        config.taxa_credito_vista = float(request.form.get('taxa_credito_vista', 0))
        config.taxa_credito_parcelado = float(request.form.get('taxa_credito_parcelado', 0))
        config.minimo_parcelamento = float(request.form.get('minimo_parcelamento', 0))
        config.capacidade_mensal = int(request.form.get('capacidade_mensal', 40))
        
        db.session.commit()
        flash('Configurações financeiras atualizadas com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao salvar configurações: {e}', 'error')
    return redirect(url_for('financeiro'))

@app.route('/atualizar_preco', methods=['POST'])
def atualizar_preco():
    try:
        servico_id = request.form.get('id')
        novo_valor = request.form.get('valor')
        servico = Servico.query.get(servico_id)
        if servico:
            servico.valor = float(novo_valor)
            db.session.commit()
            flash('Preço atualizado!', 'success')
    except Exception as e:
        flash(f'Erro: {e}', 'error')
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
                    if p.estoque_atual >= p.gasto_medio_lavagem:
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
