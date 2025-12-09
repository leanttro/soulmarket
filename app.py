import os
import requests
import json
import re
import urllib3
import mercadopago
from flask import Flask, render_template, request, jsonify, redirect, url_for

app = Flask(__name__)

# =========================================================================
# 1. CONFIGURAÇÕES
# =========================================================================

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chave_secreta_padrao")
BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

# DIRECTUS
DIRECTUS_URL = os.environ.get("DIRECTUS_URL", "https://directus.leanttro.com")
DIRECTUS_TOKEN = os.environ.get("DIRECTUS_TOKEN", "SEU_TOKEN_AQUI") 

# Mercado Pago
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
if MP_ACCESS_TOKEN:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
else:
    class FakeSDK:
        def preference(self): return self
        def create(self, data): return {"response": {"id": "fake_preference_id"}}
    sdk = FakeSDK()

# SSL
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# =========================================================================
# 3. ROTAS DE PÁGINAS (FRONTEND)
# =========================================================================

@app.route('/')
@app.route('/confras.html') # ADICIONADO PARA O LINK FUNCIONAR
def home():
    return render_template("confras.html")

@app.route('/login.html')
def login_page():
    return render_template('login.html')

@app.route('/admin.html')
def admin_page_redirect():
    return redirect(url_for('login_page'))

@app.route('/admin')
def admin_panel():
    email_param = request.args.get('email')
    if not email_param:
        return redirect(url_for('login_page'))

    t_data = directus_request('GET', '/items/tenants', params={"filter[email][_eq]": email_param})
    
    if not t_data or not t_data.get('data'):
         return "<h1>Painel não encontrado ou e-mail inválido.</h1>", 404
    
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
    if slug in ['favicon.ico', 'robots.txt'] or slug.startswith('api') or '.' in slug:
        return "Not Found", 404

    data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
    
    if not data or not data.get('data'):
        return "<h1>Evento não encontrado</h1>", 404
        
    tenant = data['data'][0]
    
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
        resp = directus_request('GET', '/items/tenants', params={'filter[email][_eq]': email})
        
        if resp and resp.get('data') and len(resp['data']) > 0:
            user = resp['data'][0]
            stored_pass = user.get('senha')
            
            if stored_pass == password:
                return jsonify({
                    "token": "session_valid",
                    "email": email,
                    "tenant_id": user['id']
                })
            else:
                return jsonify({"message": "Senha incorreta."}), 401
        else:
            return jsonify({"message": "E-mail não encontrado."}), 404

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
                return jsonify({"status": "error", "message": "Erro ao salvar imagem."}), 500

        directus_request('POST', '/items/vaquinha_guests', data={
            "tenant_id": tenant['id'],
            "name": request.form.get('name'),
            "payment_proof_url": file_id,
            "status": "PENDING"
        })

        return jsonify({"status": "success", "message": "Comprovante enviado com sucesso!"})

    except Exception as e:
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
            "back_urls": {"success": f"{BASE_URL}/admin", "failure": BASE_URL, "pending": BASE_URL},
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

    tenant_payload = {
        "company_name": data.get('company_name'),
        "subdomain": slug,
        "email": data.get('email'),
        "pix_key": data.get('pix_key'),
        "senha": data.get('password'),
        "pix_owner_name": "Organizador",
        "status": "active",
        "plan_type": "free",
        "guest_limit": 20
    }
    
    resp = directus_request('POST', '/items/tenants', data=tenant_payload)
    
    if resp and resp.get('data'):
        new_id = resp['data']['id']
        try:
            directus_request('POST', '/users', data={
                "first_name": "Admin",
                "last_name": data.get('company_name'),
                "email": data.get('email'), 
                "password": data.get('password'), 
                "role": USER_ROLE_ID,
                "tenant_id": new_id
            })
        except:
            pass 
        
        return jsonify({
            "status": "success",
            "url": f"/festa/{slug}",
            "admin_token": "autologin" 
        })
    
    return jsonify({"status": "error", "message": "Erro ao criar (link em uso?)"}), 400

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