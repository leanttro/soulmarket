from flask import Flask, render_template, request, abort
import requests
import os
import urllib3

# Desabilita aquele aviso chato de segurança no log
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# --- CONFIGURAÇÃO ---
# Pega do ambiente ou usa um valor padrão se não achar
DIRECTUS_URL = os.getenv("DIRECTUS_URL", "https://soul-market-directussoulmarket-wlthkj-7a0857-213-199-56-207.traefik.me")

def get_tenant_data(subdomain):
    """
    Vai no Directus e busca os dados da loja pelo subdomínio.
    """
    url = f"{DIRECTUS_URL}/items/tenants"
    
    params = {
        "filter[subdomain][_eq]": subdomain,
        "limit": 1
    }
    
    try:
        # AQUI ESTÁ A CORREÇÃO: verify=False ignora o erro do certificado
        response = requests.get(url, params=params, verify=False)
        data = response.json()
        
        if data.get('data'):
            return data['data'][0]
        return None
    except Exception as e:
        print(f"Erro ao conectar no Directus: {e}")
        return None

def get_products(tenant_id):
    """
    Busca os produtos daquela loja específica
    """
    url = f"{DIRECTUS_URL}/items/products"
    params = {
        "filter[tenant_id][_eq]": tenant_id,
        "filter[status][_eq]": "published"
    }
    try:
        # AQUI TAMBÉM: verify=False
        response = requests.get(url, params=params, verify=False)
        return response.json().get('data', [])
    except Exception as e:
        print(f"Erro ao buscar produtos: {e}")
        return []

@app.route('/')
def home():
    # 1. Descobre o subdomínio
    host = request.headers.get('Host')
    
    # Lógica para pegar o subdomínio
    if 'localhost' in host or '127.0.0.1' in host:
        subdomain = host.split('.')[0]
        if subdomain == 'localhost': 
            subdomain = 'padaria'
    else:
        # Em produção, pega a primeira parte da URL
        subdomain = host.split('.')[0]

    print(f"DEBUG: Tentando carregar loja para subdomínio: {subdomain}")

    # 2. Busca a Loja
    tenant = get_tenant_data(subdomain)
    
    if not tenant:
        return f"<h1>Loja não encontrada (404)</h1><p>Não achamos a loja: <strong>{subdomain}</strong> no Directus.</p><p>Verifique se o 'subdomain' está igualzinho lá.</p>", 404

    # 3. Busca os Produtos
    products = get_products(tenant['id'])

    # 4. Renderiza
    return render_template('home.html', tenant=tenant, products=products)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
