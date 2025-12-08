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
# URL Pública (para redirects)
BASE_URL = "https://confras.leanttro.com"

# Directus Config
DIRECTUS_URL_EXTERNAL = "https://directus.leanttro.com"
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 
DIRECTUS_URLS_TO_TRY = [DIRECTUS_URL_EXTERNAL, DIRECTUS_URL_INTERNAL]
GLOBAL_SUCCESSFUL_URL = None

# =========================================================================
# FUNÇÕES AUXILIARES
# =========================================================================
def clean_url(url):
    if url and url.endswith('/'): return url[:-1]
    return url

def get_directus_url():
    """Retorna a melhor URL disponível para o Directus"""
    global GLOBAL_SUCCESSFUL_URL
    if GLOBAL_SUCCESSFUL_URL: return GLOBAL_SUCCESSFUL_URL
    
    for url in DIRECTUS_URLS_TO_TRY:
        try:
            r = requests.get(f"{url}/server/ping", verify=False, timeout=2)
            if r.status_code == 200:
                GLOBAL_SUCCESSFUL_URL = url
                return url
        except: continue
    return DIRECTUS_URL_EXTERNAL # Fallback

def fetch_collection_data(url_base, collection_name, tenant_id, params=None):
    if params is None: params = {}
    params["filter[tenant_id][_eq]"] = tenant_id
    try:
        r = requests.get(f"{url_base}/items/{collection_name}", params=params, verify=False, timeout=5)
        return r.json().get('data', []) if r.status_code == 200 else []
    except: return []

# =========================================================================
# ROTAS PRINCIPAIS
# =========================================================================

# 1. ROTA DA HOME (Página de Criação e Login)
@app.route('/')
def home():
    # Se alguém acessar a raiz, mostra a página de criar/entrar
    return render_template("confras.html")

# 2. ROTA DA FESTA (Onde o convidado entra)
# Exemplo: confras.leanttro.com/festa/natal-familia
@app.route('/festa/<slug>')
def festa_view(slug):
    directus_url = get_directus_url()
    
    # Busca o tenant pelo 'subdomain' (que agora é o slug da URL)
    try:
        r = requests.get(
            f"{directus_url}/items/tenants", 
            params={"filter[subdomain][_eq]": slug}, 
            verify=False, 
            timeout=5
        )
    except:
        return "<h1>Erro de conexão com Banco de Dados</h1>", 500

    data = r.json()
    if not data.get('data'):
         return render_template("404_festa.html", slug=slug), 404

    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Carrega dados da festa
    products = fetch_collection_data(directus_url, "products", tenant_id)
    sections = fetch_collection_data(directus_url, "sections", tenant_id)
    guests = fetch_collection_data(directus_url, "vaquinha_guests", tenant_id, params={"sort": "-created_at"})
    
    settings_list = fetch_collection_data(directus_url, "vaquinha_settings", tenant_id)
    settings = settings_list[0] if settings_list else {}
    
    return render_template(
        f"{tenant.get('template_name', 'home')}.html",
        tenant=tenant, 
        products=products, 
        sections=sections,
        guests_confirmed=[g for g in guests if g.get('status') == 'CONFIRMED'],
        guests_all=guests, 
        vaquinha_settings=settings,
        directus_external_url=DIRECTUS_URL_EXTERNAL,
        current_slug=slug # Passamos o slug para usar no template
    )

