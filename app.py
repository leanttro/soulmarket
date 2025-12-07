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
# CONFIGURAÇÃO DE CONEXÃO (ROBUSTA)
# =========================================================================
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 

DIRECTUS_URLS_TO_TRY = [
    DIRECTUS_URL_EXTERNAL,
    DIRECTUS_URL_INTERNAL
]

# Variável global para armazenar a URL que funcionou (usada na API POST)
GLOBAL_SUCCESSFUL_URL = None
# =========================================================================

def clean_url(url):
    if url and url.endswith('/'):
        return url[:-1]
    return url

# Função auxiliar para buscar coleções de forma segura
def fetch_collection_data(url, collection_name, tenant_id, params=None):
    if params is None:
        params = {}
    
    params["filter[tenant_id][_eq]"] = tenant_id

    try:
        response = requests.get(
            f"{url}/items/{collection_name}",
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

# --- ROTA PRINCIPAL (Renderização de Páginas) ---
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

    # 1. LOOP DE TENTATIVA DE CONEXÃO (TENANTS)
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
            
    # 2. TRATAMENTO DE ERROS CRÍTICOS (Sem conexão)
    if not successful_url:
        return f"""
        <h1>ERRO CRÍTICO DE CONEXÃO</h1>
        <p>O Flask não conseguiu falar com o Directus em nenhuma das URLs tentadas.</p>
        <p><strong>URLs tentadas:</strong> {', '.join(DIRECTUS_URLS_TO_TRY)}</p>
        <p><strong>Erro Técnico:</strong> {str(last_exception)}</p>
        """, 500

    # 3. TRATAMENTO DE ERROS HTTP DO DIRECTUS
    if response.status_code != 200:
        return f"""
        <h1>ERRO NO DIRECTUS: {response.status_code}</h1>
        <p>O Directus recusou a conexão usando a URL: <strong>{successful_url}</strong></p>
        <p><strong>Motivo:</strong> {response.text}</p>
        <p>Verifique a permissão da Role PUBLIC na tabela Tenants.</p>
        """, 500

    data = response.json()
    
    # 4. TRATAMENTO DE LOJA NÃO ENCONTRADA (404 Lógico)
    if not data.get('data'):
         # Se o subdomínio é 'confras', ele tenta carregar a página de cadastro
         if subdomain == 'confras':
             template_file_name = "confras.html"
             # Não precisa passar dados, pois a página de cadastro é estática
             return render_template(template_file_name)
         
         # Caso contrário, retorna 404
         return f"""
            <h1>Loja não encontrada (404)</h1>
            <p>O sistema conectou no Directus, mas não achou nenhuma loja com o subdomínio: <strong>{subdomain}</strong></p>
            """, 404

    # 5. BUSCA DE DADOS (Coleções)
    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Busca Produtos e SECTIONS (igual)
    products = fetch_collection_data(successful_url, "products", tenant_id)
    sections = fetch_collection_data(
        successful_url, 
        "sections", 
        tenant_id, 
        params={
            "sort": "order_index", 
            "filter[page_slug][_eq]": "home", 
            "fields": "*.*" 
        }
    )
    
    # Busca TODOS os Convidados (para o Painel do Organizador no template)
    guests_all = fetch_collection_data(
        successful_url, 
        "vaquinha_guests", 
        tenant_id, 
        params={"sort": "-created_at"}
    )
    
    # Filtra os confirmados para o contador público/lógica Freemium
    guests_confirmed = [g for g in guests_all if g.get('status') == 'CONFIRMED']
    
    # Busca Configurações da Vaquinha
    vaquinha_settings_list = fetch_collection_data(successful_url, "vaquinha_settings", tenant_id)
    vaquinha_settings = vaquinha_settings_list[0] if vaquinha_settings_list else {}
    
    # 6. RENDERIZAÇÃO
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

# --- ROTA DE API PARA CRIAÇÃO DE TENANT (Novo Endpoint de Escala) ---
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    ADMIN_TOKEN = os.getenv("DIRECTUS_ADMIN_TOKEN")
    
    if not ADMIN_TOKEN:
        return jsonify({"status": "error", "message": "Token de administração não configurado (DIRECTUS_ADMIN_TOKEN)."}), 500

    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    try:
        data = request.get_json()
        
        required_fields = ['company_name', 'subdomain', 'email', 'pix_key', 'password']
        if not all(data.get(field) for field in required_fields):
            return jsonify({"status": "error", "message": "Preencha todos os campos obrigatórios."}), 400
        
        # Limpeza do subdomínio
        subdomain_clean = data['subdomain'].lower()
        # Permite apenas letras, números e hífens
        subdomain_clean = re.sub(r'[^a-z0-9-]', '', subdomain_clean) 
        
        if not subdomain_clean:
             return jsonify({"status": "error", "message": "Subdomínio inválido."}), 400

        tenant_data = {
            "company_name": data['company_name'],
            "subdomain": subdomain_clean,
            "email": data['email'],
            "pix_key": data['pix_key'],
            "pix_owner_name": data['company_name'], 
            "guest_limit": 20, 
            "plan_type": "free",
            "template_name": "vaquinha", 
            "status": "active",
            "primary_color": "#22C55E", 
            "admin_token": subdomain_clean.upper() + "_TOKEN_MASTER" 
        }
        
        headers = {
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "Content-Type": "application/json"
        }

        # 3. Criação do Tenant
        tenant_create_resp = requests.post(
            f"{directus_api_url}/items/tenants", 
            headers=headers, 
            json=tenant_data, 
            verify=False
        )
        
        if tenant_create_resp.status_code != 200:
            error_msg = tenant_create_resp.json().get('errors', [{}])[0].get('message', 'Erro desconhecido ao criar o Tenant.')
            if 'subdomain' in error_msg:
                 error_msg = f"O subdomínio '{subdomain_clean}' já está em uso."
            
            return jsonify({
                "status": "error", 
                "message": error_msg
            }), 400

        new_tenant_id = tenant_create_resp.json()['data']['id']
        
        # 4. Criação do Usuário na tabela 'users' (CORRIGIDO PARA LOJA ADMIN)
        user_data = {
            "tenant_id": new_tenant_id,
            "email": data['email'],
            "password_hash": data['password'], 
            "role": "Loja Admin", # <-- CORREÇÃO CRÍTICA PARA MULTITENANT
            "name": data['company_name']
        }
        
        requests.post(
            f"{directus_api_url}/items/users", 
            headers=headers, 
            json=user_data, 
            verify=False
        )
        
        return jsonify({
            "status": "success", 
            "message": "Sua vaquinha foi criada!",
            "url": f"http://{subdomain_clean}.leanttro.com",
            "subdomain": subdomain_clean # Retorna o subdominio para uso no frontend
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro interno do servidor: {str(e)}"}), 500

# --- ROTA DE API PARA APROVAÇÃO (PATCH) ---
@app.route('/api/approve_guest', methods=['POST'])
def approve_guest():
    
    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    try:
        data = request.get_json()
        guest_id = data.get('guest_id')
    except Exception:
        return jsonify({"status": "error", "message": "ID do convidado não fornecido."}), 400

    if not guest_id:
        return jsonify({"status": "error", "message": "ID do convidado é obrigatório."}), 400

    try:
        update_data = {"status": "CONFIRMED"}
        
        update_resp = requests.patch(
            f"{directus_api_url}/items/vaquinha_guests/{guest_id}", 
            json=update_data, 
            verify=False
        )
        
        if update_resp.status_code == 200:
            return jsonify({"status": "success", "message": "Convidado aprovado com sucesso!"}), 200
        else:
            return jsonify({"status": "error", "message": "Falha ao atualizar status no Directus."}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro de comunicação ao aprovar o registro: {str(e)}"}), 500

# --- ROTA DE API PARA ENVIO DE COMPROVANTE (POST) ---
@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    
    directus_api_url = GLOBAL_SUCCESSFUL_URL or clean_url(os.getenv("DIRECTUS_URL", "https://directus.leanttro.com"))
    
    guest_name = request.form.get('name')
    guest_email = request.form.get('email') # <-- NOVO: Puxa o campo email
    proof_file = request.files.get('proof')

    if not all([guest_name, guest_email, proof_file]): # <-- NOVO: Valida o email
        return jsonify({"status": "error", "message": "Nome, email e comprovante são obrigatórios."}), 400

    host = request.headers.get('Host', '')
    subdomain = host.split('.')[0]
    tenant_id = None
    
    try:
        tenant_resp = requests.get(
            f"{directus_api_url}/items/tenants",
            params={"filter[subdomain][_eq]": subdomain},
            verify=False
        )
        tenant_data = tenant_resp.json().get('data', [{}])
        if tenant_data:
            tenant_id = tenant_data[0].get('id')
    except Exception:
        pass 

    if not tenant_id:
        return jsonify({"status": "error", "message": "Tenant não encontrado no Directus."}), 404

    file_id = None
    try:
        files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
        
        upload_resp = requests.post(
            f"{directus_api_url}/files", 
            files=files, 
            verify=False, 
            timeout=10
        )
        
        if upload_resp.status_code == 200 or upload_resp.status_code == 204:
            file_id = upload_resp.json()['data']['id']
        else:
            return jsonify({"status": "error", "message": f"Falha ao enviar arquivo para o Directus. Status: {upload_resp.status_code}. Motivo: {upload_resp.text[:50]}..."}), 500
            
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro de comunicação ao fazer upload do comprovante: {str(e)}"}), 500

    try:
        guest_data = {
            "tenant_id": tenant_id,
            "name": guest_name,
            "email": guest_email, # <-- SALVA O EMAIL
            "payment_proof_url": file_id, 
            "status": "PENDING"
        }
        
        save_resp = requests.post(
            f"{directus_api_url}/items/vaquinha_guests", 
            json=guest_data, 
            verify=False
        )
        
        if save_resp.status_code == 200 or save_resp.status_code == 204:
            return """
                <!DOCTYPE html>
                <html lang="pt-br">
                <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Sucesso!</title><script src="https://cdn.tailwindcss.com"></script></head>
                <body class="bg-gray-100 flex items-center justify-center h-screen">
                    <div class="bg-white p-10 rounded-xl shadow-2xl text-center border-t-4 border-green-500 max-w-lg">
                        <h1 class="text-3xl font-bold text-green-600 mb-4">✅ Comprovante Enviado!</h1>
                        <p class="text-gray-700 mb-6">O comprovante de **{guest_name}** foi enviado e está sob análise do organizador.</p>
                        <a href="/" class="bg-green-500 hover:bg-green-600 text-white font-bold py-2 px-6 rounded-lg transition duration-300">Voltar para a Vaquinha</a>
                    </div>
                </body>
                </html>
            """.format(guest_name=guest_name)
        else:
            return jsonify({"status": "error", "message": f"Falha ao registrar convidado. Status: {save_resp.status_code}. Motivo: {save_resp.text[:50]}..."}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro de comunicação ao salvar o registro: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)