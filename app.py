import os
import requests
from flask import Flask, render_template, request
import urllib3

# ISSO AQUI QUE RESOLVE O PROBLEMA DO "INSEGURO"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# Pega a URL que você configurou no Dokploy
DIRECTUS_URL = os.getenv("DIRECTUS_URL")

@app.route('/')
def home():
    # 1. Pega o subdomínio (ex: 'teste' de teste.leanttro.com)
    host = request.headers.get('Host', '')
    if 'localhost' in host or '127.0.0.1' in host:
        subdomain = 'teste' # Fallback pra teste local
    else:
        subdomain = host.split('.')[0] # Pega 'teste'

    # 2. Tenta conectar no Directus
    try:
        # --- A MÁGICA ESTÁ AQUI: verify=False ---
        # Sem isso, o Python bloqueia a conexão e dá erro 404
        response = requests.get(
            f"{DIRECTUS_URL}/items/tenants", 
            params={"filter[subdomain][_eq]": subdomain},
            verify=False 
        )
        
        data = response.json()
        
        # Se achou a loja, mostra o site
        if data.get('data'):
            tenant = data['data'][0]
            # Busca produtos (também com verify=False)
            prod_resp = requests.get(
                f"{DIRECTUS_URL}/items/products",
                params={"filter[tenant_id][_eq]": tenant['id']},
                verify=False
            )
            products = prod_resp.json().get('data', [])
            
            return render_template('home.html', tenant=tenant, products=products)
            
    except Exception as e:
        print(f"ERRO DE CONEXÃO: {e}") # Isso aparece no Log do Dokploy

    # Se falhou a conexão ou não achou a loja:
    return f"""
    <h1>Loja não encontrada (404)</h1>
    <p>Buscamos por: <strong>{subdomain}</strong></p>
    <p>Se o nome está certo no Directus, o erro é de CONEXÃO (SSL).</p>
    """, 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
