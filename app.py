import os
import requests
import json
import re
import urllib3
import mercadopago
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, jsonify, redirect, url_for
from itsdangerous import URLSafeTimedSerializer

app = Flask(__name__)

# =========================================================================
# 1. CONFIGURAÇÕES & AMBIENTE
# =========================================================================

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chave_secreta_padrao")
BASE_URL = os.environ.get("APP_BASE_URL", "https://www.divideopix.com.br")

# DIRECTUS CONFIG
DIRECTUS_URL = os.environ.get("DIRECTUS_URL", "https://directus.leanttro.com")
DIRECTUS_TOKEN = os.environ.get("DIRECTUS_TOKEN", "SEU_TOKEN_AQUI") 

# MERCADO PAGO CONFIG
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")

if MP_ACCESS_TOKEN:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
else:
    class FakeSDK:
        def preference(self): return self
        def payment(self): return self
        def create(self, data): return {"response": {"id": "fake", "init_point": "#"}}
        def get(self, id): return {"response": {"status": "approved"}}
    sdk = FakeSDK()

# SMTP EMAIL CONFIG (NOVOS)
SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))

# SSL IGNORE
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_ROLE_ID = "92676066-7506-4c16-9177-3bc0a7530b30" 

# =========================================================================
# 2. FUNÇÕES AUXILIARES
# =========================================================================

def directus_request(method, endpoint, data=None, params=None):
    """Wrapper para chamadas ao Directus"""
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

