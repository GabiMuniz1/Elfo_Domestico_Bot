# Agenda Encantada de Hogwarts

Bot de Discord em Python para usar como agenda pessoal dos jogadores e agenda oficial da diretoria.

## O que ele faz

### Agenda pessoal
- `/lembrete_criar`
- `/minha_agenda`
- `/lembrete_hoje`
- `/lembrete_semana`
- `/lembrete_remover`
- `/agenda_painel` com botões e modal

### Agenda da diretoria
- `/diretoria_agendar`
- `/agenda_diretoria`
- `/diretoria_hoje`
- `/diretoria_remover`
- permissão por cargo, por padrão: `Diretor`

### Notificações
- DM para lembretes pessoais
- canal oficial para compromissos da diretoria
- aviso antecipado
- aviso na hora exata

## Instalação

```bash
pip install -U discord.py
```

Crie um arquivo `.env` baseado em `.env_hogwarts_agenda_exemplo` e defina:

- `DISCORD_TOKEN`
- `DEFAULT_GUILD_ID`
- `ROLE_DIRETORIA`
- `CANAL_DIRETORIA_ID`
- `ANTECEDENCIA_PADRAO`
- `AGENDA_DB_PATH`

## Como rodar

```bash
python hogwarts_agenda_bot_v2.py
```

## Canais recomendados no servidor

- `#comandos-do-bot`
- `#agenda-da-diretoria`
- `#avisos-da-direcao`

## Produto final esperado

### Exemplo 1 — lembrete pessoal
Comando:

`/lembrete_criar titulo:Estudar Poções data:10/03/2026 hora:19:00 categoria:estudo`

Resposta do bot:
- embed privado com ID, título, categoria, horário e antecedência.

DM recebida antes do horário:
- `🦉 Sua coruja trouxe um lembrete`

DM recebida no horário:
- `🔔 Compromisso iniciado`

### Exemplo 2 — compromisso oficial da diretoria
Comando:

`/diretoria_agendar titulo:Reunião com chefes de casa data:12/03/2026 hora:20:30 categoria:reunião`

Resposta do bot:
- embed público confirmando o registro no Livro Oficial da Diretoria.

Mensagem enviada no canal oficial antes do horário:
- `📯 Aviso da Diretoria`

Mensagem enviada no horário:
- `🏰 Compromisso oficial iniciado`

## Observações

- O banco de dados é SQLite e é criado automaticamente.
- Para o cargo funcionar, o nome do cargo no Discord precisa bater com `ROLE_DIRETORIA`.
- Se quiser, você pode separar o projeto em vários arquivos depois, mas esta versão já está pronta em arquivo único.
