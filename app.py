import os
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
import urllib3
import re 

# Desabilita alertas de SSL (importante para comunicação interna)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# CONFIGURAÇÕES BLINDADAS (HARDCODED)
# =========================================================================
BASE_URL = "https://confras.leanttro.com"

# --- SEU NOVO TOKEN FIXO AQUI ---
DIRECTUS_TOKEN_FIXED = "cz8LXaAjjkVFj87l6Mq60PFm4vvpxr_H"

# URLs do Directus
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055"
DIRECTUS_URL_EXTERNAL = "https://directus.leanttro.com"

# =========================================================================
# FUNÇÕES AUXILIARES
# =========================================================================
def get_directus_url():
    """Tenta usar a rede interna primeiro (mais rápido), senão vai pela externa"""
    try:
        # Tenta pingar a interna com timeout curto
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

# 2. ROTA DA FESTA (VISUALIZAÇÃO / PAGAMENTO)
@app.route('/festa/<slug>')
def festa_view(slug):
    api_url = get_directus_url()
    
    try:
        # Busca o tenant/festa no banco
        r = requests.get(
            f"{api_url}/items/tenants", 
            params={"filter[subdomain][_eq]": slug}, 
            verify=False, timeout=5
        )
        data = r.json()
    except Exception as e:
        # Se der erro de conexão, mostra mensagem clara em vez de erro 500 genérico
        return f"<h1>Erro de Conexão com Banco de Dados</h1><p>Detalhes: {str(e)}</p>", 500

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
    
    # Busca dados relacionados (Produtos, Convidados, Configurações)
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

# 3. API - CRIAR NOVA FESTA (USANDO O TOKEN NOVO)
@app.route('/api/create_tenant', methods=['POST'])
def create_tenant():
    # Usa o token FIXO que você mandou agora
    ADMIN_TOKEN = DIRECTUS_TOKEN_FIXED
    api_url = get_directus_url()
    
    try:
        data = request.get_json()
        raw_slug = data.get('subdomain', '').lower()
        slug = re.sub(r'[^a-z0-9-]', '', raw_slug) # Limpa caracteres especiais
        
        if not slug: return jsonify({"status": "error", "message": "Link inválido. Use apenas letras e números."}), 400

        print(f"DEBUG: Criando festa '{slug}' usando token fixo...")

        headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}

        # 1. Verifica se já existe (Evita duplicidade)
        try:
            check = requests.get(f"{api_url}/items/tenants", params={"filter[subdomain][_eq]": slug}, headers=headers, verify=False)
            
            if check.status_code == 401:
                 return jsonify({"status": "error", "message": "O Token do Directus no app.py foi recusado. Verifique se copiou certo."}), 500
            
            if check.json().get('data'):
                 return jsonify({"status": "error", "message": "Este nome de link já existe. Escolha outro."}), 400
        except Exception as conn_err:
             return jsonify({"status": "error", "message": f"Erro ao conectar no Directus: {str(conn_err)}"}), 500

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
            return jsonify({"status": "error", "message": f"Erro ao salvar: {resp.text}"}), 500

        new_id = resp.json()['data']['id']
        
        # 3. Cria Usuário Admin da Loja (Opcional, mas útil para login futuro)
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
    
    if not slug_origem: return "Erro: Origem desconhecida (slug faltando)", 400

    # Busca ID do Tenant
    try:
        t_resp = requests.get(f"{api_url}/items/tenants", params={"filter[subdomain][_eq]": slug_origem}, verify=False)
        data_tenant = t_resp.json().get('data')
        
        if not data_tenant:
             return "Erro: Evento não encontrado no banco.", 404
             
        tenant_id = data_tenant[0]['id']
    except:
        return "Erro de conexão ao validar evento", 500

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
    try:
        requests.post(
            f"{api_url}/items/vaquinha_guests",
            json={
                "tenant_id": tenant_id, "name": guest_name, 
                "email": guest_email, "payment_proof_url": file_id, "status": "PENDING"
            },
            verify=False
        )
    except Exception as e:
        return f"Erro ao salvar convidado: {str(e)}", 500
    
    # Página de sucesso simples com botão de voltar
    return f"""
    <body style="font-family:sans-serif; text-align:center; padding:50px; background-color: #f0fdf4;">
        <div style="background: white; padding: 40px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); max-width: 500px; margin: auto;">
            <h1 style="color:green; margin-bottom: 20px;">✅ Enviado com Sucesso!</h1>
            <p style="color: #374151; font-size: 18px;">Obrigado, <strong>{guest_name}</strong>.</p>
            <p style="color: #6b7280;">Seu comprovante foi enviado para o organizador.</p>
            <br>
            <a href="/festa/{slug_origem}" style="display: inline-block; background-color: #22c55e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">&larr; Voltar para a Festa</a>
        </div>
    </body>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)