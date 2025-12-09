import os
import requests
import json
import re
import smtplib
import urllib3
import mercadopago
from flask import Flask, render_template, request, jsonify, redirect, url_for
from email.mime.text import MIMEText

app = Flask(__name__)

# =========================================================================
# 1. CONFIGURAÇÕES
# =========================================================================

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chave_secreta_padrao")
BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

# DIRECTUS
DIRECTUS_URL = os.environ.get("DIRECTUS_URL", "https://directus.leanttro.com")
DIRECTUS_TOKEN = os.environ.get("DIRECTUS_TOKEN", "SEU_TOKEN_AQUI") 

# MERCADO PAGO
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
if MP_ACCESS_TOKEN:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
else:
    class FakeSDK:
        def preference(self): return self
        def create(self, data): return {"response": {"id": "fake_preference_id"}}
    sdk = FakeSDK()
    print("⚠️ AVISO: MP_ACCESS_TOKEN não configurado.")

# SSL
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- IMPORTANTE: ATUALIZE ISTO NO SEU DIRECTUS ---
# Esse ID deve ser pego em Configurações > Roles & Permissions > Clicar na Role > Copiar ID da URL
USER_ROLE_ID = "92676066-7506-4c16-9177-3bc0a7530b30" 

# =========================================================================
# 2. FUNÇÕES AUXILIARES
# =========================================================================

