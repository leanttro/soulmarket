import os
import requests
from flask import Flask, render_template, request, jsonify 
import urllib3

# Desabilita alertas de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =========================================================================
# CONFIGURAÇÃO DE CONEXÃO (ROBUSTA)
# =========================================================================
DIRECTUS_URL_EXTERNAL = os.getenv("DIRECTUS_URL", "https://directus.leanttro.com")
DIRECTUS_URL_INTERNAL = "http://213.199.56.207:8055" 

DIRECTUS_URLS_TO_TRY = [
    DIRECTUS_URL_EXTERNAL,
    DIRECTUS_URL_INTERNAL
]
# =========================================================================

def clean_url(url):
    if url and url.endswith('/'):
        return url[:-1]
    return url

# Função auxiliar para buscar coleções de forma segura
def fetch_collection_data(url, collection_name, tenant_id, params=None):
    if params is None:
        params = {}
    
    # Adiciona o filtro de tenant_id, garantindo que não sobrescreva outros filtros
    params["filter[tenant_id][_eq]"] = tenant_id

    try:
        response = requests.get(
            f"{url}/items/{collection_name}",
            params=params,
            verify=False,
            timeout=5
        )
        if response.status_code == 200:
            return response.json().get('data', [])
        else:
            # Em caso de erro HTTP (ex: 403, 404), retorna lista vazia
            print(f"Alerta: Falha ao buscar {collection_name}. Status: {response.status_code}")
            return []
    except Exception as e:
        # Em caso de erro de conexão, retorna lista vazia
        print(f"Erro ao buscar {collection_name}: {str(e)}")
        return []

# --- ROTA PRINCIPAL (Renderização de Páginas) ---
@app.route('/')
def home():
    host = request.headers.get('Host', '')
    if 'localhost' in host or '127.0.0.1' in host:
        subdomain = 'teste'
    else:
        subdomain = host.split('.')[0] 

    successful_url = None
    response = None
    last_exception = None

    # 1. LOOP DE TENTATIVA DE CONEXÃO (TENANTS)
    for current_url in DIRECTUS_URLS_TO_TRY:
        current_url = clean_url(current_url)
        try:
            url_tenants = f"{current_url}/items/tenants"
            params = {"filter[subdomain][_eq]": subdomain}
            response = requests.get(url_tenants, params=params, verify=False, timeout=5)
            
            if response is not None and response.status_code is not None:
                successful_url = current_url
                break
        except Exception as e:
            last_exception = e
            continue 
            
    # 2. TRATAMENTO DE ERROS CRÍTICOS (Sem conexão)
    if not successful_url:
        return f"""
        <h1>ERRO CRÍTICO DE CONEXÃO</h1>
        <p>O Flask não conseguiu falar com o Directus em nenhuma das URLs tentadas.</p>
        <p><strong>URLs tentadas:</strong> {', '.join(DIRECTUS_URLS_TO_TRY)}</p>
        <p><strong>Erro Técnico:</strong> {str(last_exception)}</p>
        """, 500

    # 3. TRATAMENTO DE ERROS HTTP DO DIRECTUS
    if response.status_code != 200:
        return f"""
        <h1>ERRO NO DIRECTUS: {response.status_code}</h1>
        <p>O Directus recusou a conexão usando a URL: <strong>{successful_url}</strong></p>
        <p><strong>Motivo:</strong> {response.text}</p>
        <p>Verifique a permissão da Role PUBLIC na tabela Tenants.</p>
        """, 500

    data = response.json()
    
    # 4. TRATAMENTO DE LOJA NÃO ENCONTRADA (404 Lógico)
    if not data.get('data'):
         return f"""
        <h1>Loja não encontrada (404)</h1>
        <p>O sistema conectou no Directus, mas não achou nenhuma loja com o subdomínio: <strong>{subdomain}</strong></p>
        """, 404

    # 5. BUSCA DE DADOS (Coleções)
    tenant = data['data'][0]
    tenant_id = tenant['id']
    
    # Busca Produtos
    products = fetch_collection_data(successful_url, "products", tenant_id)
    
    # Busca SECTIONS (CORRIGIDO: O campo de ordenação é 'order_index' ou 'Order Index' - usando o nome correto)
    sections = fetch_collection_data(
        successful_url, 
        "sections", 
        tenant_id, 
        params={
            "sort": "order_index", # Usando o nome minúsculo do banco, que é mais seguro
            "filter[page_slug][_eq]": "home", 
            "fields": "*.*" 
        }
    )
    
    # Busca Convidados Confirmados (para a lógica Freemium)
    guests_confirmed = fetch_collection_data(
        successful_url, 
        "vaquinha_guests", 
        tenant_id, 
        params={"filter[status][_eq]": "CONFIRMED"}
    )
    
    # Busca Configurações da Vaquinha (pega o primeiro item, se existir)
    vaquinha_settings_list = fetch_collection_data(successful_url, "vaquinha_settings", tenant_id)
    vaquinha_settings = vaquinha_settings_list[0] if vaquinha_settings_list else {}
    
    # 6. RENDERIZAÇÃO
    template_base_name = tenant.get('template_name') or 'home'
    template_file_name = f"{template_base_name}.html"
    
    return render_template(
        template_file_name, 
        tenant=tenant, 
        products=products, 
        sections=sections,
        guests_confirmed=guests_confirmed,
        vaquinha_settings=vaquinha_settings
    )

# --- ROTA DE API (Para o Formulário de Envio de Comprovante) ---
@app.route('/api/confirm_vaquinha', methods=['POST'])
def confirm_vaquinha():
    # Implementação futura para salvar comprovantes
    return jsonify({
        "status": "pending_implementation", 
        "message": "Endpoint de salvamento de comprovante ainda não implementado."
    }), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)