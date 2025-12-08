import os
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
import urllib3
import re 

# Desabilita alertas de SSL (importante para comunicação interna)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# CONFIGURAÇÕES
# =========================================================================
BASE_URL = "https://confras.leanttro.com"

# --- CONFIGURAÇÃO BLINDADA DO DIRECTUS ---
# Coloquei as credenciais fixas aqui para não depender de variáveis de ambiente
DIRECTUS_TOKEN_FIXED = "jr6zCuYS16YuM6AGFpXI9aEd5IIIZdrn" 
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055"
DIRECTUS_URL_EXTERNAL = "https://directus.leanttro.com"

# =========================================================================
# FUNÇÕES AUXILIARES
# =========================================================================
def get_directus_url():
    """Tenta usar a rede interna primeiro (mais rápido), senão vai pela externa"""
    try:
        requests.get(f"{DIRECTUS_URL_INTERNAL}/server/ping", timeout=1)
        return DIRECTUS_URL_INTERNAL
    except:
        return DIRECTUS_URL_EXTERNAL

def fetch_collection_data(url_base, collection_name, tenant_id, params=None):
    if params is None: params = {}
    params["filter[tenant_id][_eq]"] = tenant_id
    try:
        # Tenta buscar dados. Se falhar, retorna lista vazia para não quebrar o site
        r = requests.get(f"{url_base}/items/{collection_name}", params=params, verify=False, timeout=5)
        return r.json().get('data', []) if r.status_code == 200 else []
    except: 
        return []

# =========================================================================
# ROTAS
# =========================================================================

# 1. ROTA PRINCIPAL (CRIAÇÃO)
@app.route('/')
def home():
    return render_template("confras.html")

# 2. ROTA DA FESTA (VISUALIZAÇÃO)
@app.route('/festa/<slug>')
def festa_view(slug):
    api_url = get_directus_url()
    
    try:
        # Busca o tenant/festa
        r = requests.get(
            f"{api_url}/items/tenants", 
            params={"filter[subdomain][_eq]": slug}, 
            verify=False, timeout=5
        )
        data = r.json()
    except Exception as e:
        return f"<h1>Erro de Conexão</h1><p>Não foi possível conectar ao banco de dados: {str(e)}</p>", 500

    if not data.get('data'):
         return f"<h1>Evento não encontrado</h1><p>O link <strong>{slug}</strong> não existe.</p><a href='/'>Criar Novo</a>", 404

    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Busca dados relacionados
    products = fetch_collection_data(api_url, "products", tenant_id)
    sections = fetch_collection_data(api_url, "sections", tenant_id)
    guests = fetch_collection_data(api_url, "vaquinha_guests", tenant_id, params={"sort": "-created_at"})
    
    settings_list = fetch_collection_data(api_url, "vaquinha_settings", tenant_id)
    settings = settings_list[0] if settings_list else {}
    
    return render_template(
        "vaquinha.html", # Forçando o template vaquinha.html que te mandei antes
        tenant=tenant, 
        products=products, 
        sections=sections,
        guests_confirmed=[g for g in guests if g.get('status') == 'CONFIRMED'],
        guests_all=guests, 
        vaquinha_settings=settings,
        directus_external_url=DIRECTUS_URL_EXTERNAL,
        current_slug=slug
    )

# 3. API - CRIAR NOVA FESTA
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    # Usa o token FIXO que pegamos do seu print
    ADMIN_TOKEN = DIRECTUS_TOKEN_FIXED
    api_url = get_directus_url()
    
    try:
        data = request.get_json()
        raw_slug = data.get('subdomain', '').lower()
        slug = re.sub(r'[^a-z0-9-]', '', raw_slug) # Limpa caracteres especiais
        
        if not slug: return jsonify({"status": "error", "message": "Link inválido. Use apenas letras e números."}), 400

        print(f"DEBUG: Criando festa '{slug}'...")

        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}

        # 1. Verifica se já existe
        check = requests.get(f"{api_url}/items/tenants", params={"filter[subdomain][_eq]": slug}, headers=headers, verify=False)
        
        if check.status_code == 401:
             return jsonify({"status": "error", "message": "Erro de Permissão (Token Inválido). Verifique o app.py"}), 500
        
        if check.json().get('data'):
             return jsonify({"status": "error", "message": "Este link já está em uso. Tente outro nome."}), 400

        # 2. Cria Tenant no Banco
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
        
        resp = requests.post(f"{api_url}/items/tenants", headers=headers, json=tenant_payload, verify=False)
        
        if resp.status_code != 200:
            return jsonify({"status": "error", "message": f"Erro do Banco: {resp.text}"}), 500

        new_id = resp.json()['data']['id']
        
        # 3. Cria Usuário Admin da Loja
        requests.post(f"{api_url}/items/users", headers=headers, json={
            "tenant_id": new_id, "email": data.get('email'), 
            "password_hash": data.get('password'), "role": "Loja Admin"
        }, verify=False)
        
        return jsonify({
            "status": "success",
            "url": f"{BASE_URL}/festa/{slug}",
            "admin_token": full_admin_token
        }), 200

    except Exception as e:
        print(f"ERRO FATAL: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# 4. API - APROVAR CONVIDADO
@app.route('/api/approve_guest', methods=['POST'])
def approve_guest():
    api_url = get_directus_url()
    try:
        data = request.get_json()
        requests.patch(
            f"{api_url}/items/vaquinha_guests/{data.get('guest_id')}",
            json={"status": "CONFIRMED"},
            verify=False
        )
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# 5. API - CONFIRMAR PAGAMENTO (UPLOAD)
@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    api_url = get_directus_url()
    
    guest_name = request.form.get('name')
    guest_email = request.form.get('email')
    slug_origem = request.form.get('origin_slug')
    proof_file = request.files.get('proof')
    
    if not slug_origem: return "Erro: Origem desconhecida", 400

    # Busca ID do Tenant
    try:
        t_resp = requests.get(f"{api_url}/items/tenants", params={"filter[subdomain][_eq]": slug_origem}, verify=False)
        tenant_id = t_resp.json()['data'][0]['id']
    except:
        return "Erro ao identificar evento", 500

    # Upload
    file_id = None
    if proof_file:
        try:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            up_resp = requests.post(f"{api_url}/files", files=files, verify=False)
            if up_resp.status_code in [200, 201]:
                file_id = up_resp.json()['data']['id']
        except: pass

    # Salva convidado
    requests.post(
        f"{api_url}/items/vaquinha_guests",
        json={
            "tenant_id": tenant_id, "name": guest_name, 
            "email": guest_email, "payment_proof_url": file_id, "status": "PENDING"
        },
        verify=False
    )
    
    return f"""
    <body style="font-family:sans-serif; text-align:center; padding:50px;">
        <h1 style="color:green;">✅ Enviado!</h1>
        <p>Aguarde a aprovação do organizador.</p>
        <a href="/festa/{slug_origem}">Voltar</a>
    </body>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)