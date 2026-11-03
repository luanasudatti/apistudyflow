"""StudyFlow API - v1: autenticação, matérias, horários, frequência e tarefas."""
import os
import re
from datetime import date, datetime, timedelta

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import (JWTManager, create_access_token,
                                get_jwt_identity, jwt_required)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///studyflow.db"
app.config["JWT_SECRET_KEY"] = os.environ.get(
    "JWT_SECRET_KEY", "chave-so-para-desenvolvimento-troque-depois")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=7)

db = SQLAlchemy(app)
jwt = JWTManager(app)
CORS(app)  # aberto só para desenvolvimento; restrinja em produção

STATUS_FREQ = ("present", "absent")
ESTADOS = ("pendente", "em_andamento", "concluida")
PRIORIDADES = ("baixa", "media", "alta")


# ----------------------------------------------------------------- modelos
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def to_dict(self):
        return {"id": self.id, "nome": self.nome, "email": self.email}


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    professor = db.Column(db.String(120))
    absence_limit = db.Column(db.Integer, nullable=False, default=0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    slots = db.relationship("ScheduleSlot", backref="subject",
                            cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "nome": self.nome, "professor": self.professor,
                "limite_faltas": self.absence_limit}


class ScheduleSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    weekday = db.Column(db.Integer, nullable=False)  # 0 = segunda
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    period_number = db.Column(db.Integer)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"),
                           nullable=False)
    attendances = db.relationship("Attendance", backref="slot",
                                  cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "materia_id": self.subject_id,
                "dia_semana": self.weekday,
                "inicio": self.start_time.strftime("%H:%M"),
                "fim": self.end_time.strftime("%H:%M"),
                "periodo": self.period_number}


class Attendance(db.Model):
    __table_args__ = (db.UniqueConstraint("schedule_slot_id", "date"),)
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), nullable=False)
    schedule_slot_id = db.Column(db.Integer, db.ForeignKey("schedule_slot.id"),
                                 nullable=False)

    def to_dict(self):
        return {"id": self.id, "horario_id": self.schedule_slot_id,
                "data": self.date.isoformat(), "status": self.status}


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(160), nullable=False)
    descricao = db.Column(db.String(1000))
    prazo = db.Column(db.Date, nullable=False)
    prioridade = db.Column(db.String(10), nullable=False, default="media")
    estado = db.Column(db.String(15), nullable=False, default="pendente")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    def to_dict(self):
        return {"id": self.id, "titulo": self.titulo,
                "descricao": self.descricao, "prazo": self.prazo.isoformat(),
                "prioridade": self.prioridade, "estado": self.estado,
                "vencida": self.prazo < date.today()
                and self.estado != "concluida"}


# ------------------------------------------------------------------- erros
class ApiError(Exception):
    def __init__(self, codigo, mensagem, status=400):
        self.codigo, self.mensagem, self.status = codigo, mensagem, status


@app.errorhandler(ApiError)
def _api_error(e):
    return jsonify(erro=e.codigo, mensagem=e.mensagem), e.status


@app.errorhandler(HTTPException)
def _http_error(e):
    return jsonify(erro=f"http_{e.code}", mensagem=e.description), e.code


@app.errorhandler(Exception)
def _inesperado(e):
    app.logger.exception("Erro inesperado")  # detalhe só no log
    return jsonify(erro="erro_interno",
                   mensagem="Erro inesperado. Tente novamente."), 500


@jwt.unauthorized_loader
@jwt.invalid_token_loader
def _token_ausente(_motivo):
    return jsonify(erro="nao_autenticado",
                   mensagem="Faça login para continuar."), 401


@jwt.expired_token_loader
def _token_expirado(_header, _payload):
    return jsonify(erro="token_expirado",
                   mensagem="Sessão expirada. Entre novamente."), 401


# ---------------------------------------------------------------- helpers
def corpo():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        raise ApiError("dados_invalidos", "Envie um JSON válido.")
    return dados


def uid():
    return int(get_jwt_identity())


def texto(dados, campo, obrigatorio=False, max_len=120):
    valor = dados.get(campo)
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        if obrigatorio:
            raise ApiError("dados_invalidos", f"Informe o campo '{campo}'.")
        return None
    if not isinstance(valor, str) or len(valor.strip()) > max_len:
        raise ApiError("dados_invalidos", f"Campo '{campo}' inválido.")
    return valor.strip()


