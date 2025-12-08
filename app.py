import os
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
import urllib3
import re 

app = Flask(__name__)

# =========================================================================
# CONFIGURAÇÕES (Via Variáveis de Ambiente)
# =========================================================================

# Chave de segurança do Flask (Obrigatório para produção)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "uma_chave_padrao_insegura_mude_no_dokploy")

# URL Base da sua aplicação (usado para gerar links)
BASE_URL = os.environ.get("APP_BASE_URL", "https://confras.leanttro.com")

# Configurações do Directus
DIRECTUS_TOKEN = os.environ.get("DIRECTUS_TOKEN")
DIRECTUS_URL = os.environ.get("DIRECTUS_URL", "https://directus.leanttro.com")

# Controle de verificação SSL (True para produção externa, False se tiver erros de certificado interno)
# No Dokploy, defina VERIFY_SSL como "false" se estiver usando IP interno ou container, ou "true" se usar HTTPS externo
VERIFY_SSL_STR = os.environ.get("VERIFY_SSL", "true").lower()
VERIFY_SSL = VERIFY_SSL_STR == "true"

if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Validação crítica na inicialização
if not DIRECTUS_TOKEN:
    print("⚠️  AVISO CRÍTICO: DIRECTUS_TOKEN não foi configurado nas variáveis de ambiente!")

# =========================================================================
# FUNÇÕES AUXILIARES
# =========================================================================

def fetch_collection_data(collection_name, tenant_id, params=None):
    if params is None: params = {}
    params["filter[tenant_id][_eq]"] = tenant_id
    
    try:
        headers = {"Authorization": f"Bearer {DIRECTUS_TOKEN}"}
        r = requests.get(
            f"{DIRECTUS_URL}/items/{collection_name}", 
            params=params, 
            headers=headers,
            verify=VERIFY_SSL, 
            timeout=5
        )
        return r.json().get('data', []) if r.status_code == 200 else []
    except Exception as e: 
        print(f"Erro ao buscar {collection_name}: {e}")
        return []

# =========================================================================
# ROTAS
# =========================================================================

# 1. ROTA PRINCIPAL (CRIAÇÃO)
@app.route('/')
def home():
    return render_template("confras.html")

# 2. ROTA DA FESTA
@app.route('/festa/<slug>')
def festa_view(slug):
    try:
        headers = {"Authorization": f"Bearer {DIRECTUS_TOKEN}"}
        r = requests.get(
            f"{DIRECTUS_URL}/items/tenants", 
            params={"filter[subdomain][_eq]": slug}, 
            headers=headers,
            verify=VERIFY_SSL, 
            timeout=5
        )
        data = r.json()
    except Exception as e:
        return f"<h1>Erro de Conexão com Banco de Dados</h1><p>Verifique o log do servidor.</p>", 500

    if not data.get('data'):
         return f"""
         <div style="font-family:sans-serif; text-align:center; padding:50px;">
            <h1>Evento não encontrado (404)</h1>
            <p>O link <strong>/festa/{slug}</strong> não existe.</p>
            <a href='/'>Criar Novo Evento</a>
         </div>
         """, 404

    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Busca dados
    products = fetch_collection_data("products", tenant_id)
    sections = fetch_collection_data("sections", tenant_id)
    guests = fetch_collection_data("vaquinha_guests", tenant_id, params={"sort": "-created_at"})
    
    settings_list = fetch_collection_data("vaquinha_settings", tenant_id)
    settings = settings_list[0] if settings_list else {}
    
    # URL para assets (imagens) - Pode ser diferente da API interna
    assets_url = os.environ.get("DIRECTUS_ASSETS_URL", DIRECTUS_URL)

    return render_template(
        "vaquinha.html", 
        tenant=tenant, 
        products=products, 
        sections=sections,
        guests_confirmed=[g for g in guests if g.get('status') == 'CONFIRMED'],
        guests_all=guests, 
        vaquinha_settings=settings,
        directus_external_url=assets_url,
        current_slug=slug
    )

