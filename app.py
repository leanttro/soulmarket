import os
import requests
from flask import Flask, render_template, request, jsonify 
import urllib3
from urllib.parse import urljoin 
import json 
import re 

# Desabilita alertas de SSL (útil se o Directus interno não tiver SSL)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# 1. CONFIGURAÇÃO DE CONEXÃO COM DIRECTUS (ROBUSTA)
# =========================================================================
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
# Se o Docker interno tiver outro IP, ajuste aqui. Caso contrário, use o mesmo.
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 

DIRECTUS_URLS_TO_TRY = [
    DIRECTUS_URL_EXTERNAL,
    DIRECTUS_URL_INTERNAL
]

# Variável global para armazenar a URL que funcionou
GLOBAL_SUCCESSFUL_URL = None

# =========================================================================
# 2. AUTOMAÇÃO DOKPLOY (Para HostGator/SSL)
# =========================================================================
def create_dokploy_domain(subdomain):
    """
    Cria o domínio automaticamente no Dokploy para garantir o SSL.
    Necessário para quem usa HostGator/Hostinger sem API de DNS.
    """
    # Pega as configs das variáveis de ambiente (configure isso no Dokploy)
    DOKPLOY_URL = os.getenv("DOKPLOY_URL") 
    DOKPLOY_TOKEN = os.getenv("DOKPLOY_TOKEN")
    APP_ID = os.getenv("DOKPLOY_APP_ID")
    
    # Se não tiver configurado, apenas avisa no log e segue a vida
    if not all([DOKPLOY_URL, DOKPLOY_TOKEN, APP_ID]):
        print("⚠️ AVISO: Variáveis do Dokploy não configuradas. O domínio não será criado automaticamente.")
        return False

    # Limpa a URL do Dokploy se tiver barra no final
    if DOKPLOY_URL.endswith('/'):
        DOKPLOY_URL = DOKPLOY_URL[:-1]

    full_domain = f"{subdomain}.leanttro.com"
    print(f"🔄 Solicitando ao Dokploy criação de: {full_domain}...")

    headers = {
        "Authorization": f"Bearer {DOKPLOY_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "applicationId": APP_ID,
        "host": full_domain,
        "path": "/",
        "port": 5000,           # Porta interna do Flask
        "https": True,          # ATIVA O SSL (Let's Encrypt)
        "certificateType": "letsencrypt"
    }
    
    try:
        response = requests.post(
            f"{DOKPLOY_URL}/api/domain.create", 
            json=payload, 
            headers=headers, 
            timeout=10
        )
        
        if response.status_code in [200, 201]:
            print(f"✅ SUCESSO! Domínio {full_domain} criado no Dokploy.")
            return True
        else:
            print(f"⚠️ ERRO DOKPLOY ({response.status_code}): {response.text}")
            return False
            
    except Exception as e:
        print(f"⛔ FALHA DE CONEXÃO COM DOKPLOY: {str(e)}")
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
            print(f"Alerta: Falha ao buscar {collection_name}. Status: {response.status_code}.")
            return []
    except Exception as e:
        print(f"Erro Crítico ao buscar {collection_name}: {str(e)}")
        return []

# =========================================================================
# 4. ROTAS DO FLASK
# =========================================================================

@app.route('/')
def home():
    global GLOBAL_SUCCESSFUL_URL
    
    host = request.headers.get('Host', '')
    if 'localhost' in host or '127.0.0.1' in host:
        subdomain = 'teste'
    else:
        subdomain = host.split('.')[0]

    successful_url = None
    response = None
    last_exception = None

    # Tenta conectar no Directus (Internal vs External)
    for current_url in DIRECTUS_URLS_TO_TRY:
        current_url = clean_url(current_url)
        try:
            url_tenants = f"{current_url}/items/tenants"
            params = {"filter[subdomain][_eq]": subdomain}
            response = requests.get(url_tenants, params=params, verify=False, timeout=5)
            
            if response is not None and response.status_code is not None:
                successful_url = current_url
                GLOBAL_SUCCESSFUL_URL = current_url
                break
        except Exception as e:
            last_exception = e
            continue
            
    if not successful_url:
        return f"""
        <h1>ERRO CRÍTICO DE CONEXÃO</h1>
        <p>O Flask não conseguiu falar com o Directus.</p>
        <p>Erro Técnico: {str(last_exception)}</p>
        """, 500

    data = response.json()
    
    # Se não achou tenant, verifica se é a página de criar (confras) ou 404
    if not data.get('data'):
         if subdomain == 'confras':
             return render_template("confras.html")
         
         return f"""
            <h1>Loja não encontrada (404)</h1>
            <p>Subdomínio: <strong>{subdomain}</strong> não registrado.</p>
            """, 404

    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Busca dados usando a URL que funcionou
    products = fetch_collection_data(successful_url, "products", tenant_id)
    sections = fetch_collection_data(
        successful_url,
        "sections",
        tenant_id,
        params={"sort": "order_index", "filter[page_slug][_eq]": "home", "fields": "*.*"}
    )
    
    guests_all = fetch_collection_data(
        successful_url, 
        "vaquinha_guests", 
        tenant_id, 
        params={"sort": "-created_at"}
    )
    
    guests_confirmed = [g for g in guests_all if g.get('status') == 'CONFIRMED']
    
    vaquinha_settings_list = fetch_collection_data(successful_url, "vaquinha_settings", tenant_id)
    vaquinha_settings = vaquinha_settings_list[0] if vaquinha_settings_list else {}
    
    template_base_name = tenant.get('template_name') or 'home'
    template_file_name = f"{template_base_name}.html"
    
    return render_template(
        template_file_name,
        tenant=tenant,
        products=products,
        sections=sections,
        guests_confirmed=guests_confirmed,
        guests_all=guests_all,
        vaquinha_settings=vaquinha_settings,
        directus_external_url=DIRECTUS_URL_EXTERNAL
    )