def inteiro(dados, campo, minimo, maximo=None):
    valor = dados.get(campo)
    if (not isinstance(valor, int) or isinstance(valor, bool)
            or valor < minimo or (maximo is not None and valor > maximo)):
        raise ApiError("dados_invalidos", f"Campo '{campo}' inválido.")
    return valor


def data_iso(valor, campo="data"):
    try:
        return datetime.strptime(str(valor), "%Y-%m-%d").date()
    except ValueError:
        raise ApiError("dados_invalidos", f"Informe '{campo}' como AAAA-MM-DD.")


def hora(valor, campo):
    try:
        return datetime.strptime(str(valor), "%H:%M").time()
    except ValueError:
        raise ApiError("dados_invalidos", f"Informe '{campo}' como HH:MM.")


def escolha(valor, campo, validos):
    if valor not in validos:
        raise ApiError("dados_invalidos",
                       f"'{campo}' deve ser um de: {', '.join(validos)}.")
    return valor


def nao_encontrado():
    return ApiError("nao_encontrado", "Registro não encontrado.", 404)


def materia_do_usuario(id_):
    m = db.session.get(Subject, id_)
    if not m or m.user_id != uid():
        raise nao_encontrado()
    return m


def horario_do_usuario(id_):
    s = db.session.get(ScheduleSlot, id_)
    if not s or s.subject.user_id != uid():
        raise nao_encontrado()
    return s


def frequencia_do_usuario(id_):
    a = db.session.get(Attendance, id_)
    if not a or a.slot.subject.user_id != uid():
        raise nao_encontrado()
    return a


def tarefa_do_usuario(id_):
    t = db.session.get(Task, id_)
    if not t or t.user_id != uid():
        raise nao_encontrado()
    return t


def salvar():
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ApiError("conflito", "Registro duplicado.", 409)

@app.get("/")
def raiz():
    return jsonify(status="ok", app="StudyFlow API")

# ------------------------------------------------------------------- auth
@app.post("/auth/cadastro")
def cadastro():
    d = corpo()
    nome = texto(d, "nome", True)
    email = (texto(d, "email", True, 160) or "").lower()
    senha = d.get("senha")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ApiError("dados_invalidos", "E-mail inválido.")
    if not isinstance(senha, str) or len(senha) < 8:
        raise ApiError("dados_invalidos", "A senha precisa de 8+ caracteres.")
    if User.query.filter_by(email=email).first():
        raise ApiError("conflito", "E-mail já cadastrado.", 409)
    user = User(nome=nome, email=email,
                password_hash=generate_password_hash(senha))
    db.session.add(user)
    salvar()
    return jsonify(user.to_dict()), 201


@app.post("/auth/login")
def login():
    d = corpo()
    user = User.query.filter_by(email=str(d.get("email", "")).lower()).first()
    if not user or not check_password_hash(user.password_hash,
                                           str(d.get("senha", ""))):
        raise ApiError("credenciais_invalidas",
                       "E-mail ou senha incorretos.", 401)
    token = create_access_token(identity=str(user.id))
    return jsonify(token=token, usuario=user.to_dict())


@app.get("/auth/me")
@jwt_required()
def me():
    return jsonify(db.get_or_404(User, uid()).to_dict())


# --------------------------------------------------------------- matérias
@app.get("/materias")
@jwt_required()
def listar_materias():
    ms = Subject.query.filter_by(user_id=uid()).order_by(Subject.nome).all()
    return jsonify([m.to_dict() for m in ms])


@app.post("/materias")
@jwt_required()
def criar_materia():
    d = corpo()
    m = Subject(nome=texto(d, "nome", True), professor=texto(d, "professor"),
                absence_limit=inteiro(d, "limite_faltas", 0), user_id=uid())
    db.session.add(m)
    salvar()
    return jsonify(m.to_dict()), 201


@app.get("/materias/<int:id_>")
@jwt_required()
def ver_materia(id_):
    return jsonify(materia_do_usuario(id_).to_dict())


