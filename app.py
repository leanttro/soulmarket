# =========================================================================
# 2. AUTOMAÇÃO DOKPLOY (HARDCODED - FORÇADO PARA FUNCIONAR)
# =========================================================================
def create_dokploy_domain(subdomain):
    # --- AQUI ESTÁ A MÁGICA: CHAVES COLADAS DIRETO NO CÓDIGO ---
    # Copiei do seu print image_7129c1.png
    DOKPLOY_URL = "http://213.199.56.207:3000"
    DOKPLOY_TOKEN = "hDeLWmSnMyLtTDQlthigbwqWCFMhvIkjzqNPYIdoXUzmPFRQsjsqMOBhFRYixrvk"
    APP_ID = "GYJuZwAcZAMb8s9v-S-" 
    # -----------------------------------------------------------
    
    # Limpa a URL
    if DOKPLOY_URL.endswith('/'): DOKPLOY_URL = DOKPLOY_URL[:-1]

    full_domain = f"{subdomain}.leanttro.com"
    print(f"DEBUG: Forçando criação de {full_domain} no App ID {APP_ID}")

    headers = {
        "Authorization": f"Bearer {DOKPLOY_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "applicationId": APP_ID,
        "host": full_domain,
        "path": "/",
        "port": 5000,
        "https": True,
        "certificateType": "letsencrypt"
    }
    
    try:
        response = requests.post(
            f"{DOKPLOY_URL}/api/domain.create", 
            json=payload, 
            headers=headers, 
            timeout=20
        )
        
        if response.status_code in [200, 201]:
            print(f"✅ [SUCESSO] Domínio {full_domain} criado!")
            return True
        else:
            print(f"⚠️ [ERRO] Dokploy respondeu: {response.text}")
            return False
            
    except Exception as e:
        print(f"⛔ [ERRO FATAL] {str(e)}")
        return False