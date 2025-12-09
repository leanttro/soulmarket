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
# 1. CONFIGURAÇÕES E CREDENCIAIS (Variáveis de Ambiente)
# =========================================================================

# Flask
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sua_chave_secreta_aqui")
BASE_URL = os.environ.get("APP_BASE_URL", "https://confras.leanttro.com")

# Directus (Banco de Dados)
DIRECTUS_TOKEN = os.environ.get("DIRECTUS_TOKEN")
DIRECTUS_URL = os.environ.get("DIRECTUS_URL", "https://directus.leanttro.com")

# Mercado Pago (Pagamentos)
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
if MP_ACCESS_TOKEN:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
else:
    print("⚠️ AVISO: MP_ACCESS_TOKEN não configurado. Pagamentos não funcionarão.")

# Email (SMTP Gmail)
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")

# SSL Verify (Ajuste para False se tiver problemas internos no Dokploy)
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================================================================
# 2. FUNÇÕES AUXILIARES (Helpers)
# =========================================================================

def directus_request(method, endpoint, data=None, params=None):
    """Função central para falar com o banco de dados"""
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
    """Envia o email com acesso ao painel"""
    if not SMTP_USER or not SMTP_PASS:
        print("⚠️ Email não configurado. Apenas logando envio.")
        print(f"--> Email para: {to_email} | Senha: {senha}")
        return

    msg = MIMEText(f"""
    Olá! Seu pagamento foi confirmado e seu evento PRO está ativo.
    
    Acesse seu painel administrativo para configurar sua chave PIX e ver os convidados:
    
    🔗 Link: {link}
    👤 Login: {to_email}
    🔑 Senha Provisória: {senha}
    
    Obrigado por usar o Divide o Pix!
    """)
    
    msg['Subject'] = "Acesso Liberado - Divide o Pix PRO"
    msg['From'] = SMTP_USER
    msg['To'] = to_email

    try:
        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [to_email], msg.as_string())
        s.quit()
        print(f"✅ Email enviado com sucesso para {to_email}")
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")

# =========================================================================
# 3. ROTAS DO FRONTEND (Páginas)
# =========================================================================

@app.route('/')
def home():
    return render_template("confras.html") # Sua Landing Page

@app.route('/festa/<slug>')
def festa_view(slug):
    # Busca o evento pelo slug
    data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
    
    if not data or not data.get('data'):
         return "<h1>Evento não encontrado (404)</h1><p>Verifique o link digitado.</p>", 404

    tenant = data['data'][0]
    
    # Busca convidados
    guests_data = directus_request('GET', '/items/vaquinha_guests', params={
        "filter[tenant_id][_eq]": tenant['id'],
        "sort": "-created_at"
    })
    guests = guests_data.get('data', []) if guests_data else []
    
    # Contagem de confirmados
    confirmed_guests = [g for g in guests if g.get('status') == 'CONFIRMED']
    confirmed_count = len(confirmed_guests)
    
    return render_template(
        "vaquinha.html", 
        tenant=tenant,
        guests_confirmed=confirmed_guests,
        is_limit_reached=(confirmed_count >= tenant.get('guest_limit', 20)),
        current_slug=slug # Usado para enviar o form
    )

# =========================================================================
# 4. API - GERAÇÃO DE LINK DE PAGAMENTO (PRO)
# =========================================================================

