from flask import Flask, render_template, request, abort
import requests

app = Flask(__name__)

# --- CONFIGURAÇÃO ---
# URL do seu Directus (Aquele que você acabou de instalar)
# Se estiver rodando local, use o endereço do Dokploy
DIRECTUS_URL = "https://soul-market-directussoulmarket-wlthkj-7a0857-213-199-56-207.traefik.me" 

def get_tenant_data(subdomain):
    """
    Vai no Directus e busca os dados da loja pelo subdomínio.
    """
    url = f"{DIRECTUS_URL}/items/tenants"
    
    # Filtro mágico do Directus: ?filter[subdomain][_eq]=padaria
    params = {
        "filter[subdomain][_eq]": subdomain,
        "limit": 1
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        # Se achou a loja, retorna os dados dela
        if data['data']:
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
        "filter[status][_eq]": "published" # Só traz o que tá ativo
    }
    response = requests.get(url, params=params)
    return response.json()['data']

@app.route('/')
def home():
    # 1. Descobre o subdomínio (Ex: padaria.localhost -> padaria)
    host = request.headers.get('Host')
    
    # Lógica simples para pegar o subdomínio (ajustar conforme seu domínio real)
    if 'localhost' in host or '127.0.0.1' in host:
        # Para testes locais, vamos forçar um subdomínio ou pegar da URL
        # Ex: padaria.localhost:5000
        subdomain = host.split('.')[0]
        if subdomain == 'localhost': 
            subdomain = 'padaria' # Fallback para teste
    else:
        # Em produção (padaria.soulmarket.com.br)
        subdomain = host.split('.')[0]

    # 2. Busca a Loja
    tenant = get_tenant_data(subdomain)
    
    if not tenant:
        return "<h1>Loja não encontrada (404)</h1><p>Verifique o endereço.</p>", 404

    # 3. Busca os Produtos dessa Loja
    products = get_products(tenant['id'])

    # 4. Renderiza o HTML passando as cores e dados da loja
    return render_template('home.html', tenant=tenant, products=products)

if __name__ == '__main__':
    app.run(debug=True, port=5000)