def directus_request(method, endpoint, data=None, params=None):
    headers = {
        "Authorization": f"Bearer {DIRECTUS_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"{DIRECTUS_URL}{endpoint}"
    try:
        if method == 'GET':
            r = requests.get(url, params=params, headers=headers, verify=VERIFY_SSL, timeout=10)
        elif method == 'POST':
            r = requests.post(url, json=data, headers=headers, verify=VERIFY_SSL, timeout=10)
        elif method == 'PATCH':
            r = requests.patch(url, json=data, headers=headers, verify=VERIFY_SSL, timeout=10)
        elif method == 'DELETE':
            r = requests.delete(url, headers=headers, verify=VERIFY_SSL, timeout=10)
        
        try:
            return r.json()
        except:
            return None
    except Exception as e:
        print(f"❌ Erro Directus ({endpoint}): {e}")
        return None

def send_welcome_email(to_email, link, senha):
    # Implementar lógica de SMTP aqui se necessário
    pass

# =========================================================================
# 3. ROTAS DE PÁGINAS (FRONTEND)
# =========================================================================

@app.route('/')
def home():
    # Rota principal agora aponta para a Landing Page do Divide o PIX
    return render_template("confras.html")

@app.route('/login.html')
def login_page():
    return render_template('login.html')

@app.route('/admin.html')
def admin_page_direct():
    return render_template('admin.html')

@app.route('/admin')
def admin_panel():
    email_param = request.args.get('email')
    if not email_param:
        return redirect(url_for('login_page'))

    t_data = directus_request('GET', '/items/tenants', params={"filter[email][_eq]": email_param})
    
    if not t_data or not t_data.get('data'):
         return "<h1>Painel não encontrado ou acesso negado.</h1>", 404
    
    tenant = t_data['data'][0]
    
    guests_req = directus_request('GET', '/items/vaquinha_guests', params={
        "filter[tenant_id][_eq]": tenant['id'],
        "sort": "-created_at"
    })
    all_guests = guests_req.get('data', []) if guests_req else []
    
    pending = [g for g in all_guests if g.get('status') == 'PENDING']
    confirmed = [g for g in all_guests if g.get('status') == 'CONFIRMED']

    return render_template("admin.html", tenant=tenant, guests_pending=pending, guests_confirmed=confirmed)

@app.route('/festa/<slug>')
@app.route('/<slug>')
def festa_view(slug):
    # Ignora arquivos estáticos comuns
    if slug in ['favicon.ico', 'robots.txt'] or slug.startswith('api') or '.' in slug:
        return "Not Found", 404

    data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
    
    if not data or not data.get('data'):
        return "<h1>Evento não encontrado</h1>", 404
        
    tenant = data['data'][0]
    
    # Se for o dono acessando a rota pública, redireciona para o admin (opcional)
    if request.args.get('admin'):
        return redirect(url_for('admin_panel', email=tenant['email']))

    guests_data = directus_request('GET', '/items/vaquinha_guests', params={
        "filter[tenant_id][_eq]": tenant['id']
    })
    guests = guests_data.get('data', []) if guests_data else []
    confirmed = [g for g in guests if g.get('status') == 'CONFIRMED']
    
    return render_template(
        "vaquinha.html", 
        tenant=tenant,
        guests_confirmed=confirmed,
        is_limit_reached=(len(confirmed) >= tenant.get('guest_limit', 20)),
        current_slug=slug
    )

# =========================================================================
# 4. API (BACKEND)
# =========================================================================

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({"message": "Preencha e-mail e senha"}), 400

    try:
        # Autentica direto no endpoint /auth/login do Directus
        auth_url = f"{DIRECTUS_URL}/auth/login"
        auth_resp = requests.post(
            auth_url, 
            json={"email": email, "password": password},
            headers={"Content-Type": "application/json"},
            verify=VERIFY_SSL
        )
        
        if auth_resp.status_code == 200:
            token = auth_resp.json()['data']['access_token']
            return jsonify({"token": token, "email": email})
        else:
            return jsonify({"message": "E-mail ou senha incorretos."}), 401

    except Exception as e:
        print(f"Erro Login: {e}")
        return jsonify({"message": "Erro no servidor."}), 500


@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    origin_slug = request.form.get('origin_slug')
    
    if not origin_slug and request.referrer:
        parts = request.referrer.split('/')
        origin_slug = parts[-1] if parts[-1] else parts[-2]

    t_data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": origin_slug})
    if not t_data or not t_data.get('data'): 
        return jsonify({"status": "error", "message": "Evento não encontrado"}), 404
    
    tenant = t_data['data'][0]
    file_id = None
    proof_file = request.files.get('proof')

    try:
        if proof_file:
            file_content = proof_file.read()
            files = {'file': (proof_file.filename, file_content, proof_file.mimetype)}
            h_auth = {"Authorization": f"Bearer {DIRECTUS_TOKEN}"}
            
            up = requests.post(f"{DIRECTUS_URL}/files", files=files, headers=h_auth, verify=VERIFY_SSL)
            
            if up.status_code in [200, 201]: 
                file_id = up.json()['data']['id']
            else:
                print(f"Erro Directus Upload: {up.text}")
                return jsonify({"status": "error", "message": "Erro ao salvar imagem. Tente novamente."}), 500

        directus_request('POST', '/items/vaquinha_guests', data={
            "tenant_id": tenant['id'],
            "name": request.form.get('name'),
            "payment_proof_url": file_id,
            "status": "PENDING"
        })

        return jsonify({"status": "success", "message": "Comprovante enviado com sucesso!"})

    except Exception as e:
        print(f"Erro Geral: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/create_preference', methods=['POST'])
def create_preference():
    try:
        data = request.json
        plan = data.get('plan')
        price = 9.99 if plan == 'plus' else 17.99
        title = f"Plano PIX {plan.capitalize()}"

        preference_data = {
            "items": [{"title": title, "quantity": 1, "unit_price": price, "currency_id": "BRL"}],
            "back_urls": {
                "success": f"{BASE_URL}/admin",
                "failure": BASE_URL,
                "pending": BASE_URL
            },
            "auto_return": "approved"
        }
        
        response = sdk.preference().create(preference_data)
        return jsonify({"id": response["response"]["id"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/create_tenant_free', methods=['POST'])
def create_tenant_free():
    data = request.get_json()
    slug = re.sub(r'[^a-z0-9-]', '', data.get('subdomain', '').lower())
    
    if not slug: return jsonify({"status": "error", "message": "Link inválido"}), 400

    # 1. Cria o Tenant (Evento)
    tenant_payload = {
        "company_name": data.get('company_name'),
        "subdomain": slug,
        "email": data.get('email'),
        "pix_key": data.get('pix_key'),
        "pix_owner_name": "Organizador",
        "status": "active",
        "plan_type": "free",
        "guest_limit": 20
    }
    
    resp = directus_request('POST', '/items/tenants', data=tenant_payload)
    
    if resp and resp.get('data'):
        new_tenant_id = resp['data']['id']
        
        # 2. Cria o Usuário de Login (CRÍTICO: Validar se deu certo)
        user_payload = {
            "first_name": "Admin",
            "last_name": data.get('company_name'),
            "email": data.get('email'), 
            "password": data.get('password'), 
            "role": USER_ROLE_ID,
            "tenant_id": new_tenant_id
        }
        
        user_resp = directus_request('POST', '/users', data=user_payload)
        
        # Verifica falha na criação do usuário
        if not user_resp or 'data' not in user_resp:
            print(f"❌ ERRO FATAL AO CRIAR USUÁRIO: {user_resp}")
            
            # (Opcional) Remove o tenant criado para não ficar "órfão" sem dono
            # directus_request('DELETE', f'/items/tenants/{new_tenant_id}')
            
            # Retorna erro detalhado para o frontend entender
            error_msg = "Erro interno ao criar login."
            if user_resp and 'errors' in user_resp:
                error_msg = user_resp['errors'][0].get('message', error_msg)
            
            return jsonify({"status": "error", "message": f"Falha no cadastro: {error_msg}"}), 500
        
        # Se chegou aqui, tudo certo
        return jsonify({
            "status": "success",
            "url": f"/festa/{slug}",
            "admin_token": "autologin" 
        })
    
    # Se falhar ao criar o Tenant (provavelmente slug duplicado)
    return jsonify({"status": "error", "message": "Erro ao criar. Verifique se o Link já não existe."}), 400


@app.route('/api/admin/update_guest', methods=['POST'])
def admin_update_guest():
    data = request.get_json()
    guest_id = data.get('guest_id')
    new_status = data.get('status') 
    
    if not guest_id: return jsonify({"error": "ID faltando"}), 400

    resp = directus_request('PATCH', f'/items/vaquinha_guests/{guest_id}', data={
        "status": new_status
    })
    
    if resp: return jsonify({"status": "success"}), 200
    return jsonify({"error": "Falha ao atualizar"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)