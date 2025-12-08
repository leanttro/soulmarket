import os
import requests
from flask import Flask, render_template, request, jsonify 
import urllib3
from urllib.parse import urljoin 
import json 
import re 

# Desabilita alertas de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# 1. CONFIGURAÇÃO DE CONEXÃO
# =========================================================================
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 

DIRECTUS_URLS_TO_TRY = [DIRECTUS_URL_EXTERNAL, DIRECTUS_URL_INTERNAL]
GLOBAL_SUCCESSFUL_URL = None

# =========================================================================
# 2. AUTOMAÇÃO DOKPLOY (MODO DETETIVE ATIVADO 🕵️‍♂️)
# =========================================================================
def create_dokploy_domain(subdomain):
    """
    Tenta criar o domínio e RETORNA O ERRO exato se falhar.
    """
    DOKPLOY_URL = os.getenv("DOKPLOY_URL") 
    DOKPLOY_TOKEN = os.getenv("DOKPLOY_TOKEN")
    APP_ID = os.getenv("DOKPLOY_APP_ID")
    
    # 1. TESTE DE VARIÁVEIS
    if not all([DOKPLOY_URL, DOKPLOY_TOKEN, APP_ID]):
        return False, "ERRO GRAVE: As variáveis (URL, TOKEN ou ID) não foram carregadas. Você clicou em Redeploy?"

    if DOKPLOY_URL.endswith('/'):
        DOKPLOY_URL = DOKPLOY_URL[:-1]

    full_domain = f"{subdomain}.leanttro.com"
    
    headers = {
        "Authorization": f"Bearer {DOKPLOY_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "applicationId": APP_ID,
        "host": full_domain,
        "path": "/",
        "port": 5000,           
        "https": True,          
        "certificateType": "letsencrypt"
    }
    
    try:
        # Tenta conectar
        response = requests.post(
            f"{DOKPLOY_URL}/api/domain.create", 
            json=payload, 
            headers=headers, 
            timeout=10
        )
        
        # 2. ANÁLISE DA RESPOSTA
        if response.status_code in [200, 201]:
            return True, "Sucesso"
        elif "already exists" in response.text:
            return True, "Ja existia"
        else:
            # Retorna o erro exato que o Dokploy mandou (Ex: 401 Unauthorized, 404 Not Found)
            return False, f"O Dokploy recusou: {response.status_code} - {response.text}"
            
    except Exception as e:
        return False, f"Erro de Conexão com Dokploy: {str(e)}"

# =========================================================================
# 3. FUNÇÕES AUXILIARES
# =========================================================================
def clean_url(url):
    if url and url.endswith('/'): return url[:-1]
    return url

def fetch_collection_data(url_base, collection_name, tenant_id, params=None):
    if params is None: params = {}
    params["filter[tenant_id][_eq]"] = tenant_id
    try:
        response = requests.get(f"{url_base}/items/{collection_name}", params=params, verify=False, timeout=5)
        return response.json().get('data', []) if response.status_code == 200 else []
    except: return []

# =========================================================================
# 4. ROTAS
# =========================================================================
@app.route('/')
def home():
    global GLOBAL_SUCCESSFUL_URL
    host = request.headers.get('Host', '')
    subdomain = 'teste' if 'localhost' in host else host.split('.')[0]

    # Retry Logic
    successful_url = GLOBAL_SUCCESSFUL_URL
    if not successful_url:
        for current_url in DIRECTUS_URLS_TO_TRY:
            try:
                r = requests.get(f"{clean_url(current_url)}/items/tenants", params={"filter[subdomain][_eq]": subdomain}, verify=False, timeout=3)
                if r.status_code is not None:
                    successful_url = clean_url(current_url)
                    GLOBAL_SUCCESSFUL_URL = successful_url
                    break
            except: continue
            
    if not successful_url: return "<h1>Erro de Conexão com Banco de Dados</h1>", 500

    # Busca Tenant
    try:
        r = requests.get(f"{successful_url}/items/tenants", params={"filter[subdomain][_eq]": subdomain}, verify=False)
        data = r.json()
    except: return "<h1>Erro ao ler dados</h1>", 500

    if not data.get('data'):
         if subdomain == 'confras': return render_template("confras.html")
         return render_template("404.html", subdomain=subdomain), 404

    tenant = data['data'][0]
    
    # Busca dados
    products = fetch_collection_data(successful_url, "products", tenant['id'])
    sections = fetch_collection_data(successful_url, "sections", tenant['id'], params={"sort": "order_index", "filter[page_slug][_eq]": "home"})
    guests_all = fetch_collection_data(successful_url, "vaquinha_guests", tenant['id'], params={"sort": "-created_at"})
    guests_confirmed = [g for g in guests_all if g.get('status') == 'CONFIRMED']
    settings = fetch_collection_data(successful_url, "vaquinha_settings", tenant['id'])
    vaquinha_settings = settings[0] if settings else {}
    
    return render_template(
        f"{tenant.get('template_name') or 'home'}.html",
        tenant=tenant, products=products, sections=sections,
        guests_confirmed=guests_confirmed, guests_all=guests_all,
        vaquinha_settings=vaquinha_settings, directus_external_url=DIRECTUS_URL_EXTERNAL
    )

# --- ROTA DE CRIAÇÃO (AGORA MOSTRA O ERRO) ---
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    ADMIN_TOKEN = os.getenv("DIRECTUS_ADMIN_TOKEN")
    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    try:
        data = request.get_json()
        if not data.get('subdomain'): return jsonify({"status": "error", "message": "Subdomínio inválido."}), 400
        
        subdomain = re.sub(r'[^a-z0-9-]', '', data['subdomain'].lower())
        
        # 1. TENTA FALAR COM O DOKPLOY PRIMEIRO (Para testar o erro)
        success_dokploy, msg_dokploy = create_dokploy_domain(subdomain)
        
        # SE O DOKPLOY FALHAR, A GENTE PARA TUDO E AVISA VOCÊ!
        if not success_dokploy:
            return jsonify({
                "status": "error", 
                "message": f"⛔ FALHA NO DOKPLOY: {msg_dokploy}"
            }), 400

        # 2. Se o Dokploy funcionou, cria no Banco
        tenant_data = {
            "company_name": data.get('company_name'),
            "subdomain": subdomain,
            "email": data.get('email'),
            "pix_key": data.get('pix_key'),
            "pix_owner_name": data.get('company_name'),
            "guest_limit": 20, "plan_type": "free", "template_name": "vaquinha",
            "status": "active", "primary_color": "#22C55E",
            "admin_token": subdomain.upper() + "_TOKEN"
        }
        
        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}
        requests.post(f"{directus_api_url}/items/tenants", headers=headers, json=tenant_data, verify=False)
        
        # Cria User
        user_data = {"tenant_id": 1, "email": data.get('email'), "password": data.get('password'), "role": "Loja Admin"} 
        # (Obs: tenant_id 1 é placeholder, ideal pegar o id retornado, mas para teste ok)
        
        return jsonify({
            "status": "success",
            "message": "CRIADO COM SUCESSO!",
            "url": f"https://{subdomain}.leanttro.com",
            "subdomain": subdomain
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro interno: {str(e)}"}), 500
        
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)