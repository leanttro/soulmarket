import os
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
import urllib3
import re 

# Desabilita alertas de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# CONFIGURAÇÕES BLINDADAS
# =========================================================================
BASE_URL = "https://confras.leanttro.com"

# --- TOKEN NOVO (Copiado do seu print image_66539c.png) ---
DIRECTUS_TOKEN_FIXED = "HcBe-VBoIm31kD-gxpOsv-mgWjTX8UfD"

# URLs do Directus
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

# 2. ROTA DA FESTA
@app.route('/festa/<slug>')
def festa_view(slug):
    api_url = get_directus_url()
    
    try:
        # Tenta buscar usando o token fixo para garantir permissão
        headers = {"Authorization": f"Bearer {DIRECTUS_TOKEN_FIXED}"}
        r = requests.get(
            f"{api_url}/items/tenants", 
            params={"filter[subdomain][_eq]": slug}, 
            headers=headers,
            verify=False, timeout=5
        )
        data = r.json()
    except Exception as e:
        return f"<h1>Erro de Conexão</h1><p>{str(e)}</p>", 500

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
    products = fetch_collection_data(api_url, "products", tenant_id)
    sections = fetch_collection_data(api_url, "sections", tenant_id)
    guests = fetch_collection_data(api_url, "vaquinha_guests", tenant_id, params={"sort": "-created_at"})
    
    settings_list = fetch_collection_data(api_url, "vaquinha_settings", tenant_id)
    settings = settings_list[0] if settings_list else {}
    
    return render_template(
        "vaquinha.html", 
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
    ADMIN_TOKEN = DIRECTUS_TOKEN_FIXED
    api_url = get_directus_url()
    
    try:
        data = request.get_json()
        raw_slug = data.get('subdomain', '').lower()
        slug = re.sub(r'[^a-z0-9-]', '', raw_slug)
        
        if not slug: return jsonify({"status": "error", "message": "Link inválido"}), 400

        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}

        # Verifica duplicidade
        check = requests.get(f"{api_url}/items/tenants", params={"filter[subdomain][_eq]": slug}, headers=headers, verify=False)
        
        if check.status_code == 401:
             return jsonify({"status": "error", "message": "ERRO FATAL: Token do Directus Inválido."}), 500
             
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
        
        resp = requests.post(f"{api_url}/items/tenants", headers=headers, json=tenant_payload, verify=False)
        if resp.status_code != 200:
            return jsonify({"status": "error", "message": f"Erro DB: {resp.text}"}), 500

        new_id = resp.json()['data']['id']
        
        # Cria User
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
        return jsonify({"status": "error", "message": str(e)}), 500

# 4. API - APROVAR e 5. CONFIRMAR (Mantidos iguais e funcionais)
@app.route('/api/approve_guest', methods=['POST'])
def approve_guest():
    api_url = get_directus_url()
    try:
        data = request.get_json()
        requests.patch(f"{api_url}/items/vaquinha_guests/{data.get('guest_id')}", json={"status": "CONFIRMED"}, verify=False)
        return jsonify({"status": "success"}), 200
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    api_url = get_directus_url()
    guest_name = request.form.get('name')
    guest_email = request.form.get('email')
    slug_origem = request.form.get('origin_slug')
    proof_file = request.files.get('proof')
    
    if not slug_origem: return "Erro: Link inválido", 400

    try:
        t_resp = requests.get(f"{api_url}/items/tenants", params={"filter[subdomain][_eq]": slug_origem}, verify=False)
        tenant_id = t_resp.json()['data'][0]['id']
    except: return "Evento não encontrado", 404

    file_id = None
    if proof_file:
        try:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            up = requests.post(f"{api_url}/files", files=files, verify=False)
            if up.status_code in [200, 201]: file_id = up.json()['data']['id']
        except: pass

    requests.post(f"{api_url}/items/vaquinha_guests", json={
        "tenant_id": tenant_id, "name": guest_name, "email": guest_email, 
        "payment_proof_url": file_id, "status": "PENDING"
    }, verify=False)
    
    return f"""<body style="font-family:sans-serif; text-align:center; padding:50px;"><h1 style="color:green">Sucesso!</h1><a href="/festa/{slug_origem}">Voltar</a></body>"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)