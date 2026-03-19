# SamoanosBox v2

Compartilhamento de arquivos para os Samoanos.
P2P direto + fallback pro server quando amigo esta offline.

## Como funciona

```
Pedro compartilha "game.iso" (5GB)
  → Arquivo fica NO PC DO PEDRO (P2P ativo)
  → Em background, sobe pro server como backup

Amigo quer baixar:
  → Pedro ONLINE?  → Download P2P direto (velocidade maxima)
  → Pedro OFFLINE? → Download do server (mais lento, mas funciona)
```

## Legenda de status na lista de arquivos

- VERDE (online): "Pedro (online - P2P direto)" → download rapido
- LARANJA (offline + backup): "Pedro (offline - via server, mais lento)" → funciona
- VERMELHO (offline sem backup): "Pedro (offline - indisponivel)" → precisa esperar

## Setup

### Server (Linux)
```bash
cd server
pip3 install -r requirements.txt
python3 main.py
```

### Client (Windows)
```powershell
cd client
pip install -r requirements.txt
python main.py
```

## Estrutura

```
SamoanosBox/
├── server/
│   ├── main.py          # API + WebSocket + storage fallback
│   ├── database.py      # SQLite
│   ├── config.py
│   ├── requirements.txt
│   └── Dockerfile
├── client/
│   ├── main.py          # GUI Flet
│   ├── api_client.py    # Download P2P/server inteligente
│   ├── p2p_server.py    # Mini HTTP server embutido
│   ├── config.py
│   └── requirements.txt
├── docker-compose.yml
└── README.md
```

## Fluxo tecnico

1. Client abre → inicia mini HTTP server numa porta aleatoria
2. Client conecta WebSocket → anuncia IP:porta P2P pro server
3. Client compartilha arquivo → registra metadados no server + serve via P2P
4. Em background, arquivo sobe pro server como fallback
5. Outro client quer baixar → server informa se dono ta online e seu IP:porta
6. Se online → download P2P direto (HTTP entre os dois PCs)
7. Se offline → download do server (fallback)

## Acesso externo

Use ZeroTier, Tailscale ou bore para acesso fora da rede local.
