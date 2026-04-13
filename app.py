from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import os
import re
from functools import wraps
from dotenv import load_dotenv
import pyodbc
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta
from flask import jsonify, request, render_template, session
import pyodbc
import os
import re
import uuid
import unicodedata
import pyodbc
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta   # pip install python-dateutil
from flask import jsonify, request, render_template, session
import pyodbc
from flask import (
    Flask, render_template, request, jsonify, session,
    flash, current_app, url_for
)
from werkzeug.utils import secure_filename

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

# ── Pasta base das modalidades ────────────────────────────────
MODAL_ROOT = os.path.join("static", "modalidades")
IMG_EXTS   = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VID_EXTS   = {".mp4", ".mov", ".webm"}

def listar_galeria(slug):
    """Retorna lista de arquivos de imagem na pasta galeria/ do slug."""
    pasta = os.path.join(MODAL_ROOT, slug, "galeria")
    if not os.path.isdir(pasta):
        return []
    arqs = sorted(os.listdir(pasta))
    return [f for f in arqs if os.path.splitext(f)[1].lower() in IMG_EXTS]

# ========== PÁGINAS PRINCIPAIS ==========

@app.route("/")
def home():
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
 
        # ── Modalidades + galeria ──────────────────────────────
        cursor.execute("""
            SELECT
                m.id_modalidade, m.titulo, m.resumo, m.descricao,
                m.nivel, m.slug, m.foto_professora,
                c.nome_completo AS responsavel_nome,
                c.tipo_cadastro AS responsavel_tipo
            FROM modalidades m
            LEFT JOIN cadastro c ON c.id_cadastro = m.id_responsavel_cadastro
            WHERE m.ativo = 1
            ORDER BY m.titulo
        """)
        modalidades = []
        galeria_data = {}  # { id_modalidade: [url_estatica, ...] }
 
        for r in cursor.fetchall():
            slug = r[5] or ""
            gal  = listar_galeria(slug)
            # foto de capa = foto_professora ou primeira da galeria
            foto_capa = r[6] or (gal[0] if gal else "")
 
            modalidades.append({
                "id_modalidade":    r[0],
                "titulo":           r[1],
                "resumo":           r[2] or "",
                "descricao":        r[3] or "",
                "nivel":            r[4] or "",
                "slug":             slug,
                "foto_capa":        foto_capa,
                "galeria":          gal,
                "responsavel_nome": r[7] or "",
                "responsavel_tipo": r[8] or "",
            })
            # URLs para o lightbox (JSON no frontend)
            galeria_data[r[0]] = [
                f"/static/modalidades/{slug}/galeria/{f}" for f in gal
            ]
 
        # ── Professoras (professores + admins) ─────────────────
        cursor.execute("""
            SELECT DISTINCT
                c.id_cadastro, c.nome_completo, c.tipo_cadastro,
                m.slug, m.foto_professora,
                m.titulo AS modalidade_nome
            FROM cadastro c
            LEFT JOIN modalidades m ON m.id_responsavel_cadastro = c.id_cadastro AND m.ativo = 1
            WHERE c.tipo_cadastro IN ('professor', 'administrador')
            ORDER BY c.nome_completo
        """)
        _raw_prof = cursor.fetchall()
 
        # Agrupar por professor (pode ser responsável por várias modalidades)
        prof_map = {}
        for r in _raw_prof:
            pid = r[0]
            if pid not in prof_map:
                prof_map[pid] = {
                    "id_cadastro":  pid,
                    "nome_completo":r[1],
                    "tipo_cadastro":r[2],
                    "slug":         r[3] or "",
                    "foto_professora": r[4] or "",
                    "instagram":    "",
                    "modalidades":  [],
                }
            if r[5] and r[5] not in prof_map[pid]["modalidades"]:
                prof_map[pid]["modalidades"].append(r[5])
 
        professoras = list(prof_map.values())
 
        # ── Turmas com horários ────────────────────────────────
        cursor.execute("""
            SELECT
                t.id_turma,
                m.titulo AS modalidade,
                s.nome   AS sala,
                h.hora_inicio,
                h.hora_fim,
                h.dias_semana,
                c.nome_completo AS professora,
                t.capacidade_maxima
            FROM turmas t
            JOIN modalidades m ON m.id_modalidade = t.id_modalidade
            JOIN salas       s ON s.id_sala        = t.id_sala
            JOIN horarios    h ON h.id_horario     = t.id_horario
            LEFT JOIN cadastro c ON c.id_cadastro  = m.id_responsavel_cadastro
            WHERE t.ativo = 1
            ORDER BY h.hora_inicio, m.titulo
        """)
        turmas = []
        dias_set = set()
        for r in cursor.fetchall():
            turmas.append({
                "id_turma":         r[0],
                "modalidade":       r[1],
                "sala":             r[2],
                "hora_inicio":      str(r[3])[:5] if r[3] else "",
                "hora_fim":         str(r[4])[:5] if r[4] else "",
                "dias_semana":      r[5] or "",
                "professora":       r[6] or "",
                "capacidade_maxima":r[7] or 0,
            })
            # contar dias distintos
            for d in (r[5] or "").split(","):
                d = d.strip().lower()
                if d:
                    dias_set.add(d.split()[0])
 
        # ── Pacotes ativos ─────────────────────────────────────
        cursor.execute("""
            SELECT p.id_pacote, p.nome, p.tipo_cobranca, p.valor,
                   p.aulas_por_semana, p.qt_modalidades, p.observacao
            FROM pacotes p
            WHERE p.ativo = 1
            ORDER BY p.valor
        """)
        pacotes = []
        for r in cursor.fetchall():
            pid = r[0]
            cursor.execute("""
                SELECT m.titulo FROM pacote_modalidades pm
                JOIN modalidades m ON m.id_modalidade = pm.id_modalidade
                WHERE pm.id_pacote = ?
            """, (pid,))
            mods_nomes = [x[0] for x in cursor.fetchall()]
            pacotes.append({
                "id_pacote":        pid,
                "nome":             r[1],
                "tipo_cobranca":    r[2],
                "valor":            float(r[3] or 0),
                "aulas_por_semana": r[4] or 1,
                "qt_modalidades":   r[5] or 1,
                "observacao":       r[6] or "",
                "modalidades_nomes": mods_nomes,
            })
 
        # ── Stats strip ────────────────────────────────────────
        stats = {
            "total_modalidades": len(modalidades),
            "total_turmas":      len(turmas),
            "total_pacotes":     len(pacotes),
            "dias_ativos":       len(dias_set) or 6,
        }
 
        return render_template(
            "index.html",
            modalidades=modalidades,
            galeria_data=galeria_data,
            professoras=professoras,
            turmas=turmas,
            pacotes=pacotes,
            stats=stats,
        )
 
    except pyodbc.Error as e:
        # Se banco falhar, renderiza página com dados vazios
        return render_template(
            "index.html",
            modalidades=[], galeria_data={},
            professoras=[], turmas=[], pacotes=[],
            stats={"total_modalidades":0,"total_turmas":0,"total_pacotes":0,"dias_ativos":6},
        )
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
# ================================================================
# AULA EXPERIMENTAL (nova rota)
# ================================================================
 
