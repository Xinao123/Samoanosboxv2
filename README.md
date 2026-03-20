# SamoanosBox v2

SamoanosBox e um sistema privado de compartilhamento de arquivos para grupos fechados.  
Seu objetivo e distribuir arquivos com baixa espera e manter disponibilidade mesmo quando o dono do arquivo sai da rede.

## O que e

- Plataforma de compartilhamento ponto a ponto assistida por servidor.
- Catalogo central de arquivos e status de disponibilidade por usuario.
- Transferencia direta entre usuarios quando o publicador esta online.
- Copia de contingencia para manter downloads disponiveis quando o publicador fica offline.

## Para que serve

- Compartilhar arquivos grandes entre membros de um mesmo grupo.
- Reduzir o tempo de entrega com rota direta quando possivel.
- Garantir continuidade de acesso com fallback automatico.
- Exibir o estado real de disponibilidade de cada arquivo.

## Comportamento operacional

1. Um usuario publica um arquivo para o grupo.
2. Enquanto o publicador esta online, o download prioriza conexao direta.
3. Se o publicador sair da rede, o sistema usa a copia de contingencia.
4. O usuario final recebe o arquivo pela melhor rota disponivel sem troca manual.
