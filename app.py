import os
import requests
from flask import Flask, render_template, request, jsonify 
import urllib3
from urllib.parse import urljoin 
import json 
import re 

# Desabilita alertas de SSL (útil pois sua comunicação interna pode não ter SSL validado)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# 1. CONFIGURAÇÃO DE CONEXÃO COM DIRECTUS
# =========================================================================
# Tenta conectar tanto externamente quanto internamente (Docker network)
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 

DIRECTUS_URLS_TO_TRY = [
    DIRECTUS_URL_EXTERNAL,
    DIRECTUS_URL_INTERNAL
]

# Variável global para armazenar a URL que funcionou (otimização)
GLOBAL_SUCCESSFUL_URL = None

# =========================================================================
# 2. AUTOMAÇÃO DOKPLOY (CRIA O DOMÍNIO AUTOMATICAMENTE)
# =========================================================================
def create_dokploy_domain(subdomain):
    """
    Função que chama a API do Dokploy para criar o subdomínio e gerar SSL.
    Lê as variáveis de ambiente que você configurou no painel.
    """
    DOKPLOY_URL = os.getenv("DOKPLOY_URL") 
    DOKPLOY_TOKEN = os.getenv("DOKPLOY_TOKEN")
    APP_ID = os.getenv("DOKPLOY_APP_ID") # O ID que pegamos da URL (wqngj...)
    
    # Verificação de segurança
    if not all([DOKPLOY_URL, DOKPLOY_TOKEN, APP_ID]):
        print("⚠️ AVISO: Variáveis do Dokploy (URL, TOKEN ou APP_ID) não configuradas.")
        return False

    # Limpa a URL se tiver barra no final
    if DOKPLOY_URL.endswith('/'):
        DOKPLOY_URL = DOKPLOY_URL[:-1]

    full_domain = f"{subdomain}.leanttro.com"
    print(f"🔄 [DOKPLOY] Solicitando criação de: {full_domain} no App ID: {APP_ID}")

    headers = {
        "Authorization": f"Bearer {DOKPLOY_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Payload para criar o domínio com SSL (Let's Encrypt)
    payload = {
        "applicationId": APP_ID,
        "host": full_domain,
        "path": "/",
        "port": 5000,           # Porta interna do Flask (definida no seu Start Cmd)
        "https": True,          # Força HTTPS
        "certificateType": "letsencrypt"
    }
    
    try:
        response = requests.post(
            f"{DOKPLOY_URL}/api/domain.create", 
            json=payload, 
            headers=headers, 
            timeout=15
        )
        
        if response.status_code in [200, 201]:
            print(f"✅ [DOKPLOY] SUCESSO! Domínio {full_domain} criado.")
            return True
        else:
            print(f"⚠️ [DOKPLOY] ERRO ({response.status_code}): {response.text}")
            return False
            
    except Exception as e:
        print(f"⛔ [DOKPLOY] FALHA DE CONEXÃO: {str(e)}")
        return False

# =========================================================================
# 3. FUNÇÕES AUXILIARES (DIRECTUS)
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
    except Exception as e:
        print(f"Erro ao buscar {collection_name}: {str(e)}")
        return []

# =========================================================================
# 4. ROTAS DO FLASK
# =========================================================================

@app.route('/')
def home():
    global GLOBAL_SUCCESSFUL_URL
    
    # Identifica o subdomínio acessado
    host = request.headers.get('Host', '')
    if 'localhost' in host or '127.0.0.1' in host:
        subdomain = 'teste' # Fallback para dev local
    else:
        subdomain = host.split('.')[0]

    successful_url = None
    response = None

    # Tenta encontrar o Directus (Retry Logic)
    for current_url in DIRECTUS_URLS_TO_TRY:
        current_url = clean_url(current_url)
        try:
            # Busca o Tenant pelo subdomínio
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
        return "<h1>Erro de Conexão com o Banco de Dados (Directus).</h1>", 500

    data = response.json()
    
    # Roteamento Lógico: Se não achou tenant, vê se é a página principal 'confras'
    if not data.get('data'):
         if subdomain == 'confras':
             return render_template("confras.html")
         
         return f"<h1>404 - Página não encontrada</h1><p>O endereço {subdomain} não existe.</p>", 404

    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Carrega dados do evento (Produtos, Convidados, Configs)
    products = fetch_collection_data(successful_url, "products", tenant_id)
    sections = fetch_collection_data(successful_url, "sections", tenant_id)
    
    guests_all = fetch_collection_data(successful_url, "vaquinha_guests", tenant_id, params={"sort": "-created_at"})
    guests_confirmed = [g for g in guests_all if g.get('status') == 'CONFIRMED']
    
    vaquinha_settings_list = fetch_collection_data(successful_url, "vaquinha_settings", tenant_id)
    vaquinha_settings = vaquinha_settings_list[0] if vaquinha_settings_list else {}
    
    # Renderiza o template (padrão 'vaquinha.html')
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

# --- ROTA DE CRIAÇÃO (PROCESSO PRINCIPAL) ---
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    # Token para escrever no Directus (Admin)
    ADMIN_TOKEN = os.getenv("DIRECTUS_ADMIN_TOKEN") # O token 'jr6z...' do seu print
    
    if not ADMIN_TOKEN:
        return jsonify({"status": "error", "message": "Erro de config: Token ADMIN faltando."}), 500

    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    try:
        data = request.get_json()
        
        # Validação Básica
        if not data.get('subdomain'):
             return jsonify({"status": "error", "message": "Subdomínio é obrigatório."}), 400
        
        # Higieniza o subdomínio
        subdomain_clean = data['subdomain'].lower()
        subdomain_clean = re.sub(r'[^a-z0-9-]', '', subdomain_clean)

        # Gera o token do administrador dessa nova página
        admin_token = subdomain_clean.upper() + "_TOKEN_MASTER"

        # Prepara dados para o Directus
        tenant_data = {
            "company_name": data.get('company_name'),
            "subdomain": subdomain_clean,
            "email": data.get('email'),
            "pix_key": data.get('pix_key'),
            "pix_owner_name": data.get('company_name'), # Usa nome da empresa como dono se não informado
            "guest_limit": 20,
            "plan_type": "free",
            "template_name": "vaquinha",
            "status": "active",
            "primary_color": "#22C55E", # Verde padrão
            "admin_token": admin_token
        }
        
        headers = {
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "Content-Type": "application/json"
        }

        # 1. Cria o Tenant no Directus
        tenant_create_resp = requests.post(
            f"{directus_api_url}/items/tenants",
            headers=headers,
            json=tenant_data,
            verify=False
        )
        
        # Tratamento de erro (ex: duplicidade)
        if tenant_create_resp.status_code != 200:
            error_msg = "Erro ao criar registro."
            try:
                error_body = tenant_create_resp.json()
                if 'errors' in error_body:
                    msg_detalhada = error_body['errors'][0].get('message')
                    if 'subdomain' in msg_detalhada and 'unique' in msg_detalhada:
                        error_msg = "Este subdomínio já existe. Escolha outro."
                    else:
                        error_msg = msg_detalhada
            except:
                pass
            
            return jsonify({"status": "error", "message": error_msg}), 400

        new_tenant_id = tenant_create_resp.json()['data']['id']
        
        # 2. Cria Usuário Admin da Loja (Opcional, mas bom para login futuro)
        user_data = {
            "tenant_id": new_tenant_id,
            "email": data.get('email'),
            "password_hash": data.get('password'),
            "role": "Loja Admin",
            "name": data.get('company_name')
        }
        requests.post(f"{directus_api_url}/items/users", headers=headers, json=user_data, verify=False)
        
        # 3. AUTOMAÇÃO DOKPLOY (CRIAÇÃO DO DOMÍNIO REAL)
        # Tenta criar o domínio via API do Dokploy
        dokploy_success = False
        try:
            dokploy_success = create_dokploy_domain(subdomain_clean)
        except Exception as e_dok:
            print(f"Erro ao tentar criar domínio no Dokploy: {e_dok}")

        # Mensagem final dependendo se o domínio foi criado automaticamente ou não
        msg_final = "CRIADO COM SUCESSO!"
        if not dokploy_success:
            msg_final += " (Porém houve um erro ao registrar o domínio no servidor. Contate o suporte.)"

        # 4. RETORNO FINAL PARA O FRONTEND
        return jsonify({
            "status": "success",
            "message": msg_final,
            # Retorna https pois tentamos criar o certificado
            "url": f"https://{subdomain_clean}.leanttro.com", 
            "subdomain": subdomain_clean,
            "admin_token": admin_token  # <--- CRÍTICO: Token para o usuário acessar o painel
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro interno do servidor: {str(e)}"}), 500

# --- ROTA DE APROVAÇÃO (PAINEL ADMIN) ---
@app.route('/api/approve_guest', methods=['POST'])
def approve_guest():
    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    try:
        data = request.get_json()
        guest_id = data.get('guest_id')
        
        if not guest_id:
            return jsonify({"status": "error", "message": "ID faltando"}), 400

        # Atualiza status para CONFIRMED
        requests.patch(
            f"{directus_api_url}/items/vaquinha_guests/{guest_id}",
            json={"status": "CONFIRMED"},
            verify=False
        )
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- ROTA DE ENVIO DE COMPROVANTE (PÚBLICO) ---
@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    guest_name = request.form.get('name')
    guest_email = request.form.get('email')
    proof_file = request.files.get('proof')

    host = request.headers.get('Host', '')
    subdomain = host.split('.')[0]
    
    # Busca ID do Tenant
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

    # Upload do Arquivo no Directus
    file_id = None
    if proof_file:
        try:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            up_resp = requests.post(f"{directus_api_url}/files", files=files, verify=False)
            if up_resp.status_code in [200, 201]:
                file_id = up_resp.json()['data']['id']
        except:
            print("Erro no upload da imagem")

    # Cria o convidado (Status PENDING)
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
                <h1 style="color:#16a34a;">✅ Recebido!</h1>
                <p>Obrigado <b>{guest_name}</b>.</p>
                <p>O organizador analisará seu comprovante em breve.</p>
                <a href="/" style="display:inline-block; margin-top:20px; text-decoration:none; color:#16a34a; font-weight:bold; border: 1px solid #16a34a; padding: 10px 20px; border-radius: 5px;">&larr; Voltar para a Vaquinha</a>
            </div>
        </body>
        """
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)