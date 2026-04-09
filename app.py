from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import os
import re
from functools import wraps
from dotenv import load_dotenv
import pyodbc
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv("credencial.env")


def _clean(x):
    return x.strip() if isinstance(x, str) else x


DB_SERVER = _clean(os.getenv("DB_SERVER"))
DB_DATABASE = _clean(os.getenv("DB_DATABASE"))
DB_USERNAME = _clean(os.getenv("DB_USERNAME"))
DB_PASSWORD = _clean(os.getenv("DB_PASSWORD"))

if not all([DB_SERVER, DB_DATABASE, DB_USERNAME, DB_PASSWORD]):
    raise RuntimeError("Credenciais do banco não carregadas. Verifique o credencial.env")


conn_str = (
    "Driver={ODBC Driver 18 for SQL Server};"
    f"Server=tcp:{DB_SERVER},1433;"
    f"Database={DB_DATABASE};"
    f"Uid={DB_USERNAME};"
    f"Pwd={DB_PASSWORD};"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
    "Connection Timeout=120;"
    "Login Timeout=120;"
)


def get_conn():
    return pyodbc.connect(conn_str, timeout=120)


def only_digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def senha_forte_valida(senha: str) -> bool:
    if len(senha) < 8:
        return False
    if not re.search(r"[A-Z]", senha):
        return False
    if not re.search(r"[a-z]", senha):
        return False
    if not re.search(r"\d", senha):
        return False
    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=/\\[\];\'`~]', senha):
        return False
    return True


app = Flask(__name__)
app.secret_key = "clara-lucatti-dev"


