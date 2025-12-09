import os
import requests
import json
import re
import smtplib
import urllib3
import mercadopago # pip install mercadopago
from flask import Flask, render_template, request, jsonify, redirect, url_for
from email.mime.text import MIMEText

app = Flask(__name__)

# =========================================================================
# 1. CONFIGURAÇÕES E CREDENCIAIS
# =========================================================================

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chave_secreta_padrao")
BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000") # Ajuste para local se precisar

# Directus (CONFIGURE SEUS DADOS AQUI SE NÃO ESTIVER USANDO ENV)
DIRECTUS_TOKEN = os.environ.get("DIRECTUS_TOKEN", "SEU_TOKEN_DIRECTUS")
DIRECTUS_URL = os.environ.get("DIRECTUS_URL", "https://seu-diretus.com")

# Mercado Pago (CONFIGURE SEU TOKEN AQUI)
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "TEST-seu-token-aqui") 
if MP_ACCESS_TOKEN:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
else:
    print("⚠️ AVISO: MP_ACCESS_TOKEN não configurado. Pagamentos Pro não funcionarão.")

# Email (SMTP Gmail)
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")

# SSL Verify
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ID da role "Loja Admin" no Directus
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
            
        return r.json() if r.status_code in [200, 201] else None
    except Exception as e:
        print(f"❌ Erro Directus ({endpoint}): {e}")
        return None

def send_welcome_email(to_email, link, senha):
    if not SMTP_USER or not SMTP_PASS:
        print(f"⚠️ Email não configurado. Log: Para {to_email}, Senha {senha}")
        return

    msg = MIMEText(f"""
    Bem-vindo ao Divide o Pix!
    
    Seu painel administrativo PRO está pronto.
    
    🔗 Painel: {link}
    👤 Login: {to_email}
    🔑 Senha: {senha}
    """)
    
    msg['Subject'] = "Acesso PRO - Divide o Pix"
    msg['From'] = SMTP_USER
    msg['To'] = to_email

    try:
        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [to_email], msg.as_string())
        s.quit()
    except Exception as e:
        print(f"❌ Erro envio email: {e}")

# =========================================================================
# 3. ROTAS DO SITE (VISUALIZAÇÃO)
# =========================================================================

@app.route('/')
def home():
    return render_template("confras.html")

@app.route('/festa/<slug>')
def festa_view(slug):
    # 1. Busca tenant
    data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
    
    if not data or not data.get('data'):
         return "<h1>Evento não encontrado (404)</h1>", 404

    tenant = data['data'][0]
    
    # 2. REDIRECIONAMENTO PARA ADMIN
    if request.args.get('admin'):
        return redirect(url_for('admin_panel', email=tenant['email']))

    # 3. Carrega a festa normal
    guests_data = directus_request('GET', '/items/vaquinha_guests', params={
        "filter[tenant_id][_eq]": tenant['id'],
        "sort": "-created_at"
    })
    guests = guests_data.get('data', []) if guests_data else []
    
    confirmed_guests = [g for g in guests if g.get('status') == 'CONFIRMED']
    confirmed_count = len(confirmed_guests)
    
    return render_template(
        "vaquinha.html", 
        tenant=tenant,
        guests_confirmed=confirmed_guests,
        is_limit_reached=(confirmed_count >= tenant.get('guest_limit', 20)),
        current_slug=slug
    )

@app.route('/admin')
def admin_panel():
    email_param = request.args.get('email')
    
    if not email_param:
        return "<h1>Acesso Negado</h1><p>Link inválido. Use o link recebido no cadastro.</p>", 403

    t_data = directus_request('GET', '/items/tenants', params={"filter[email][_eq]": email_param})
    
    if not t_data or not t_data.get('data'):
         return "Usuário não encontrado.", 404

    tenant = t_data['data'][0]
    
    guests_req = directus_request('GET', '/items/vaquinha_guests', params={
        "filter[tenant_id][_eq]": tenant['id'],
        "sort": "-created_at"
    })
    all_guests = guests_req.get('data', [])
    
    pending = [g for g in all_guests if g.get('status') == 'PENDING']
    confirmed = [g for g in all_guests if g.get('status') == 'CONFIRMED']

    return render_template(
        "admin.html", 
        tenant=tenant,
        guests_pending=pending,
        guests_confirmed=confirmed
    )

# =========================================================================
# 4. API - CRIAÇÃO E PAGAMENTOS
# =========================================================================

