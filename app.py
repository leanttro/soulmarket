import os
import requests
from flask import Flask, render_template, request, jsonify 
import urllib3
from urllib.parse import urljoin 
import json 
import re 

# Desabilita alertas de SSL (importante para comunicação interna)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# 1. CONFIGURAÇÃO DE CONEXÃO COM DIRECTUS
# =========================================================================
# Tenta conectar tanto externamente quanto internamente
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 

DIRECTUS_URLS_TO_TRY = [
    DIRECTUS_URL_EXTERNAL,
    DIRECTUS_URL_INTERNAL
]

# Variável global para cache da URL que funcionou
GLOBAL_SUCCESSFUL_URL = None

# =========================================================================
# 2. AUTOMAÇÃO DOKPLOY (CRIA O DOMÍNIO AUTOMATICAMENTE)
# =========================================================================
def create_dokploy_domain(subdomain):
    """
    Chama a API do Dokploy para registrar o domínio no Traefik (Proxy).
    Isso faz o erro '404 page not found' do Traefik sumir.
    """
    DOKPLOY_URL = os.getenv("DOKPLOY_URL") 
    DOKPLOY_TOKEN = os.getenv("DOKPLOY_TOKEN")
    APP_ID = os.getenv("DOKPLOY_APP_ID") # O ID wqngj...
    
    # Verifica se as variáveis existem
    if not all([DOKPLOY_URL, DOKPLOY_TOKEN, APP_ID]):
        print("⚠️ AVISO: Variáveis do Dokploy não configuradas. Domínio não será criado.")
        return False

    # Limpa a URL
    if DOKPLOY_URL.endswith('/'):
        DOKPLOY_URL = DOKPLOY_URL[:-1]

    full_domain = f"{subdomain}.leanttro.com"
    print(f"🔄 [DOKPLOY] Tentando criar rota para: {full_domain} no App ID: {APP_ID}")

    headers = {
        "Authorization": f"Bearer {DOKPLOY_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Payload exato para o Dokploy
    payload = {
        "applicationId": APP_ID,
        "host": full_domain,
        "path": "/",
        "port": 5000,           # Deve bater com o seu comando gunicorn :5000
        "https": True,          # Força SSL
        "certificateType": "letsencrypt"
    }
    
    try:
        response = requests.post(
            f"{DOKPLOY_URL}/api/domain.create", 
            json=payload, 
            headers=headers, 
            timeout=20
        )
        
        if response.status_code in [200, 201]:
            print(f"✅ [DOKPLOY] SUCESSO! Rota criada para {full_domain}")
            return True
        else:
            print(f"⚠️ [DOKPLOY] ERRO API ({response.status_code}): {response.text}")
            return False
            
    except Exception as e:
        print(f"⛔ [DOKPLOY] FALHA DE CONEXÃO: {str(e)}")
        return False

# =========================================================================
# 3. FUNÇÕES AUXILIARES
# =========================================================================

def clean_url(url):
    if url and url.endswith('/'):
        return url[:-1]
    return url

def fetch_collection_data(url_base, collection_name, tenant_id, params=None):
    if params is None:
        params = {}
    params["filter[tenant_id][_eq]"] = tenant_id

    try:
        response = requests.get(
            f"{url_base}/items/{collection_name}",
            params=params,
            verify=False,
            timeout=5
        )
        if response.status_code == 200:
            return response.json().get('data', [])
        else:
            return []
    except:
        return []

# =========================================================================
# 4. ROTAS DO FLASK
# =========================================================================

@app.route('/')
def home():
    global GLOBAL_SUCCESSFUL_URL
    
    # Lógica de Subdomínio
    host = request.headers.get('Host', '')
    if 'localhost' in host or '127.0.0.1' in host:
        subdomain = 'teste'
    else:
        # Pega a primeira parte do domínio (ex: bia.leanttro.com -> bia)
        subdomain = host.split('.')[0]

    # Se acessar direto a raiz ou 'confras', mostra a página de venda
    if subdomain == 'leanttro' or subdomain == 'www' or subdomain == 'confras':
         return render_template("confras.html")

    successful_url = None
    response = None

    # Tenta conectar no Directus
    for current_url in DIRECTUS_URLS_TO_TRY:
        current_url = clean_url(current_url)
        try:
            # Busca o Tenant
            url_tenants = f"{current_url}/items/tenants"
            params = {"filter[subdomain][_eq]": subdomain}
            response = requests.get(url_tenants, params=params, verify=False, timeout=5)
            
            if response is not None and response.status_code is not None:
                successful_url = current_url
                GLOBAL_SUCCESSFUL_URL = current_url
                break
        except:
            continue
            
    if not successful_url:
        return "<h1>Erro: Banco de Dados Indisponível</h1><p>Não foi possível conectar ao Directus.</p>", 500

    data = response.json()
    
    # SE NÃO ACHOU O TENANT NO BANCO
    if not data.get('data'):
         return f"""
         <div style="text-align:center; padding: 50px; font-family: sans-serif;">
            <h1>Evento não encontrado (404)</h1>
            <p>O endereço <strong>{subdomain}.leanttro.com</strong> não está registrado em nossa base.</p>
            <p><a href="https://confras.leanttro.com">Criar meu evento agora</a></p>
         </div>
         """, 404

    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Carrega dados
    products = fetch_collection_data(successful_url, "products", tenant_id)
    sections = fetch_collection_data(successful_url, "sections", tenant_id)
    guests_all = fetch_collection_data(successful_url, "vaquinha_guests", tenant_id, params={"sort": "-created_at"})
    guests_confirmed = [g for g in guests_all if g.get('status') == 'CONFIRMED']
    
    vaquinha_settings_list = fetch_collection_data(successful_url, "vaquinha_settings", tenant_id)
    vaquinha_settings = vaquinha_settings_list[0] if vaquinha_settings_list else {}
    
    template_base_name = tenant.get('template_name') or 'home'
    
    return render_template(
        f"{template_base_name}.html",
        tenant=tenant,
        products=products,
        sections=sections,
        guests_confirmed=guests_confirmed,
        guests_all=guests_all,
        vaquinha_settings=vaquinha_settings,
        directus_external_url=DIRECTUS_URL_EXTERNAL
    )

# --- ROTA DE CRIAÇÃO (ONDE A MÁGICA ACONTECE) ---
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    ADMIN_TOKEN = os.getenv("DIRECTUS_ADMIN_TOKEN")
    
    if not ADMIN_TOKEN:
        return jsonify({"status": "error", "message": "Token ADMIN não configurado no servidor."}), 500

    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    try:
        data = request.get_json()
        
        # Limpa o subdomínio
        if not data.get('subdomain'):
             return jsonify({"status": "error", "message": "Subdomínio inválido."}), 400
        
        subdomain_clean = data['subdomain'].lower()
        subdomain_clean = re.sub(r'[^a-z0-9-]', '', subdomain_clean)
        admin_token = subdomain_clean.upper() + "_TOKEN_MASTER"

        tenant_data = {
            "company_name": data.get('company_name'),
            "subdomain": subdomain_clean,
            "email": data.get('email'),
            "pix_key": data.get('pix_key'),
            "pix_owner_name": data.get('company_name'),
            "guest_limit": 20,
            "plan_type": "free",
            "template_name": "vaquinha",
            "status": "active",
            "primary_color": "#22C55E",
            "admin_token": admin_token
        }
        
        headers = {
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "Content-Type": "application/json"
        }

        # 1. Salva no Directus
        tenant_create_resp = requests.post(
            f"{directus_api_url}/items/tenants",
            headers=headers,
            json=tenant_data,
            verify=False
        )
        
        if tenant_create_resp.status_code != 200:
            error_msg = "Este nome de site já está em uso."
            try:
                if 'unique' not in tenant_create_resp.text:
                    error_msg = tenant_create_resp.json()['errors'][0]['message']
            except:
                pass
            return jsonify({"status": "error", "message": error_msg}), 400

        new_tenant_id = tenant_create_resp.json()['data']['id']
        
        # 2. Cria User Admin
        user_data = {
            "tenant_id": new_tenant_id,
            "email": data.get('email'),
            "password_hash": data.get('password'),
            "role": "Loja Admin",
            "name": data.get('company_name')
        }
        requests.post(f"{directus_api_url}/items/users", headers=headers, json=user_data, verify=False)
        
        # 3. DOKPLOY AUTOMATION (CRIA O DOMÍNIO)
        # Se isso falhar, o site não abre (dá erro 404 no navegador)
        try:
            create_dokploy_domain(subdomain_clean)
        except Exception as e:
            print(f"Erro ao chamar Dokploy: {e}")

        # 4. Retorno JSON (Com Admin Token!)
        return jsonify({
            "status": "success",
            "message": "Criado com sucesso!",
            "url": f"https://{subdomain_clean}.leanttro.com",
            "subdomain": subdomain_clean,
            "admin_token": admin_token # Essencial para o link do painel funcionar
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro Interno: {str(e)}"}), 500

@app.route('/api/approve_guest', methods=['POST'])
def approve_guest():
    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    try:
        data = request.get_json()
        guest_id = data.get('guest_id')
        requests.patch(
            f"{directus_api_url}/items/vaquinha_guests/{guest_id}",
            json={"status": "CONFIRMED"},
            verify=False
        )
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    guest_name = request.form.get('name')
    guest_email = request.form.get('email')
    proof_file = request.files.get('proof')
    
    host = request.headers.get('Host', '')
    subdomain = host.split('.')[0]
    
    tenant_id = None
    try:
        t_resp = requests.get(
            f"{directus_api_url}/items/tenants",
            params={"filter[subdomain][_eq]": subdomain},
            verify=False
        )
        if t_resp.status_code == 200 and t_resp.json()['data']:
            tenant_id = t_resp.json()['data'][0]['id']
    except:
        pass

    if not tenant_id:
        return jsonify({"status": "error", "message": "Evento não encontrado."}), 404

    file_id = None
    if proof_file:
        try:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            up_resp = requests.post(f"{directus_api_url}/files", files=files, verify=False)
            if up_resp.status_code in [200, 201]:
                file_id = up_resp.json()['data']['id']
        except:
            pass

    try:
        requests.post(
            f"{directus_api_url}/items/vaquinha_guests",
            json={
                "tenant_id": tenant_id,
                "name": guest_name,
                "email": guest_email,
                "payment_proof_url": file_id,
                "status": "PENDING"
            },
            verify=False
        )
        
        return f"""
        <body style="font-family:sans-serif; text-align:center; padding:50px; background:#f0fdf4;">
            <div style="background:white; padding:40px; border-radius:10px; max-width:500px; margin:auto; box-shadow:0 10px 25px rgba(0,0,0,0.1);">
                <h1 style="color:#16a34a;">✅ Enviado!</h1>
                <p>Obrigado <b>{guest_name}</b>. Seu comprovante está em análise.</p>
                <a href="/" style="display:inline-block; margin-top:20px; text-decoration:none; color:#16a34a; font-weight:bold;">&larr; Voltar</a>
            </div>
        </body>
        """
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)