def login_required(tipo=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login_page"))

            if tipo and session.get("user_tipo") != tipo:
                return redirect(url_for("login_page"))

            return f(*args, **kwargs)
        return wrapper
    return decorator


# ========== PÁGINAS PRINCIPAIS ==========

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET"])
def login_page():
    if "user_id" in session:
        tipo = session.get("user_tipo")

        if tipo == "aluno":
            return redirect(url_for("aluno_page"))
        elif tipo == "administrador":
            return redirect(url_for("admin_page"))
        elif tipo == "professor":
            return redirect(url_for("professor_page"))

    return render_template("login.html")


@app.route("/aluno")
@login_required(tipo="aluno")
def aluno_page():
    return render_template(
        "aluno.html",
        user_name=session.get("user_name", "Aluno"),
        user_tipo=session.get("user_tipo", "aluno")
    )


@app.route("/admin")
@login_required(tipo="administrador")
def admin_page():
    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id_cadastro, nome_completo, email, cpf, tipo_cadastro
            FROM cadastro
            ORDER BY tipo_cadastro, nome_completo
        """)
        usuarios = []
        for row in cursor.fetchall():
            usuarios.append({
                "id_cadastro": row[0],
                "nome_completo": row[1],
                "email": row[2],
                "cpf": row[3],
                "tipo_cadastro": row[4]
            })

        cursor.execute("""
            SELECT id_cadastro, nome_completo, tipo_cadastro
            FROM cadastro
            WHERE tipo_cadastro IN ('professor', 'administrador')
            ORDER BY nome_completo
        """)
        responsaveis_modalidade = []
        for row in cursor.fetchall():
            responsaveis_modalidade.append({
                "id_cadastro": row[0],
                "nome_completo": row[1],
                "tipo_cadastro": row[2]
            })

        cursor.execute("""
            SELECT id_professora, nome, instagram
            FROM professoras
            WHERE ativo = 1
            ORDER BY nome
        """)
        professoras = [{"id_professora": r[0], "nome": r[1], "instagram": r[2] or ""} for r in cursor.fetchall()]

        cursor.execute("""
            SELECT m.id_modalidade,
                   m.titulo,
                   m.resumo,
                   m.descricao,
                   m.nivel,
                   m.id_professora,
                   p.nome,
                   p.instagram
            FROM modalidades m
            LEFT JOIN professoras p ON p.id_professora = m.id_professora
            WHERE m.ativo = 1
            ORDER BY m.titulo
        """)
        modalidades = [{
            "id_modalidade": r[0],
            "titulo": r[1],
            "resumo": r[2] or "",
            "descricao": r[3] or "",
            "nivel": r[4] or "",
            "id_professora": r[5],
            "professora": r[6] or "",
            "instagram_prof": r[7] or ""
        } for r in cursor.fetchall()]

        cursor.execute("SELECT id_sala, nome, descricao FROM salas WHERE ativo = 1 ORDER BY nome")
        salas = [{"id_sala": r[0], "nome": r[1], "descricao": r[2] or ""} for r in cursor.fetchall()]

        cursor.execute("""
            SELECT t.id_turma, t.nome_exibicao, m.titulo, s.nome, h.hora_inicio, h.hora_fim,
                   h.vezes_semana, h.dias_semana, p.nome
            FROM turmas t
            JOIN modalidades m ON m.id_modalidade = t.id_modalidade
            JOIN salas s ON s.id_sala = t.id_sala
            JOIN horarios h ON h.id_horario = t.id_horario
            JOIN professoras p ON p.id_professora = t.id_professora
            WHERE t.ativo = 1
            ORDER BY m.titulo, h.hora_inicio
        """)
        turmas = [{
            "id_turma": r[0],
            "nome_exibicao": r[1] or "",
            "modalidade": r[2],
            "sala": r[3],
            "hora_inicio": str(r[4])[:5],
            "hora_fim": str(r[5])[:5],
            "vezes_semana": r[6],
            "dias_semana": r[7] or "",
            "professora": r[8]
        } for r in cursor.fetchall()]

        cursor.execute("""
            SELECT id_pacote, nome, tipo_cobranca, valor, aulas_por_semana, ativo
            FROM pacotes
            ORDER BY nome
        """)
        pacotes = [{
            "id_pacote": r[0],
            "nome": r[1],
            "tipo_cobranca": r[2],
            "valor": float(r[3]),
            "aulas_por_semana": r[4],
            "ativo": bool(r[5])
        } for r in cursor.fetchall()]

        return render_template(
            "admin.html",
            user_name=session.get("user_name", "Administrador"),
            user_tipo=session.get("user_tipo", "administrador"),
            usuarios=usuarios,
            responsaveis_modalidade=responsaveis_modalidade,
            professoras=professoras,
            modalidades=modalidades,
            salas=salas,
            turmas=turmas,
            pacotes=pacotes,
        )

    except pyodbc.Error as e:
        flash(f"Erro ao carregar painel: {e}", "erro")
        return render_template(
            "admin.html",
            user_name=session.get("user_name", "Administrador"),
            user_tipo=session.get("user_tipo", "administrador"),
            usuarios=[],
            responsaveis_modalidade=[],
            professoras=[],
            modalidades=[],
            salas=[],
            turmas=[],
            pacotes=[],
        )
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/admin/modalidade", methods=["POST"])
@login_required(tipo="administrador")
def salvar_modalidade():
    data = request.get_json(silent=True) or {}

    id_modalidade = data.get("id_modalidade")  # se vier, é edição
    titulo = (data.get("titulo") or "").strip()
    resumo = (data.get("resumo") or "").strip()
    descricao = (data.get("descricao") or "").strip()
    id_professora = data.get("id_professora")
    nivel = (data.get("nivel") or "").strip()

    if not titulo:
        return jsonify(ok=False, mensagem="Informe o título da modalidade."), 400

    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        if id_modalidade:
            # UPDATE
            cursor.execute(
                """
                UPDATE modalidades
                   SET titulo = ?,
                       resumo = ?,
                       descricao = ?,
                       id_professora = ?,
                       nivel = ?
                 WHERE id_modalidade = ?
                """,
                (titulo, resumo, descricao, id_professora, nivel, id_modalidade)
            )
        else:
            # INSERT
            cursor.execute(
                """
                INSERT INTO modalidades
                    (titulo, resumo, descricao, id_professora, nivel, ativo)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (titulo, resumo, descricao, id_professora, nivel)
            )

        conn.commit()
        return jsonify(ok=True, mensagem="Modalidade salva com sucesso."), 200

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=f"Erro ao salvar modalidade: {e}"), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/admin/modalidade/<int:id_modalidade>", methods=["DELETE"])
@login_required(tipo="administrador")
def excluir_modalidade(id_modalidade):
    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
        # aqui prefiro inativar em vez de deletar duro
        cursor.execute(
            "UPDATE modalidades SET ativo = 0 WHERE id_modalidade = ?",
            (id_modalidade,)
        )
        conn.commit()
        return jsonify(ok=True, mensagem="Modalidade excluída (inativada)."), 200

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=f"Erro ao excluir modalidade: {e}"), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()      
            
