from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

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
    saldo_desconto = db.Column(db.Boolean, default=False)
    
    motos = db.relationship('Moto', backref='dono', lazy=True)
    agendamentos = db.relationship('Agendamento', backref='cliente', lazy=True)
    indicacoes = db.relationship('Cliente', backref=db.backref('padrinho', remote_side=[id]), lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'telefone': self.telefone,
            'endereco': self.endereco,
            'tem_desconto': self.saldo_desconto,
            'indicado_por': self.padrinho.nome if self.padrinho else "Sem indicação"
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

    @property
    def custo_por_dose(self):
        if self.quantidade_compra > 0:
            return (self.custo_compra / self.quantidade_compra) * self.gasto_medio_lavagem
        return 0.0

# ---------------------------
# NOVO MODELO: SERVIÇOS (Preços Editáveis)
# ---------------------------
class Servico(db.Model):
    __tablename__ = 'servicos'
    
    id = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(50), nullable=False) # Ex: Naked, Sport
    nome = db.Column(db.String(100), nullable=False)     # Ex: Standard Naked
    valor = db.Column(db.Float, nullable=False)          # Ex: 50.00

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