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
# CONFIGURAÇÕES GLOBAIS
# =========================================================================
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
# Fallback interno se necessário
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 

DIRECTUS_URLS_TO_TRY = [DIRECTUS_URL_EXTERNAL, DIRECTUS_URL_INTERNAL]
GLOBAL_SUCCESSFUL_URL = None

# =========================================================================
# FUNÇÃO DE AUTOMAÇÃO DOKPLOY (COM DEBUG)
# =========================================================================
def create_dokploy_domain(subdomain):
    DOKPLOY_URL = os.getenv("DOKPLOY_URL") 
    DOKPLOY_TOKEN = os.getenv("DOKPLOY_TOKEN")
    APP_ID = os.getenv("DOKPLOY_APP_ID") 
    
    # --- DEBUG: O X-9 QUE VAI TE DIZER A VERDADE ---
    print(f"\n--- INÍCIO DEBUG DOKPLOY ---")
    print(f"🕵️ [DEBUG] Token (Início): {DOKPLOY_TOKEN[:5] if DOKPLOY_TOKEN else 'NULO'}...")
    print(f"🕵️ [DEBUG] App ID carregado: {APP_ID}")
    print(f"🕵️ [DEBUG] URL Alvo: {DOKPLOY_URL}")
    print(f"-----------------------------\n")

    if not all([DOKPLOY_URL, DOKPLOY_TOKEN, APP_ID]):
        print("⛔ AVISO: Variáveis de ambiente faltando!")
        return False

    if DOKPLOY_URL.endswith('/'):
        DOKPLOY_URL = DOKPLOY_URL[:-1]

    full_domain = f"{subdomain}.leanttro.com"
    print(f"🔄 [DOKPLOY] Enviando requisição para criar: {full_domain}")

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
        # Tenta criar o domínio
        response = requests.post(
            f"{DOKPLOY_URL}/api/domain.create", 
            json=payload, 
            headers=headers, 
            timeout=15
        )
        
        if response.status_code in [200, 201]:
            print(f"✅ [DOKPLOY] SUCESSO! Domínio criado.")
            return True
        elif response.status_code == 401:
            print(f"❌ [DOKPLOY] ERRO 401 (Não Autorizado). O Token ou App ID estão errados para essa URL.")
            return False
        else:
            print(f"⚠️ [DOKPLOY] Erro API ({response.status_code}): {response.text}")
            return False
            
    except Exception as e:
        print(f"⛔ [DOKPLOY] Falha de Conexão: {str(e)}")
        return False

# =========================================================================
# FUNÇÕES AUXILIARES
# =========================================================================
def clean_url(url):
    return url[:-1] if url and url.endswith('/') else url

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

    # Tenta achar o tenant no Directus
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
            
    if not successful_url:
        return "<h1>Erro: Banco de Dados Indisponível</h1>", 500

    data = response.json()
    if not data.get('data'):
         return f"<h1>Evento não encontrado</h1><p>{subdomain}.leanttro.com não existe.</p>", 404

    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    products = fetch_collection_data(successful_url, "products", tenant_id)
    sections = fetch_collection_data(successful_url, "sections", tenant_id)
    guests = fetch_collection_data(successful_url, "vaquinha_guests", tenant_id, params={"sort": "-created_at"})
    guests_confirmed = [g for g in guests if g.get('status') == 'CONFIRMED']
    
    settings_list = fetch_collection_data(successful_url, "vaquinha_settings", tenant_id)
    settings = settings_list[0] if settings_list else {}
    
    return render_template(
        f"{tenant.get('template_name', 'home')}.html",
        tenant=tenant, products=products, sections=sections,
        guests_confirmed=guests_confirmed, guests_all=guests,
        vaquinha_settings=settings, directus_external_url=DIRECTUS_URL_EXTERNAL
    )

# --- ROTA DE CRIAÇÃO ---
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    ADMIN_TOKEN = os.getenv("DIRECTUS_ADMIN_TOKEN")
    if not ADMIN_TOKEN: return jsonify({"error": "Token ADMIN ausente"}), 500

    api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", DIRECTUS_URL_EXTERNAL))
    
    try:
        data = request.get_json()
        sub = re.sub(r'[^a-z0-9-]', '', data.get('subdomain', '').lower())
        if not sub: return jsonify({"message": "Subdomínio inválido"}), 400

        # 1. Salva no Directus
        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}
        tenant_payload = {
            "company_name": data.get('company_name'),
            "subdomain": sub,
            "email": data.get('email'),
            "pix_key": data.get('pix_key'),
            "status": "active",
            "admin_token": f"{sub.upper()}_MASTER"
        }
        
        resp = requests.post(f"{api_url}/items/tenants", headers=headers, json=tenant_payload, verify=False)
        if resp.status_code != 200:
            return jsonify({"message": "Nome indisponível ou erro no banco"}), 400

        new_id = resp.json()['data']['id']
        
        # 2. Cria Usuário
        requests.post(f"{api_url}/items/users", headers=headers, json={
            "tenant_id": new_id, "email": data.get('email'), 
            "password_hash": data.get('password'), "role": "Loja Admin"
        }, verify=False)
        
        # 3. Tenta criar Domínio no Dokploy (Async)
        try:
            create_dokploy_domain(sub)
        except Exception as e:
            print(f"Erro Dokploy: {e}")

        return jsonify({
            "status": "success", 
            "url": f"https://{sub}.leanttro.com",
            "admin_token": tenant_payload["admin_token"]
        })

    except Exception as e:
        return jsonify({"message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)