@app.route("/solicitar-aula-experimental", methods=["POST"])
def solicitar_aula_experimental():
    nome              = request.form.get("nome", "").strip()
    telefone          = request.form.get("telefone", "").strip()
    email             = request.form.get("email", "").strip().lower()
    cpf               = only_digits(request.form.get("cpf") or "")
    modalidade        = request.form.get("modalidade", "").strip()
    horario_preferido = request.form.get("horario_preferido", "").strip()
    mensagem          = request.form.get("mensagem", "").strip()
 
    if not nome or not telefone or not email or not modalidade:
        return jsonify(ok=False, mensagem="Preencha os campos obrigatórios: nome, telefone, e-mail e modalidade."), 400
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO aulas_experimentais
                (nome, telefone, email, cpf, modalidade, horario_preferido, mensagem, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pendente')
        """, (nome, telefone, email, cpf or None, modalidade, horario_preferido or None, mensagem or None))
        conn.commit()
        return jsonify(ok=True, mensagem="Solicitação recebida! Entraremos em contato pelo WhatsApp para confirmar.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=f"Erro ao registrar solicitação: {str(e)}"), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()

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

@app.route("/esqueci-senha", methods=["POST"])
def esqueci_senha():
    try:
        data = request.get_json(silent=True) or {}

        email = (data.get("email") or "").strip().lower()
        cpf = only_digits(data.get("cpf") or "")
        nova_senha = data.get("nova_senha") or ""

        if not email or not cpf or not nova_senha:
            return jsonify(ok=False, mensagem="Dados obrigatórios não informados."), 400

        if len(cpf) != 11:
            return jsonify(ok=False, mensagem="Informe um CPF válido com 11 dígitos."), 400

        if not senha_forte_valida(nova_senha):
            return jsonify(ok=False, mensagem="A nova senha não atende aos critérios mínimos de segurança."), 400

        senha_hash = generate_password_hash(nova_senha)

        with get_conn() as conn:
            cursor = conn.cursor()

            # Ajuste o nome da tabela/colunas se no seu banco forem diferentes
            cursor.execute("""
                SELECT id
                FROM alunos
                WHERE LOWER(LTRIM(RTRIM(email))) = ?
                  AND cpf = ?
            """, (email, cpf))

            row = cursor.fetchone()

            if not row:
                return jsonify(ok=False, mensagem="Aluno não encontrado para este e-mail e CPF."), 404

            aluno_id = row[0]

            cursor.execute("""
                UPDATE alunos
                SET senha = ?
                WHERE id = ?
            """, (senha_hash, aluno_id))

            conn.commit()

        return jsonify(ok=True, mensagem="Senha atualizada com sucesso. Faça login com a nova senha."), 200

    except Exception as e:
        return jsonify(ok=False, mensagem=f"Erro interno ao redefinir senha: {str(e)}"), 500
# ── helpers ──────────────────────────────────────────────────────
def slugify(txt):
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    txt = re.sub(r"[^\w\s-]", "", txt).strip().lower()
    return re.sub(r"[\s_-]+", "-", txt)
 
UPLOAD_ROOT = os.path.join("static", "modalidades")
ALLOWED_IMG = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_VID = {"mp4", "mov", "webm"}
 
def allowed(filename, exts):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in exts
 
def salvar_arquivo(file, pasta, exts):
    if not file or file.filename == "":
        return None
    if not allowed(file.filename, exts):
        return None
    os.makedirs(pasta, exist_ok=True)
    fname = secure_filename(file.filename)
    file.save(os.path.join(pasta, fname))
    return fname
 
 
# ================================================================
# DASHBOARD PRINCIPAL (simplificado — só KPIs)
# ================================================================
@app.route("/admin")
@login_required(tipo="administrador")
def admin_page():
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        def count(sql):
            cursor.execute(sql)
            return cursor.fetchone()[0] or 0

        return render_template(
            "admin.html",
            user_name=session.get("user_name", "Administrador"),
            total_usuarios=count("SELECT COUNT(*) FROM cadastro"),
            total_modalidades=count("SELECT COUNT(*) FROM modalidades WHERE ativo=1"),
            total_turmas=count("SELECT COUNT(*) FROM turmas WHERE ativo=1"),
            total_pacotes=count("SELECT COUNT(*) FROM pacotes"),
            total_experimentais=count("SELECT COUNT(*) FROM aulas_experimentais")
        )

    except pyodbc.Error as e:
        flash(str(e), "erro")
        return render_template(
            "admin.html",
            user_name=session.get("user_name", "Administrador"),
            total_usuarios=0,
            total_modalidades=0,
            total_turmas=0,
            total_pacotes=0,
            total_experimentais=0
        )
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
 
 
# ================================================================
# SUB-PÁGINA: USUÁRIOS
# ================================================================
@app.route("/admin/usuarios")
@login_required(tipo="administrador")
def admin_usuarios():
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            SELECT id_cadastro,nome_completo,email,cpf,tipo_cadastro
            FROM cadastro ORDER BY nome_completo
        """)
        usuarios = [{"id_cadastro":r[0],"nome_completo":r[1],"email":r[2],"cpf":r[3],"tipo_cadastro":r[4]}
                    for r in cursor.fetchall()]
        return render_template("admin_usuarios.html",
            user_name=session.get("user_name","Administrador"), usuarios=usuarios)
    except pyodbc.Error as e:
        flash(str(e),"erro")
        return render_template("admin_usuarios.html",
            user_name=session.get("user_name","Administrador"), usuarios=[])
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
@app.route("/admin/cadastrar-usuario", methods=["POST"])
@login_required(tipo="administrador")
def cadastrar_usuario_admin():
    data = request.get_json(silent=True) or {}
    nome  = (data.get("nome_completo") or "").strip()
    email = (data.get("email") or "").strip().lower()
    cpf   = only_digits(data.get("cpf") or "")
    senha = data.get("senha") or ""
    tipo  = (data.get("tipo_cadastro") or "").strip().lower()
    t_img = 1 if data.get("termo_imagem") else 0
    t_seg = 1 if data.get("termo_seguranca") else 0
 
    if not all([nome, email, cpf, senha, tipo]):
        return jsonify(ok=False, mensagem="Preencha todos os campos."), 400
    if tipo not in {"aluno","professor","administrador"}:
        return jsonify(ok=False, mensagem="Tipo inválido."), 400
    if len(cpf) != 11:
        return jsonify(ok=False, mensagem="CPF inválido."), 400
    if not senha_forte_valida(senha):
        return jsonify(ok=False, mensagem="Senha fraca. Use 8+ chars, maiúsc., número e símbolo."), 400
 
    h = generate_password_hash(senha)
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM cadastro WHERE email=?", (email,))
        if cursor.fetchone(): return jsonify(ok=False, mensagem="E-mail já cadastrado."), 400
        cursor.execute("SELECT 1 FROM cadastro WHERE cpf=?", (cpf,))
        if cursor.fetchone(): return jsonify(ok=False, mensagem="CPF já cadastrado."), 400
        cursor.execute(
            "INSERT INTO cadastro(nome_completo,email,cpf,senha_hash,termo_imagem,termo_seguranca,tipo_cadastro) VALUES(?,?,?,?,?,?,?)",
            (nome,email,cpf,h,t_img,t_seg,tipo))
        conn.commit()
        return jsonify(ok=True, mensagem="Usuário cadastrado com sucesso."), 201
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
@app.route("/admin/usuario/<int:id_cadastro>", methods=["DELETE"])
@login_required(tipo="administrador")
def excluir_usuario(id_cadastro):
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("DELETE FROM cadastro WHERE id_cadastro=?", (id_cadastro,))
        conn.commit()
        return jsonify(ok=True, mensagem="Usuário removido.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
# ================================================================
# SUB-PÁGINA: MODALIDADES
# ================================================================
@app.route("/admin/modalidades")
@login_required(tipo="administrador")
def admin_modalidades():
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
 
        cursor.execute("""
            SELECT c.id_cadastro, c.nome_completo
            FROM cadastro c
            WHERE c.tipo_cadastro IN ('professor','administrador')
            ORDER BY c.nome_completo
        """)
        professoras = [{"id_cadastro":r[0],"nome_completo":r[1]} for r in cursor.fetchall()]
 
        cursor.execute("""
            SELECT m.id_modalidade, m.titulo, m.resumo, m.descricao, m.nivel,
                   m.id_responsavel_cadastro, c.nome_completo, c.tipo_cadastro,
                   m.slug, m.foto_professora
            FROM modalidades m
            LEFT JOIN cadastro c ON c.id_cadastro=m.id_responsavel_cadastro
            WHERE m.ativo=1 ORDER BY m.titulo
        """)
        modalidades = []
        for r in cursor.fetchall():
            modalidades.append({
                "id_modalidade": r[0], "titulo": r[1], "resumo": r[2] or "",
                "descricao": r[3] or "", "nivel": r[4] or "",
                "id_responsavel_cadastro": r[5], "responsavel_nome": r[6] or "",
                "responsavel_tipo": r[7] or "", "slug": r[8] or "",
                "foto_professora": r[9] or "",
            })
        return render_template("admin_modalidades.html",
            user_name=session.get("user_name","Administrador"),
            professoras=professoras, modalidades=modalidades)
    except pyodbc.Error as e:
        flash(str(e),"erro")
        return render_template("admin_modalidades.html",
            user_name=session.get("user_name","Administrador"),
            professoras=[], modalidades=[])
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
@app.route("/admin/modalidade", methods=["POST"])
@login_required(tipo="administrador")
def salvar_modalidade():
    # Aceita multipart/form-data por causa do upload de arquivos
    id_modalidade       = request.form.get("id_modalidade") or None
    titulo              = (request.form.get("titulo") or "").strip()
    resumo              = (request.form.get("resumo") or "").strip()
    descricao           = (request.form.get("descricao") or "").strip()
    id_resp             = request.form.get("id_responsavel_cadastro") or None
    nivel               = (request.form.get("nivel") or "").strip()
    slug_val            = slugify(titulo) if titulo else ""
 
    if not titulo:
        return jsonify(ok=False, mensagem="Informe o título."), 400
 
    # Diretórios de upload
    pasta_prof  = os.path.join(UPLOAD_ROOT, slug_val, "professora")
    pasta_gal   = os.path.join(UPLOAD_ROOT, slug_val, "galeria")
 
    foto_prof = salvar_arquivo(request.files.get("foto_professora"), pasta_prof, ALLOWED_IMG)
    for f in request.files.getlist("galeria"):
        salvar_arquivo(f, pasta_gal, ALLOWED_IMG | ALLOWED_VID)
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        if id_modalidade:
            sql = """UPDATE modalidades SET titulo=?,resumo=?,descricao=?,
                     id_responsavel_cadastro=?,nivel=?,slug=?"""
            params = [titulo,resumo,descricao,id_resp,nivel,slug_val]
            if foto_prof:
                sql += ",foto_professora=?"
                params.append(foto_prof)
            sql += " WHERE id_modalidade=?"
            params.append(id_modalidade)
            cursor.execute(sql, params)
        else:
            cursor.execute("""
                INSERT INTO modalidades(titulo,resumo,descricao,id_responsavel_cadastro,nivel,slug,foto_professora,ativo)
                VALUES(?,?,?,?,?,?,?,1)""",
                (titulo,resumo,descricao,id_resp,nivel,slug_val,foto_prof))
        conn.commit()
        return jsonify(ok=True, mensagem="Modalidade salva.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
@app.route("/admin/modalidade/<int:id_modalidade>", methods=["DELETE"])
@login_required(tipo="administrador")
def excluir_modalidade(id_modalidade):
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("UPDATE modalidades SET ativo=0 WHERE id_modalidade=?", (id_modalidade,))
        conn.commit()
        return jsonify(ok=True, mensagem="Modalidade removida.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
# ================================================================
# SUB-PÁGINA: HORÁRIOS & TURMAS & SALAS
# ================================================================
@app.route("/admin/horarios")
@login_required(tipo="administrador")
def admin_horarios():
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id_sala, nome, descricao
            FROM salas
            WHERE ativo = 1
            ORDER BY nome
        """)
        salas = [
            {"id_sala": r[0], "nome": r[1], "descricao": r[2] or ""}
            for r in cursor.fetchall()
        ]

        cursor.execute("""
            SELECT id_modalidade, titulo
            FROM modalidades
            WHERE ativo = 1
            ORDER BY titulo
        """)
        modalidades = [
            {"id_modalidade": r[0], "titulo": r[1]}
            for r in cursor.fetchall()
        ]

        cursor.execute("""
            SELECT c.id_cadastro, c.nome_completo
            FROM cadastro c
            WHERE c.tipo_cadastro IN ('professor', 'administrador')
            ORDER BY c.nome_completo
        """)
        professoras = [
            {"id_professora": r[0], "nome": r[1]}
            for r in cursor.fetchall()
        ]

        cursor.execute("""
            SELECT
                t.id_turma,
                t.nome_exibicao,
                t.id_modalidade,
                m.titulo,
                t.id_sala,
                s.nome,
                t.id_professora,
                c.nome_completo,
                h.hora_inicio,
                h.hora_fim,
                h.vezes_semana,
                h.dias_semana,
                t.capacidade_maxima,
                (
                    SELECT COUNT(*)
                    FROM matriculas ma
                    WHERE ma.id_turma = t.id_turma
                      AND ma.ativo = 1
                ) AS alunos_mat
            FROM turmas t
            JOIN modalidades m ON m.id_modalidade = t.id_modalidade
            JOIN salas s ON s.id_sala = t.id_sala
            JOIN horarios h ON h.id_horario = t.id_horario
            LEFT JOIN cadastro c ON c.id_cadastro = t.id_professora
            WHERE t.ativo = 1
            ORDER BY m.titulo, h.hora_inicio
        """)

        turmas = []
        for r in cursor.fetchall():
            turmas.append({
                "id_turma": r[0],
                "nome_exibicao": r[1] or "",
                "id_modalidade": r[2],
                "modalidade": r[3],
                "id_sala": r[4],
                "sala": r[5],
                "id_professora": r[6] or "",
                "professora": r[7] or "",
                "hora_inicio": str(r[8])[:5] if r[8] else "",
                "hora_fim": str(r[9])[:5] if r[9] else "",
                "vezes_semana": r[10],
                "dias_semana": r[11] or "",
                "capacidade_maxima": r[12] or 0,
                "alunos_matriculados": r[13] or 0,
            })

        return render_template(
            "admin_horarios.html",
            user_name=session.get("user_name", "Administrador"),
            salas=salas,
            modalidades=modalidades,
            professoras=professoras,
            turmas=turmas
        )

    except pyodbc.Error as e:
        flash(str(e), "erro")
        return render_template(
            "admin_horarios.html",
            user_name=session.get("user_name", "Administrador"),
            salas=[],
            modalidades=[],
            professoras=[],
            turmas=[]
        )
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
 
 
# ── SALAS ────────────────────────────────────────────────────────
@app.route("/admin/sala", methods=["POST"])
@login_required(tipo="administrador")
def salvar_sala():
    data = request.get_json(silent=True) or {}
    id_sala = data.get("id_sala")
    nome    = (data.get("nome") or "").strip()
    desc    = (data.get("descricao") or "").strip()
    if not nome: return jsonify(ok=False, mensagem="Informe o nome da sala."), 400
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        if id_sala:
            cursor.execute("UPDATE salas SET nome=?,descricao=? WHERE id_sala=?", (nome,desc,id_sala))
        else:
            cursor.execute("INSERT INTO salas(nome,descricao,ativo) VALUES(?,?,1)", (nome,desc))
        conn.commit()
        return jsonify(ok=True, mensagem="Sala salva.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
@app.route("/admin/sala/<int:id_sala>", methods=["DELETE"])
@login_required(tipo="administrador")
def excluir_sala(id_sala):
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("UPDATE salas SET ativo=0 WHERE id_sala=?", (id_sala,))
        conn.commit()
        return jsonify(ok=True, mensagem="Sala removida.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
# ── TURMAS ───────────────────────────────────────────────────────
@app.route("/admin/turma", methods=["POST"])
@login_required(tipo="administrador")
def salvar_turma():
    data = request.get_json(silent=True) or {}

    id_turma    = data.get("id_turma")
    id_modal    = data.get("id_modalidade")
    id_sala     = data.get("id_sala")
    id_prof     = data.get("id_professora") or None
    hora_inicio = data.get("hora_inicio", "")
    hora_fim    = data.get("hora_fim", "")
    dias        = (data.get("dias_semana") or "").strip()
    nome_exib   = (data.get("nome_exibicao") or "").strip()
    cap         = data.get("capacidade_maxima")

    if cap in ("", None):
        cap = None

    vezes = len([d for d in dias.split(",") if d.strip()]) if dias else 0

    if not all([id_modal, id_sala, hora_inicio, hora_fim, dias]):
        return jsonify(ok=False, mensagem="Preencha modalidade, sala, horário e dias."), 400

    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        if id_turma:
            cursor.execute("""
                UPDATE horarios
                   SET hora_inicio=?, hora_fim=?, vezes_semana=?, dias_semana=?
                 WHERE id_horario=(SELECT id_horario FROM turmas WHERE id_turma=?)
            """, (hora_inicio, hora_fim, vezes, dias, id_turma))

            cursor.execute("""
                UPDATE turmas
                   SET id_modalidade=?,
                       id_sala=?,
                       id_professora=?,
                       nome_exibicao=?,
                       capacidade_maxima=?
                 WHERE id_turma=?
            """, (id_modal, id_sala, id_prof, nome_exib or None, cap, id_turma))

        else:
            cursor.execute("""
                INSERT INTO horarios(hora_inicio, hora_fim, vezes_semana, dias_semana)
                OUTPUT INSERTED.id_horario
                VALUES (?, ?, ?, ?)
            """, (hora_inicio, hora_fim, vezes, dias))

            row = cursor.fetchone()
            if not row or row[0] is None:
                raise Exception("Não foi possível obter o id_horario inserido.")

            id_horario = int(row[0])

            cursor.execute("""
                INSERT INTO turmas(
                    id_modalidade,
                    id_sala,
                    id_professora,
                    id_horario,
                    nome_exibicao,
                    capacidade_maxima,
                    ativo
                )
                VALUES(?,?,?,?,?,?,1)
            """, (id_modal, id_sala, id_prof, id_horario, nome_exib or None, cap))

        conn.commit()
        return jsonify(ok=True, mensagem="Turma salva.")
    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
 
@app.route("/admin/turma/<int:id_turma>", methods=["DELETE"])
@login_required(tipo="administrador")
def excluir_turma(id_turma):
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("UPDATE turmas SET ativo=0 WHERE id_turma=?", (id_turma,))
        conn.commit()
        return jsonify(ok=True, mensagem="Turma removida.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
# ================================================================
# SUB-PÁGINA: PACOTES
# ================================================================
# ================================================================
# SUB-PÁGINA: PACOTES
# ================================================================
@app.route("/admin/pacotes")
@login_required(tipo="administrador")
def admin_pacotes_page():
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id_modalidade, titulo
            FROM modalidades
            WHERE ativo = 1
            ORDER BY titulo
        """)
        modalidades = [
            {"id_modalidade": r[0], "titulo": r[1]}
            for r in cursor.fetchall()
        ]

        cursor.execute("""
            SELECT id_pacote, nome, tipo_cobranca, valor,
                   aulas_por_semana, qt_modalidades, ativo, observacao
            FROM pacotes
            ORDER BY nome
        """)
        pacotes = []

        for r in cursor.fetchall():
            pid = r[0]

            cursor2 = conn.cursor()
            cursor2.execute("""
                SELECT pm.id_modalidade, m.titulo
                FROM pacote_modalidades pm
                JOIN modalidades m ON m.id_modalidade = pm.id_modalidade
                WHERE pm.id_pacote = ?
                ORDER BY m.titulo
            """, (pid,))
            mods = cursor2.fetchall()
            cursor2.close()

            pacotes.append({
                "id_pacote": pid,
                "nome": r[1],
                "tipo_cobranca": r[2],
                "valor": float(r[3] or 0),
                "aulas_por_semana": r[4] or 1,
                "qt_modalidades": r[5] or 1,
                "ativo": bool(r[6]),
                "observacao": r[7] or "",
                "modalidades_ids": [str(m[0]) for m in mods],
                "modalidades_nomes": [m[1] for m in mods],
            })

        return render_template(
            "admin_pacotes.html",
            user_name=session.get("user_name", "Administrador"),
            modalidades=modalidades,
            pacotes=pacotes
        )

    except pyodbc.Error as e:
        flash(str(e), "erro")
        return render_template(
            "admin_pacotes.html",
            user_name=session.get("user_name", "Administrador"),
            modalidades=[],
            pacotes=[]
        )
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin/pacote", methods=["POST"])
@login_required(tipo="administrador")
def salvar_pacote_api():
    data = request.get_json(silent=True) or {}

    id_pacote = data.get("id_pacote")
    nome = (data.get("nome") or "").strip()
    tipo = (data.get("tipo_cobranca") or "mensal").strip()
    valor = float(data.get("valor") or 0)
    aulas = int(data.get("aulas_por_semana") or 1)
    qt_mod = int(data.get("qt_modalidades") or 1)
    status = (data.get("status") or "ativo").strip()
    obs = (data.get("observacao") or "").strip()
    mod_ids = data.get("modalidades_ids") or []

    ativo = 1 if status == "ativo" else 0

    if not nome:
        return jsonify(ok=False, mensagem="Informe o nome do pacote."), 400

    if qt_mod < 1:
        return jsonify(ok=False, mensagem="A quantidade de modalidades deve ser no mínimo 1."), 400

    if not mod_ids:
        return jsonify(ok=False, mensagem="Selecione pelo menos uma modalidade."), 400

    if len(mod_ids) > qt_mod:
        return jsonify(ok=False, mensagem=f"Selecione no máximo {qt_mod} modalidade(s)."), 400

    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        if id_pacote:
            cursor.execute("""
                UPDATE pacotes
                   SET nome = ?,
                       tipo_cobranca = ?,
                       valor = ?,
                       aulas_por_semana = ?,
                       qt_modalidades = ?,
                       ativo = ?,
                       observacao = ?
                 WHERE id_pacote = ?
            """, (nome, tipo, valor, aulas, qt_mod, ativo, obs, id_pacote))

            cursor.execute("DELETE FROM pacote_modalidades WHERE id_pacote = ?", (id_pacote,))

        else:
            cursor.execute("""
                INSERT INTO pacotes(
                    nome, tipo_cobranca, valor,
                    aulas_por_semana, qt_modalidades, ativo, observacao
                )
                OUTPUT INSERTED.id_pacote
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (nome, tipo, valor, aulas, qt_mod, ativo, obs))

            row = cursor.fetchone()
            if not row or row[0] is None:
                raise Exception("Não foi possível obter o id do pacote.")

            id_pacote = int(row[0])

        for mid in mod_ids:
            cursor.execute("""
                INSERT INTO pacote_modalidades(id_pacote, id_modalidade)
                VALUES (?, ?)
            """, (id_pacote, int(mid)))

        conn.commit()
        return jsonify(ok=True, mensagem="Pacote salvo.")

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin/pacote/<int:id_pacote>", methods=["DELETE"])
@login_required(tipo="administrador")
def excluir_pacote_api(id_pacote):
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM pacote_modalidades WHERE id_pacote = ?", (id_pacote,))
        cursor.execute("DELETE FROM pacotes WHERE id_pacote = ?", (id_pacote,))

        conn.commit()
        return jsonify(ok=True, mensagem="Pacote excluído.")

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
# ================================================================
# SUB-PÁGINA: ALOCAÇÃO DE ALUNOS
# ================================================================
@app.route("/admin/alocacao")
@login_required(tipo="administrador")
def admin_alocacao():
    return render_template("admin_alocacao.html",
        user_name=session.get("user_name","Administrador"))
 
 
@app.route("/admin/alocacao/turmas")
@login_required(tipo="administrador")
def alocacao_turmas():
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            SELECT t.id_turma, t.nome_exibicao, m.id_modalidade, m.titulo,
                   s.nome, h.hora_inicio, h.hora_fim, h.dias_semana, t.capacidade_maxima
            FROM turmas t
            JOIN modalidades m ON m.id_modalidade=t.id_modalidade
            JOIN salas s ON s.id_sala=t.id_sala
            JOIN horarios h ON h.id_horario=t.id_horario
            WHERE t.ativo=1 ORDER BY m.titulo,h.hora_inicio
        """)
        turmas = []
        for r in cursor.fetchall():
            turmas.append({
                "id_turma":r[0],"nome_exibicao":r[1] or "","id_modalidade":r[2],
                "modalidade":r[3],"sala":r[4],
                "hora_inicio":str(r[5])[:5] if r[5] else "","hora_fim":str(r[6])[:5] if r[6] else "",
                "dias_semana":r[7] or "","capacidade_maxima":r[8] or 0,
            })
 
        cursor.execute("""
            SELECT ma.id_turma, c.id_cadastro, c.nome_completo, c.email,
                   p.nome AS plano
            FROM matriculas ma
            JOIN cadastro c ON c.id_cadastro=ma.id_aluno
            LEFT JOIN aluno_pacote ap ON ap.id_aluno=c.id_cadastro AND ap.ativo=1
            LEFT JOIN pacotes p ON p.id_pacote=ap.id_pacote
            WHERE ma.ativo=1 ORDER BY c.nome_completo
        """)
        matriculas = {}
        for r in cursor.fetchall():
            t = r[0]
            if t not in matriculas: matriculas[t] = []
            matriculas[t].append({"id_cadastro":r[1],"nome_completo":r[2],"email":r[3] or "","plano":r[4] or ""})
 
        return jsonify(ok=True, turmas=turmas, matriculas=matriculas)
    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=str(e), turmas=[], matriculas={}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
@app.route("/admin/alocacao/alunos")
@login_required(tipo="administrador")
def alocacao_alunos():
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id_cadastro, c.nome_completo, c.email,
                   p.nome AS plano, p.aulas_por_semana,
                   ap.id_pacote
            FROM cadastro c
            LEFT JOIN aluno_pacote ap ON ap.id_aluno=c.id_cadastro AND ap.ativo=1
            LEFT JOIN pacotes p ON p.id_pacote=ap.id_pacote
            WHERE c.tipo_cadastro='aluno'
            ORDER BY c.nome_completo
        """)
        alunos = [{"id_cadastro":r[0],"nome_completo":r[1],"email":r[2] or "",
                   "plano":r[3] or "","aulas_por_semana":r[4] or 0,"id_pacote":r[5]}
                  for r in cursor.fetchall()]
        return jsonify(ok=True, alunos=alunos)
    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=str(e), alunos=[]), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
 
 
@app.route("/admin/alocacao/matricular", methods=["POST"])
@login_required(tipo="administrador")
def matricular_aluno():
    data = request.get_json(silent=True) or {}
    id_turma = data.get("id_turma")
    id_aluno = data.get("id_aluno")

    if not id_turma or not id_aluno:
        return jsonify(ok=False, mensagem="Informe id_turma e id_aluno."), 400

    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        # 1. Dados da turma (capacidade + modalidade)
        cursor.execute("""
            SELECT t.capacidade_maxima,
                   (SELECT COUNT(*) FROM matriculas ma
                    WHERE ma.id_turma = t.id_turma AND ma.ativo = 1) AS ocup,
                   t.id_modalidade
            FROM turmas t
            WHERE t.id_turma = ?
        """, (id_turma,))
        row = cursor.fetchone()
        if not row:
            return jsonify(ok=False, mensagem="Turma não encontrada."), 404

        cap, ocup, id_modal = row[0] or 0, row[1] or 0, row[2]

        # 2. Capacidade
        if cap > 0 and ocup >= cap:
            return jsonify(
                ok=False,
                codigo="TURMA_CHEIA",
                mensagem=f"Turma lotada. Capacidade máxima: {cap} alunos."
            ), 400

        # 3. Plano do aluno: precisa ter pacote ativo
        cursor.execute("""
            SELECT ap.id_pacote, p.nome, p.aulas_por_semana
            FROM aluno_pacote ap
            JOIN pacotes p ON p.id_pacote = ap.id_pacote
            WHERE ap.id_aluno = ? AND ap.ativo = 1
        """, (id_aluno,))
        pacote_row = cursor.fetchone()
        if not pacote_row:
            return jsonify(
                ok=False,
                codigo="SEM_PLANO",
                mensagem="O aluno não possui um pacote de aulas ativo."
            ), 400

        id_pacote, nome_plano, aulas_semanais = pacote_row[0], pacote_row[1], pacote_row[2] or 0

        # 4. Verificar se o pacote permite a modalidade da turma
        cursor.execute("""
            SELECT pm.id_modalidade
            FROM pacote_modalidades pm
            WHERE pm.id_pacote = ?
        """, (id_pacote,))
        mods_permitidas = {r[0] for r in cursor.fetchall()}

        # Se o pacote tiver modalidades definidas, precisa conter essa
        if mods_permitidas and int(id_modal) not in mods_permitidas:
            return jsonify(
                ok=False,
                codigo="PLANO_INCOMPATIVEL",
                mensagem=f"O plano \"{nome_plano}\" do aluno não inclui esta modalidade."
            ), 400

        # 5. (Opcional) Limitar aulas por semana conforme o plano
        if aulas_semanais > 0:
            cursor.execute("""
                SELECT COUNT(*)
                FROM matriculas ma
                JOIN turmas t ON t.id_turma = ma.id_turma
                JOIN horarios h ON h.id_horario = t.id_horario
                WHERE ma.id_aluno = ?
                  AND ma.ativo = 1
                  AND h.vezes_semana >= 1
            """, (id_aluno,))
            qtd_atual = cursor.fetchone()[0] or 0

            if qtd_atual >= aulas_semanais:
                return jsonify(
                    ok=False,
                    codigo="LIMITE_AULAS",
                    mensagem=f"O aluno já atingiu o limite de {aulas_semanais} aula(s) por semana do plano \"{nome_plano}\"."
                ), 400

        # 6. Já matriculado nesta turma?
        cursor.execute("""
            SELECT id_matricula, ativo
            FROM matriculas
            WHERE id_turma = ? AND id_aluno = ?
        """, (id_turma, id_aluno))
        exist = cursor.fetchone()

        if exist:
            if exist[1]:
                return jsonify(ok=False, mensagem="Aluno já matriculado nesta turma."), 400
            cursor.execute("UPDATE matriculas SET ativo = 1 WHERE id_matricula = ?", (exist[0],))
        else:
            cursor.execute("""
                INSERT INTO matriculas(id_turma, id_aluno, ativo)
                VALUES (?, ?, 1)
            """, (id_turma, id_aluno))

        conn.commit()
        return jsonify(ok=True, mensagem="Aluno matriculado."), 201

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
 
@app.route("/admin/alocacao/desmatricular", methods=["DELETE"])
@login_required(tipo="administrador")
def desmatricular_aluno():
    data = request.get_json(silent=True) or {}
    id_turma = data.get("id_turma")
    id_aluno = data.get("id_aluno")
    if not id_turma or not id_aluno:
        return jsonify(ok=False, mensagem="Informe id_turma e id_aluno."), 400
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("UPDATE matriculas SET ativo=0 WHERE id_turma=? AND id_aluno=? AND ativo=1", (id_turma,id_aluno))
        if cursor.rowcount == 0:
            return jsonify(ok=False, mensagem="Matrícula não encontrada."), 404
        conn.commit()
        return jsonify(ok=True, mensagem="Matrícula removida.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
                          

# ================================================================
# CONTATO GERAL (substitui a rota contato() existente)
# ================================================================
 
@app.route("/contato", methods=["POST"])
def contato():
    nome      = request.form.get("nome", "").strip()
    telefone  = request.form.get("telefone", "").strip()
    email     = request.form.get("email", "").strip()
    modalidade= request.form.get("modalidade", "").strip()
    mensagem  = request.form.get("mensagem", "").strip()
 
    if not nome or not telefone or not email:
        return jsonify(ok=False, mensagem="Preencha nome, telefone e e-mail."), 400
 
    # Log simples — pode integrar e-mail/CRM futuramente
    print({
        "tipo": "contato_geral",
        "nome": nome, "telefone": telefone, "email": email,
        "modalidade": modalidade, "mensagem": mensagem,
    })
    return jsonify(ok=True, mensagem="Mensagem recebida! Retornaremos em breve.")
 


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

WORKSHOP_UPLOAD = os.path.join("static", "workshops")
ALLOWED_IMG = {"png", "jpg", "jpeg", "webp", "gif"}
 
def _save_img(file, pasta):
    if not file or file.filename == "":
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMG:
        return None
    os.makedirs(pasta, exist_ok=True)
    fname = secure_filename(file.filename)
    file.save(os.path.join(pasta, fname))
    return fname
# ================================================================
# SUB-PÁGINA: PRIMEIRA AULA
# ================================================================
 
@app.route("/admin/primeira-aula")
@login_required(tipo="administrador")
def admin_primeira_aula():
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                m.id_modalidade,
                m.titulo,
                m.nivel,
                (
                    SELECT COUNT(*)
                    FROM primeira_aula_itens i
                    WHERE i.id_modalidade = m.id_modalidade
                      AND i.ativo = 1
                ) AS total_itens
            FROM modalidades m
            WHERE m.ativo = 1
            ORDER BY m.titulo
        """)

        modalidades = []
        for r in cursor.fetchall():
            modalidades.append({
                "id_modalidade": r[0],
                "titulo": r[1],
                "nivel": r[2] or "",
                "total_itens": r[3] or 0,
            })

        return render_template(
            "admin_primeira_aula.html",
            user_name=session.get("user_name", "Administrador"),
            modalidades=modalidades
        )

    except pyodbc.Error:
        return render_template(
            "admin_primeira_aula.html",
            user_name=session.get("user_name", "Administrador"),
            modalidades=[]
        )
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin/primeira-aula/<int:id_modalidade>/itens")
@login_required(tipo="administrador")
def listar_itens_primeira_aula(id_modalidade):
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                id_item,
                id_modalidade,
                nome,
                categoria,
                funcao,
                observacao,
                obrigatorio,
                ordem
            FROM primeira_aula_itens
            WHERE id_modalidade = ?
              AND ativo = 1
            ORDER BY
                CASE WHEN ordem IS NULL THEN 1 ELSE 0 END,
                ordem,
                nome
        """, (id_modalidade,))

        itens = []
        for r in cursor.fetchall():
            itens.append({
                "id_item": r[0],
                "id_modalidade": r[1],
                "nome": r[2] or "",
                "categoria": r[3] or "outros",
                "funcao": r[4] or "",
                "observacao": r[5] or "",
                "obrigatorio": bool(r[6]),
                "ordem": r[7],
            })

        return jsonify(ok=True, itens=itens)

    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=str(e), itens=[]), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/admin/primeira-aula/item", methods=["POST"])
@login_required(tipo="administrador")
def salvar_item_primeira_aula():
    data = request.get_json(silent=True) or {}

    id_item = data.get("id_item") or None
    id_modal = data.get("id_modalidade") or None
    nome = (data.get("nome") or "").strip()
    categoria = (data.get("categoria") or "outros").strip()
    funcao = (data.get("funcao") or "").strip()
    observacao = (data.get("observacao") or "").strip()
    obrigatorio = 1 if data.get("obrigatorio") else 0
    ordem = data.get("ordem")

    if ordem in ("", None):
        ordem = None

    categorias_validas = {"vestuario", "equipamento", "higiene", "alimentacao", "documentos", "outros"}
    if categoria not in categorias_validas:
        categoria = "outros"

    if not nome:
        return jsonify(ok=False, mensagem="Informe o nome do item."), 400

    if not id_modal:
        return jsonify(ok=False, mensagem="Informe a modalidade."), 400

    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 1
            FROM modalidades
            WHERE id_modalidade = ? AND ativo = 1
        """, (id_modal,))
        if not cursor.fetchone():
            return jsonify(ok=False, mensagem="Modalidade não encontrada."), 404

        if id_item:
            cursor.execute("""
                SELECT 1
                FROM primeira_aula_itens
                WHERE id_item = ?
            """, (id_item,))
            if not cursor.fetchone():
                return jsonify(ok=False, mensagem="Item não encontrado."), 404

            cursor.execute("""
                UPDATE primeira_aula_itens
                   SET id_modalidade = ?,
                       nome = ?,
                       categoria = ?,
                       funcao = ?,
                       observacao = ?,
                       obrigatorio = ?,
                       ordem = ?
                 WHERE id_item = ?
            """, (
                id_modal,
                nome,
                categoria,
                funcao,
                observacao,
                obrigatorio,
                ordem,
                id_item
            ))
        else:
            cursor.execute("""
                INSERT INTO primeira_aula_itens
                    (id_modalidade, nome, categoria, funcao, observacao, obrigatorio, ordem, ativo)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                id_modal,
                nome,
                categoria,
                funcao,
                observacao,
                obrigatorio,
                ordem
            ))

        conn.commit()
        return jsonify(ok=True, mensagem="Item salvo.")

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin/primeira-aula/item/<int:id_item>", methods=["DELETE"])
@login_required(tipo="administrador")
def excluir_item_primeira_aula(id_item):
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE primeira_aula_itens
               SET ativo = 0
             WHERE id_item = ?
        """, (id_item,))

        if cursor.rowcount == 0:
            return jsonify(ok=False, mensagem="Item não encontrado."), 404

        conn.commit()
        return jsonify(ok=True, mensagem="Item removido.")

    except pyodbc.Error as e:
        if conn:
            conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
 
 