@app.route("/admin/cadastrar-usuario", methods=["POST"])
@login_required(tipo="administrador")
def cadastrar_usuario_admin():
    data = request.get_json(silent=True) or {}

    nome = (data.get("nome_completo") or "").strip()
    email = (data.get("email") or "").strip().lower()
    cpf = only_digits(data.get("cpf") or "")
    senha = data.get("senha") or ""
    tipo_cadastro = (data.get("tipo_cadastro") or "").strip().lower()
    termo_imagem = 1 if bool(data.get("termo_imagem")) else 0
    termo_seguranca = 1 if bool(data.get("termo_seguranca")) else 0

    tipos_validos = {"aluno", "professor", "administrador"}

    if not nome or not email or not cpf or not senha or not tipo_cadastro:
        return jsonify(ok=False, mensagem="Preencha todos os campos obrigatórios."), 400

    if tipo_cadastro not in tipos_validos:
        return jsonify(ok=False, mensagem="Tipo de cadastro inválido."), 400

    if len(cpf) != 11 or not cpf.isdigit():
        return jsonify(ok=False, mensagem="CPF inválido."), 400

    if not senha_forte_valida(senha):
        return jsonify(
            ok=False,
            mensagem="A senha deve ter no mínimo 8 caracteres, com letra maiúscula, minúscula, número e símbolo."
        ), 400

    if tipo_cadastro == "aluno" and not termo_seguranca:
        return jsonify(ok=False, mensagem="É necessário aceitar o termo de segurança para aluno."), 400

    senha_hash = generate_password_hash(senha)

    conn = None
    cursor = None

    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM cadastro WHERE email = ?", (email,))
        if cursor.fetchone():
            return jsonify(ok=False, mensagem="Já existe um cadastro com este e-mail."), 400

        cursor.execute("SELECT 1 FROM cadastro WHERE cpf = ?", (cpf,))
        if cursor.fetchone():
            return jsonify(ok=False, mensagem="Já existe um cadastro com este CPF."), 400

        cursor.execute(
            """
            INSERT INTO cadastro (
                nome_completo,
                email,
                cpf,
                senha_hash,
                termo_imagem,
                termo_seguranca,
                tipo_cadastro
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome,
                email,
                cpf,
                senha_hash,
                termo_imagem,
                termo_seguranca,
                tipo_cadastro
            ),
        )

        conn.commit()
        return jsonify(ok=True, mensagem="Usuário cadastrado com sucesso."), 201

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=f"Erro ao salvar usuário: {str(e)}"), 500

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=f"Erro interno: {str(e)}"), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
                  
@app.route("/professor")
@login_required(tipo="professor")
def professor_page():
    return render_template(
        "professor.html",
        user_name=session.get("user_name", "Professor"),
        user_tipo=session.get("user_tipo", "professor")
    )


# ========== CONTATO ==========

@app.route("/contato", methods=["POST"])
def contato():
    nome = request.form.get("nome", "").strip()
    telefone = request.form.get("telefone", "").strip()
    email = request.form.get("email", "").strip()
    modalidade = request.form.get("modalidade", "").strip()
    mensagem = request.form.get("mensagem", "").strip()

    if not nome or not telefone or not email or not modalidade:
        flash("Por favor, preencha os campos obrigatórios.", "erro")
        return redirect(url_for("home") + "#contato")

    print({
        "nome": nome,
        "telefone": telefone,
        "email": email,
        "modalidade": modalidade,
        "mensagem": mensagem,
    })

    flash("Mensagem enviada com sucesso! Em breve entraremos em contato. ✓", "sucesso")
    return redirect(url_for("home") + "#contato")


# ========== CADASTRO DE ALUNO ==========

@app.route("/registrar-aluno", methods=["POST"])
def registrar_aluno():
    data = request.get_json(silent=True) or {}

    nome = (data.get("nome_completo") or "").strip()
    email = (data.get("email") or "").strip().lower()
    cpf = only_digits(data.get("cpf") or "")
    senha = data.get("senha") or ""
    termo_imagem = 1 if bool(data.get("termo_imagem")) else 0
    termo_seguranca = 1 if bool(data.get("termo_seguranca")) else 0

    if not nome or not email or not cpf or not senha:
        return jsonify(ok=False, mensagem="Preencha todos os campos obrigatórios."), 400

    if not termo_seguranca:
        return jsonify(ok=False, mensagem="É necessário aceitar o termo de segurança."), 400

    if len(cpf) != 11 or not cpf.isdigit():
        return jsonify(ok=False, mensagem="CPF inválido."), 400

    if not senha_forte_valida(senha):
        return jsonify(
            ok=False,
            mensagem="A senha deve ter no mínimo 8 caracteres, com letra maiúscula, minúscula, número e símbolo."
        ), 400

    senha_hash = generate_password_hash(senha)

    conn = None
    cursor = None

    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM cadastro WHERE email = ?", (email,))
        if cursor.fetchone():
            return jsonify(ok=False, mensagem="Já existe um cadastro com este e-mail."), 400

        cursor.execute("SELECT 1 FROM cadastro WHERE cpf = ?", (cpf,))
        if cursor.fetchone():
            return jsonify(ok=False, mensagem="Já existe um cadastro com este CPF."), 400

        cursor.execute(
            """
            INSERT INTO cadastro (
                nome_completo,
                email,
                cpf,
                senha_hash,
                termo_imagem,
                termo_seguranca,
                tipo_cadastro
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome,
                email,
                cpf,
                senha_hash,
                termo_imagem,
                termo_seguranca,
                "aluno"
            ),
        )
        conn.commit()

        return jsonify(ok=True, mensagem="Cadastro realizado com sucesso."), 201

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=f"Erro ao salvar cadastro no banco: {str(e)}"), 500

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=f"Erro interno: {str(e)}"), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ========== LOGIN REAL ==========

