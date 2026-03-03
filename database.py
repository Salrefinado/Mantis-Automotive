from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# ---------------------------
# TABELA DE ASSOCIAÇÃO: SERVIÇO <-> PRODUTO
# ---------------------------
servico_produto_assoc = db.Table('servico_produto',
    db.Column('servico_id', db.Integer, db.ForeignKey('servicos.id'), primary_key=True),
    db.Column('produto_id', db.Integer, db.ForeignKey('produtos.id'), primary_key=True)
)

# ---------------------------
# MODELO: CLIENTES
# ---------------------------
class Cliente(db.Model):
    __tablename__ = 'clientes'
    
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    telefone = db.Column(db.String(20), nullable=False, unique=True)
    endereco = db.Column(db.String(200), nullable=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)
    
    indicado_por_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=True)
    
    # Alterado para Inteiro para gerenciar fila de descontos (1 uso por vez)
    qtd_descontos = db.Column(db.Integer, default=0)
    
    # Novos Campos de CRM
    preferencias = db.Column(db.Text, nullable=True)
    feedback_texto = db.Column(db.Text, nullable=True)
    feedback_estrelas = db.Column(db.Integer, default=0)
    
    motos = db.relationship('Moto', backref='dono', lazy=True)
    agendamentos = db.relationship('Agendamento', backref='cliente', lazy=True)
    indicacoes = db.relationship('Cliente', backref=db.backref('padrinho', remote_side=[id]), lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'telefone': self.telefone,
            'endereco': self.endereco,
            'qtd_descontos': self.qtd_descontos,
            'indicado_por': self.padrinho.nome if self.padrinho else "Sem indicação",
            'preferencias': self.preferencias if self.preferencias else ""
        }

# ---------------------------
# MODELO: VEÍCULOS (Motos)
# ---------------------------
class Moto(db.Model):
    __tablename__ = 'motos'
    
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    placa = db.Column(db.String(10), nullable=True)
    modelo = db.Column(db.String(50), nullable=False)
    marca = db.Column(db.String(50), nullable=True)
    categoria = db.Column(db.String(20), default='Naked') 
    observacoes = db.Column(db.String(200), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'modelo': self.modelo,
            'placa': self.placa,
            'marca': self.marca,
            'categoria': self.categoria
        }

# ---------------------------
# MODELO: PRODUTOS
# ---------------------------
class Produto(db.Model):
    __tablename__ = 'produtos'
    
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    unidade_medida = db.Column(db.String(10), nullable=False)
    estoque_atual = db.Column(db.Float, default=0.0)
    custo_compra = db.Column(db.Float, nullable=False)
    quantidade_compra = db.Column(db.Float, nullable=False)
    gasto_medio_lavagem = db.Column(db.Float, nullable=False)
    ponto_pedido = db.Column(db.Float, default=10.0)
    
    # Novo campo para link de compra (opcional)
    link_compra = db.Column(db.String(300), nullable=True)

    @property
    def custo_por_dose(self):
        if self.quantidade_compra > 0:
            return (self.custo_compra / self.quantidade_compra) * self.gasto_medio_lavagem
        return 0.0

# ---------------------------
# MODELO: SERVIÇOS (Preços Editáveis e Receita de Produtos)
# ---------------------------
class Servico(db.Model):
    __tablename__ = 'servicos'
    
    id = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(50), nullable=False) # Ex: Naked, Sport
    nome = db.Column(db.String(100), nullable=False)     # Ex: Standard Naked
    valor = db.Column(db.Float, nullable=False)          # Ex: 50.00
    
    # Relacionamento com os Produtos (Receita do Serviço)
    produtos_vinculados = db.relationship('Produto', secondary=servico_produto_assoc, lazy='subquery',
        backref=db.backref('servicos', lazy=True))

# ---------------------------
# MODELO: AGENDAMENTOS
# ---------------------------
class Agendamento(db.Model):
    __tablename__ = 'agendamentos'
    
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    moto_id = db.Column(db.Integer, db.ForeignKey('motos.id'), nullable=False)
    
    moto = db.relationship('Moto', backref='agendamentos_moto')
    
    data_agendada = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='Agendado') 
    
    tipo_servico = db.Column(db.String(50), nullable=True)
    valor_cobrado = db.Column(db.Float, nullable=False)
    desconto_aplicado = db.Column(db.Boolean, default=False)
    
    tempo_inicio = db.Column(db.DateTime, nullable=True)
    tempo_fim = db.Column(db.DateTime, nullable=True)
    
    custo_total_produtos = db.Column(db.Float, default=0.0)
    gastos_extras = db.Column(db.Float, default=0.0)
    
    # --- NOVOS CAMPOS: FINANCEIRO E PAGAMENTO ---
    forma_pagamento_prevista = db.Column(db.String(50), nullable=True)
    forma_pagamento_real = db.Column(db.String(50), nullable=True)
    parcelas = db.Column(db.Integer, default=1)
    taxa_aplicada = db.Column(db.Float, default=0.0)
    valor_liquido = db.Column(db.Float, nullable=True)
    
    midias = db.relationship('MidiaAgendamento', backref='agendamento', lazy=True)

    @property
    def dia_para_agrupamento(self):
        return self.data_agendada.date()

# ---------------------------
# MODELO: MÍDIA
# ---------------------------
class MidiaAgendamento(db.Model):
    __tablename__ = 'midia_agendamento'
    
    id = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey('agendamentos.id'), nullable=False)
    caminho_arquivo = db.Column(db.String(300), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)

# ---------------------------
# MODELO: CONFIGURAÇÃO FINANCEIRA (CUSTOS FIXOS)
# ---------------------------
class ConfiguracaoFinanceira(db.Model):
    __tablename__ = 'configuracao_financeira'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Custos Fixos (Mensais)
    aluguel_iptu = db.Column(db.Float, default=100.0)
    pro_labore = db.Column(db.Float, default=6000.0)
    agua_energia_base = db.Column(db.Float, default=0.0)
    internet_telefone = db.Column(db.Float, default=0.0)
    mei_impostos = db.Column(db.Float, default=0.0)
    marketing = db.Column(db.Float, default=0.0)
    seguro = db.Column(db.Float, default=0.0)
    
    # Taxas e Regras (Porcentagens e Valores)
    taxa_debito = db.Column(db.Float, default=1.09)
    taxa_credito_vista = db.Column(db.Float, default=2.99)
    taxa_credito_parcelado = db.Column(db.Float, default=7.99)
    minimo_parcelamento = db.Column(db.Float, default=300.0)
    
    # Operação
    capacidade_mensal = db.Column(db.Integer, default=40)

# ---------------------------
# MODELO: FECHAMENTO MENSAL (DRE)
# ---------------------------
class FechamentoMensal(db.Model):
    __tablename__ = 'fechamento_mensal'
    
    id = db.Column(db.Integer, primary_key=True)
    mes_ano = db.Column(db.String(20), nullable=False, unique=True) # Ex: '2026-02'
    data_fechamento = db.Column(db.DateTime, default=datetime.utcnow)
    
    total_faturado = db.Column(db.Float, default=0.0)
    custos_totais = db.Column(db.Float, default=0.0)
    lucro_real = db.Column(db.Float, default=0.0)
    deficit_acumulado = db.Column(db.Float, default=0.0) # Valor negativo que transita para o próximo mês
