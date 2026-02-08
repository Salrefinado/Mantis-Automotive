import os
import locale
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime
from sqlalchemy import text
from urllib.parse import unquote
from database import db, Cliente, Moto, Agendamento, Produto, MidiaAgendamento, Servico

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
    """
    Verifica se as novas colunas existem no banco de dados.
    Se não existirem, cria-as automaticamente.
    """
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
                        
        except Exception as e:
            print(f"Erro ao verificar migrações: {e}")

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
    """
    Cadastra os produtos iniciais com estoque zerado e valor zerado
    apenas se a tabela estiver vazia.
    """
    try:
        if Produto.query.first() is None:
            # Lista fornecida: Nome, Unidade, Gasto Médio
            # Custo e Estoque iniciam Zerados (0.0)
            produtos_iniciais = [
                ("V-Floc (Shampoo)", "ml", 10.0),
                ("Vexus (Rodas/Motor)", "ml", 100.0),
                ("Izer (Descont. Ferroso)", "ml", 40.0),
                ("H-7 (Desengraxante)", "ml", 150.0),
                ("Shiny (Pneu/Brilho)", "ml", 20.0),
                ("Hidracouro (Bancos)", "ml", 15.0),
                ("Prizm (Vidros/Metais)", "ml", 10.0),
                ("Glazy (Viseira)", "ml", 15.0),
                ("Restaurax (Plásticos Ext)", "ml", 20.0),
                ("Intense (Plásticos Int)", "ml", 20.0),
                ("Sanitizante (Estofados)", "ml", 30.0),
                ("V-Lub (Borrachas)", "ml", 10.0),
                ("Blend Spray (Cera/Brilho)", "ml", 15.0),
                ("Lub. Corrente (Spray)", "ml", 15.0),
                ("Graxa Branca/Lítio", "g", 300.0)
            ]
            
            for nome, un, gasto in produtos_iniciais:
                novo = Produto(
                    nome=nome,
                    unidade_medida=un,
                    estoque_atual=0.0,
                    custo_compra=0.0,
                    quantidade_compra=0.0, # Evita divisão por zero na view, mas valor é 0
                    gasto_medio_lavagem=gasto,
                    ponto_pedido=5.0, # Alerta padrão
                    link_compra=""
                )
                db.session.add(novo)
            
            db.session.commit()
            print("--- Produtos Iniciais Cadastrados (Zerados) ---")
    except Exception as e:
        print(f"Erro ao inicializar produtos: {e}")

with app.app_context():
    db.create_all()
    verificar_migracoes_banco()
    inicializar_servicos_padrao()
    inicializar_produtos_padrao() # <--- Executa a carga inicial dos produtos

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
                           tabela_precos=tabela_precos)

# --- FINANCEIRO ---
@app.route('/financeiro')
def financeiro():
    concluidos = Agendamento.query.filter_by(status='Lavagem Concluída').all()
    
    faturamento_total = sum(a.valor_cobrado for a in concluidos)
    custo_produtos_total = sum(a.custo_total_produtos for a in concluidos)
    lucro_estimado = faturamento_total - custo_produtos_total
    ticket_medio = faturamento_total / len(concluidos) if concluidos else 0
    
    servicos = Servico.query.order_by(Servico.categoria, Servico.valor).all()
    
    return render_template('financeiro.html', 
                           faturamento=faturamento_total, 
                           custos=custo_produtos_total, 
                           lucro=lucro_estimado,
                           ticket_medio=ticket_medio,
                           qtd_servicos=len(concluidos),
                           servicos=servicos)

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
        
        data_agendada = datetime.strptime(f"{data_str} {hora_str}", '%Y-%m-%d %H:%M')
        
        cliente = Cliente.query.get(cliente_id)
        aplicar_desconto = False
        
        if cliente.qtd_descontos > 0:
            valor = valor * 0.90 # 10% de desconto
            aplicar_desconto = True
            cliente.qtd_descontos -= 1 
        
        novo_agendamento = Agendamento(
            cliente_id=cliente_id, moto_id=moto_id, data_agendada=data_agendada,
            tipo_servico=tipo_servico, valor_cobrado=valor, desconto_aplicado=aplicar_desconto
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
            if a.status != 'Cancelado' and a.status != 'Lavagem Concluída' and a.desconto_aplicado:
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
            for p in Produto.query.all():
                if p.estoque_atual >= p.gasto_medio_lavagem:
                    p.estoque_atual -= p.gasto_medio_lavagem
                    custo += p.custo_por_dose
            a.custo_total_produtos = custo
        
        if a.cliente.indicado_por_id:
            lavagens_concluidas = Agendamento.query.filter_by(cliente_id=a.cliente.id, status='Lavagem Concluída').count()
            if lavagens_concluidas == 1:
                padrinho = Cliente.query.get(a.cliente.indicado_por_id)
                if padrinho:
                    padrinho.qtd_descontos += 1
    
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
        # Criação de novo produto
        db.session.add(Produto(
            nome=request.form.get('nome'), 
            unidade_medida=request.form.get('unidade'),
            custo_compra=float(request.form.get('custo')), 
            quantidade_compra=float(request.form.get('qtd_compra')),
            gasto_medio_lavagem=float(request.form.get('gasto_medio')), 
            estoque_atual=float(request.form.get('estoque_inicial')),
            link_compra=request.form.get('link_compra') # Salva o link
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
            db.session.delete(prod)
            db.session.commit()
            flash('Produto excluído.', 'success')
    except Exception as e:
        flash(f'Erro ao excluir: {e}', 'error')
    return redirect(url_for('gerenciar_produtos'))

@app.route('/clientes')
def listar_clientes():
    clientes_brutos = Cliente.query.all()
    clientes_processados = []
    
    for c in clientes_brutos:
        agendamentos_recentes = sorted(c.agendamentos, key=lambda x: x.data_agendada, reverse=True)
        lavagens_concluidas = [a for a in c.agendamentos if a.status == 'Lavagem Concluída']
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