# ================================================================
# SUB-PÁGINA: WORKSHOPS
# ================================================================
 
@app.route("/admin/workshops")
@login_required(tipo="administrador")
def admin_workshops():
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
 
        # Modalidades para o select
        cursor.execute("SELECT id_modalidade, titulo FROM modalidades WHERE ativo=1 ORDER BY titulo")
        modalidades = [{"id_modalidade":r[0],"titulo":r[1]} for r in cursor.fetchall()]
 
        # Professoras/instrutores
        cursor.execute("""
            SELECT id_cadastro, nome_completo FROM cadastro
            WHERE tipo_cadastro IN ('professor','administrador')
            ORDER BY nome_completo
        """)
        professoras = [{"id_cadastro":r[0],"nome_completo":r[1]} for r in cursor.fetchall()]
 
        # Salas
        cursor.execute("SELECT id_sala, nome FROM salas WHERE ativo=1 ORDER BY nome")
        salas = [{"id_sala":r[0],"nome":r[1]} for r in cursor.fetchall()]
 
        # Todos os usuários para inscrição
        cursor.execute("SELECT id_cadastro, nome_completo FROM cadastro ORDER BY nome_completo")
        todos_usuarios = [{"id_cadastro":r[0],"nome_completo":r[1]} for r in cursor.fetchall()]
 
        # Workshops com contagem de inscritos
        cursor.execute("""
            SELECT w.id_workshop, w.nome, w.descricao, w.status,
                   w.data_workshop, w.hora_inicio, w.hora_fim,
                   w.vagas_totais, w.valor, w.imagem_capa,
                   c.nome_completo AS instrutor_nome,
                   m.titulo        AS modalidade_nome,
                   s.nome          AS sala_nome,
                   (SELECT COUNT(*) FROM workshop_inscricoes wi
                    WHERE wi.id_workshop=w.id_workshop AND wi.ativo=1) AS inscritos
            FROM workshops w
            LEFT JOIN cadastro c ON c.id_cadastro = w.id_instrutor
            LEFT JOIN modalidades m ON m.id_modalidade = w.id_modalidade
            LEFT JOIN salas s ON s.id_sala = w.id_sala
            ORDER BY w.data_workshop DESC, w.nome
        """)
        workshops = []
        for r in cursor.fetchall():
            workshops.append({
                "id_workshop":    r[0],
                "nome":           r[1],
                "descricao":      r[2] or "",
                "status":         r[3] or "rascunho",
                "data_workshop":  r[4],
                "hora_inicio":    str(r[5])[:5] if r[5] else "",
                "hora_fim":       str(r[6])[:5] if r[6] else "",
                "vagas_totais":   r[7] or 0,
                "valor":          float(r[8]) if r[8] else 0.0,
                "imagem_capa":    r[9] or "",
                "instrutor_nome": r[10] or "",
                "modalidade_nome":r[11] or "",
                "sala_nome":      r[12] or "",
                "inscritos":      r[13] or 0,
            })
 
        return render_template("admin_workshops.html",
            user_name=session.get("user_name","Administrador"),
            modalidades=modalidades,
            professoras=professoras,
            salas=salas,
            todos_usuarios=todos_usuarios,
            workshops=workshops)
 
    except pyodbc.Error as e:
        return render_template("admin_workshops.html",
            user_name=session.get("user_name","Administrador"),
            modalidades=[], professoras=[], salas=[],
            todos_usuarios=[], workshops=[])
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/workshop", methods=["POST"])
@login_required(tipo="administrador")
def salvar_workshop():
    id_ws       = request.form.get("id_workshop") or None
    nome        = (request.form.get("nome") or "").strip()
    id_modal    = request.form.get("id_modalidade") or None
    id_instr    = request.form.get("id_instrutor") or None
    descricao   = (request.form.get("descricao") or "").strip()
    requisitos  = (request.form.get("requisitos") or "").strip()
    observacao  = (request.form.get("observacao") or "").strip()
    data_ws     = request.form.get("data_workshop") or None
    hora_ini    = request.form.get("hora_inicio") or None
    hora_fim    = request.form.get("hora_fim") or None
    id_sala     = request.form.get("id_sala") or None
    vagas       = request.form.get("vagas_totais") or None
    valor       = request.form.get("valor") or None
    status      = (request.form.get("status") or "rascunho").strip()
 
    if not nome:
        return jsonify(ok=False, mensagem="Informe o nome do workshop."), 400
 
    imagem = _save_img(request.files.get("imagem_capa"), WORKSHOP_UPLOAD)
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        if id_ws:
            sql = """UPDATE workshops SET nome=?,id_modalidade=?,id_instrutor=?,
                     descricao=?,requisitos=?,observacao=?,data_workshop=?,
                     hora_inicio=?,hora_fim=?,id_sala=?,vagas_totais=?,valor=?,status=?"""
            params = [nome,id_modal,id_instr,descricao,requisitos,observacao,
                      data_ws,hora_ini,hora_fim,id_sala,vagas,valor,status]
            if imagem:
                sql += ",imagem_capa=?"
                params.append(imagem)
            sql += " WHERE id_workshop=?"
            params.append(id_ws)
            cursor.execute(sql, params)
        else:
            cursor.execute("""
                INSERT INTO workshops
                    (nome,id_modalidade,id_instrutor,descricao,requisitos,observacao,
                     data_workshop,hora_inicio,hora_fim,id_sala,vagas_totais,valor,status,imagem_capa)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (nome,id_modal,id_instr,descricao,requisitos,observacao,
                  data_ws,hora_ini,hora_fim,id_sala,vagas,valor,status,imagem))
        conn.commit()
        return jsonify(ok=True, mensagem="Workshop salvo.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/workshop/<int:id_ws>", methods=["GET"])
@login_required(tipo="administrador")
def get_workshop(id_ws):
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            SELECT id_workshop,nome,id_modalidade,id_instrutor,descricao,requisitos,
                   observacao,data_workshop,hora_inicio,hora_fim,id_sala,
                   vagas_totais,valor,status,imagem_capa
            FROM workshops WHERE id_workshop=?
        """, (id_ws,))
        r = cursor.fetchone()
        if not r: return jsonify(ok=False, mensagem="Workshop não encontrado."), 404
        return jsonify(ok=True, workshop={
            "id_workshop":  r[0], "nome":r[1], "id_modalidade":r[2],
            "id_instrutor": r[3], "descricao":r[4] or "", "requisitos":r[5] or "",
            "observacao":   r[6] or "",
            "data_workshop":r[7].isoformat() if r[7] else None,
            "hora_inicio":  str(r[8])[:5] if r[8] else "",
            "hora_fim":     str(r[9])[:5] if r[9] else "",
            "id_sala":      r[10], "vagas_totais":r[11], "valor":float(r[12] or 0),
            "status":       r[13] or "rascunho", "imagem_capa":r[14] or "",
        })
    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/workshop/<int:id_ws>", methods=["DELETE"])