# --- ROTA DE CRIAÇÃO (AGORA COM DOKPLOY AUTOMATION) ---
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    ADMIN_TOKEN = os.getenv("DIRECTUS_ADMIN_TOKEN")
    
    if not ADMIN_TOKEN:
        return jsonify({"status": "error", "message": "Token ADMIN não configurado."}), 500

    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    try:
        data = request.get_json()
        
        # Validação simples
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

        # 1. Cria Tenant no Directus
        tenant_create_resp = requests.post(
            f"{directus_api_url}/items/tenants",
            headers=headers,
            json=tenant_data,
            verify=False
        )
        
        if tenant_create_resp.status_code != 200:
            error_msg = "Erro ao criar loja."
            try:
                error_body = tenant_create_resp.json()
                if 'errors' in error_body:
                    error_msg = error_body['errors'][0].get('message')
                    if 'subdomain' in error_msg and 'unique' in error_msg:
                        error_msg = "Este subdomínio já está em uso."
            except:
                pass
            
            return jsonify({"status": "error", "message": error_msg}), 400

        new_tenant_id = tenant_create_resp.json()['data']['id']
        
        # 2. Cria Usuário Admin da Loja
        user_data = {
            "tenant_id": new_tenant_id,
            "email": data.get('email'),
            "password_hash": data.get('password'),
            "role": "Loja Admin",
            "name": data.get('company_name')
        }
        
        requests.post(
            f"{directus_api_url}/items/users",
            headers=headers,
            json=user_data,
            verify=False
        )
        
        # 3. AUTOMAÇÃO DOKPLOY (ESSENCIAL PARA HOSTGATOR)
        # Tenta criar o domínio lá no Dokploy para gerar o SSL
        try:
            create_dokploy_domain(subdomain_clean)
        except Exception as e_dok:
            print(f"Erro silencioso ao criar domínio no Dokploy: {e_dok}")
            # Não retornamos erro pro usuário aqui, pois o tenant já foi criado.
            # O site vai funcionar (HTTP), só o SSL que pode demorar ou falhar se config errada.

        return jsonify({
            "status": "success",
            "message": "Criado com sucesso!",
            "url": f"http://{subdomain_clean}.leanttro.com",
            "subdomain": subdomain_clean,
            "admin_token": admin_token
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro interno: {str(e)}"}), 500

@app.route('/api/approve_guest', methods=['POST'])
def approve_guest():
    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    try:
        data = request.get_json()
        guest_id = data.get('guest_id')
        
        if not guest_id:
            return jsonify({"status": "error", "message": "ID faltando"}), 400

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

    # Identifica tenant pelo subdomínio
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
        return jsonify({"status": "error", "message": "Erro ao identificar evento."}), 404

    # Upload Arquivo
    file_id = None
    if proof_file:
        try:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            up_resp = requests.post(f"{directus_api_url}/files", files=files, verify=False)
            if up_resp.status_code in [200, 201]:
                file_id = up_resp.json()['data']['id']
        except:
            pass

    # Cria Guest
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
        
        # HTML de Sucesso Simples
        return f"""
        <body style="font-family:sans-serif; text-align:center; padding:50px; background:#f0fdf4;">
            <div style="background:white; padding:40px; border-radius:10px; max-width:500px; margin:auto; box-shadow:0 10px 25px rgba(0,0,0,0.1);">
                <h1 style="color:#16a34a;">✅ Recebido!</h1>
                <p>Obrigado <b>{guest_name}</b>. Seu comprovante foi enviado.</p>
                <a href="/" style="display:inline-block; margin-top:20px; text-decoration:none; color:#16a34a; font-weight:bold;">&larr; Voltar</a>
            </div>
        </body>
        """
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)