@app.route('/api/create_preference', methods=['POST'])
def create_preference():
    """Cria o checkout no Mercado Pago e devolve o link"""
    data = request.get_json()
    user_email = data.get('email')
    company_name = data.get('company_name')

    if not user_email or not company_name:
        return jsonify({"error": "Dados incompletos"}), 400

    # Criação da preferência de pagamento
    preference_data = {
        "items": [
            {
                "title": "Plano PRO - Divide o Pix",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": 49.90 # SEU PREÇO AQUI
            }
        ],
        "payer": {"email": user_email},
        "back_urls": {
            "success": f"{BASE_URL}/admin?status=success",
            "failure": f"{BASE_URL}/?status=failure",
            "pending": f"{BASE_URL}/?status=pending"
        },
        "auto_return": "approved",
        "external_reference": company_name, # Passamos o nome da empresa aqui para recuperar no webhook
        "notification_url": f"{BASE_URL}/api/webhook/payment_success" # Onde o MP avisa
    }

    try:
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response["response"]
        return jsonify({"checkout_url": preference["init_point"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================================================================
# 5. API - CRIAÇÃO DE CONTA (FREE vs WEBHOOK PRO)
# =========================================================================

@app.route('/api/create_tenant_free', methods=['POST'])
def create_tenant_free():
    """Cria conta Grátis via formulário do site"""
    data = request.get_json()
    raw_slug = data.get('subdomain', '').lower()
    slug = re.sub(r'[^a-z0-9-]', '', raw_slug)

    # Verifica se já existe
    check = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
    if check and check.get('data'):
        return jsonify({"error": "Este link já está em uso."}), 400

    # Cria Tenant Free
    tenant_payload = {
        "company_name": data.get('company_name'),
        "pix_owner_name": data.get('organizer_name'),
        "subdomain": slug,
        "email": data.get('email'),
        "status": "active",
        "plan_type": "free",
        "guest_limit": 20,
        "template_name": "vaquinha"
    }
    
    resp = directus_request('POST', '/items/tenants', data=tenant_payload)
    
    if resp and resp.get('data'):
        new_id = resp['data']['id']
        # Cria Usuário
        directus_request('POST', '/items/users', data={
            "tenant_id": new_id, 
            "email": data.get('email'), 
            "password": data.get('password', 'mudar123'), 
            "role": "ESCOLHA_O_ID_DA_ROLE_NO_DIRECTUS" 
        })
        return jsonify({"status": "success", "redirect_url": f"{BASE_URL}/admin"})
    
    return jsonify({"error": "Erro ao criar conta"}), 500

@app.route('/api/webhook/payment_success', methods=['POST'])
def webhook_payment():
    """Recebe aviso do Mercado Pago e cria conta PRO"""
    data = request.get_json()
    
    # Valida se é notificação de pagamento
    if data.get("type") == "payment":
        payment_id = data.get("data", {}).get("id")
        
        # CONSULTA SEGURA NA API DO MP
        try:
            payment_info = sdk.payment().get(payment_id)
            payment = payment_info.get("response", {})
            
            if payment.get("status") == "approved":
                payer_email = payment.get("payer", {}).get("email")
                company_name = payment.get("external_reference", "Empresa Pro")
                
                # Gera slug único
                slug = re.sub(r'[^a-z0-9]', '', company_name.lower())[:15] + "-pro"
                # Verifica colisão de slug
                check_slug = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
                if check_slug and check_slug.get('data'):
                    slug = slug + str(payment_id)[-4:] # Adiciona sufixo se já existir
                
                temp_password = "pro" + os.urandom(4).hex()

                # Cria Tenant PRO
                tenant_payload = {
                    "company_name": company_name,
                    "subdomain": slug,
                    "email": payer_email,
                    "status": "active",
                    "plan_type": "pro",
                    "guest_limit": 200, # Limite maior
                    "pix_owner_name": "Admin",
                }

                # Evita duplicidade por email
                check_email = directus_request('GET', '/items/tenants', params={"filter[email][_eq]": payer_email})
                if not check_email or not check_email.get('data'):
                    resp = directus_request('POST', '/items/tenants', data=tenant_payload)
                    if resp and resp.get('data'):
                        new_id = resp['data']['id']
                        directus_request('POST', '/items/users', data={
                            "tenant_id": new_id, 
                            "email": payer_email, 
                            "password": temp_password, 
                            "role": "ESCOLHA_O_ID_DA_ROLE_NO_DIRECTUS"
                        })
                        
                        send_welcome_email(payer_email, f"{BASE_URL}/admin", temp_password)
                        return jsonify({"status": "created"}), 201
                else:
                    return jsonify({"status": "exists"}), 200

        except Exception as e:
            print(f"Erro webhook: {e}")
            return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ignored"}), 200

# =========================================================================
# 6. API - CONFIRMAÇÃO DE CONVIDADO (Upload)
# =========================================================================

@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    # Tenta pegar o slug do form hidden ou do header
    # IMPORTANTE: Adicione <input type="hidden" name="origin_slug" value="{{ current_slug }}"> no HTML
    origin_slug = request.form.get('origin_slug')
    if not origin_slug:
         # Fallback para o header referer
         if request.referrer:
             origin_slug = request.referrer.split('/')[-1]
    
    if not origin_slug: return "Erro: Link inválido", 400

    guest_name = request.form.get('name')
    proof_file = request.files.get('proof')

    # Busca Tenant
    t_data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": origin_slug})
    if not t_data or not t_data.get('data'): return "Evento não encontrado", 404
    tenant = t_data['data'][0]

    # Validação de Limite no Backend
    guests_req = directus_request('GET', '/items/vaquinha_guests', params={"filter[tenant_id][_eq]": tenant['id']})
    guests = guests_req.get('data', [])
    confirmed_count = len([g for g in guests if g['status'] == 'CONFIRMED'])
    
    if confirmed_count >= tenant.get('guest_limit', 20):
        return f"<h1>Limite Atingido!</h1><p>Este evento já atingiu o máximo de {tenant.get('guest_limit')} convidados confirmados.</p>", 403

    # Upload do Comprovante
    file_id = None
    if proof_file:
        try:
            files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
            # Requests direto para upload (multipart/form-data)
            h_auth = {"Authorization": f"Bearer {DIRECTUS_TOKEN}"}
            up = requests.post(f"{DIRECTUS_URL}/files", files=files, headers=h_auth, verify=VERIFY_SSL)
            if up.status_code in [200, 201]: 
                file_id = up.json()['data']['id']
        except Exception as e:
            print(f"Erro upload: {e}")

    # Salva Convidado
    directus_request('POST', '/items/vaquinha_guests', data={
        "tenant_id": tenant['id'],
        "name": guest_name,
        "payment_proof_url": file_id,
        "status": "PENDING"
    })

    return redirect(f"/festa/{origin_slug}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)