# --- [ADICIONADO] ROTA QUE FALTAVA PARA O MERCADO PAGO ---
@app.route('/api/create_preference', methods=['POST'])
def create_preference():
    try:
        if not MP_ACCESS_TOKEN:
            return jsonify({"error": "Configuração de pagamento ausente"}), 500

        data = request.json
        plan = data.get('plan')
        
        if plan == 'plus':
            title = "Plano PIX Plus"
            price = 9.99
        elif plan == 'pro':
            title = "Plano PIX Pro"
            price = 17.99
        else:
            return jsonify({"error": "Plano inválido"}), 400

        preference_data = {
            "items": [{"title": title, "quantity": 1, "unit_price": price, "currency_id": "BRL"}],
            "back_urls": {
                "success": f"{BASE_URL}/admin", 
                "failure": f"{BASE_URL}",
                "pending": f"{BASE_URL}"
            },
            "auto_return": "approved"
        }

        preference_response = sdk.preference().create(preference_data)
        preference = preference_response["response"]
        return jsonify({"id": preference["id"]})

    except Exception as e:
        print(f"Erro MP: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/create_tenant_free', methods=['POST'])
def create_tenant_free():
    data = request.get_json()
    raw_slug = data.get('subdomain', '').lower()
    slug = re.sub(r'[^a-z0-9-]', '', raw_slug)

    if not slug: return jsonify({"status": "error", "message": "Link inválido"}), 400

    check = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
    if check and check.get('data'):
        return jsonify({"status": "error", "message": "Este link já existe."}), 400

    tenant_payload = {
        "company_name": data.get('company_name'),
        "subdomain": slug,
        "email": data.get('email'),
        "pix_key": data.get('pix_key'),
        "pix_owner_name": "Organizador",
        "status": "active",
        "plan_type": "free",
        "guest_limit": 20,
        "template_name": "vaquinha"
    }
    
    resp = directus_request('POST', '/items/tenants', data=tenant_payload)
    
    if resp and resp.get('data'):
        new_id = resp['data']['id']
        directus_request('POST', '/items/users', data={
            "tenant_id": new_id, 
            "email": data.get('email'), 
            "password": data.get('password', 'mudar123'), 
            "role": USER_ROLE_ID 
        })
        
        return jsonify({
            "status": "success",
            "url": f"/festa/{slug}", # Ajustado para sua rota interna
            "admin_token": "autologin" 
        })
    
    return jsonify({"status": "error", "message": "Erro no banco de dados"}), 500


@app.route('/api/webhook/payment_success', methods=['POST'])
def webhook_payment():
    data = request.get_json()
    
    if data.get("type") == "payment":
        payment_id = data.get("data", {}).get("id")
        
        try:
            payment_info = sdk.payment().get(payment_id)
            payment = payment_info.get("response", {})
            
            if payment.get("status") == "approved":
                payer_email = payment.get("payer", {}).get("email")
                company_name = payment.get("external_reference", "Nova Festa Pro")
                
                slug = re.sub(r'[^a-z0-9]', '', company_name.lower())[:15] + "-pro"
                # Evita colisão de slug
                check = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
                if check and check.get('data'):
                    slug += str(payment_id)[-4:]

                temp_password = "pro" + os.urandom(4).hex()

                tenant_payload = {
                    "company_name": company_name,
                    "subdomain": slug,
                    "email": payer_email,
                    "status": "active",
                    "plan_type": "pro",
                    "guest_limit": 200,
                    "pix_owner_name": "Admin",
                    "template_name": "vaquinha"
                }

                # Cria apenas se não existir por email
                check_email = directus_request('GET', '/items/tenants', params={"filter[email][_eq]": payer_email})
                if not check_email or not check_email.get('data'):
                    resp = directus_request('POST', '/items/tenants', data=tenant_payload)
                    if resp and resp.get('data'):
                        new_id = resp['data']['id']
                        directus_request('POST', '/items/users', data={
                            "tenant_id": new_id, 
                            "email": payer_email, 
                            "password": temp_password, 
                            "role": USER_ROLE_ID
                        })
                        
                        admin_link = f"{BASE_URL}/admin?email={payer_email}"
                        send_welcome_email(payer_email, admin_link, temp_password)
                        return jsonify({"status": "created"}), 201

        except Exception as e:
            print(f"Erro Webhook: {e}")
            return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ignored"}), 200

# =========================================================================
# 5. API - AÇÕES DE USUÁRIO E ADMIN
# =========================================================================

# --- [MODIFICADO] PARA RETORNAR JSON (AJUSTE PARA O LOADING JS) ---
@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    # Tenta pegar do form, se não, tenta do referrer como fallback
    origin_slug = request.form.get('origin_slug')
    
    # Lógica de fallback se o hidden input falhar
    if not origin_slug and request.referrer:
        # Ex: http://site.com/festa/churras -> churras
        parts = request.referrer.split('/')
        if 'festa' in parts:
            idx = parts.index('festa')
            if len(parts) > idx + 1:
                origin_slug = parts[idx + 1].split('?')[0]
    
    if not origin_slug: 
        return jsonify({"status": "error", "message": "Link inválido"}), 400

    # Busca o tenant
    t_data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": origin_slug})
    if not t_data or not t_data.get('data'): 
        return jsonify({"status": "error", "message": "Evento não encontrado"}), 404
    
    tenant = t_data['data'][0]
    
    # Upload
    guest_name = request.form.get('name')
    proof_file = request.files.get('proof')
    file_id = None
    
    try:
        if proof_file:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            h_auth = {"Authorization": f"Bearer {DIRECTUS_TOKEN}"}
            # Upload direto via requests (para lidar com multipart form data melhor que o wrapper)
            up = requests.post(f"{DIRECTUS_URL}/files", files=files, headers=h_auth, verify=VERIFY_SSL)
            if up.status_code in [200, 201]: 
                file_id = up.json()['data']['id']
            else:
                return jsonify({"status": "error", "message": "Erro no upload da imagem"}), 500

        # Cria o convidado
        directus_request('POST', '/items/vaquinha_guests', data={
            "tenant_id": tenant['id'],
            "name": guest_name,
            "payment_proof_url": file_id,
            "status": "PENDING"
        })

        # RETORNA JSON para o Javascript funcionar
        return jsonify({"status": "success", "message": "Comprovante enviado com sucesso!"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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