@app.patch("/materias/<int:id_>")
@jwt_required()
def editar_materia(id_):
    m, d = materia_do_usuario(id_), corpo()
    if "nome" in d:
        m.nome = texto(d, "nome", True)
    if "professor" in d:
        m.professor = texto(d, "professor")
    if "limite_faltas" in d:
        m.absence_limit = inteiro(d, "limite_faltas", 0)
    salvar()
    return jsonify(m.to_dict())


@app.delete("/materias/<int:id_>")
@jwt_required()
def excluir_materia(id_):
    # Decisão: exclui também horários e frequência da matéria (cascade).
    db.session.delete(materia_do_usuario(id_))
    salvar()
    return "", 204


# ---------------------------------------------------------------- horário
def checar_conflito(dia, ini, fim, ignorar_id=None):
    q = (ScheduleSlot.query.join(Subject)
         .filter(Subject.user_id == uid(), ScheduleSlot.weekday == dia,
                 ScheduleSlot.start_time < fim, ScheduleSlot.end_time > ini))
    if ignorar_id:
        q = q.filter(ScheduleSlot.id != ignorar_id)
    if q.first():
        raise ApiError("conflito", "Já existe uma aula nesse horário.", 409)


@app.get("/horario")
@jwt_required()
def listar_horario():
    ss = (ScheduleSlot.query.join(Subject).filter(Subject.user_id == uid())
          .order_by(ScheduleSlot.weekday, ScheduleSlot.start_time).all())
    return jsonify([s.to_dict() for s in ss])


@app.post("/horario")
@jwt_required()
def criar_horario():
    d = corpo()
    materia = materia_do_usuario(d.get("materia_id"))
    dia = inteiro(d, "dia_semana", 0, 6)
    ini, fim = hora(d.get("inicio"), "inicio"), hora(d.get("fim"), "fim")
    if ini >= fim:
        raise ApiError("dados_invalidos", "O início deve ser antes do fim.")
    checar_conflito(dia, ini, fim)
    periodo = inteiro(d, "periodo", 1) if "periodo" in d else None
    s = ScheduleSlot(subject_id=materia.id, weekday=dia, start_time=ini,
                     end_time=fim, period_number=periodo)
    db.session.add(s)
    salvar()
    return jsonify(s.to_dict()), 201


@app.patch("/horario/<int:id_>")
@jwt_required()
def editar_horario(id_):
    s, d = horario_do_usuario(id_), corpo()
    dia = inteiro(d, "dia_semana", 0, 6) if "dia_semana" in d else s.weekday
    ini = hora(d["inicio"], "inicio") if "inicio" in d else s.start_time
    fim = hora(d["fim"], "fim") if "fim" in d else s.end_time
    if ini >= fim:
        raise ApiError("dados_invalidos", "O início deve ser antes do fim.")
    checar_conflito(dia, ini, fim, ignorar_id=s.id)
    s.weekday, s.start_time, s.end_time = dia, ini, fim
    if "periodo" in d:
        s.period_number = inteiro(d, "periodo", 1)
    salvar()
    return jsonify(s.to_dict())


@app.delete("/horario/<int:id_>")
@jwt_required()
def excluir_horario(id_):
    db.session.delete(horario_do_usuario(id_))
    salvar()
    return "", 204


# ------------------------------------------------------------- frequência
def faltas_da_materia(materia):
    return (Attendance.query.join(ScheduleSlot)
            .filter(ScheduleSlot.subject_id == materia.id,
                    Attendance.status == "absent").count())


@app.post("/frequencia")
@jwt_required()
def registrar_frequencia():
    d = corpo()
    slot = horario_do_usuario(d.get("horario_id"))
    dia = data_iso(d.get("data"))
    status = escolha(d.get("status"), "status", STATUS_FREQ)
    if Attendance.query.filter_by(schedule_slot_id=slot.id, date=dia).first():
        raise ApiError("conflito", "Já existe registro para essa aula e data.",
                       409)
    a = Attendance(schedule_slot_id=slot.id, date=dia, status=status)
    db.session.add(a)
    salvar()
    return jsonify(a.to_dict()), 201


