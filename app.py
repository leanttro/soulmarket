import os
import requests
from flask import Flask, render_template, request, jsonify 
import urllib3
import re 

# Desabilita alertas de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- AQUI ESTÁ A CORREÇÃO DO ERRO ATUAL ---
# O Gunicorn precisa encontrar esta variável 'app' para iniciar.
app = Flask(__name__) 

# =========================================================================
# CONFIGURAÇÕES (HARDCODED PARA ELIMINAR O ERRO 401)
# =========================================================================
DIRECTUS_URL_EXTERNAL = "https://directus.leanttro.com"
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 
DIRECTUS_URLS_TO_TRY = [DIRECTUS_URL_EXTERNAL, DIRECTUS_URL_INTERNAL]
GLOBAL_SUCCESSFUL_URL = None

# Credenciais travadas no código para o servidor não pegar a velha
DOKPLOY_URL_FIXED = "http://213.199.56.207:3000"
DOKPLOY_TOKEN_FIXED = "hDeLWmSnMyLtTDQlthigbwqWCFMhvIkjzqNPYIdoXUzmPFRQsjsqMOBhFRYixrvk"
APP_ID_FIXED = "GYJuZwAcZAMb8s9v-S-"

# =========================================================================
# AUTOMAÇÃO DOKPLOY
# =========================================================================
def create_dokploy_domain(subdomain):
    print(f"DEBUG: Iniciando criação de {subdomain} com credenciais fixas...")
    
    headers = {
        "Authorization": f"Bearer {DOKPLOY_TOKEN_FIXED}",
        "Content-Type": "application/json"
    }
    
    full_domain = f"{subdomain}.leanttro.com"
    payload = {
        "applicationId": APP_ID_FIXED,
        "host": full_domain,
        "path": "/",
        "port": 5000,
        "https": True,
        "certificateType": "letsencrypt"
    }
    
    try:
        response = requests.post(
            f"{DOKPLOY_URL_FIXED}/api/domain.create", 
            json=payload, 
            headers=headers, 
            timeout=20
        )
        
        if response.status_code in [200, 201]:
            print(f"✅ [SUCESSO] Domínio {full_domain} criado!")
            return True
        elif response.status_code == 409:
             print(f"⚠️ [AVISO] Domínio já existe no painel.")
             return True
        else:
            print(f"❌ [ERRO] Dokploy recusou: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"⛔ [ERRO FATAL] Conexão falhou: {str(e)}")
        return False

# =========================================================================
# FUNÇÕES AUXILIARES
# =========================================================================
def clean_url(url):
    if url and url.endswith('/'): return url[:-1]
    return url

def fetch_collection_data(url_base, collection_name, tenant_id, params=None):
    if params is None: params = {}
    params["filter[tenant_id][_eq]"] = tenant_id
    try:
        r = requests.get(f"{url_base}/items/{collection_name}", params=params, verify=False, timeout=5)
        return r.json().get('data', []) if r.status_code == 200 else []
    except: return []

# =========================================================================
# ROTAS
# =========================================================================
@app.route('/')
def home():
    global GLOBAL_SUCCESSFUL_URL
    host = request.headers.get('Host', '')
    if 'localhost' in host: subdomain = 'teste'
    else: subdomain = host.split('.')[0]

    if subdomain in ['leanttro', 'www', 'confras']:
         return render_template("confras.html")

    successful_url = None
    response = None

    for url in DIRECTUS_URLS_TO_TRY:
        url = clean_url(url)
        try:
            r = requests.get(f"{url}/items/tenants", params={"filter[subdomain][_eq]": subdomain}, verify=False, timeout=5)
            if r.status_code == 200:
                successful_url = url
                GLOBAL_SUCCESSFUL_URL = url
                response = r
                break
        except: continue
            
    if not successful_url: return "<h1>Erro: Banco de Dados Indisponível</h1>", 500

    data = response.json()
    if not data.get('data'):
         return f"<h1>Evento não encontrado (404)</h1>", 404

    tenant = data['data'][0]
    
    products = fetch_collection_data(successful_url, "products", tenant['id'])
    sections = fetch_collection_data(successful_url, "sections", tenant['id'])
    guests = fetch_collection_data(successful_url, "vaquinha_guests", tenant['id'], params={"sort": "-created_at"})
    
    settings_list = fetch_collection_data(successful_url, "vaquinha_settings", tenant['id'])
    settings = settings_list[0] if settings_list else {}
    
    return render_template(
        f"{tenant.get('template_name', 'home')}.html",
        tenant=tenant, products=products, sections=sections,
        guests_confirmed=[g for g in guests if g.get('status') == 'CONFIRMED'],
        guests_all=guests, vaquinha_settings=settings,
        directus_external_url=DIRECTUS_URL_EXTERNAL
    )

@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    # Usa a variável de ambiente para o Admin Token do Directus
    ADMIN_TOKEN = os.getenv("DIRECTUS_ADMIN_TOKEN")
    if not ADMIN_TOKEN: return jsonify({"status": "error", "message": "Token ADMIN Server Error"}), 500

    api_url = GLOBAL_SUCCESSFUL_URL or clean_url(DIRECTUS_URL_EXTERNAL)
    
    try:
        data = request.get_json()
        sub = re.sub(r'[^a-z0-9-]', '', data.get('subdomain', '').lower())
        
        if not sub: return jsonify({"status": "error", "message": "Subdomínio inválido"}), 400

        # 1. Cria no Directus
        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}
        tenant_resp = requests.post(f"{api_url}/items/tenants", headers=headers, json={
            "company_name": data.get('company_name'),
            "subdomain": sub,
            "email": data.get('email'),
            "pix_key": data.get('pix_key'),
            "status": "active",
            "admin_token": f"{sub.upper()}_TOKEN_MASTER"
        }, verify=False)
        
        if tenant_resp.status_code != 200:
            return jsonify({"status": "error", "message": "Nome indisponível."}), 400

        new_id = tenant_resp.json()['data']['id']
        
        # 2. Cria usuário
        requests.post(f"{api_url}/items/users", headers=headers, json={
            "tenant_id": new_id, "email": data.get('email'), 
            "password_hash": data.get('password'), "role": "Loja Admin"
        }, verify=False)
        
        # 3. CRIA DOMÍNIO NO DOKPLOY (HARDCODED)
        try:
            create_dokploy_domain(sub)
        except Exception as e:
            print(f"Erro Dokploy: {e}")

        return jsonify({
            "status": "success",
            "url": f"https://{sub}.leanttro.com",
            "admin_token": f"{sub.upper()}_TOKEN_MASTER"
        }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)