@login_required(tipo="administrador")
def excluir_workshop(id_ws):
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("UPDATE workshop_inscricoes SET ativo=0 WHERE id_workshop=?", (id_ws,))
        cursor.execute("DELETE FROM workshops WHERE id_workshop=?", (id_ws,))
        conn.commit()
        return jsonify(ok=True, mensagem="Workshop excluído.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/workshop/<int:id_ws>/inscricoes")
@login_required(tipo="administrador")
def listar_inscricoes_workshop(id_ws):
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
 
        # Dados do workshop
        cursor.execute("""
            SELECT nome, data_workshop, hora_inicio, hora_fim, vagas_totais, status
            FROM workshops WHERE id_workshop=?
        """, (id_ws,))
        r = cursor.fetchone()
        if not r: return jsonify(ok=False, mensagem="Workshop não encontrado."), 404
 
        workshop = {
            "id_workshop":  id_ws,
            "nome":         r[0],
            "data_workshop":r[1].isoformat() if r[1] else None,
            "hora_inicio":  str(r[2])[:5] if r[2] else "",
            "hora_fim":     str(r[3])[:5] if r[3] else "",
            "vagas_totais": r[4] or 0,
            "status":       r[5] or "rascunho",
        }
 
        # Inscritos
        cursor.execute("""
            SELECT c.id_cadastro, c.nome_completo, c.email, wi.data_inscricao
            FROM workshop_inscricoes wi
            JOIN cadastro c ON c.id_cadastro = wi.id_participante
            WHERE wi.id_workshop=? AND wi.ativo=1
            ORDER BY wi.data_inscricao, c.nome_completo
        """, (id_ws,))
        inscritos = []
        for row in cursor.fetchall():
            inscritos.append({
                "id_cadastro":    row[0],
                "nome_completo":  row[1],
                "email":          row[2] or "",
                "data_inscricao": row[3].isoformat() if row[3] else None,
            })
 
        return jsonify(ok=True, workshop=workshop, inscritos=inscritos)
    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/workshop/inscrever", methods=["POST"])