# 3. ROTA API - CRIAR CONTA (Modificada para não chamar Dokploy)
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    ADMIN_TOKEN = os.getenv("DIRECTUS_ADMIN_TOKEN")
    if not ADMIN_TOKEN: return jsonify({"status": "error", "message": "Erro de Configuração no Servidor"}), 500

    api_url = get_directus_url()
    
    try:
        data = request.get_json()
        # Limpa o slug/subdominio
        raw_slug = data.get('subdomain', '').lower()
        slug = re.sub(r'[^a-z0-9-]', '', raw_slug)
        
        if not slug: return jsonify({"status": "error", "message": "Nome do link inválido"}), 400

        # Verifica se já existe
        check = requests.get(f"{api_url}/items/tenants", params={"filter[subdomain][_eq]": slug}, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, verify=False)
        if check.json().get('data'):
             return jsonify({"status": "error", "message": "Este link já está em uso. Escolha outro."}), 400

        # Prepara dados
        admin_token_suffix = re.sub(r'[^a-zA-Z0-9]', '', data.get('password', 'master')) # Usa parte da senha pro token
        full_admin_token = f"{slug.upper()}_{admin_token_suffix}"

        tenant_payload = {
            "company_name": data.get('company_name'),
            "subdomain": slug, # Salvamos como subdomain, mas usaremos como rota
            "email": data.get('email'),
            "pix_key": data.get('pix_key'),
            "status": "active",
            "admin_token": full_admin_token
        }
        
        # Cria no Directus
        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}
        tenant_resp = requests.post(f"{api_url}/items/tenants", headers=headers, json=tenant_payload, verify=False)
        
        if tenant_resp.status_code != 200:
            return jsonify({"status": "error", "message": "Erro ao salvar no banco de dados."}), 500

        new_id = tenant_resp.json()['data']['id']
        
        # Cria usuário no Directus (Opcional, mas mantido pra registro)
        requests.post(f"{api_url}/items/users", headers=headers, json={
            "tenant_id": new_id, "email": data.get('email'), 
            "password_hash": data.get('password'), "role": "Loja Admin"
        }, verify=False)
        
        # NÃO CHAMAMOS MAIS O DOKPLOY AQUI!

        return jsonify({
            "status": "success",
            "url": f"{BASE_URL}/festa/{slug}",
            "admin_token": full_admin_token
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# 4. ROTA API - APROVAR CONVIDADO
@app.route('/api/approve_guest', methods=['POST'])
def approve_guest():
    # Lógica idêntica, apenas busca a URL correta
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

# 5. ROTA API - ENVIAR COMPROVANTE (Upload)
@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    api_url = get_directus_url()
    
    guest_name = request.form.get('name')
    guest_email = request.form.get('email')
    proof_file = request.files.get('proof')
    slug_origem = request.form.get('origin_slug') # Novo campo hidden no form
    
    # Busca Tenant pelo slug
    try:
        t_resp = requests.get(f"{api_url}/items/tenants", params={"filter[subdomain][_eq]": slug_origem}, verify=False)
        tenant_data = t_resp.json().get('data', [])
        if not tenant_data: return "Evento não encontrado", 404
        tenant_id = tenant_data[0]['id']
    except:
        return "Erro ao buscar evento", 500

    # Upload Imagem
    file_id = None
    if proof_file:
        try:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            up_resp = requests.post(f"{api_url}/files", files=files, verify=False)
            if up_resp.status_code in [200, 201]:
                file_id = up_resp.json()['data']['id']
        except: pass

    # Salva Convidado
    requests.post(
        f"{api_url}/items/vaquinha_guests",
        json={
            "tenant_id": tenant_id, "name": guest_name, 
            "email": guest_email, "payment_proof_url": file_id, "status": "PENDING"
        },
        verify=False
    )
    
    # Página de sucesso simples
    return f"""
    <body style="font-family:sans-serif; text-align:center; padding:50px; background:#f0fdf4;">
        <div style="background:white; padding:40px; border-radius:10px; max-width:500px; margin:auto; box-shadow:0 10px 25px rgba(0,0,0,0.1);">
            <h1 style="color:#16a34a;">✅ Enviado!</h1>
            <p>Obrigado <b>{guest_name}</b>.</p>
            <p>O organizador irá conferir seu comprovante.</p>
            <a href="/festa/{slug_origem}" style="display:inline-block; margin-top:20px; text-decoration:none; color:#16a34a; font-weight:bold;">&larr; Voltar para a Festa</a>
        </div>
    </body>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)