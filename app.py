import os
import requests
from flask import Flask, render_template, request
import urllib3

# Desabilita alertas de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# VARIÁVEIS DE CONEXÃO (Lógica de Múltiplas Tentativas)
# =========================================================================
# 1. Tenta pegar do ambiente (idealmente HTTPS externo)
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
# 2. Endereço IP interno (sem HTTPS) para fallback no Dokploy
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055"

# Lista de URLs para tentar, priorizando o EXTERNO
DIRECTUS_URLS_TO_TRY = [
    DIRECTUS_URL_EXTERNAL,
    DIRECTUS_URL_INTERNAL
]
# =========================================================================

# Garante que a URL não termine com barra /
def clean_url(url):
    if url and url.endswith('/'):
        return url[:-1]
    return url

@app.route('/')
def home():
    # 1. Identifica o subdomínio
    host = request.headers.get('Host', '')
    if 'localhost' in host or '127.0.0.1' in host:
        subdomain = 'teste'
    else:
        # Pega o subdomínio (ex: 'coach' de coach.leanttro.com)
        subdomain = host.split('.')[0] 

    # Variáveis para armazenar o resultado da tentativa
    successful_url = None
    response = None
    last_exception = None

    # 2. Tenta conectar no Directus (Loop de tentativas)
    for current_url in DIRECTUS_URLS_TO_TRY:
        current_url = clean_url(current_url)
        try:
            url_tenants = f"{current_url}/items/tenants"
            params = {"filter[subdomain][_eq]": subdomain}
            
            # O verify=False ignora erro de SSL
            # O timeout=5 evita que o site fique carregando pra sempre se travar
            response = requests.get(url_tenants, params=params, verify=False, timeout=5)
            
            # Se conseguiu uma resposta 200 (Sucesso), usa essa URL e para o loop
            if response.status_code == 200:
                successful_url = current_url
                break
            
            # Se recebeu um código de erro HTTP (ex: 403, 500) do Directus, 
            # não é um erro de conexão. Encerra o loop e trata o erro HTTP abaixo.
            if response.status_code != 200:
                 successful_url = current_url
                 break

        except Exception as e:
            # Captura a exceção de conexão e tenta a próxima URL na lista
            last_exception = e
            continue 
            
    # 3. Trata Falha Total de Conexão
    # Se nenhuma URL estabeleceu conexão (apenas exceções ocorreram)
    if not successful_url:
        return f"""
        <h1>ERRO CRÍTICO DE CONEXÃO</h1>
        <p>O Flask não conseguiu falar com o Directus em nenhuma das URLs tentadas.</p>
        <p><strong>URLs tentadas:</strong> {', '.join(DIRECTUS_URLS_TO_TRY)}</p>
        <p><strong>Erro Técnico:</strong> {str(last_exception)}</p>
        <hr>
        <h3>Solução:</h3>
        <p>Verifique se o Directus está online e as regras de firewall do Dokploy.</p>
        """, 500

    # 4. Trata erros HTTP (Se estabeleceu conexão, mas o status não é 200/OK)
    if response.status_code != 200:
        # Se conectou, mas o Directus devolveu um erro (ex: 403 Proibido)
        return f"""
        <h1>ERRO NO DIRECTUS: {response.status_code}</h1>
        <p>O Directus recusou a conexão usando a URL: <strong>{successful_url}</strong></p>
        <p><strong>Motivo:</strong> {response.text}</p>
        <p>Verifique se a Role PUBLIC tem permissão de LEITURA na tabela Tenants.</p>
        """, 500

    data = response.json()
    
    # 5. Trata Loja Não Encontrada (404 Lógico)
    # Se conectou com sucesso, mas a lista veio vazia (não achou a loja)
    if not data.get('data'):
         return f"""
        <h1>Loja não encontrada (404)</h1>
        <p>O sistema conectou no Directus, mas não achou nenhuma loja com o subdomínio: <strong>{subdomain}</strong></p>
        <p>Confira no Directus > Tenants se o campo 'subdomain' é exatamente: <code>{subdomain}</code></p>
        """, 404

    # 6. Carrega a loja e busca produtos
    tenant = data['data'][0]
    
    # Busca produtos usando a URL que funcionou
    prod_resp = requests.get(
        f"{successful_url}/items/products",
        params={"filter[tenant_id][_eq]": tenant['id']},
        verify=False
    )
    products = prod_resp.json().get('data', [])
    
    # --- Renderização do Template ---
    
    template_base_name = tenant.get('template_name') or 'home'
    template_file_name = f"{template_base_name}.html"
    
    # Renderiza o template escolhido
    return render_template(template_file_name, tenant=tenant, products=products)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)