@login_required(tipo="administrador")
def inscrever_workshop():
    data = request.get_json(silent=True) or {}
    id_ws   = data.get("id_workshop")
    id_part = data.get("id_participante")
    if not id_ws or not id_part:
        return jsonify(ok=False, mensagem="Informe id_workshop e id_participante."), 400
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
 
        # Verificar vagas
        cursor.execute("""
            SELECT vagas_totais, status,
                   (SELECT COUNT(*) FROM workshop_inscricoes wi
                    WHERE wi.id_workshop=w.id_workshop AND wi.ativo=1) AS inscritos
            FROM workshops w WHERE id_workshop=?
        """, (id_ws,))
        r = cursor.fetchone()
        if not r:
            return jsonify(ok=False, mensagem="Workshop não encontrado."), 404
 
        vagas, status, inscritos = r[0] or 0, r[1], r[2] or 0
 
        if status == "encerrado":
            return jsonify(ok=False, codigo="WS_ENCERRADO",
                mensagem="Este workshop está com inscrições encerradas."), 400
 
        if vagas > 0 and inscritos >= vagas:
            return jsonify(ok=False, codigo="WS_CHEIO",
                mensagem=f"Workshop lotado. Capacidade máxima: {vagas} participantes."), 400
 
        # Verificar duplicata
        cursor.execute("""
            SELECT id_inscricao, ativo FROM workshop_inscricoes
            WHERE id_workshop=? AND id_participante=?
        """, (id_ws, id_part))
        exist = cursor.fetchone()
        if exist:
            if exist[1]:
                return jsonify(ok=False, mensagem="Participante já inscrito."), 400
            cursor.execute("UPDATE workshop_inscricoes SET ativo=1 WHERE id_inscricao=?", (exist[0],))
        else:
            cursor.execute("""
                INSERT INTO workshop_inscricoes (id_workshop, id_participante, ativo)
                VALUES (?, ?, 1)
            """, (id_ws, id_part))
 
        conn.commit()
        return jsonify(ok=True, mensagem="Inscrição realizada."), 201
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/workshop/desinscrever", methods=["DELETE"])
@login_required(tipo="administrador")
def desinscrever_workshop():
    data = request.get_json(silent=True) or {}
    id_ws   = data.get("id_workshop")
    id_part = data.get("id_participante")
    if not id_ws or not id_part:
        return jsonify(ok=False, mensagem="Informe id_workshop e id_participante."), 400
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            UPDATE workshop_inscricoes SET ativo=0
            WHERE id_workshop=? AND id_participante=? AND ativo=1
        """, (id_ws, id_part))
        if cursor.rowcount == 0:
            return jsonify(ok=False, mensagem="Inscrição não encontrada."), 404
        conn.commit()
        return jsonify(ok=True, mensagem="Inscrição cancelada.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()

# ── helpers ──────────────────────────────────────────────────────
def fim_do_mes(d=None):
    d = d or date.today()
    if d.month == 12:
        return d.replace(day=31)
    return (d.replace(day=1, month=d.month+1) - timedelta(days=1))
 
 
# ================================================================
# PROFESSOR
# ================================================================
 
@app.route("/professor")
@login_required(tipo="professor")
def professor_page():
    return render_template(
        "professor.html",
        user_name=session.get("user_name", "Professora"),
        user_tipo="professor"
    )
 
def _tabela_existe(cursor, tabela):
    """Verifica se uma tabela existe no banco."""
    try:
        cursor.execute("""
            SELECT 1 FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE='BASE TABLE' AND TABLE_NAME = ?
        """, (tabela,))
        return cursor.fetchone() is not None
    except Exception:
        return False

def _col_exists(cursor, tabela, coluna):
    """Verifica se uma coluna existe em uma tabela SQL Server."""
    try:
        cursor.execute("""
            SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = ? AND COLUMN_NAME = ?
        """, (tabela, coluna))
        return cursor.fetchone() is not None
    except Exception:
        return False
        
@app.route("/professor/dados")
@login_required(tipo="professor")
def professor_dados():
    id_prof = session["user_id"]
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
 
        # ── Turmas do professor ────────────────────────────────
        # Busca por id_responsavel_cadastro OU id_professora (compatibilidade)
        has_id_prof_col = _col_exists(cursor, "turmas", "id_professora")
 
        if has_id_prof_col:
            sql_turmas = """
                SELECT t.id_turma, t.nome_exibicao, t.capacidade_maxima,
                       m.titulo, s.nome, h.hora_inicio, h.hora_fim, h.dias_semana
                FROM turmas t
                JOIN modalidades m ON m.id_modalidade = t.id_modalidade
                JOIN salas s ON s.id_sala = t.id_sala
                JOIN horarios h ON h.id_horario = t.id_horario
                WHERE t.ativo = 1
                  AND (m.id_responsavel_cadastro = ? OR t.id_professora = ?)
                ORDER BY m.titulo, h.hora_inicio
            """
            cursor.execute(sql_turmas, (id_prof, id_prof))
        else:
            cursor.execute("""
                SELECT t.id_turma, t.nome_exibicao, t.capacidade_maxima,
                       m.titulo, s.nome, h.hora_inicio, h.hora_fim, h.dias_semana
                FROM turmas t
                JOIN modalidades m ON m.id_modalidade = t.id_modalidade
                JOIN salas s ON s.id_sala = t.id_sala
                JOIN horarios h ON h.id_horario = t.id_horario
                WHERE t.ativo = 1 AND m.id_responsavel_cadastro = ?
                ORDER BY m.titulo, h.hora_inicio
            """, (id_prof,))
 
        turmas = []
        for r in cursor.fetchall():
            turma = {
                "id_turma": r[0], "nome_exibicao": r[1] or "",
                "capacidade_maxima": r[2] or 0,
                "modalidade": r[3], "sala": r[4],
                "hora_inicio": str(r[5])[:5] if r[5] else "",
                "hora_fim":    str(r[6])[:5] if r[6] else "",
                "dias_semana": r[7] or "",
                "alunos": [],
            }
 
            # Alunos da turma
            cursor.execute("""
                SELECT c.id_cadastro, c.nome_completo, c.email, c.termo_imagem,
                       p.nome AS plano
                FROM matriculas ma
                JOIN cadastro c ON c.id_cadastro = ma.id_aluno
                LEFT JOIN aluno_pacote ap ON ap.id_aluno = c.id_cadastro AND ap.ativo = 1
                LEFT JOIN pacotes p ON p.id_pacote = ap.id_pacote
                WHERE ma.id_turma = ? AND ma.ativo = 1
                ORDER BY c.nome_completo
            """, (turma["id_turma"],))
            for a in cursor.fetchall():
                turma["alunos"].append({
                    "id_cadastro":   a[0],
                    "nome_completo": a[1],
                    "email":         a[2] or "",
                    "termo_imagem":  bool(a[3]),
                    "plano":         a[4] or "",
                })
            turmas.append(turma)
 
        # ── Reposições geradas pelo professor ──────────────────
        reposicoes = []
        if _tabela_existe(cursor, "reposicoes") and _tabela_existe(cursor, "aulas_canceladas"):
            try:
                cursor.execute("""
                    SELECT rep.id_reposicao, c.nome_completo,
                           rep.tipo, rep.usada,
                           t.nome_exibicao, m.titulo,
                           ac.data_aula, ac.motivo
                    FROM reposicoes rep
                    JOIN cadastro c ON c.id_cadastro = rep.id_aluno
                    JOIN aulas_canceladas ac ON ac.id_cancelamento = rep.id_cancelamento
                    JOIN turmas t ON t.id_turma = ac.id_turma
                    JOIN modalidades m ON m.id_modalidade = t.id_modalidade
                    WHERE ac.cancelado_por = ? AND rep.tipo = 'especial'
                    ORDER BY rep.criado_em DESC
                """, (id_prof,))
                for r in cursor.fetchall():
                    reposicoes.append({
                        "id_reposicao": r[0],
                        "aluno_nome":   r[1],
                        "tipo":         r[2],
                        "usada":        bool(r[3]),
                        "turma_nome":   r[4] or r[5],
                        "data_aula":    str(r[6]) if r[6] else "",
                        "motivo":       r[7] or "",
                    })
            except pyodbc.Error:
                reposicoes = []
 
        # ── Histórico de cancelamentos ─────────────────────────
        historico = []
        if _tabela_existe(cursor, "aulas_canceladas"):
            try:
                cursor.execute("""
                    SELECT ac.id_cancelamento,
                           m.titulo, t.nome_exibicao,
                           ac.data_aula, ac.motivo, ac.cancelado_em,
                           (SELECT COUNT(*) FROM reposicoes rep
                            WHERE rep.id_cancelamento = ac.id_cancelamento) AS afetados
                    FROM aulas_canceladas ac
                    JOIN turmas t ON t.id_turma = ac.id_turma
                    JOIN modalidades m ON m.id_modalidade = t.id_modalidade
                    WHERE ac.cancelado_por = ?
                    ORDER BY ac.cancelado_em DESC
                """, (id_prof,))
                for r in cursor.fetchall():
                    historico.append({
                        "id_cancelamento": r[0],
                        "turma_nome":      r[2] or r[1],
                        "data_aula":       str(r[3]) if r[3] else "",
                        "motivo":          r[4] or "",
                        "cancelado_em":    str(r[5])[:10] if r[5] else "",
                        "alunos_afetados": r[6] or 0,
                    })
            except pyodbc.Error:
                historico = []
 
        return jsonify(ok=True, turmas=turmas, reposicoes=reposicoes, historico=historico)
 
    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
@app.route("/professor/cancelar-aula", methods=["POST"])
@login_required(tipo="professor")
def professor_cancelar_aula():
    data_req  = request.get_json(silent=True) or {}
    id_turma  = data_req.get("id_turma")
    data_aula = data_req.get("data_aula")
    motivo    = (data_req.get("motivo") or "").strip()
    id_prof   = session["user_id"]
 
    if not id_turma or not data_aula:
        return jsonify(ok=False, mensagem="Informe a turma e a data da aula."), 400
 
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
 
        # Verificar se tabelas existem
        if not _tabela_existe(cursor, "aulas_canceladas"):
            return jsonify(ok=False, mensagem="Funcionalidade de cancelamento ainda não configurada no banco. Execute o schema SQL."), 400
 
        cursor.execute("""
            SELECT 1 FROM aulas_canceladas WHERE id_turma = ? AND data_aula = ?
        """, (id_turma, data_aula))
        if cursor.fetchone():
            return jsonify(ok=False, mensagem="Esta aula já foi cancelada anteriormente."), 400
 
        cursor.execute("""
            INSERT INTO aulas_canceladas (id_turma, data_aula, motivo, cancelado_por)
            VALUES (?, ?, ?, ?)
        """, (id_turma, data_aula, motivo, id_prof))
        cursor.execute("SELECT SCOPE_IDENTITY()")
        id_cancel = int(cursor.fetchone()[0])
 
        cursor.execute("SELECT id_aluno FROM matriculas WHERE id_turma = ? AND ativo = 1", (id_turma,))
        alunos = [r[0] for r in cursor.fetchall()]
 
        if _tabela_existe(cursor, "reposicoes"):
            valida_ate = fim_do_mes()
            for id_aluno in alunos:
                cursor.execute("""
                    INSERT INTO reposicoes
                        (id_aluno, id_cancelamento, tipo, usada,
                         data_aula_orig, id_turma_orig, valida_ate)
                    VALUES (?, ?, 'especial', 0, ?, ?, ?)
                """, (id_aluno, id_cancel, data_aula, id_turma, valida_ate))
 
        conn.commit()
        return jsonify(ok=True, mensagem=f"Aula cancelada. {len(alunos)} aluno(s) notificados.")
 
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
  
# ================================================================
# ALUNO
# ================================================================
 
@app.route("/aluno")
@login_required(tipo="aluno")
def aluno_page():
    return render_template(
        "aluno.html",
        user_name=session.get("user_name", "Aluno"),
        user_tipo="aluno"
    )
 
@app.route("/aluno/dados")
@login_required(tipo="aluno")
def aluno_dados():
    id_aluno = session["user_id"]
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
 
        # ── Info do aluno ──────────────────────────────────────
        cursor.execute("""
            SELECT id_cadastro, nome_completo, email, termo_imagem
            FROM cadastro WHERE id_cadastro = ?
        """, (id_aluno,))
        row = cursor.fetchone()
        if not row:
            return jsonify(ok=False, mensagem="Aluno não encontrado."), 404
        aluno = {
            "id_cadastro":  row[0],
            "nome_completo":row[1],
            "email":        row[2] or "",
            "termo_imagem": bool(row[3]),
        }
 
        # ── Plano ativo ────────────────────────────────────────
        plano = None
        mods_plano_ids = set()
        status_acesso = "sem_plano"
 
        tem_aluno_pacote = _tabela_existe(cursor, "aluno_pacote")
        if tem_aluno_pacote:
            has_status_col = _col_exists(cursor, "aluno_pacote", "status_acesso")
 
            if has_status_col:
                cursor.execute("""
                    SELECT p.id_pacote, p.nome, p.tipo_cobranca, p.valor,
                           p.aulas_por_semana, p.qt_modalidades,
                           ap.data_inicio, ap.data_fim, ap.status_acesso
                    FROM aluno_pacote ap
                    JOIN pacotes p ON p.id_pacote = ap.id_pacote
                    WHERE ap.id_aluno = ? AND ap.ativo = 1
                """, (id_aluno,))
            else:
                cursor.execute("""
                    SELECT p.id_pacote, p.nome, p.tipo_cobranca, p.valor,
                           p.aulas_por_semana, p.qt_modalidades,
                           ap.data_inicio, ap.data_fim, 'ativo' AS status_acesso
                    FROM aluno_pacote ap
                    JOIN pacotes p ON p.id_pacote = ap.id_pacote
                    WHERE ap.id_aluno = ? AND ap.ativo = 1
                """, (id_aluno,))
 
            p = cursor.fetchone()
            if p:
                pid = p[0]
                status_acesso = p[8] if p[8] else "ativo"
 
                cursor.execute("""
                    SELECT pm.id_modalidade, m.titulo
                    FROM pacote_modalidades pm
                    JOIN modalidades m ON m.id_modalidade = pm.id_modalidade
                    WHERE pm.id_pacote = ?
                """, (pid,))
                mods_rows = cursor.fetchall()
                mods_plano_ids = {r[0] for r in mods_rows}
 
                plano = {
                    "id_pacote":         pid,
                    "nome":              p[1],
                    "tipo_cobranca":     p[2],
                    "valor":             float(p[3] or 0),
                    "aulas_por_semana":  p[4] or 1,
                    "qt_modalidades":    p[5] or 1,
                    "status":            status_acesso,
                    "data_inicio":       str(p[6]) if p[6] else "",
                    "data_fim":          str(p[7]) if p[7] else "",
                    "modalidades_nomes": [r[1] for r in mods_rows],
                    "modalidades_ids":   list(mods_plano_ids),
                }
 
        # ── Turmas matriculadas ────────────────────────────────
        # SEM tipo_matricula — coluna pode não existir ainda
        cursor.execute("""
            SELECT
                t.id_turma, t.nome_exibicao, t.capacidade_maxima,
                m.titulo AS modalidade,
                s.nome   AS sala,
                h.hora_inicio, h.hora_fim, h.dias_semana,
                c.nome_completo AS professora
            FROM matriculas ma
            JOIN turmas      t ON t.id_turma      = ma.id_turma
            JOIN modalidades m ON m.id_modalidade = t.id_modalidade
            JOIN salas       s ON s.id_sala        = t.id_sala
            JOIN horarios    h ON h.id_horario     = t.id_horario
            LEFT JOIN cadastro c ON c.id_cadastro  = m.id_responsavel_cadastro
            WHERE ma.id_aluno = ? AND ma.ativo = 1 AND t.ativo = 1
            ORDER BY h.hora_inicio
        """, (id_aluno,))
 
        turmas_mat = []
        ids_mat = set()
        for r in cursor.fetchall():
            turmas_mat.append({
                "id_turma":         r[0],
                "nome_exibicao":    r[1] or "",
                "capacidade_maxima":r[2] or 0,
                "modalidade":       r[3],
                "sala":             r[4],
                "hora_inicio":      str(r[5])[:5] if r[5] else "",
                "hora_fim":         str(r[6])[:5] if r[6] else "",
                "dias_semana":      r[7] or "",
                "professora":       r[8] or "",
                "tipo_matricula":   "normal",   # padrão
                "inscritos":        0,
            })
            ids_mat.add(r[0])
 
        # ── Turmas disponíveis (do plano) ──────────────────────
        cursor.execute("""
            SELECT
                t.id_turma, t.nome_exibicao, t.capacidade_maxima,
                m.id_modalidade, m.titulo AS modalidade,
                s.nome, h.hora_inicio, h.hora_fim, h.dias_semana,
                c.nome_completo AS professora,
                (SELECT COUNT(*) FROM matriculas ma
                 WHERE ma.id_turma = t.id_turma AND ma.ativo = 1) AS inscritos
            FROM turmas t
            JOIN modalidades m ON m.id_modalidade = t.id_modalidade
            JOIN salas       s ON s.id_sala        = t.id_sala
            JOIN horarios    h ON h.id_horario     = t.id_horario
            LEFT JOIN cadastro c ON c.id_cadastro  = m.id_responsavel_cadastro
            WHERE t.ativo = 1
            ORDER BY m.titulo, h.hora_inicio
        """)
        turmas_disp = []
        for r in cursor.fetchall():
            id_t   = r[0]
            id_mod = r[3]
            cap    = r[2] or 0
            ocp    = r[10] or 0
            lotada = cap > 0 and ocp >= cap
            no_plano = id_mod in mods_plano_ids if mods_plano_ids else False
 
            if not no_plano:
                # Turma fora do plano — adicionar como reposição especial
                if not lotada:
                    turmas_disp.append({
                        "id_turma": id_t, "nome_exibicao": r[1] or "",
                        "capacidade_maxima": cap, "modalidade": r[4],
                        "sala": r[5],
                        "hora_inicio": str(r[6])[:5] if r[6] else "",
                        "hora_fim":    str(r[7])[:5] if r[7] else "",
                        "dias_semana": r[8] or "", "professora": r[9] or "",
                        "inscritos": ocp, "lotada": False,
                        "apenas_reposicao_especial": True,
                    })
                continue
 
            turmas_disp.append({
                "id_turma": id_t, "nome_exibicao": r[1] or "",
                "capacidade_maxima": cap, "modalidade": r[4],
                "sala": r[5],
                "hora_inicio": str(r[6])[:5] if r[6] else "",
                "hora_fim":    str(r[7])[:5] if r[7] else "",
                "dias_semana": r[8] or "", "professora": r[9] or "",
                "inscritos": ocp, "lotada": lotada,
                "apenas_reposicao_especial": False,
            })
 
        # ── Reposições ─────────────────────────────────────────
        reposicoes = []
        if _tabela_existe(cursor, "reposicoes"):
            try:
                cursor.execute("""
                    SELECT rep.id_reposicao, rep.tipo, rep.usada,
                           rep.data_aula_orig, rep.valida_ate,
                           m.titulo, t.nome_exibicao
                    FROM reposicoes rep
                    LEFT JOIN turmas t ON t.id_turma = rep.id_turma_orig
                    LEFT JOIN modalidades m ON m.id_modalidade = t.id_modalidade
                    WHERE rep.id_aluno = ?
                    ORDER BY rep.criado_em DESC
                """, (id_aluno,))
                for r in cursor.fetchall():
                    reposicoes.append({
                        "id_reposicao":    r[0],
                        "tipo":            r[1],
                        "usada":           bool(r[2]),
                        "data_aula":       str(r[3]) if r[3] else "",
                        "valida_ate":      str(r[4]) if r[4] else "",
                        "turma_cancelada": r[6] or r[5] or "—",
                    })
            except pyodbc.Error:
                reposicoes = []
 
        # ── Pacotes disponíveis ────────────────────────────────
        cursor.execute("""
            SELECT p.id_pacote, p.nome, p.tipo_cobranca, p.valor,
                   p.aulas_por_semana, p.qt_modalidades
            FROM pacotes p WHERE p.ativo = 1 ORDER BY p.valor
        """)
        pacotes = []
        for r in cursor.fetchall():
            pid = r[0]
            cursor.execute("""
                SELECT m.titulo FROM pacote_modalidades pm
                JOIN modalidades m ON m.id_modalidade = pm.id_modalidade
                WHERE pm.id_pacote = ?
            """, (pid,))
            mods_p = [x[0] for x in cursor.fetchall()]
            pacotes.append({
                "id_pacote":        pid,
                "nome":             r[1],
                "tipo_cobranca":    r[2],
                "valor":            float(r[3] or 0),
                "aulas_por_semana": r[4] or 1,
                "qt_modalidades":   r[5] or 1,
                "status":           "ativo",
                "modalidades_nomes":mods_p,
            })
 
        # ── Solicitação pendente ───────────────────────────────
        sol_pend = None
        if _tabela_existe(cursor, "solicitacoes_plano"):
            try:
                cursor.execute("""
                    SELECT sp.id_solicitacao, sp.id_pacote, p.nome
                    FROM solicitacoes_plano sp
                    JOIN pacotes p ON p.id_pacote = sp.id_pacote
                    WHERE sp.id_aluno = ? AND sp.status = 'pendente'
                """, (id_aluno,))
                r_sol = cursor.fetchone()
                if r_sol:
                    sol_pend = {
                        "id_solicitacao": r_sol[0],
                        "id_pacote":      r_sol[1],
                        "pacote_nome":    r_sol[2],
                    }
            except pyodbc.Error:
                sol_pend = None
 
        # ── Histórico de pagamentos ────────────────────────────
        hist_pag = []
        if _tabela_existe(cursor, "historico_pagamentos"):
            try:
                cursor.execute("""
                    SELECT data_pagamento, valor, descricao
                    FROM historico_pagamentos
                    WHERE id_aluno = ?
                    ORDER BY data_pagamento DESC
                """, (id_aluno,))
                hist_pag = [
                    {
                        "data_pagamento": str(r[0]) if r[0] else "",
                        "valor":          float(r[1] or 0),
                        "descricao":      r[2] or "",
                    }
                    for r in cursor.fetchall()
                ]
            except pyodbc.Error:
                hist_pag = []
 
        return jsonify(
            ok=True,
            aluno=aluno,
            plano=plano,
            turmas_matriculadas=turmas_mat,
            turmas_disponiveis=turmas_disp,
            reposicoes=reposicoes,
            pacotes=pacotes,
            status_acesso=status_acesso,
            solicitacao_pendente=sol_pend,
            historico_pagamentos=hist_pag,
        )
 
    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
@app.route("/aluno/inscrever", methods=["POST"])
@login_required(tipo="aluno")
def aluno_inscrever():
    data_req = request.get_json(silent=True) or {}
    id_turma = data_req.get("id_turma")
    id_aluno = session["user_id"]
    if not id_turma:
        return jsonify(ok=False, mensagem="Informe a turma."), 400
 
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
 
        # 0. Verificar suspensão
        if _tabela_existe(cursor, "aluno_pacote") and _col_exists(cursor, "aluno_pacote", "status_acesso"):
            cursor.execute("SELECT status_acesso FROM aluno_pacote WHERE id_aluno=? AND ativo=1", (id_aluno,))
            r_st = cursor.fetchone()
            if r_st and r_st[0] == "suspenso":
                return jsonify(ok=False, mensagem="Seu acesso está suspenso por falta de pagamento. Entre em contato com o estúdio."), 403
 
        # 1. Plano ativo
        if _tabela_existe(cursor, "aluno_pacote"):
            cursor.execute("SELECT id_pacote FROM aluno_pacote WHERE id_aluno=? AND ativo=1", (id_aluno,))
            p = cursor.fetchone()
            if not p:
                return jsonify(ok=False, mensagem="Você não possui um plano ativo. Entre em contato com o estúdio."), 400
 
            # 2. Modalidade no plano
            cursor.execute("""
                SELECT pm.id_modalidade
                FROM turmas t
                JOIN pacote_modalidades pm ON pm.id_pacote = ? AND pm.id_modalidade = t.id_modalidade
                WHERE t.id_turma = ?
            """, (p[0], id_turma))
            if not cursor.fetchone():
                return jsonify(ok=False, mensagem="Esta modalidade não está incluída no seu plano."), 400
 
        # 3. Vagas
        cursor.execute("""
            SELECT t.capacidade_maxima,
                   (SELECT COUNT(*) FROM matriculas ma WHERE ma.id_turma=t.id_turma AND ma.ativo=1)
            FROM turmas t WHERE t.id_turma = ?
        """, (id_turma,))
        r = cursor.fetchone()
        if r and r[0] and r[1] >= r[0]:
            return jsonify(ok=False, mensagem="Esta turma está lotada."), 400
 
        # 4. Conflito de horário
        cursor.execute("""
            SELECT h.dias_semana, h.hora_inicio, h.hora_fim
            FROM turmas t JOIN horarios h ON h.id_horario=t.id_horario
            WHERE t.id_turma=?
        """, (id_turma,))
        nova = cursor.fetchone()
        if nova:
            cursor.execute("""
                SELECT h.dias_semana, h.hora_inicio, h.hora_fim
                FROM matriculas ma
                JOIN turmas t ON t.id_turma=ma.id_turma
                JOIN horarios h ON h.id_horario=t.id_horario
                WHERE ma.id_aluno=? AND ma.ativo=1
            """, (id_aluno,))
            for ex in cursor.fetchall():
                dias_n = set((nova[0] or "").lower().replace(" ","").split(","))
                dias_e = set((ex[0]   or "").lower().replace(" ","").split(","))
                if dias_n & dias_e:
                    ini_n = str(nova[1])[:5]; fim_n = str(nova[2])[:5]
                    ini_e = str(ex[1])[:5];   fim_e = str(ex[2])[:5]
                    if ini_n < fim_e and fim_n > ini_e:
                        return jsonify(ok=False, mensagem="Conflito de horário com outra aula já matriculada."), 400
 
        # 5. Duplicado / inserir
        cursor.execute("SELECT ativo FROM matriculas WHERE id_turma=? AND id_aluno=?", (id_turma, id_aluno))
        ex = cursor.fetchone()
        if ex:
            if ex[0]:
                return jsonify(ok=False, mensagem="Você já está inscrita nesta turma."), 400
            cursor.execute("UPDATE matriculas SET ativo=1 WHERE id_turma=? AND id_aluno=?", (id_turma, id_aluno))
        else:
            cursor.execute("INSERT INTO matriculas(id_turma, id_aluno, ativo) VALUES(?,?,1)", (id_turma, id_aluno))
 
        conn.commit()
        return jsonify(ok=True, mensagem="Inscrição realizada com sucesso!"), 201
 
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 

@app.route("/aluno/cancelar-aula", methods=["POST"])
@login_required(tipo="aluno")
def aluno_cancelar_aula():
    """
    Aluno cancela presença em uma aula (não remove a matrícula permanente,
    apenas gera uma reposição para o mês).
    """
    data_req = request.get_json(silent=True) or {}
    id_turma  = data_req.get("id_turma")
    data_aula = data_req.get("data_aula")
    id_aluno  = session["user_id"]
 
    if not id_turma or not data_aula:
        return jsonify(ok=False, mensagem="Informe a turma e a data."), 400
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
 
        # Verificar se está matriculado
        cursor.execute("SELECT 1 FROM matriculas WHERE id_turma=? AND id_aluno=? AND ativo=1", (id_turma, id_aluno))
        if not cursor.fetchone():
            return jsonify(ok=False, mensagem="Você não está inscrita nesta turma."), 400
 
        # Verificar se já cancelou esta data
        cursor.execute("""
            SELECT 1 FROM reposicoes
            WHERE id_aluno=? AND id_turma_orig=? AND data_aula_orig=? AND tipo='normal'
        """, (id_aluno, id_turma, data_aula))
        if cursor.fetchone():
            return jsonify(ok=False, mensagem="Você já cancelou esta aula."), 400
 
        # Gerar reposição normal
        valida_ate = fim_do_mes()
        cursor.execute("""
            INSERT INTO reposicoes (id_aluno, tipo, usada, data_aula_orig, id_turma_orig, valida_ate)
            VALUES (?, 'normal', 0, ?, ?, ?)
        """, (id_aluno, data_aula, id_turma, valida_ate))
 
        conn.commit()
        return jsonify(ok=True, mensagem="Aula cancelada. Uma reposição foi creditada para o mês.")
 
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/aluno/usar-reposicao-especial", methods=["POST"])
@login_required(tipo="aluno")
def aluno_usar_reposicao_especial():
    data_req     = request.get_json(silent=True) or {}
    id_turma     = data_req.get("id_turma")
    id_reposicao = data_req.get("id_reposicao")
    id_aluno     = session["user_id"]
 
    if not id_turma or not id_reposicao:
        return jsonify(ok=False, mensagem="Informe a turma e a reposição."), 400
 
    if not _tabela_existe(None, "reposicoes"):
        return jsonify(ok=False, mensagem="Funcionalidade de reposições ainda não configurada."), 400
 
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
 
        cursor.execute("""
            SELECT usada, valida_ate FROM reposicoes
            WHERE id_reposicao=? AND id_aluno=? AND tipo='especial'
        """, (id_reposicao, id_aluno))
        rep = cursor.fetchone()
        if not rep:
            return jsonify(ok=False, mensagem="Reposição não encontrada."), 404
        if rep[0]:
            return jsonify(ok=False, mensagem="Esta reposição já foi utilizada."), 400
        if rep[1] and date.today() > rep[1]:
            return jsonify(ok=False, mensagem="Esta reposição expirou."), 400
 
        # Vagas
        cursor.execute("""
            SELECT t.capacidade_maxima,
                   (SELECT COUNT(*) FROM matriculas ma WHERE ma.id_turma=t.id_turma AND ma.ativo=1)
            FROM turmas t WHERE t.id_turma=?
        """, (id_turma,))
        r = cursor.fetchone()
        if r and r[0] and r[1] >= r[0]:
            return jsonify(ok=False, mensagem="Esta turma está lotada."), 400
 
        # Conflito horário
        cursor.execute("""
            SELECT h.dias_semana, h.hora_inicio, h.hora_fim
            FROM turmas t JOIN horarios h ON h.id_horario=t.id_horario WHERE t.id_turma=?
        """, (id_turma,))
        nova = cursor.fetchone()
        if nova:
            cursor.execute("""
                SELECT h.dias_semana, h.hora_inicio, h.hora_fim
                FROM matriculas ma JOIN turmas t ON t.id_turma=ma.id_turma
                JOIN horarios h ON h.id_horario=t.id_horario
                WHERE ma.id_aluno=? AND ma.ativo=1
            """, (id_aluno,))
            for ex in cursor.fetchall():
                dias_n = set((nova[0] or "").lower().replace(" ","").split(","))
                dias_e = set((ex[0]   or "").lower().replace(" ","").split(","))
                if dias_n & dias_e:
                    ini_n = str(nova[1])[:5]; fim_n = str(nova[2])[:5]
                    ini_e = str(ex[1])[:5];   fim_e = str(ex[2])[:5]
                    if ini_n < fim_e and fim_n > ini_e:
                        return jsonify(ok=False, mensagem="Conflito de horário com outra aula."), 400
 
        # Matricular
        cursor.execute("SELECT ativo FROM matriculas WHERE id_turma=? AND id_aluno=?", (id_turma, id_aluno))
        ex = cursor.fetchone()
        if ex and ex[0]:
            return jsonify(ok=False, mensagem="Você já está inscrita nesta turma."), 400
 
        cursor.execute("INSERT INTO matriculas(id_turma, id_aluno, ativo) VALUES(?,?,1)", (id_turma, id_aluno))
        cursor.execute("UPDATE reposicoes SET usada=1 WHERE id_reposicao=?", (id_reposicao,))
        conn.commit()
        return jsonify(ok=True, mensagem="Reposição especial utilizada! Inscrição realizada.")
 
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
  
@app.route("/aluno/solicitar-plano", methods=["POST"])
@login_required(tipo="aluno")
def aluno_solicitar_plano():
    data_req  = request.get_json(silent=True) or {}
    id_pacote = data_req.get("id_pacote")
    id_aluno  = session["user_id"]
 
    if not id_pacote:
        return jsonify(ok=False, mensagem="Informe o pacote."), 400
 
    conn = cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
 
        if not _tabela_existe(cursor, "solicitacoes_plano"):
            return jsonify(ok=False, mensagem="Funcionalidade ainda não configurada. Execute o schema SQL."), 400
 
        cursor.execute("""
            SELECT 1 FROM solicitacoes_plano WHERE id_aluno=? AND status='pendente'
        """, (id_aluno,))
        if cursor.fetchone():
            return jsonify(ok=False, mensagem="Você já tem uma solicitação pendente. Aguarde a análise do estúdio."), 400
 
        tem_plano = False
        if _tabela_existe(cursor, "aluno_pacote"):
            cursor.execute("SELECT 1 FROM aluno_pacote WHERE id_aluno=? AND ativo=1", (id_aluno,))
            tem_plano = bool(cursor.fetchone())
 
        tipo = "mudanca" if tem_plano else "nova_compra"
 
        # Verificar se coluna tipo_solicitacao existe
        has_tipo_col = _col_exists(cursor, "solicitacoes_plano", "tipo_solicitacao")
        if has_tipo_col:
            cursor.execute("""
                INSERT INTO solicitacoes_plano(id_aluno, id_pacote, tipo_solicitacao, status)
                VALUES(?,?,?,'pendente')
            """, (id_aluno, id_pacote, tipo))
        else:
            cursor.execute("""
                INSERT INTO solicitacoes_plano(id_aluno, id_pacote, status)
                VALUES(?,?,'pendente')
            """, (id_aluno, id_pacote))
 
        conn.commit()
        return jsonify(ok=True, mensagem="Solicitação enviada! Envie o comprovante PIX via WhatsApp para ativar.")
 
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
def proximo_vencimento(data_inicio, tipo_cobranca, ultimo_pagamento=None):
    """Calcula a data do próximo vencimento baseado no tipo do plano."""
    base = ultimo_pagamento or data_inicio
    if isinstance(base, str):
        base = date.fromisoformat(base)
    if tipo_cobranca == 'mensal':
        return base + relativedelta(months=1)
    else:  # semestral
        return base + relativedelta(months=6)
 
 
def dias_ciclo(tipo_cobranca):
    return 30 if tipo_cobranca == 'mensal' else 183
 
 
# ================================================================
# ADMIN: PÁGINA DE CONTROLE
# ================================================================
 
@app.route("/admin/pacotes-controle")
@login_required(tipo="administrador")
def admin_pacotes_controle():
    return render_template("admin_pacotes_controle.html",
        user_name=session.get("user_name", "Administrador"))
 
 
@app.route("/admin/pacotes-controle/dados")
@login_required(tipo="administrador")
def pacotes_controle_dados():
    """Retorna tudo que o painel de controle precisa."""
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        hoje = date.today()
 
        # ── Solicitações ──────────────────────────────────────────
        cursor.execute("""
            SELECT
                sp.id_solicitacao,
                sp.tipo_solicitacao,
                sp.status,
                sp.criado_em,
                sp.obs_admin,
                c.nome_completo  AS aluno_nome,
                c.email          AS aluno_email,
                p.id_pacote,
                p.nome           AS pacote_nome,
                p.tipo_cobranca,
                p.valor,
                adm.nome_completo AS admin_nome
            FROM solicitacoes_plano sp
            JOIN cadastro c  ON c.id_cadastro  = sp.id_aluno
            JOIN pacotes p   ON p.id_pacote    = sp.id_pacote
            LEFT JOIN cadastro adm ON adm.id_cadastro = sp.processado_por
            ORDER BY
                CASE sp.status WHEN 'pendente' THEN 0 ELSE 1 END,
                sp.criado_em DESC
        """)
        solicitacoes = []
        for r in cursor.fetchall():
            sid = r[0]
            cursor.execute("""
                SELECT m.titulo FROM pacote_modalidades pm
                JOIN modalidades m ON m.id_modalidade=pm.id_modalidade
                WHERE pm.id_pacote=?
            """, (r[7],))
            mods = [x[0] for x in cursor.fetchall()]
            solicitacoes.append({
                "id_solicitacao":   sid,
                "tipo_solicitacao": r[1] or "nova_compra",
                "status":           r[2],
                "criado_em":        str(r[3])[:19] if r[3] else "",
                "obs_admin":        r[4] or "",
                "aluno_nome":       r[5],
                "aluno_email":      r[6] or "",
                "id_pacote":        r[7],
                "pacote_nome":      r[8],
                "tipo_cobranca":    r[9],
                "pacote_valor":     float(r[10] or 0),
                "admin_nome":       r[11] or "",
                "modalidades":      mods,
            })
 
        # ── Planos ativos (aluno_pacote) ──────────────────────────
        cursor.execute("""
            SELECT
                ap.id_aluno_pacote,
                ap.id_aluno,
                c.nome_completo  AS aluno_nome,
                c.email,
                p.nome           AS pacote_nome,
                p.tipo_cobranca,
                p.valor,
                ap.data_inicio,
                ap.data_fim,
                ap.status_acesso,
                ap.ultimo_pagamento,
                ap.proximo_vencimento
            FROM aluno_pacote ap
            JOIN cadastro c ON c.id_cadastro = ap.id_aluno
            JOIN pacotes  p ON p.id_pacote   = ap.id_pacote
            WHERE ap.ativo = 1
            ORDER BY ap.proximo_vencimento, c.nome_completo
        """)
        aluno_pacotes = []
        for r in cursor.fetchall():
            prox_venc = r[11]
            if prox_venc is None and r[7]:
                prox_venc = proximo_vencimento(r[7], r[5], r[10])
            aluno_pacotes.append({
                "id_aluno_pacote":      r[0],
                "id_aluno":             r[1],
                "aluno_nome":           r[2],
                "aluno_email":          r[3] or "",
                "pacote_nome":          r[4],
                "tipo_cobranca":        r[5],
                "valor":                float(r[6] or 0),
                "data_inicio":          str(r[7]) if r[7] else "",
                "data_fim":             str(r[8]) if r[8] else "",
                "status_acesso":        r[9] or "ativo",
                "ultimo_pagamento":     str(r[10]) if r[10] else "",
                "proximo_vencimento":   str(prox_venc) if prox_venc else "",
                "ciclo_dias":           dias_ciclo(r[5]),
            })
 
        # ── Histórico de ações ────────────────────────────────────
        cursor.execute("""
            SELECT
                hpac.criado_em,
                c.nome_completo  AS aluno_nome,
                p.nome           AS pacote_nome,
                hpac.acao,
                hpac.observacao,
                adm.nome_completo AS admin_nome
            FROM historico_pacotes hpac
            JOIN cadastro c  ON c.id_cadastro  = hpac.id_aluno
            JOIN pacotes  p  ON p.id_pacote    = hpac.id_pacote
            LEFT JOIN cadastro adm ON adm.id_cadastro = hpac.id_admin
            ORDER BY hpac.criado_em DESC
        """)
        historico = [{
            "criado_em":   str(r[0])[:10] if r[0] else "",
            "aluno_nome":  r[1],
            "pacote_nome": r[2],
            "acao":        r[3],
            "observacao":  r[4] or "",
            "admin_nome":  r[5] or "",
        } for r in cursor.fetchall()]
 
        # ── KPIs ──────────────────────────────────────────────────
        pend = sum(1 for s in solicitacoes if s["status"] == "pendente")
        ap_hoje = sum(1 for s in solicitacoes if s["status"] == "aprovada" and str(s["criado_em"])[:10] == str(hoje))
        neg = sum(1 for s in solicitacoes if s["status"] == "negada")
        ativos = sum(1 for ap in aluno_pacotes if ap["status_acesso"] == "ativo")
        susp = sum(1 for ap in aluno_pacotes if ap["status_acesso"] == "suspenso")
 
        return jsonify(
            ok=True,
            solicitacoes=solicitacoes,
            aluno_pacotes=aluno_pacotes,
            historico=historico,
            kpis={"pendentes":pend,"aprovadas_hoje":ap_hoje,"negadas":neg,"ativos":ativos,"suspensos":susp}
        )
 
    except pyodbc.Error as e:
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/pacotes-controle/aprovar", methods=["POST"])
@login_required(tipo="administrador")
def pacotes_aprovar():
    data_req     = request.get_json(silent=True) or {}
    id_sol       = data_req.get("id_solicitacao")
    data_pag     = data_req.get("data_pagamento")
    data_inicio  = data_req.get("data_inicio")
    obs          = data_req.get("obs", "")
    id_admin     = session["user_id"]
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
 
        # Buscar solicitação
        cursor.execute("""
            SELECT sp.id_aluno, sp.id_pacote, sp.tipo_solicitacao, p.tipo_cobranca, p.valor
            FROM solicitacoes_plano sp
            JOIN pacotes p ON p.id_pacote=sp.id_pacote
            WHERE sp.id_solicitacao=? AND sp.status='pendente'
        """, (id_sol,))
        sol = cursor.fetchone()
        if not sol:
            return jsonify(ok=False, mensagem="Solicitação não encontrada ou já processada."), 404
 
        id_aluno, id_pacote, tipo_sol, tipo_cob, valor = sol
 
        # Calcular próximo vencimento
        d_ini  = date.fromisoformat(data_inicio)
        d_pag  = date.fromisoformat(data_pag)
        prox   = proximo_vencimento(d_ini, tipo_cob, d_pag)
 
        # Inativar plano anterior se existir
        cursor.execute("UPDATE aluno_pacote SET ativo=0 WHERE id_aluno=? AND ativo=1", (id_aluno,))
 
        # Criar novo aluno_pacote
        cursor.execute("""
            INSERT INTO aluno_pacote
                (id_aluno, id_pacote, data_inicio, status_acesso,
                 ultimo_pagamento, proximo_vencimento, ativo)
            VALUES (?, ?, ?, 'ativo', ?, ?, 1)
        """, (id_aluno, id_pacote, d_ini, d_pag, prox))
 
        # Registrar pagamento inicial no histórico
        cursor.execute("""
            INSERT INTO historico_pacotes (id_aluno, id_pacote, acao, observacao, id_admin)
            VALUES (?, ?, 'aprovacao', ?, ?)
        """, (id_aluno, id_pacote, f"Aprovado. Pagamento: {data_pag}. {obs}", id_admin))
 
        # Registrar no historico_pagamentos
        cursor.execute("""
            INSERT INTO historico_pagamentos (id_aluno, id_pacote, data_pagamento, valor, descricao)
            VALUES (?, ?, ?, ?, ?)
        """, (id_aluno, id_pacote, d_pag, valor, obs or "Primeiro pagamento"))
 
        # Atualizar solicitação
        cursor.execute("""
            UPDATE solicitacoes_plano
            SET status='aprovada', processado_por=?, processado_em=GETDATE(), obs_admin=?
            WHERE id_solicitacao=?
        """, (id_admin, obs, id_sol))
 
        conn.commit()
        return jsonify(ok=True, mensagem="Solicitação aprovada e plano ativado com sucesso!")
 
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/pacotes-controle/negar", methods=["POST"])
@login_required(tipo="administrador")
def pacotes_negar():
    data_req = request.get_json(silent=True) or {}
    id_sol   = data_req.get("id_solicitacao")
    motivo   = data_req.get("motivo", "")
    id_admin = session["user_id"]
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("SELECT id_aluno, id_pacote FROM solicitacoes_plano WHERE id_solicitacao=? AND status='pendente'", (id_sol,))
        sol = cursor.fetchone()
        if not sol:
            return jsonify(ok=False, mensagem="Solicitação não encontrada."), 404
 
        cursor.execute("""
            UPDATE solicitacoes_plano
            SET status='negada', processado_por=?, processado_em=GETDATE(), obs_admin=?
            WHERE id_solicitacao=?
        """, (id_admin, motivo, id_sol))
 
        cursor.execute("""
            INSERT INTO historico_pacotes (id_aluno, id_pacote, acao, observacao, id_admin)
            VALUES (?, ?, 'negacao', ?, ?)
        """, (sol[0], sol[1], motivo or "Sem motivo informado", id_admin))
 
        conn.commit()
        return jsonify(ok=True, mensagem="Solicitação negada.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/pacotes-controle/registrar-pagamento", methods=["POST"])
@login_required(tipo="administrador")
def registrar_pagamento():
    data_req       = request.get_json(silent=True) or {}
    id_aluno_pacote= data_req.get("id_aluno_pacote")
    data_pag       = data_req.get("data_pagamento")
    obs            = data_req.get("obs", "")
    id_admin       = session["user_id"]
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            SELECT id_aluno, id_pacote, tipo_cobranca, valor
            FROM aluno_pacote ap
            JOIN pacotes p ON p.id_pacote=ap.id_pacote
            WHERE ap.id_aluno_pacote=? AND ap.ativo=1
        """, (id_aluno_pacote,))
        ap = cursor.fetchone()
        if not ap:
            return jsonify(ok=False, mensagem="Plano não encontrado."), 404
 
        id_aluno, id_pacote, tipo_cob, valor = ap
        d_pag = date.fromisoformat(data_pag)
        prox  = proximo_vencimento(d_pag, tipo_cob, d_pag)
 
        # Atualizar aluno_pacote
        cursor.execute("""
            UPDATE aluno_pacote
            SET ultimo_pagamento=?, proximo_vencimento=?, status_acesso='ativo'
            WHERE id_aluno_pacote=?
        """, (d_pag, prox, id_aluno_pacote))
 
        # Registrar pagamento
        cursor.execute("""
            INSERT INTO historico_pagamentos (id_aluno, id_pacote, data_pagamento, valor, descricao)
            VALUES (?, ?, ?, ?, ?)
        """, (id_aluno, id_pacote, d_pag, valor, obs or "Pagamento registrado"))
 
        cursor.execute("""
            INSERT INTO historico_pacotes (id_aluno, id_pacote, acao, observacao, id_admin)
            VALUES (?, ?, 'pagamento', ?, ?)
        """, (id_aluno, id_pacote, f"{obs} | Próx. venc.: {prox}", id_admin))
 
        conn.commit()
        return jsonify(ok=True, mensagem=f"Pagamento registrado. Próximo vencimento: {prox.strftime('%d/%m/%Y')}.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/pacotes-controle/suspender", methods=["POST"])