def enviar_email(destinatario, link_recuperacao):
    """Envia e-mail de recuperação usando SMTP"""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("⚠️ SMTP não configurado nas variáveis de ambiente.")
        return False

    msg = MIMEMultipart()
    msg['From'] = SMTP_EMAIL
    msg['To'] = destinatario
    msg['Subject'] = "Recuperação de Senha - Divide o PIX"

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
            <h2 style="color: #10B981;">Divide o Pix</h2>
            <p>Olá,</p>
            <p>Recebemos uma solicitação para redefinir sua senha.</p>
            <p>Clique no botão abaixo para criar uma nova senha:</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{link_recuperacao}" style="background-color: #10B981; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold;">Criar Nova Senha</a>
            </div>
            <p style="font-size: 12px; color: #999;">Se você não solicitou isso, ignore este e-mail.</p>
        </div>
      </body>
    </html>
    """
    msg.attach(MIMEText(html, 'html'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, destinatario, msg.as_string())
        server.quit()
        print(f"✅ E-mail enviado para {destinatario}")
        return True
    except Exception as e:
        print(f"❌ Erro ao enviar e-mail: {e}")
        return False

# =========================================================================
# 3. ROTAS DE PÁGINAS (FRONTEND)
# =========================================================================

@app.route('/')
@app.route('/confras.html')
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

    limit = tenant.get('guest_limit', 20)
    usage_percent = (len(confirmed) / limit) * 100 if limit > 0 else 0
    limit_warning = usage_percent >= 80 and usage_percent < 100
    limit_reached = usage_percent >= 100

    return render_template(
        "admin.html", 
        tenant=tenant, 
        guests_pending=pending, 
        guests_confirmed=confirmed,
        limit_warning=limit_warning,
        limit_reached=limit_reached
    )

@app.route('/<slug>')
def festa_view(slug):
    ignored_routes = ['admin', 'api', 'login', 'login.html', 'confras.html', 'static', 'favicon.ico', 'robots.txt']
    
    if slug in ignored_routes or slug.startswith('api') or '.' in slug:
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

# --- LÓGICA DE RESET DE SENHA ---

@app.route('/api/request_reset', methods=['POST'])
def request_reset():
    email = request.json.get('email')
    if not email: return jsonify({"message": "Informe o e-mail"}), 400

    # Verifica se o usuário existe
    resp = directus_request('GET', '/items/tenants', params={'filter[email][_eq]': email})
    if not resp or not resp.get('data'):
        # Retorna sucesso fake por segurança
        return jsonify({"status": "success", "message": "Se o e-mail existir, um link foi enviado."})

    # Gera token seguro
    s = URLSafeTimedSerializer(app.secret_key)
    token = s.dumps(email, salt='recover-key')
    
    # Gera o link
    reset_link = f"{BASE_URL}/login.html?reset_token={token}"
    
    # Envia e-mail real usando as variáveis de ambiente
    enviado = enviar_email(email, reset_link)
    
    if enviado:
        return jsonify({"status": "success", "message": "Link de recuperação enviado para seu e-mail."})
    else:
        # Fallback para debug (mostra no console se o envio falhar)
        print(f"LINK DEBUG: {reset_link}")
        return jsonify({"status": "success", "message": "Link gerado (verifique o console do servidor - erro SMTP)."})

@app.route('/api/reset_password_confirm', methods=['POST'])
def reset_password_confirm():
    token = request.json.get('token')
    new_password = request.json.get('password')
    
    if not token or not new_password:
        return jsonify({"message": "Dados inválidos"}), 400

    s = URLSafeTimedSerializer(app.secret_key)
    try:
        email = s.loads(token, salt='recover-key', max_age=3600)
    except:
        return jsonify({"message": "Link inválido ou expirado."}), 400

    # Busca e atualiza
    resp = directus_request('GET', '/items/tenants', params={'filter[email][_eq]': email})
    if resp and resp.get('data'):
        user_id = resp['data'][0]['id']
        directus_request('PATCH', f'/items/tenants/{user_id}', data={'senha': new_password})
        return jsonify({"status": "success", "message": "Senha atualizada com sucesso!"})
    
    return jsonify({"message": "Usuário não encontrado."}), 404

# ---------------------------------------

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
            "whatsapp": request.form.get('whatsapp'),
            "payment_proof_url": file_id,
            "status": "PENDING"
        })

        return jsonify({"status": "success", "message": "Comprovante enviado com sucesso!"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/create_tenant_free', methods=['POST'])
def create_tenant_free():
    data = request.get_json()
    slug = re.sub(r'[^a-z0-9-]', '', data.get('subdomain', '').lower())
    plan = data.get('plan') 
    
    valor_pix_raw = data.get('valor_pix')
    valor_pix_final = None
    if valor_pix_raw:
        try:
            clean_val = str(valor_pix_raw).replace('R$', '').replace(' ', '').replace(',', '.')
            valor_pix_final = float(clean_val)
        except:
            valor_pix_final = None

    if not slug: 
        return jsonify({"status": "error", "message": "Link inválido"}), 400

    tenant_payload = {
        "company_name": data.get('company_name'),
        "subdomain": slug,
        "email": data.get('email'),
        "pix_key": data.get('pix_key'),
        "valor_pix": valor_pix_final,
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
        
        if plan in ['plus', 'pro']:
            try:
                price = 9.99 if plan == 'plus' else 17.99
                title = f"Upgrade Divide o Pix {plan.capitalize()}"
                preference_data = {
                    "items": [{"title": title, "quantity": 1, "unit_price": price, "currency_id": "BRL"}],
                    "external_reference": str(new_id),
                    "back_urls": {
                        "success": f"{BASE_URL}/admin?email={data.get('email')}",
                        "failure": BASE_URL, 
                        "pending": BASE_URL
                    },
                    "auto_return": "approved",
                    "notification_url": f"{BASE_URL}/api/webhook/payment_success"
                }
                mp_response = sdk.preference().create(preference_data)
                return jsonify({"status": "success", "checkout_url": mp_response["response"].get("init_point"), "tenant_id": new_id})
            except Exception as e:
                print(f"Erro MP Create: {e}")
                return jsonify({"status": "success", "url": f"/{slug}", "admin_token": "autologin", "message": "Conta criada, mas erro ao gerar pagamento. Plano Free ativo."})

        return jsonify({"status": "success", "url": f"/{slug}", "admin_token": "autologin"})
    
    return jsonify({"status": "error", "message": "Erro ao criar (Link ou Email já em uso?)"}), 400

@app.route('/api/create_upgrade_preference', methods=['POST'])
def create_upgrade_preference():
    data = request.get_json()
    tenant_id = data.get('tenant_id')
    plan = data.get('plan')
    email = data.get('email')

    if not tenant_id or not plan:
        return jsonify({"error": "Dados incompletos"}), 400

    price = 9.99 if plan == 'plus' else 17.99
    title = f"Upgrade {plan.capitalize()} - Aumento de Limite"

    try:
        preference_data = {
            "items": [{"title": title, "quantity": 1, "unit_price": price, "currency_id": "BRL"}],
            "external_reference": str(tenant_id),
            "back_urls": {
                "success": f"{BASE_URL}/admin?email={email}",
                "failure": f"{BASE_URL}/admin?email={email}",
                "pending": f"{BASE_URL}/admin?email={email}"
            },
            "auto_return": "approved",
            "notification_url": f"{BASE_URL}/api/webhook/payment_success"
        }
        mp_response = sdk.preference().create(preference_data)
        return jsonify({"checkout_url": mp_response["response"].get("init_point")})
    except Exception as e:
        print(f"Erro MP Upgrade: {e}")
        return jsonify({"error": "Erro ao gerar pagamento"}), 500

@app.route('/api/admin/update_guest', methods=['POST'])
def admin_update_guest():
    data = request.get_json()
    guest_id = data.get('guest_id')
    new_status = data.get('status') 
    if not guest_id: return jsonify({"error": "ID faltando"}), 400
    resp = directus_request('PATCH', f'/items/vaquinha_guests/{guest_id}', data={"status": new_status})
    if resp: return jsonify({"status": "success"}), 200
    return jsonify({"error": "Falha ao atualizar"}), 500

@app.route('/api/webhook/payment_success', methods=['POST'])
def webhook_payment():
    try:
        topic = request.args.get('topic') or request.args.get('type')
        payment_id = request.args.get('data.id') or request.args.get('id')

        if not payment_id and request.is_json:
            json_data = request.get_json()
            if json_data.get('action') == 'payment.created' or json_data.get('type') == 'payment':
                payment_id = json_data.get('data', {}).get('id')

        if not payment_id: return jsonify({"status": "ignored"}), 200

        payment_info = sdk.payment().get(payment_id)
        if payment_info["status"] == 200:
            payment_data = payment_info["response"]
            status = payment_data.get("status")
            external_ref = payment_data.get("external_reference")
            transaction_amount = float(payment_data.get("transaction_amount", 0))

            if status == 'approved' and external_ref:
                new_plan = 'plus'
                new_limit = 50
                if transaction_amount > 15:
                    new_plan = 'pro'
                    new_limit = 100
                directus_request('PATCH', f'/items/tenants/{external_ref}', data={"plan_type": new_plan, "guest_limit": new_limit})

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)