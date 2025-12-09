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
BASE_URL = os.environ.get("APP_BASE_URL", "https://confras.leanttro.com")

# Directus
DIRECTUS_TOKEN = os.environ.get("DIRECTUS_TOKEN")
DIRECTUS_URL = os.environ.get("DIRECTUS_URL", "https://directus.leanttro.com")

# Mercado Pago
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
if MP_ACCESS_TOKEN:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
else:
    print("⚠️ MP_ACCESS_TOKEN não configurado. Pagamentos Pro não funcionarão.")

# Email (SMTP Gmail)
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")

# SSL Verify (Mantenha True em produção se tiver SSL válido)
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ID DA ROLE (PERMISSÃO) DO USUÁRIO NO DIRECTUS
# Pegue em: Configurações > Funções e Permissões > Clique na Role > Copie o ID da URL
USER_ROLE_ID = "JkKzBvSS9TTu5YYstA-rLmoWeHdU9Eas" 

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
        print(f"⚠️ Email não configurado. Simulação: Para {to_email}, Senha {senha}")
        return

    msg = MIMEText(f"""
    Bem-vindo ao Divide o Pix!
    
    Seu painel administrativo está pronto.
    
    🔗 Painel: {link}
    👤 Login: {to_email}
    🔑 Senha: {senha}
    """)
    
    msg['Subject'] = "Acesso ao Divide o Pix"
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
# 3. ROTAS DE PÁGINAS (FRONTEND)
# =========================================================================

@app.route('/')
def home():
    return render_template("confras.html")

@app.route('/festa/<slug>')
def festa_view(slug):
    # Busca tenant
    data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
    
    if not data or not data.get('data'):
         return "<h1>Evento não encontrado (404)</h1>", 404

    tenant = data['data'][0]
    
    # Busca convidados
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

# =========================================================================
# 4. API - CRIAÇÃO DE CONTA (FREE e PRO)
# =========================================================================

@app.route('/api/create_tenant_free', methods=['POST'])
def create_tenant_free():
    data = request.get_json()
    raw_slug = data.get('subdomain', '').lower()
    slug = re.sub(r'[^a-z0-9-]', '', raw_slug)

    if not slug: return jsonify({"status": "error", "message": "Link inválido"}), 400

    # Verifica duplicidade
    check = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": slug})
    if check and check.get('data'):
        return jsonify({"status": "error", "message": "Este link já existe. Escolha outro."}), 400

    # Cria Tenant Free
    tenant_payload = {
        "company_name": data.get('company_name'),
        "subdomain": slug,
        "email": data.get('email'),
        "pix_key": data.get('pix_key'),      # Salva a chave PIX do form
        "pix_owner_name": "Organizador",     # Padrão
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
            "role": USER_ROLE_ID  # <--- ID DA ROLE AQUI
        })
        
        # Retorna JSON no formato que o JS espera
        return jsonify({
            "status": "success",
            "url": f"{BASE_URL}/festa/{slug}",
            "admin_token": "login"
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
                
                # Gera slug
                slug = re.sub(r'[^a-z0-9]', '', company_name.lower())[:15] + "-pro"
                temp_password = "pro" + os.urandom(4).hex()

                # Tenant PRO
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

                # Cria se não existir email
                check = directus_request('GET', '/items/tenants', params={"filter[email][_eq]": payer_email})
                if not check or not check.get('data'):
                    resp = directus_request('POST', '/items/tenants', data=tenant_payload)
                    if resp and resp.get('data'):
                        new_id = resp['data']['id']
                        directus_request('POST', '/items/users', data={
                            "tenant_id": new_id, 
                            "email": payer_email, 
                            "password": temp_password, 
                            "role": USER_ROLE_ID # <--- ID DA ROLE AQUI
                        })
                        send_welcome_email(payer_email, f"{BASE_URL}/admin", temp_password)
                        return jsonify({"status": "created"}), 201

        except Exception as e:
            print(f"Erro Webhook: {e}")
            return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ignored"}), 200

# =========================================================================
# 5. API - UPLOAD COMPROVANTE
# =========================================================================

@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    # Tenta pegar slug do hidden ou referrer
    origin_slug = request.form.get('origin_slug')
    if not origin_slug and request.referrer:
        origin_slug = request.referrer.split('/')[-1]
    
    if not origin_slug: return "Link inválido", 400

    # Lógica de salvar convidado e upload...
    # (Mantida igual ao anterior, omitida para brevidade mas funcional)
    # Requer implementação completa se não tiver copiado do chat anterior
    # Vou incluir o básico para funcionar:
    
    t_data = directus_request('GET', '/items/tenants', params={"filter[subdomain][_eq]": origin_slug})
    if not t_data or not t_data.get('data'): return "Evento 404", 404
    tenant = t_data['data'][0]
    
    guest_name = request.form.get('name')
    proof_file = request.files.get('proof')
    file_id = None
    
    if proof_file:
        files = {'file': (proof_file.filename, proof_file.stream, proof_file.mimetype)}
        up = requests.post(f"{DIRECTUS_URL}/files", files=files, headers={"Authorization": f"Bearer {DIRECTUS_TOKEN}"}, verify=VERIFY_SSL)
        if up.status_code in [200, 201]: file_id = up.json()['data']['id']

    directus_request('POST', '/items/vaquinha_guests', data={
        "tenant_id": tenant['id'],
        "name": guest_name,
        "payment_proof_url": file_id,
        "status": "PENDING"
    })

    return redirect(f"/festa/{origin_slug}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)