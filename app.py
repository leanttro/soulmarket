import os
import requests
from flask import Flask, render_template, request
import urllib3

# Desabilita alertas de SSL, pois estamos usando IPs internos/certificados auto-assinados
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# VARIÁVEIS DE CONEXÃO (Lógica de Múltiplas Tentativas - Mais Robusto)
# =========================================================================
# 1. Tenta pegar do ambiente (idealmente HTTPS externo)
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
# 2. Endereço IP interno (sem HTTPS) para fallback no Dokploy
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 

# Lista de URLs para tentar, priorizando o EXTERNO (Dokploy)
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
            response = requests.get(url_tenants, params=params, verify=False, timeout=5)
            
            # Se conseguiu uma resposta 200 (Sucesso) ou um erro HTTP, encerra o loop
            if response is not None and response.status_code is not None:
                successful_url = current_url
                break

        except Exception as e:
            last_exception = e
            continue 
            
    # 3. Trata Falha Total de Conexão
    if not successful_url:
        return f"""
        <h1>ERRO CRÍTICO DE CONEXÃO</h1>
        <p>O Flask não conseguiu falar com o Directus em nenhuma das URLs tentadas.</p>
        <p><strong>URLs tentadas:</strong> {', '.join(DIRECTUS_URLS_TO_TRY)}</p>
        <p><strong>Erro Técnico:</strong> {str(last_exception)}</p>
        <hr>
        <h3>Solução:</h3>
        <p>Verifique o status do seu Directus.</p>
        """, 500

    # 4. Trata erros HTTP (Se conectou, mas o status não é 200/OK)
    if response.status_code != 200:
        return f"""
        <h1>ERRO NO DIRECTUS: {response.status_code}</h1>
        <p>O Directus recusou a conexão usando a URL: <strong>{successful_url}</strong></p>
        <p><strong>Motivo:</strong> {response.text}</p>
        <p>Verifique a permissão da Role PUBLIC na tabela Tenants.</p>
        """, 500

    data = response.json()
    
    # 5. Trata Loja Não Encontrada (404 Lógico)
    if not data.get('data'):
         return f"""
        <h1>Loja não encontrada (404)</h1>
        <p>O sistema conectou no Directus, mas não achou nenhuma loja com o subdomínio: <strong>{subdomain}</strong></p>
        """, 404

    # 6. Carrega a loja e busca dados relacionados
    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Busca produtos
    prod_resp = requests.get(
        f"{successful_url}/items/products",
        params={"filter[tenant_id][_eq]": tenant_id},
        verify=False
    )
    products = prod_resp.json().get('data', [])
    
    # Busca SECTIONS (Com fields=*.* para garantir que todo o conteúdo aninhado seja carregado)
    sections_resp = requests.get(
        f"{successful_url}/items/sections", 
        params={
            "filter[tenant_id][_eq]": tenant_id, 
            "sort": "Order_Index", 
            "filter[page_slug][_eq]": "home", 
            "fields": "*.*" 
        }, 
        verify=False
    )
    sections = sections_resp.json().get('data', []) 
    
    # =========================================================================
    # --- NOVAS BUSCAS PARA VAQUINHA/FREEMIUM ---
    # =========================================================================

    # Busca Convidados (apenas confirmados)
    guests_resp = requests.get(
        f"{successful_url}/items/vaquinha_guests", 
        params={
            "filter[tenant_id][_eq]": tenant_id,
            "filter[status][_eq]": "CONFIRMED" # Buscamos apenas os que pagaram
        }, 
        verify=False
    )
    # A lista de convidados confirmados é usada para o contador Freemium
    guests_confirmed = guests_resp.json().get('data', []) 
    
    # Busca Configurações da Vaquinha (geralmente é um item por tenant)
    settings_resp = requests.get(
        f"{successful_url}/items/vaquinha_settings", 
        params={"filter[tenant_id][_eq]": tenant_id}, 
        verify=False
    )
    # Pega o primeiro item, se existir
    vaquinha_settings = settings_resp.json().get('data', [{}])[0]
    
    # =========================================================================
    
    # --- Renderização do Template ---
    
    template_base_name = tenant.get('template_name') or 'home'
    template_file_name = f"{template_base_name}.html"
    
    # 3. Renderiza o template, passando TODAS as coleções
    return render_template(
        template_file_name, 
        tenant=tenant, 
        products=products, 
        sections=sections,
        # Variáveis Adicionais para Vaquinha
        guests_confirmed=guests_confirmed,
        vaquinha_settings=vaquinha_settings
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)