@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    senha = data.get("senha") or ""

    if not email or not senha:
        return jsonify(ok=False, mensagem="Informe e-mail e senha."), 400

    conn = None
    cursor = None

    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id_cadastro, nome_completo, senha_hash, tipo_cadastro
            FROM cadastro
            WHERE email = ?
            """,
            (email,)
        )
        row = cursor.fetchone()

        if not row:
            return jsonify(ok=False, mensagem="Usuário não encontrado."), 400

        id_cadastro, nome_completo, senha_hash_db, tipo_cadastro = row

        if not check_password_hash(senha_hash_db, senha):
            return jsonify(ok=False, mensagem="Senha inválida."), 400

        session["user_id"] = int(id_cadastro)
        session["user_name"] = nome_completo
        session["user_tipo"] = tipo_cadastro

        if tipo_cadastro == "aluno":
            destino = url_for("aluno_page")
        elif tipo_cadastro == "administrador":
            destino = url_for("admin_page")
        elif tipo_cadastro == "professor":
            destino = url_for("professor_page")
        else:
            destino = url_for("login_page")

        return jsonify(
            ok=True,
            mensagem="Login realizado com sucesso.",
            redirect_url=destino,
            tipo_cadastro=tipo_cadastro
        ), 200

    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=f"Erro ao consultar login: {str(e)}"), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(ok=True, mensagem="Logout realizado."), 200


if __name__ == "__main__":
    app.run(debug=True)