@login_required(tipo="administrador")
def suspender_aluno():
    data_req        = request.get_json(silent=True) or {}
    id_aluno_pacote = data_req.get("id_aluno_pacote")
    motivo          = data_req.get("motivo", "")
    id_admin        = session["user_id"]
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("SELECT id_aluno, id_pacote FROM aluno_pacote WHERE id_aluno_pacote=? AND ativo=1", (id_aluno_pacote,))
        ap = cursor.fetchone()
        if not ap:
            return jsonify(ok=False, mensagem="Plano não encontrado."), 404
 
        cursor.execute("UPDATE aluno_pacote SET status_acesso='suspenso' WHERE id_aluno_pacote=?", (id_aluno_pacote,))
        cursor.execute("""
            INSERT INTO historico_pacotes (id_aluno, id_pacote, acao, observacao, id_admin)
            VALUES (?, ?, 'suspensao', ?, ?)
        """, (ap[0], ap[1], motivo or "Suspensão por falta de pagamento", id_admin))
 
        conn.commit()
        return jsonify(ok=True, mensagem="Acesso suspenso. O aluno não poderá se inscrever em novas aulas.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/pacotes-controle/reativar", methods=["POST"])
@login_required(tipo="administrador")
def reativar_aluno():
    data_req        = request.get_json(silent=True) or {}
    id_aluno_pacote = data_req.get("id_aluno_pacote")
    id_admin        = session["user_id"]
 
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("SELECT id_aluno, id_pacote FROM aluno_pacote WHERE id_aluno_pacote=? AND ativo=1", (id_aluno_pacote,))
        ap = cursor.fetchone()
        if not ap:
            return jsonify(ok=False, mensagem="Plano não encontrado."), 404
 
        cursor.execute("UPDATE aluno_pacote SET status_acesso='ativo' WHERE id_aluno_pacote=?", (id_aluno_pacote,))
        cursor.execute("""
            INSERT INTO historico_pacotes (id_aluno, id_pacote, acao, observacao, id_admin)
            VALUES (?, ?, 'reativacao', 'Acesso reativado manualmente', ?)
        """, (ap[0], ap[1], id_admin))
 
        conn.commit()
        return jsonify(ok=True, mensagem="Acesso reativado com sucesso.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 # ================================================================
# ADMIN: AULAS EXPERIMENTAIS (nova sub-página)
# ================================================================
 
@app.route("/admin/aulas-experimentais")
@login_required(tipo="administrador")
def admin_aulas_experimentais():
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            SELECT id_experimental, nome, telefone, email, cpf,
                   modalidade, horario_preferido, mensagem, status, criado_em
            FROM aulas_experimentais
            ORDER BY criado_em DESC
        """)
        solicitacoes = []
        for r in cursor.fetchall():
            solicitacoes.append({
                "id":               r[0],
                "nome":             r[1],
                "telefone":         r[2],
                "email":            r[3] or "",
                "cpf":              r[4] or "",
                "modalidade":       r[5],
                "horario_preferido":r[6] or "",
                "mensagem":         r[7] or "",
                "status":           r[8] or "pendente",
                "criado_em":        str(r[9])[:16] if r[9] else "",
            })
        return render_template("admin_aulas_experimentais.html",
            user_name=session.get("user_name","Administrador"),
            solicitacoes=solicitacoes)
    except pyodbc.Error as e:
        flash(str(e), "erro")
        return render_template("admin_aulas_experimentais.html",
            user_name=session.get("user_name","Administrador"), solicitacoes=[])
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
 
 
@app.route("/admin/aula-experimental/<int:id_exp>/status", methods=["POST"])
@login_required(tipo="administrador")
def atualizar_status_experimental(id_exp):
    data   = request.get_json(silent=True) or {}
    status = data.get("status", "").strip()
    obs    = data.get("obs", "").strip()
    if status not in {"pendente", "confirmada", "realizada", "cancelada"}:
        return jsonify(ok=False, mensagem="Status inválido."), 400
    conn = cursor = None
    try:
        conn = get_conn(); cursor = conn.cursor()
        cursor.execute("""
            UPDATE aulas_experimentais SET status=?, observacao_admin=?
            WHERE id_experimental=?
        """, (status, obs, id_exp))
        conn.commit()
        return jsonify(ok=True, mensagem="Status atualizado.")
    except pyodbc.Error as e:
        if conn: conn.rollback()
        return jsonify(ok=False, mensagem=str(e)), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()
           
if __name__ == "__main__":
    app.run(debug=True)