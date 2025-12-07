import os
import requests
from flask import Flask, render_template, request
import urllib3

# Desabilita alertas de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# Tenta pegar do ambiente, mas se falhar usa o IP direto (SEM HTTPS)
# Isso resolve 99% dos problemas de conexão interna no Dokploy
DIRECTUS_URL = os.getenv("DIRECTUS_URL", "http://213.199.56.207:8055")

# Garante que a URL não termine com barra /
if DIRECTUS_URL.endswith('/'):
    DIRECTUS_URL = DIRECTUS_URL[:-1]

@app.route('/')
def home():
    # 1. Identifica o subdomínio
    host = request.headers.get('Host', '')
    if 'localhost' in host or '127.0.0.1' in host:
        subdomain = 'teste'
    else:
        subdomain = host.split('.')[0]

    # 2. Tenta conectar no Directus
    try:
        url_tenants = f"{DIRECTUS_URL}/items/tenants"
        params = {"filter[subdomain][_eq]": subdomain}
        
        # O verify=False ignora erro de SSL
        # O timeout=5 evita que o site fique carregando pra sempre se travar
        response = requests.get(url_tenants, params=params, verify=False, timeout=5)
        
        # SE O DIRECTUS DER ERRO (Tipo 403 Proibido ou 500 Erro), para aqui
        if response.status_code != 200:
            return f"""
            <h1>ERRO NO DIRECTUS: {response.status_code}</h1>
            <p>O Directus recusou a conexão.</p>
            <p><strong>Motivo:</strong> {response.text}</p>
            <p>Verifique se a Role PUBLIC tem permissão de LEITURA na tabela Tenants.</p>
            """, 500

        data = response.json()
        
        # Se conectou, mas a lista veio vazia (não achou a loja)
        if not data.get('data'):
             return f"""
            <h1>Loja não encontrada (404)</h1>
            <p>O sistema conectou no Directus, mas não achou nenhuma loja com o subdomínio: <strong>{subdomain}</strong></p>
            <p>Confira no Directus > Tenants se o campo 'subdomain' é exatamente: <code>{subdomain}</code></p>
            """, 404

        # Se achou, carrega a loja
        tenant = data['data'][0]
        
        # Busca produtos
        prod_resp = requests.get(
            f"{DIRECTUS_URL}/items/products",
            params={"filter[tenant_id][_eq]": tenant['id']},
            verify=False
        )
        products = prod_resp.json().get('data', [])
        
        # --- ALTERAÇÃO AQUI: Lógica para selecionar o template ---
        
        # 1. Tenta obter template_name, usando 'home' como padrão se for vazio ou não existir
        template_base_name = tenant.get('template_name') or 'home'
        # 2. Constrói o nome completo do arquivo com a extensão .html
        template_file_name = f"{template_base_name}.html"
        
        # 3. Renderiza o template escolhido
        return render_template(template_file_name, tenant=tenant, products=products)
        # --- FIM DA ALTERAÇÃO ---
            
    except Exception as e:
        # MOSTRA O ERRO REAL NA TELA
        return f"""
        <h1>ERRO CRÍTICO DE CONEXÃO</h1>
        <p>O Flask não conseguiu falar com o Directus.</p>
        <p><strong>URL tentada:</strong> {DIRECTUS_URL}</p>
        <p><strong>Erro Técnico:</strong> {str(e)}</p>
        <hr>
        <h3>Solução:</h3>
        <p>Vá no Dokploy > Frontend > Environment e mude a variável DIRECTUS_URL para:</p>
        <code>http://213.199.56.207:8055</code>
        """, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)