@app.get("/frequencia")
@jwt_required()
def listar_frequencia():
    q = (Attendance.query.join(ScheduleSlot).join(Subject)
         .filter(Subject.user_id == uid()))
    if request.args.get("data"):
        q = q.filter(Attendance.date == data_iso(request.args["data"]))
    return jsonify([a.to_dict() for a in q.order_by(Attendance.date).all()])


@app.get("/frequencia/resumo")
@jwt_required()
def resumo_frequencia():
    saida = []
    for m in Subject.query.filter_by(user_id=uid()).order_by(Subject.nome):
        faltas = faltas_da_materia(m)
        saldo = m.absence_limit - faltas
        situacao = ("ultrapassado" if saldo < 0 else "limite_atingido"
                    if saldo == 0 else "atencao" if saldo <= 2 else "ok")
        saida.append({"materia_id": m.id, "materia": m.nome, "faltas": faltas,
                      "limite_faltas": m.absence_limit, "saldo": saldo,
                      "situacao": situacao})
    return jsonify(saida)


@app.get("/frequencia/pode-faltar")
@jwt_required()
def pode_faltar():
    """Para cada matéria com aula amanhã: quanto sobra se faltar."""
    amanha = date.today() + timedelta(days=1)
    saida = []
    for m in Subject.query.filter_by(user_id=uid()).order_by(Subject.nome):
        aulas = ScheduleSlot.query.filter_by(
            subject_id=m.id, weekday=amanha.weekday()).count()
        if not aulas:
            continue
        saldo = m.absence_limit - faltas_da_materia(m)
        apos = saldo - aulas
        saida.append({"materia_id": m.id, "materia": m.nome,
                      "saldo_atual": saldo, "aulas_amanha": aulas,
                      "saldo_se_faltar": apos,
                      "risco": "estoura" if apos < 0 else
                      "no_limite" if apos == 0 else "ok"})
    return jsonify({"data": amanha.isoformat(), "materias": saida})


@app.patch("/frequencia/<int:id_>")
@jwt_required()
def corrigir_frequencia(id_):
    a, d = frequencia_do_usuario(id_), corpo()
    a.status = escolha(d.get("status"), "status", STATUS_FREQ)
    salvar()
    return jsonify(a.to_dict())


@app.delete("/frequencia/<int:id_>")
@jwt_required()
def excluir_frequencia(id_):
    db.session.delete(frequencia_do_usuario(id_))
    salvar()
    return "", 204


# ----------------------------------------------------------------- tarefas
@app.get("/tarefas")
@jwt_required()
def listar_tarefas():
    q = Task.query.filter_by(user_id=uid())
    if request.args.get("estado"):
        q = q.filter_by(estado=escolha(request.args["estado"], "estado",
                                       ESTADOS))
    return jsonify([t.to_dict() for t in q.order_by(Task.prazo).all()])


@app.post("/tarefas")
@jwt_required()
def criar_tarefa():
    d = corpo()
    t = Task(titulo=texto(d, "titulo", True, 160),
             descricao=texto(d, "descricao", max_len=1000),
             prazo=data_iso(d.get("prazo"), "prazo"),
             prioridade=escolha(d.get("prioridade", "media"), "prioridade",
                                PRIORIDADES),
             estado=escolha(d.get("estado", "pendente"), "estado", ESTADOS),
             user_id=uid())
    db.session.add(t)
    salvar()
    return jsonify(t.to_dict()), 201


@app.patch("/tarefas/<int:id_>")
@jwt_required()
def editar_tarefa(id_):
    t, d = tarefa_do_usuario(id_), corpo()
    if "titulo" in d:
        t.titulo = texto(d, "titulo", True, 160)
    if "descricao" in d:
        t.descricao = texto(d, "descricao", max_len=1000)
    if "prazo" in d:
        t.prazo = data_iso(d["prazo"], "prazo")
    if "prioridade" in d:
        t.prioridade = escolha(d["prioridade"], "prioridade", PRIORIDADES)
    if "estado" in d:
        t.estado = escolha(d["estado"], "estado", ESTADOS)
    salvar()
    return jsonify(t.to_dict())


@app.delete("/tarefas/<int:id_>")
@jwt_required()
def excluir_tarefa(id_):
    db.session.delete(tarefa_do_usuario(id_))
    salvar()
    return "", 204


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)  # 127.0.0.1:5000, só desenvolvimento
