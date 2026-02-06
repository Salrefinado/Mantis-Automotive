import os
import locale
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime
from database import db, Cliente, Moto, Agendamento, Produto, MidiaAgendamento, Servico

app = Flask(__name__)

# --- Configuração de Banco de Dados (Vercel/PostgreSQL vs Local/SQLite) ---
# A Vercel fornece a conexão via variável de ambiente DATABASE_URL
database_url = os.environ.get('DATABASE_URL')

# Ajuste necessário para compatibilidade com bibliotecas recentes (postgres:// -> postgresql://)
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave-secreta-trocar-em-producao')
app.config['SQLALCHEMY_DATABASE_URI'] = database_url if database_url else 'sqlite:///lavagem.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'

db.init_app(app)

# Função para criar os serviços padrão se o banco estiver vazio
def inicializar_servicos_padrao():
    # Envolvemos em try/except para evitar erros em migrações ou conexões instáveis
    try:
        # Verifica se a tabela existe e está vazia
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
            print("--- Serviços Padrão Criados no Banco de Dados ---")
    except Exception as e:
        print(f"Nota: Tabela de serviços ainda não pronta ou erro de conexão: {e}")

# Criação das tabelas no contexto da aplicação
with app.app_context():
    db.create_all()
    inicializar_servicos_padrao()

# Tenta criar a pasta de uploads (pode falhar em sistemas de arquivo somente leitura como Vercel, mas não trava o app)
try:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
except OSError:
    pass

# Filtro de Data em Português
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
    
    # Busca preços do banco e formata para o Javascript do Dashboard
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
    
    # Busca a lista de serviços para edição
    servicos = Servico.query.order_by(Servico.categoria, Servico.valor).all()
    
    return render_template('financeiro.html', 
                           faturamento=faturamento_total, 
                           custos=custo_produtos_total, 
                           lucro=lucro_estimado,
                           ticket_medio=ticket_medio,
                           qtd_servicos=len(concluidos),
                           servicos=servicos)

# --- ATUALIZAR PREÇO ---
@app.route('/atualizar_preco', methods=['POST'])
def atualizar_preco():
    try:
        servico_id = request.form.get('id')
        novo_valor = request.form.get('valor')
        
        servico = Servico.query.get(servico_id)
        if servico:
            servico.valor = float(novo_valor)
            db.session.commit()
            flash(f'Preço de {servico.nome} atualizado para R$ {servico.valor:.2f}', 'success')
        else:
            flash('Serviço não encontrado.', 'error')
            
    except Exception as e:
        flash(f'Erro ao atualizar preço: {str(e)}', 'error')
        
    return redirect(url_for('financeiro'))

# --- NOVO AGENDAMENTO ---
@app.route('/novo_agendamento', methods=['POST'])
def novo_agendamento():
    try:
        cliente_id = request.form.get('cliente_id')
        moto_id = request.form.get('moto_id')
        data_str = request.form.get('data_dia')
        hora_str = request.form.get('data_hora')
        data_agendada = datetime.strptime(f"{data_str} {hora_str}", '%Y-%m-%d %H:%M')
        tipo_servico = request.form.get('tipo_servico')
        valor = float(request.form.get('valor'))
        
        cliente = Cliente.query.get(cliente_id)
        aplicar_desconto = False
        if cliente.saldo_desconto:
            valor = valor * 0.90
            aplicar_desconto = True
            cliente.saldo_desconto = False 
        
        novo_agendamento = Agendamento(
            cliente_id=cliente_id, moto_id=moto_id, data_agendada=data_agendada,
            tipo_servico=tipo_servico, valor_cobrado=valor, desconto_aplicado=aplicar_desconto
        )
        db.session.add(novo_agendamento)
        db.session.commit()
        flash('Agendamento realizado!', 'success')
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
        a.status = 'Cancelado'
        db.session.commit()
        flash('Cancelado.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/cadastrar_cliente', methods=['POST'])