# 3. API - CRIAR NOVA FESTA
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    try:
        data = request.get_json()
        raw_slug = data.get('subdomain', '').lower()
        # Regex mais restritiva para evitar slugs inválidos
        slug = re.sub(r'[^a-z0-9-]', '', raw_slug)
        
        if not slug: return jsonify({"status": "error", "message": "Link inválido"}), 400

        headers = {"Authorization": f"Bearer {DIRECTUS_TOKEN}", "Content-Type": "application/json"}

        # Verifica duplicidade
        check = requests.get(
            f"{DIRECTUS_URL}/items/tenants", 
            params={"filter[subdomain][_eq]": slug}, 
            headers=headers, 
            verify=VERIFY_SSL
        )
        
        if check.status_code == 401:
             return jsonify({"status": "error", "message": "Erro de Permissão no Servidor (Token Inválido)."}), 500
             
        if check.json().get('data'):
             return jsonify({"status": "error", "message": "Este nome já existe."}), 400

        # Cria Tenant
        admin_token_suffix = re.sub(r'[^a-zA-Z0-9]', '', data.get('password', 'master'))
        full_admin_token = f"{slug.upper()}_{admin_token_suffix}"

        tenant_payload = {
            "company_name": data.get('company_name'),
            "subdomain": slug,
            "email": data.get('email'),
            "pix_key": data.get('pix_key'),
            "status": "active",
            "admin_token": full_admin_token,
            "template_name": "vaquinha"
        }
        
        resp = requests.post(f"{DIRECTUS_URL}/items/tenants", headers=headers, json=tenant_payload, verify=VERIFY_SSL)
        
        if resp.status_code != 200:
            return jsonify({"status": "error", "message": f"Erro DB: {resp.text}"}), 500

        new_id = resp.json()['data']['id']
        
        # Cria User
        requests.post(f"{DIRECTUS_URL}/items/users", headers=headers, json={
            "tenant_id": new_id, "email": data.get('email'), 
            "password_hash": data.get('password'), "role": "Loja Admin"
        }, verify=VERIFY_SSL)
        
        return jsonify({
            "status": "success",
            "url": f"{BASE_URL}/festa/{slug}",
            "admin_token": full_admin_token
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# 4. API - APROVAR GUEST
@app.route('/api/approve_guest', methods=['POST'])
def approve_guest():
    try:
        data = request.get_json()
        headers = {"Authorization": f"Bearer {DIRECTUS_TOKEN}"}
        requests.patch(
            f"{DIRECTUS_URL}/items/vaquinha_guests/{data.get('guest_id')}", 
            json={"status": "CONFIRMED"}, 
            headers=headers,
            verify=VERIFY_SSL
        )
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

# 5. API - CONFIRMAR PRESENÇA
@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    guest_name = request.form.get('name')
    guest_email = request.form.get('email')
    slug_origem = request.form.get('origin_slug')
    proof_file = request.files.get('proof')
    
    if not slug_origem: return "Erro: Link inválido", 400

    try:
        headers = {"Authorization": f"Bearer {DIRECTUS_TOKEN}"}
        t_resp = requests.get(
            f"{DIRECTUS_URL}/items/tenants", 
            params={"filter[subdomain][_eq]": slug_origem}, 
            headers=headers,
            verify=VERIFY_SSL
        )
        tenant_id = t_resp.json()['data'][0]['id']
    except: return "Evento não encontrado", 404

    file_id = None
    if proof_file:
        try:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            # O upload de arquivo requer headers limpos ou específicos, requests lida com multipart se não passarmos content-type manual
            # Mas precisamos do Auth
            auth_header = {"Authorization": f"Bearer {DIRECTUS_TOKEN}"} 
            up = requests.post(f"{DIRECTUS_URL}/files", files=files, headers=auth_header, verify=VERIFY_SSL)
            if up.status_code in [200, 201]: file_id = up.json()['data']['id']
        except Exception as e: 
            print(f"Erro upload: {e}")

    # Salva convidado
    requests.post(f"{DIRECTUS_URL}/items/vaquinha_guests", json={
        "tenant_id": tenant_id, "name": guest_name, "email": guest_email, 
        "payment_proof_url": file_id, "status": "PENDING"
    }, headers=headers, verify=VERIFY_SSL)
    
    return f"""<body style="font-family:sans-serif; text-align:center; padding:50px;"><h1 style="color:green">Sucesso!</h1><a href="/festa/{slug_origem}">Voltar</a></body>"""

if __name__ == '__main__':
    # Em produção no Dokploy, o Gunicorn que vai rodar isso, mas deixamos aqui para teste local
    app.run(host='0.0.0.0', port=5000)