def cadastrar_cliente():
    try:
        novo = Cliente(nome=request.form.get('nome'), telefone=request.form.get('telefone'), endereco=request.form.get('endereco'))
        quem = request.form.get('quem_indicou_id')
        if quem:
            novo.indicado_por_id = quem
            padrinho = Cliente.query.get(quem)
            if padrinho: padrinho.saldo_desconto = True
        db.session.add(novo)
        db.session.flush()
        moto = Moto(cliente_id=novo.id, modelo=request.form.get('modelo_moto'), placa=request.form.get('placa_moto'), categoria=request.form.get('categoria_moto'))
        db.session.add(moto)
        db.session.commit()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'cliente': {'id': novo.id, 'nome': novo.nome, 'telefone': novo.telefone}, 'moto': moto.to_dict()})

        flash('Cadastrado!', 'success')
        if request.headers.get("Referer") and "clientes" in request.headers.get("Referer"): return redirect(url_for('listar_clientes'))
    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'success': False, 'error': str(e)}), 400
        flash(f'Erro: {e}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/api/buscar_cliente')
def buscar_cliente():
    termo = request.args.get('q', '')
    clientes = Cliente.query.filter(Cliente.nome.ilike(f'%{termo}%')).limit(10).all()
    return jsonify([{'id':c.id, 'text':f"{c.nome} - {c.telefone}", 'motos':[m.to_dict() for m in c.motos], 'tem_desconto':c.saldo_desconto} for c in clientes])

@app.route('/atualizar_status/<int:id>/<status>', methods=['POST'])
def atualizar_status(id, status):
    a = Agendamento.query.get(id)
    horario_str = request.form.get('horario')
    
    if horario_str:
        agora = datetime.now()
        horario_dt = datetime.strptime(horario_str, '%H:%M').replace(year=agora.year, month=agora.month, day=agora.day)
    else:
        horario_dt = datetime.now()

    if status == 'Em Lavagem':
        a.status = status
        a.tempo_inicio = horario_dt
    elif status == 'Lavagem Concluída':
        a.status = status
        a.tempo_fim = horario_dt
        if a.custo_total_produtos == 0:
            custo = 0
            for p in Produto.query.all():
                if p.estoque_atual >= p.gasto_medio_lavagem:
                    p.estoque_atual -= p.gasto_medio_lavagem
                    custo += p.custo_por_dose
            a.custo_total_produtos = custo
            
    db.session.commit()
    flash(f'Status atualizado para {status} às {horario_dt.strftime("%H:%M")}', 'info')
    return redirect(url_for('dashboard'))

@app.route('/upload_midia/<int:agendamento_id>', methods=['POST'])
def upload_midia(agendamento_id):
    # Nota: Em Vercel, uploads locais são temporários e serão deletados após a execução.
    if 'arquivo' not in request.files: return 'Erro', 400
    arquivo = request.files['arquivo']
    if arquivo:
        filename = secure_filename(f"{agendamento_id}_{datetime.now().timestamp()}_{arquivo.filename}")
        arquivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        db.session.add(MidiaAgendamento(agendamento_id=agendamento_id, caminho_arquivo=filename, tipo=request.form.get('tipo')))
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/produtos', methods=['GET', 'POST'])
def gerenciar_produtos():
    if request.method == 'POST':
        db.session.add(Produto(
            nome=request.form.get('nome'), unidade_medida=request.form.get('unidade'),
            custo_compra=float(request.form.get('custo')), quantidade_compra=float(request.form.get('qtd_compra')),
            gasto_medio_lavagem=float(request.form.get('gasto_medio')), estoque_atual=float(request.form.get('estoque_inicial'))
        ))
        db.session.commit()
    return render_template('produtos.html', produtos=Produto.query.all())

@app.route('/clientes')
def listar_clientes():
    clientes_brutos = Cliente.query.all()
    clientes_processados = []
    for c in clientes_brutos:
        lavagens_concluidas = [a for a in c.agendamentos if a.status == 'Lavagem Concluída']
        clientes_processados.append({
            'dados': c, 'motos': c.motos,
            'qtd_lavagens': len(lavagens_concluidas),
            'total_gasto': sum(a.valor_cobrado for a in lavagens_concluidas),
            'midias': [m for a in lavagens_concluidas for m in a.midias]
        })
    clientes_processados.sort(key=lambda x: x['total_gasto'], reverse=True)
    return render_template('clientes.html', clientes=clientes_processados, clientes_todos=clientes_brutos)

if __name__ == '__main__': app.run(debug=True, host='0.0.0.0')